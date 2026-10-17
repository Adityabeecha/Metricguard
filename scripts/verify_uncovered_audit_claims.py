import sys
from pathlib import Path
import json
import os
import time
import subprocess
import sqlite3
import tempfile
import httpx
from decimal import Decimal

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from packages.metricguard_core.relational_compare import compare_relational
from packages.metricguard_core.relational_snapshot import ingest_relational, validate_manifest
from packages.metricguard_core.relational import context_for, parse_relational, validate_contract, quote
from services.supervisor.evidence_artifact import verify_artifact

DATA_DIR = Path('data/v8_final_acceptance')
keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
meta = json.loads((DATA_DIR / 'test_metadata.json').read_text(encoding='utf-8'))

pid = 'ws-relational-final'
API_URL = 'http://127.0.0.1:8001'

c_owner = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['owner']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)
c_analyst = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['analyst']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)
c_reader = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['reader']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)
c_outsider = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['outsider']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)


def ensure_service_running():
    """Ensure API and supervisor worker are active on port 8001 for isolated execution."""
    try:
        r = httpx.get(f'{API_URL}/api/v1/health', timeout=1.0)
        if r.status_code == 200:
            return None, None
    except Exception:
        pass

    env = os.environ.copy()
    env['METRICGUARD_MODE'] = 'isolated'
    env['METRICGUARD_DATA_DIR'] = str(DATA_DIR.resolve())
    env['METRICGUARD_DATABASE_URL'] = f"sqlite:///{(DATA_DIR / 'metricguard.db').resolve()}"
    env['METRICGUARD_ALLOW_UNVERIFIED_LOCAL'] = 'false'
    env['METRICGUARD_RUNNER_IMAGE'] = 'metricguard-runner:1.0'

    api_proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'services.api.app:create_app', '--factory', '--host', '127.0.0.1', '--port', '8001'],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    sup_proc = subprocess.Popen(
        [sys.executable, '-m', 'services.supervisor.worker'],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    for _ in range(30):
        try:
            r = httpx.get(f'{API_URL}/api/v1/health', timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.5)

    return api_proc, sup_proc


def poll_comparison_run(run_id, timeout_sec=60):
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        r = c_analyst.get(f'/api/v1/comparisons/{run_id}')
        if r.status_code == 200:
            st = r.json().get('state')
            if st in ('succeeded', 'failed', 'partial'):
                return r.json()
        time.sleep(1)
    raise TimeoutError(f"Comparison run {run_id} did not reach terminal state within {timeout_sec}s.")


def main():
    print("=================================================================")
    print("AUDIT AND EXECUTE UNCOVERED COVERAGE CLAIMS")
    print("=================================================================")

    api_proc, sup_proc = ensure_service_running()
    receipt_records = {}

    try:
        # -------------------------------------------------------------------
        # 1. Empty Aggregates vs All-NULL Aggregates (Direct Core Checks)
        # -------------------------------------------------------------------
        print("\n--- 1. Empty Aggregates Separately from All-NULL Aggregates (Direct Core Checks) ---")
        conn = sqlite3.connect(DATA_DIR / 'metricguard.db')
        snap_row = conn.execute("SELECT data FROM snapshots WHERE id = ?", (meta['snapshot_id'],)).fetchone()
        conn.close()
        snap_data = json.loads(snap_row[0])
        snap_path = Path(snap_data['artifact_path'])
        contract = snap_data['manifest']['contract']
        context = context_for(contract)

        base_sql = "SELECT SUM(f.amount) FROM fact_items f LEFT JOIN dim_groups d ON f.group_id = d.group_id"
        base_ir = parse_relational(base_sql, contract)

        # Case 1A: Empty aggregate (matches 0 rows)
        empty_sql = "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE f.kind = 'nonexistent_group'"
        empty_target_ir = parse_relational(empty_sql, contract)
        empty_result = compare_relational(snap_path, base_ir, empty_target_ir, context)
        print("Empty Aggregate Result:")
        print(f"  baseline_value: {empty_result['baseline_value']}, target_value: {empty_result['target_value']}")
        print(f"  delta: {empty_result['delta_target_minus_baseline']}, value_status: {empty_result['value_status']}")
        print(f"  explanation_status: {empty_result['explanation_status']}")
        assert empty_result['target_value'] is None, "Empty aggregate target_value must be None (NULL SUM)"
        assert empty_result['delta_target_minus_baseline'] is None, "Empty aggregate delta must be None"
        assert empty_result['value_status'] == 'undefined'
        assert empty_result['explanation_status'] == 'unavailable'
        for row in empty_result['evidence']:
            assert row['target_included'] is False

        # Case 1B: All-NULL aggregate (matches rows whose measure is NULL)
        import zipfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            fact_csv_content = "fact_id,group_id,amount,kind\nN1,D1,,web\nN2,D1,,web\n"
            dim_csv_content = "group_id,segment\nD1,north\n"
            null_zip = tmp / 'null_bundle.zip'
            with zipfile.ZipFile(null_zip, 'w') as zf:
                zf.writestr('fact.csv', fact_csv_content.encode('utf-8'))
                zf.writestr('dimension.csv', dim_csv_content.encode('utf-8'))
            all_null_db = tmp / 'null_snap.duckdb'
            ingest_relational(null_zip, all_null_db, contract)

            all_null_res = compare_relational(all_null_db, base_ir, base_ir, context)
            print("All-NULL Aggregate Result:")
            print(f"  baseline_value: {all_null_res['baseline_value']}, target_value: {all_null_res['target_value']}")
            print(f"  delta: {all_null_res['delta_target_minus_baseline']}, value_status: {all_null_res['value_status']}")
            assert all_null_res['baseline_value'] is None
            assert all_null_res['target_value'] is None
            assert all_null_res['delta_target_minus_baseline'] is None
            for row in all_null_res['evidence']:
                assert row['target_included'] is True
                assert row['target_null_term'] is True
                assert row['measure_value'] is None

        print("-> Empty aggregate vs All-NULL aggregate PASS: differentiated by included row terms and null_term flags.")
        receipt_records['empty_vs_all_null_aggregates'] = {
            'execution_mode': 'direct_core_function_checks',
            'empty_aggregate': {
                'target_included_rows': sum(1 for r in empty_result['evidence'] if r['target_included']),
                'target_value': empty_result['target_value'],
                'status': empty_result['value_status']
            },
            'all_null_aggregate': {
                'target_included_rows': len(all_null_res['evidence']),
                'target_null_terms': sum(1 for r in all_null_res['evidence'] if r['target_null_term']),
                'target_value': all_null_res['target_value'],
                'status': all_null_res['value_status']
            }
        }

        # -------------------------------------------------------------------
        # 2. Arithmetic Boundaries (Actual Isolated Workflow, DECIMAL(38,0))
        # -------------------------------------------------------------------
        print("\n--- 2. Actual Arithmetic Boundaries (Isolated Application Workflow, DECIMAL(38,0)) ---")
        M = 10**38 - 1  # 99999999999999999999999999999999999999

        # Independent expectation calculations using pure Python integers:
        expected_wide_base = -M
        expected_wide_target = M
        expected_wide_gap = expected_wide_target - expected_wide_base  # 2 * M = 19999999999999999999999999999999999998
        expected_wide_residual = 0

        expected_cancel_base = 1  # (+M + 1 - M) = 1
        expected_cancel_target = 1  # (+1) = 1
        expected_cancel_gap = 0
        expected_cancel_residual = 0

        # Step 2.1: Register registered relational contract declaring DECIMAL(38,0)
        contract_payload = {
            "version": 1,
            "mode": "relational_snapshot",
            "policy": "relational-snapshot-v1",
            "name": f"Decimal 38 Bounded Arithmetic Contract {int(time.time())}",
            "fact": {
                "table": "fact_items",
                "key": "fact_id",
                "grain": "one row per fact item",
                "categories": ["kind"]
            },
            "dimension": {
                "table": "dim_groups",
                "key": "group_id",
                "grain": "one row per group",
                "categories": ["segment"]
            },
            "relationship": {
                "fact_foreign_key": "group_id",
                "dimension_key": "group_id",
                "cardinality": "many_to_one",
                "missing_dimension_policy": "preserve_and_report"
            },
            "measure": {
                "column": "amount",
                "precision": 38,
                "scale": 0
            },
            "measure_meaning": "Arbitrary large integer metric units",
            "unit_label": "units",
            "values_comparable": True,
            "scope": "entire_supplied_snapshot",
            "null_policy": "blank_nullable"
        }

        r_c = c_analyst.post('/api/v1/relational/contracts', json=contract_payload)
        assert r_c.status_code == 201, f"Contract creation failed: {r_c.text}"
        c_data = r_c.json()
        contract_id = c_data['id']
        contract_hash = c_data['contract_hash']
        print(f"Registered DECIMAL(38,0) Contract: ID {contract_id}, Hash {contract_hash}")

        # Step 2.2: Import snapshot with wide, cancelling, and overflowing facts
        f_csv = (
            "fact_id,group_id,amount,kind\n"
            f"C1,D1,{M},cancel\n"
            "C2,D1,1,cancel_one\n"
            f"C3,D1,-{M},cancel\n"
            f"W1,D1,-{M},wide_neg\n"
            f"W2,D1,{M},wide_pos\n"
            f"O1,D1,{M},overflow\n"
            "O2,D1,1,overflow\n"
        )
        d_csv = "group_id,segment\nD1,alpha\n"

        files = {
            'fact_file': ('fact_items.csv', f_csv.encode('utf-8'), 'text/csv'),
            'dimension_file': ('dim_groups.csv', d_csv.encode('utf-8'), 'text/csv'),
        }
        data = {
            'contract_id': contract_id,
            'name': f"Arithmetic Boundary Snapshot {int(time.time())}"
        }
        r_imp = c_analyst.post('/api/v1/relational/snapshots', data=data, files=files)
        assert r_imp.status_code == 202, f"Snapshot import failed: {r_imp.text}"
        imp_job_id = r_imp.json()['id']

        snap_id = None
        for _ in range(60):
            r_poll = c_analyst.get(f'/api/v1/snapshot-imports/{imp_job_id}')
            if r_poll.status_code == 200:
                dt = r_poll.json()
                if dt.get('state') == 'validated':
                    snap_id = dt['snapshot_id']
                    break
                elif dt.get('state') == 'failed':
                    raise RuntimeError(f"Snapshot import failed: {dt.get('error')}")
            time.sleep(1)
        assert snap_id is not None, "Snapshot import did not validate in time"
        print(f"Imported snapshot successfully: ID {snap_id}")

        comparison_context = {
            "mode": "relational_snapshot",
            "scope": "entire_supplied_snapshot",
            "contract_hash": contract_hash,
            "unit_label": "units",
            "values_comparable": True
        }

        # -------------------------------------------------------------------
        # 2A. Wide Gap Test: baseline -M, target +M -> gap 2M, residual 0
        # -------------------------------------------------------------------
        print("\n--- 2A: Wide Gap (Baseline -M, Target +M) ---")
        r_wb = c_analyst.post('/api/v1/relational/definitions', json={
            'contract_id': contract_id,
            'name': f"Wide Base -M {int(time.time())}",
            'raw_sql': "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE f.kind = 'wide_neg'"
        })
        wb_vid = r_wb.json()['versions'][0]['id']

        r_wt = c_analyst.post('/api/v1/relational/definitions', json={
            'contract_id': contract_id,
            'name': f"Wide Target +M {int(time.time())}",
            'raw_sql': "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE f.kind = 'wide_pos'"
        })
        wt_vid = r_wt.json()['versions'][0]['id']

        r_wide_queue = c_analyst.post('/api/v1/relational/comparisons', json={
            "baseline_version_id": wb_vid,
            "target_version_id": wt_vid,
            "snapshot_id": snap_id,
            "context": comparison_context
        }, headers={'Idempotency-Key': f"wide-{time.time()}"})
        assert r_wide_queue.status_code == 202
        w_run_id = r_wide_queue.json()['id']

        w_run = poll_comparison_run(w_run_id)
        assert w_run['state'] == 'succeeded', f"Wide gap run failed: {w_run.get('error')}"
        w_res = w_run['result']
        print(f"Wide Gap Actual: Baseline={w_res['baseline_value']}, Target={w_res['target_value']}, Delta={w_res['delta_target_minus_baseline']}, Residual={w_res['residual']}")
        assert w_res['baseline_value'] == str(expected_wide_base)
        assert w_res['target_value'] == str(expected_wide_target)
        assert w_res['delta_target_minus_baseline'] == str(expected_wide_gap)
        assert w_res['residual'] == str(expected_wide_residual)

        # Verify row reconciliation from published evidence artifact
        w_ev_files = list((DATA_DIR / 'evidence').glob(f"{w_run_id}-*.jsonl"))
        assert len(w_ev_files) > 0, "Evidence artifact missing for wide gap run"
        w_ev_rows = [json.loads(line) for line in w_ev_files[0].read_text(encoding='utf-8').splitlines()]
        row_delta_sum = sum(int(r['delta']) for r in w_ev_rows)
        assert row_delta_sum == expected_wide_gap, f"Row deltas sum {row_delta_sum} != expected gap {expected_wide_gap}"
        print(f"-> Wide Gap PASS: Exact match (Gap=2M={expected_wide_gap}, Residual=0, Row deltas reconcile exactly).")

        # -------------------------------------------------------------------
        # 2B. Cancellation Test: facts ordered +M, +1, -M -> sum = 1
        # -------------------------------------------------------------------
        print("\n--- 2B: Cancellation (Ordered Facts +M, +1, -M; Intermediate > Capacity) ---")
        r_cb = c_analyst.post('/api/v1/relational/definitions', json={
            'contract_id': contract_id,
            'name': f"Cancel Base (+M, +1, -M) {int(time.time())}",
            'raw_sql': "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE f.kind IN ('cancel', 'cancel_one')"
        })
        cb_vid = r_cb.json()['versions'][0]['id']

        r_ct = c_analyst.post('/api/v1/relational/definitions', json={
            'contract_id': contract_id,
            'name': f"Cancel Target (+1 fact filter) {int(time.time())}",
            'raw_sql': "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE f.kind = 'cancel_one'"
        })
        ct_vid = r_ct.json()['versions'][0]['id']

        r_cancel_queue = c_analyst.post('/api/v1/relational/comparisons', json={
            "baseline_version_id": cb_vid,
            "target_version_id": ct_vid,
            "snapshot_id": snap_id,
            "context": comparison_context
        }, headers={'Idempotency-Key': f"cancel-{time.time()}"})
        assert r_cancel_queue.status_code == 202
        c_run_id = r_cancel_queue.json()['id']

        c_run = poll_comparison_run(c_run_id)
        assert c_run['state'] == 'succeeded', f"Cancellation run failed: {c_run.get('error')}"
        c_res = c_run['result']
        print(f"Cancellation Actual: Baseline={c_res['baseline_value']}, Target={c_res['target_value']}, Delta={c_res['delta_target_minus_baseline']}, Residual={c_res['residual']}")
        assert c_res['baseline_value'] == str(expected_cancel_base)
        assert c_res['target_value'] == str(expected_cancel_target)
        assert c_res['delta_target_minus_baseline'] == str(expected_cancel_gap)
        assert c_res['residual'] == str(expected_cancel_residual)
        print("-> Cancellation PASS: Complete SUM equals 1 despite intermediate total exceeding capacity. Gap=0, Residual=0.")

        # -------------------------------------------------------------------
        # 2C. Overflow Test: included facts +M and +1 -> endpoint sum 10^38 refused
        # -------------------------------------------------------------------
        print("\n--- 2C: Endpoint Overflow Refusal (Facts +M and +1; Sum 10^38) ---")
        r_ov = c_analyst.post('/api/v1/relational/definitions', json={
            'contract_id': contract_id,
            'name': f"Overflow Endpoint (+M and +1) {int(time.time())}",
            'raw_sql': "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE f.kind = 'overflow'"
        })
        ov_vid = r_ov.json()['versions'][0]['id']

        r_over_queue = c_analyst.post('/api/v1/relational/comparisons', json={
            "baseline_version_id": ov_vid,
            "target_version_id": ov_vid,
            "snapshot_id": snap_id,
            "context": comparison_context
        }, headers={'Idempotency-Key': f"overflow-{time.time()}"})
        assert r_over_queue.status_code == 202
        o_run_id = r_over_queue.json()['id']

        o_run = poll_comparison_run(o_run_id)
        print(f"Overflow Run Terminal State: {o_run['state']}")
        print(f"Overflow Structured Error: {o_run.get('error')}")
        assert o_run['state'] == 'failed', f"Expected terminal state 'failed', got {o_run['state']}"
        actual_error = o_run.get('error', {})
        assert actual_error.get('code') == 'ARITHMETIC_OVERFLOW', f"Expected ARITHMETIC_OVERFLOW, got {actual_error}"
        assert 'capacity' in actual_error.get('message', ''), f"Unexpected error message: {actual_error.get('message')}"
        print("-> Overflow Refusal PASS: Actual application refused 10^38 endpoint sum with structured error ARITHMETIC_OVERFLOW.")

        receipt_records['arithmetic_boundaries'] = {
            'execution_mode': 'actual_isolated_application_execution',
            'contract': {
                'declared_type': 'DECIMAL(38,0)',
                'precision': 38,
                'scale': 0,
                'm_constant': str(M)
            },
            'wide_gap': {
                'run_id': w_run_id,
                'baseline_sum': w_res['baseline_value'],
                'target_sum': w_res['target_value'],
                'delta': w_res['delta_target_minus_baseline'],
                'residual': w_res['residual'],
                'expected_delta_2M': str(expected_wide_gap),
                'exact_row_reconciliation': True,
                'residual_is_zero': True,
                'status': 'PASS'
            },
            'cancellation': {
                'run_id': c_run_id,
                'baseline_sum': c_res['baseline_value'],
                'target_sum': c_res['target_value'],
                'delta': c_res['delta_target_minus_baseline'],
                'residual': c_res['residual'],
                'intermediate_total_exceeded_capacity': True,
                'intermediate_total_sum': str(M + 1),
                'complete_sum': '1',
                'status': 'PASS'
            },
            'overflow_refusal': {
                'run_id': o_run_id,
                'endpoint_sum_attempted': str(M + 1),
                'terminal_job_state': o_run['state'],
                'actual_structured_error': actual_error,
                'status': 'PASS'
            }
        }

        # -------------------------------------------------------------------
        # 3. Duplicate/NULL Keys, Invalid Amounts & Missing Fields (Direct Core Checks)
        # -------------------------------------------------------------------
        print("\n--- 3. Duplicate/NULL Keys, Invalid Amounts & Missing Referenced Fields (Direct Core Checks) ---")
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            dim_csv_ok = "group_id,segment\nD1,north\n"

            def make_zip(f_txt, d_txt, z_name):
                z_path = tmp / z_name
                with zipfile.ZipFile(z_path, 'w') as zf:
                    zf.writestr('fact.csv', f_txt.encode('utf-8'))
                    zf.writestr('dimension.csv', d_txt.encode('utf-8'))
                return z_path

            # 3A: Duplicate Fact Key
            f_dup = "fact_id,group_id,amount,kind\nF1,D1,10.00,web\nF1,D1,20.00,store\n"
            z_dup = make_zip(f_dup, dim_csv_ok, 'dup.zip')
            dup_rejected = False
            try:
                ingest_relational(z_dup, tmp / 'dup.duckdb', contract)
            except Exception as e:
                dup_rejected = True
                print(f"Duplicate fact key rejected: {e}")
            assert dup_rejected, "Duplicate fact key must be rejected"

            # 3B: NULL Fact Key
            f_null = "fact_id,group_id,amount,kind\n,D1,10.00,web\n"
            z_null = make_zip(f_null, dim_csv_ok, 'null.zip')
            null_rejected = False
            try:
                ingest_relational(z_null, tmp / 'null.duckdb', contract)
            except Exception as e:
                null_rejected = True
                print(f"NULL fact key rejected: {e}")
            assert null_rejected, "NULL fact key must be rejected"

            # 3C: Duplicate Dimension Key
            d_dup = "group_id,segment\nD1,north\nD1,south\n"
            f_ok = "fact_id,group_id,amount,kind\nF1,D1,10.00,web\n"
            z_dim_dup = make_zip(f_ok, d_dup, 'dim_dup.zip')
            dim_dup_rejected = False
            try:
                ingest_relational(z_dim_dup, tmp / 'dim_dup.duckdb', contract)
            except Exception as e:
                dim_dup_rejected = True
                print(f"Duplicate dimension key rejected: {e}")
            assert dim_dup_rejected, "Duplicate dimension key must be rejected"

            # 3D: Invalid Decimal String
            f_bad_amt = "fact_id,group_id,amount,kind\nF1,D1,12.345,web\n"
            z_bad_amt = make_zip(f_bad_amt, dim_csv_ok, 'bad_amt.zip')
            bad_amt_rejected = False
            try:
                ingest_relational(z_bad_amt, tmp / 'bad_amt.duckdb', contract)
            except Exception as e:
                bad_amt_rejected = True
                print(f"Invalid decimal scale rejected: {e}")
            assert bad_amt_rejected, "Scale mismatch in amount must be rejected"

            # 3E: Missing referenced column in SQL
            missing_col_sql = "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE f.nonexistent_column = 'x'"
            missing_col_rejected = False
            try:
                parse_relational(missing_col_sql, contract)
            except Exception as e:
                missing_col_rejected = True
                print(f"Missing referenced column in SQL rejected: {e}")
            assert missing_col_rejected, "Missing referenced categorical column must be rejected"

        receipt_records['ingestion_and_sql_validation'] = {
            'execution_mode': 'direct_core_function_checks',
            'duplicate_fact_key_rejected': dup_rejected,
            'null_fact_key_rejected': null_rejected,
            'duplicate_dimension_key_rejected': dim_dup_rejected,
            'invalid_decimal_scale_rejected': bad_amt_rejected,
            'missing_referenced_column_rejected': missing_col_rejected
        }

        # -------------------------------------------------------------------
        # 4. Tampering Defenses (Direct Core Checks)
        # -------------------------------------------------------------------
        print("\n--- 4. Tampering Defenses (Direct Core Checks) ---")
        ev_file = list((DATA_DIR / 'evidence').glob(f"{meta['run_id']}-*.jsonl"))[0]
        artifact_path = ev_file
        manifest = snap_data['manifest']

        r_run = c_analyst.get(f"/api/v1/comparisons/{meta['run_id']}")
        run_result = r_run.json()['result']

        genuine_r_ctx = {
            'contract': contract,
            'manifest': manifest,
            'baseline': base_ir,
            'target': parse_relational("SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE d.segment = 'north'", contract)
        }

        # 4A: Tamper contract in relational_context
        contract_tamper_rejected = False
        tampered_r_ctx = dict(genuine_r_ctx)
        tampered_r_ctx['contract'] = dict(contract)
        tampered_r_ctx['contract']['unit_label'] = 'tampered_currency'
        try:
            verify_artifact(artifact_path, manifest, run_result['delta_target_minus_baseline'],
                            relational_context=tampered_r_ctx,
                            expected_result=run_result, include_join_evidence=True)
        except ValueError as e:
            contract_tamper_rejected = True
            print(f"Tampered contract rejected: {e}")
        assert contract_tamper_rejected

        # 4B: Tamper row delta in result
        delta_tamper_rejected = False
        tampered_result = dict(run_result)
        tampered_result['delta_target_minus_baseline'] = '999.00'
        try:
            verify_artifact(artifact_path, manifest, '999.00',
                            relational_context=genuine_r_ctx,
                            expected_result=tampered_result, include_join_evidence=True)
        except ValueError as e:
            delta_tamper_rejected = True
            print(f"Tampered delta rejected: {e}")
        assert delta_tamper_rejected

        # 4C: Tamper definition IR in relational_context
        def_tamper_rejected = False
        tampered_def_ctx = dict(genuine_r_ctx)
        tampered_def_ctx['baseline'] = dict(base_ir)
        tampered_def_ctx['baseline']['join'] = 'inner'  # altered from left
        try:
            verify_artifact(artifact_path, manifest, run_result['delta_target_minus_baseline'],
                            relational_context=tampered_def_ctx,
                            expected_result=run_result, include_join_evidence=True)
        except ValueError as e:
            def_tamper_rejected = True
            print(f"Tampered definition IR rejected: {e}")
        assert def_tamper_rejected

        receipt_records['tampering_defenses'] = {
            'execution_mode': 'direct_core_function_checks',
            'contract_tamper_rejected': contract_tamper_rejected,
            'delta_tamper_rejected': delta_tamper_rejected,
            'definition_ir_tamper_rejected': def_tamper_rejected
        }

        # -------------------------------------------------------------------
        # 5. Relevant Authorization & Historical-Policy Compatibility
        # -------------------------------------------------------------------
        print("\n--- 5. Authorization RBAC & Historical Policy Compatibility (Authenticated Routes) ---")
        # 5A: Reader mutation attempt
        r_reader_mut = c_reader.post('/api/v1/definitions', json={
            'name': 'Reader forbidden def',
            'raw_sql': 'SELECT SUM(f.amount) FROM fact_items f',
            'source_label': 'analytics'
        })
        print(f"Reader mutation response: HTTP {r_reader_mut.status_code}")
        assert r_reader_mut.status_code == 403, "Reader mutation must return HTTP 403"

        # 5B: Outsider access attempt
        r_out_read = c_outsider.get(f'/api/v1/comparisons/{meta["run_id"]}')
        print(f"Outsider access response: HTTP {r_out_read.status_code}")
        assert r_out_read.status_code == 403, "Outsider access must return HTTP 403"

        # 5C: Historical /evidence endpoint
        r_hist = c_analyst.get(f'/api/v1/comparisons/{meta["run_id"]}/evidence?offset=0&limit=100')
        assert r_hist.status_code == 200
        hist_json = r_hist.json()
        assert 'items' in hist_json and 'total' in hist_json
        assert hist_json['total'] == 5
        assert len(hist_json['items']) == 5
        print("Historical /evidence endpoint returned original {items, total} structure.")

        receipt_records['authorization_and_compatibility'] = {
            'execution_mode': 'authenticated_application_routes',
            'reader_mutation_refused_code': r_reader_mut.status_code,
            'outsider_access_refused_code': r_out_read.status_code,
            'historical_evidence_endpoint_verified': True
        }

        # -------------------------------------------------------------------
        # Save Receipt
        # -------------------------------------------------------------------
        out_receipt = {
            'gate': 'UNCOVERED_COVERAGE_CLAIMS_AUDIT',
            'status': 'PASS',
            'records': receipt_records
        }
        out_file = DATA_DIR / 'receipts' / 'receipt_uncovered_audit_claims.json'
        out_file.write_text(json.dumps(out_receipt, indent=2), encoding='utf-8')
        print(f"\nSaved updated receipt to {out_file}")

    finally:
        if api_proc:
            api_proc.terminate()
            api_proc.wait(timeout=5)
        if sup_proc:
            sup_proc.terminate()
            sup_proc.wait(timeout=5)
        print("Cleanup completed.")


if __name__ == '__main__':
    main()
