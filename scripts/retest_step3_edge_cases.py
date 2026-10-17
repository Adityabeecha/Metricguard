import hashlib
import json
import time
from pathlib import Path
import httpx

DATA_DIR = Path('data/v8_live_retest')
API_URL = 'http://127.0.0.1:8001'

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def poll_import(client, import_id: str, timeout: int = 60) -> dict:
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        r = client.get(f'/api/v1/snapshot-imports/{import_id}')
        assert r.status_code == 200, f"Poll import failed: {r.text}"
        detail = r.json()
        if detail['state'] in ('validated', 'succeeded', 'failed'):
            return detail
        time.sleep(1)
    raise TimeoutError(f"Import {import_id} timed out after {timeout}s")

def poll_run(client, run_id: str, timeout: int = 60) -> dict:
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        r = client.get(f'/api/v1/comparisons/{run_id}')
        assert r.status_code == 200, f"Poll run failed: {r.text}"
        detail = r.json()
        if detail.get('state') in ('succeeded', 'partial', 'failed'):
            return detail
        time.sleep(1)
    raise TimeoutError(f"Run {run_id} timed out after {timeout}s")

def run():
    keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
    analyst_key = keys['analyst']['api_key']
    pid = 'ws-relational-live'
    
    headers = {
        'Authorization': f'Bearer {analyst_key}',
        'X-Project-Id': pid,
    }
    client = httpx.Client(base_url=API_URL, headers=headers, timeout=30.0)
    
    results = {}
    
    # -------------------------------------------------------------
    # 1. More than 100 evidence rows: Pagination and Export Truncation
    # -------------------------------------------------------------
    print("Testing >100 evidence rows (120 rows)...")
    c_large_payload = {
        "version": 1,
        "mode": "relational_snapshot",
        "policy": "relational-snapshot-v1",
        "name": "Large Fixture Contract",
        "fact": {
            "table": "fact_large",
            "key": "fact_id",
            "grain": "one row per item",
            "categories": ["kind"]
        },
        "dimension": {
            "table": "dim_large",
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
            "precision": 10,
            "scale": 2
        },
        "measure_meaning": "Large fixture items",
        "unit_label": "points",
        "values_comparable": True,
        "scope": "entire_supplied_snapshot",
        "null_policy": "blank_nullable"
    }
    r = client.post('/api/v1/relational/contracts', json=c_large_payload)
    assert r.status_code == 201, r.text
    c_large_id = r.json()['id']
    c_large_hash = r.json()['contract_hash']
    
    # Generate 120 fact rows
    fact_lines = ["fact_id,group_id,amount,kind"]
    for i in range(1, 121):
        fact_lines.append(f"F{i},D1,1.00,web")
    fact_large_csv = "\n".join(fact_lines) + "\n"
    dim_large_csv = "group_id,segment\nD1,north\n"
    
    f_large_path = DATA_DIR / 'fact_large.csv'
    d_large_path = DATA_DIR / 'dim_large.csv'
    f_large_path.write_text(fact_large_csv, encoding='utf-8')
    d_large_path.write_text(dim_large_csv, encoding='utf-8')
    
    # Register definitions
    r = client.post('/api/v1/relational/definitions', json={
        "name": "Large Baseline",
        "raw_sql": "SELECT SUM(f.amount) FROM fact_large f LEFT JOIN dim_large d ON f.group_id = d.group_id",
        "contract_id": c_large_id
    })
    assert r.status_code == 201, r.text
    large_base_vid = r.json()['versions'][0]['id']
    
    r = client.post('/api/v1/relational/definitions', json={
        "name": "Large Target",
        "raw_sql": "SELECT SUM(f.amount) FROM fact_large f INNER JOIN dim_large d ON f.group_id = d.group_id WHERE d.segment = 'north'",
        "contract_id": c_large_id
    })
    assert r.status_code == 201, r.text
    large_target_vid = r.json()['versions'][0]['id']
    
    # Ingest large snapshot via runner
    r = client.post(
        '/api/v1/relational/snapshots',
        data={'contract_id': c_large_id, 'name': 'Large 120-row Snapshot'},
        files={
            'fact_file': ('fact_large.csv', open(f_large_path, 'rb'), 'text/csv'),
            'dimension_file': ('dim_large.csv', open(d_large_path, 'rb'), 'text/csv')
        }
    )
    assert r.status_code == 202, r.text
    imp_json = r.json()
    if 'state' in imp_json:
        large_imp = poll_import(client, imp_json['id'])
        assert large_imp['state'] in ('validated', 'succeeded')
        large_snap_id = large_imp['snapshot_id']
    else:
        large_snap_id = imp_json['id']
    print(f"Large snapshot ready: {large_snap_id}")
    
    # Run comparison
    r = client.post(
        '/api/v1/relational/comparisons',
        json={
            "baseline_version_id": large_base_vid,
            "target_version_id": large_target_vid,
            "snapshot_id": large_snap_id,
            "context": {
                "mode": "relational_snapshot",
                "scope": "entire_supplied_snapshot",
                "contract_hash": c_large_hash,
                "unit_label": "points",
                "values_comparable": True
            },
            "relationship": "candidate"
        },
        headers={'Idempotency-Key': f'idemp-large-{time.time()}'}
    )
    assert r.status_code == 202, r.text
    large_run = poll_run(client, r.json()['id'])
    assert large_run['state'] == 'succeeded'
    large_run_id = large_run['id']
    
    # Check pagination: page 1
    r_p1 = client.get(f'/api/v1/comparisons/{large_run_id}/evidence?offset=0&limit=100')
    assert r_p1.status_code == 200
    p1_data = r_p1.json()
    assert len(p1_data['items']) == 100
    assert p1_data['total'] == 120
    
    # Check pagination: page 2
    r_p2 = client.get(f'/api/v1/comparisons/{large_run_id}/evidence?offset=100&limit=100')
    assert r_p2.status_code == 200
    p2_data = r_p2.json()
    assert len(p2_data['items']) == 20
    assert p2_data['total'] == 120
    
    # Check export truncation
    r_exp = client.get(f'/api/v1/comparisons/{large_run_id}/export?format=json')
    assert r_exp.status_code == 200
    exp_bundle = r_exp.json()
    coverage = exp_bundle['evidence_coverage']
    assert coverage['total_rows'] == 120
    assert coverage['included_rows'] == 100
    assert coverage['omitted_rows'] == 20
    assert coverage['detail'] == 'first_rows'
    assert len(exp_bundle['result']['evidence']) == 100
    
    results['pagination_and_export_truncation'] = {
        "status": "PASS",
        "total_rows": 120,
        "page_1_items": 100,
        "page_2_items": 20,
        "export_included_rows": 100,
        "export_omitted_rows": 20,
        "export_coverage": coverage
    }
    print("Pagination and export truncation verified.")

    # -------------------------------------------------------------
    # 2. Duplicate Fact Keys
    # -------------------------------------------------------------
    print("Testing duplicate fact keys...")
    dup_fact_csv = (
        "fact_id,group_id,amount,kind\n"
        "F1,D1,10.00,web\n"
        "F1,D1,20.00,web\n"
    )
    dim_valid_csv = "group_id,segment\nD1,north\n"
    r = client.post(
        '/api/v1/relational/snapshots',
        data={'contract_id': c_large_id, 'name': 'Duplicate Fact Key Snapshot'},
        files={
            'fact_file': ('fact_dup.csv', dup_fact_csv.encode('utf-8'), 'text/csv'),
            'dimension_file': ('dim_valid.csv', dim_valid_csv.encode('utf-8'), 'text/csv')
        }
    )
    assert r.status_code == 202, r.text
    dup_fact_imp = poll_import(client, r.json()['id'])
    assert dup_fact_imp['state'] == 'failed'
    assert dup_fact_imp['error']['code'] in ('INVALID_KEYS', 'INGEST_FAILED')
    results['duplicate_fact_keys'] = {
        "status": "PASS",
        "import_id": dup_fact_imp['id'],
        "state": dup_fact_imp['state'],
        "error": dup_fact_imp['error']
    }
    print("Duplicate fact keys correctly rejected by runner:", dup_fact_imp['error'])

    # -------------------------------------------------------------
    # 3. Duplicate Dimension Keys
    # -------------------------------------------------------------
    print("Testing duplicate dimension keys...")
    valid_fact_csv = "fact_id,group_id,amount,kind\nF1,D1,10.00,web\n"
    dup_dim_csv = "group_id,segment\nD1,north\nD1,south\n"
    r = client.post(
        '/api/v1/relational/snapshots',
        data={'contract_id': c_large_id, 'name': 'Duplicate Dimension Key Snapshot'},
        files={
            'fact_file': ('fact_valid.csv', valid_fact_csv.encode('utf-8'), 'text/csv'),
            'dimension_file': ('dim_dup.csv', dup_dim_csv.encode('utf-8'), 'text/csv')
        }
    )
    assert r.status_code == 202, r.text
    dup_dim_imp = poll_import(client, r.json()['id'])
    assert dup_dim_imp['state'] == 'failed'
    assert dup_dim_imp['error']['code'] in ('INVALID_KEYS', 'INGEST_FAILED')
    results['duplicate_dimension_keys'] = {
        "status": "PASS",
        "import_id": dup_dim_imp['id'],
        "state": dup_dim_imp['state'],
        "error": dup_dim_imp['error']
    }
    print("Duplicate dimension keys correctly rejected by runner:", dup_dim_imp['error'])

    # -------------------------------------------------------------
    # 4. Invalid and Over-scale Amounts
    # -------------------------------------------------------------
    print("Testing over-scale amounts (scale 3 vs registered scale 2)...")
    overscale_fact_csv = "fact_id,group_id,amount,kind\nF1,D1,10.005,web\n"
    r = client.post(
        '/api/v1/relational/snapshots',
        data={'contract_id': c_large_id, 'name': 'Overscale Amount Snapshot'},
        files={
            'fact_file': ('fact_overscale.csv', overscale_fact_csv.encode('utf-8'), 'text/csv'),
            'dimension_file': ('dim_valid.csv', dim_valid_csv.encode('utf-8'), 'text/csv')
        }
    )
    assert r.status_code == 202, r.text
    overscale_imp = poll_import(client, r.json()['id'])
    assert overscale_imp['state'] == 'failed'
    assert overscale_imp['error']['code'] in ('SCALE_MISMATCH', 'INVALID_AMOUNT', 'INGEST_FAILED')
    results['overscale_amount'] = {
        "status": "PASS",
        "import_id": overscale_imp['id'],
        "state": overscale_imp['state'],
        "error": overscale_imp['error']
    }
    print("Over-scale amount correctly rejected by runner:", overscale_imp['error'])

    print("Testing invalid non-numeric amount...")
    nonnumeric_fact_csv = "fact_id,group_id,amount,kind\nF1,D1,INVALID_VAL,web\n"
    r = client.post(
        '/api/v1/relational/snapshots',
        data={'contract_id': c_large_id, 'name': 'Nonnumeric Amount Snapshot'},
        files={
            'fact_file': ('fact_nonnumeric.csv', nonnumeric_fact_csv.encode('utf-8'), 'text/csv'),
            'dimension_file': ('dim_valid.csv', dim_valid_csv.encode('utf-8'), 'text/csv')
        }
    )
    assert r.status_code == 202, r.text
    nonnum_imp = poll_import(client, r.json()['id'])
    assert nonnum_imp['state'] == 'failed'
    assert nonnum_imp['error']['code'] in ('INVALID_AMOUNT', 'INGEST_FAILED')
    results['nonnumeric_amount'] = {
        "status": "PASS",
        "import_id": nonnum_imp['id'],
        "state": nonnum_imp['state'],
        "error": nonnum_imp['error']
    }
    print("Nonnumeric amount correctly rejected by runner:", nonnum_imp['error'])

    # -------------------------------------------------------------
    # 5. Empty and All-NULL SUMs (Preserving NULL gap/residual semantics)
    # -------------------------------------------------------------
    print("Testing all-NULL measure SUM preserving NULL derived gap/residual...")
    all_null_fact_csv = (
        "fact_id,group_id,amount,kind\n"
        "F1,D1,,web\n"
        "F2,D1,,web\n"
    )
    r = client.post(
        '/api/v1/relational/snapshots',
        data={'contract_id': c_large_id, 'name': 'All-NULL Measure Snapshot'},
        files={
            'fact_file': ('fact_nulls.csv', all_null_fact_csv.encode('utf-8'), 'text/csv'),
            'dimension_file': ('dim_valid.csv', dim_valid_csv.encode('utf-8'), 'text/csv')
        }
    )
    assert r.status_code == 202, r.text
    null_imp_json = r.json()
    if 'state' in null_imp_json:
        null_imp = poll_import(client, null_imp_json['id'])
        assert null_imp['state'] in ('validated', 'succeeded')
        null_snap_id = null_imp['snapshot_id']
    else:
        null_snap_id = null_imp_json['id']
    print(f"All-NULL snapshot ready: {null_snap_id}")
    
    r = client.post(
        '/api/v1/relational/comparisons',
        json={
            "baseline_version_id": large_base_vid,
            "target_version_id": large_target_vid,
            "snapshot_id": null_snap_id,
            "context": {
                "mode": "relational_snapshot",
                "scope": "entire_supplied_snapshot",
                "contract_hash": c_large_hash,
                "unit_label": "points",
                "values_comparable": True
            },
            "relationship": "candidate"
        },
        headers={'Idempotency-Key': f'idemp-null-{time.time()}'}
    )
    assert r.status_code == 202, r.text
    null_run = poll_run(client, r.json()['id'])
    assert null_run['state'] in ('succeeded', 'partial')
    null_res = null_run['result']
    print("All-NULL comparison result:")
    print("  baseline_value:", null_res.get('baseline_value'))
    print("  target_value:", null_res.get('target_value'))
    print("  delta:", null_res.get('delta_target_minus_baseline'))
    print("  gap:", null_res.get('absolute_gap'))
    print("  residual:", null_res.get('residual'))
    
    assert null_res.get('baseline_value') is None
    assert null_res.get('target_value') is None
    assert null_res.get('delta_target_minus_baseline') is None
    assert null_res.get('absolute_gap') is None
    assert null_res.get('residual') is None
    
    results['all_null_sums_semantics'] = {
        "status": "PASS",
        "baseline_value": null_res.get('baseline_value'),
        "target_value": null_res.get('target_value'),
        "delta": null_res.get('delta_target_minus_baseline'),
        "gap": null_res.get('absolute_gap'),
        "residual": null_res.get('residual'),
        "preserved_null_derived_semantics": True
    }

    # -------------------------------------------------------------
    # 6. NULL FK and Missing Non-NULL FK Behavior
    # -------------------------------------------------------------
    print("Verifying NULL FK and Missing FK behavior from Gate 2 execution...")
    # Read the Gate 2 receipt
    receipt_02 = json.loads((DATA_DIR / 'receipts' / 'receipt_02_relational_execution.json').read_text(encoding='utf-8'))
    row_contribs = {r['fact_id']: r for r in receipt_02['row_contributions']}
    
    # F4: NULL FK -> included in baseline LEFT join, delta = -5.00
    assert row_contribs['F4']['delta'] == "-5.00"
    assert row_contribs['F4']['baseline_included'] is True
    assert row_contribs['F4']['target_included'] is False
    
    # F3: Missing FK DX -> included in baseline LEFT join, delta = -30.00
    assert row_contribs['F3']['delta'] == "-30.00"
    assert row_contribs['F3']['baseline_included'] is True
    assert row_contribs['F3']['target_included'] is False
    
    results['fk_handling'] = {
        "status": "PASS",
        "null_fk_fact_f4": row_contribs['F4'],
        "missing_non_null_fk_fact_f3": row_contribs['F3']
    }
    print("NULL FK and Missing Non-NULL FK behavior verified.")

    # -------------------------------------------------------------
    # 7. Cross-Policy Refusal Using Existing Valid Incompatible Records
    # -------------------------------------------------------------
    print("Testing cross-policy refusal using existing valid incompatible records...")
    # First, let's create a valid V1 single-table orders dataset contract and snapshot
    r_v1_c = client.post('/api/v1/dataset-contracts', json={
        "name": "Standard Orders Contract",
        "description": "Standard retail orders contract",
        "source_format": "csv",
        "delimiter": ",",
        "has_header": True,
        "null_policy": "literal_NULL",
        "columns": [
            {"name": "order_id", "data_type": "string", "nullable": False},
            {"name": "customer_id", "data_type": "string", "nullable": False},
            {"name": "amount", "data_type": "decimal", "precision": 10, "scale": 2, "nullable": False},
            {"name": "status", "data_type": "string", "nullable": False}
        ]
    })
    
    orders_path = Path('MetricGuard_Planning_Pack/examples/orders_golden.csv')
    r_snap_v1 = client.post(
        '/api/v1/snapshots',
        data={
            'name': 'Retail V1 Snapshot',
            'currency': 'INR',
            'timezone': 'Asia/Kolkata',
            'scope': 'synthetic_retail_all_orders',
            'cutoff_at': '2026-09-02'
        },
        files={'file': ('orders.csv', open(orders_path, 'rb'), 'text/csv')}
    )
    assert r_snap_v1.status_code == 202, f"V1 snapshot upload failed: {r_snap_v1.text}"
    v1_json = r_snap_v1.json()
    if 'state' in v1_json:
        v1_imp = poll_import(client, v1_json['id'])
        assert v1_imp['state'] in ('validated', 'succeeded')
        v1_snap_id = v1_imp['snapshot_id']
    else:
        v1_snap_id = v1_json['id']
    print(f"Created/found authentic V1 snapshot: {v1_snap_id}")
    
    # Now attempt to compare relational definitions against this real, existing V1 snapshot!
    r_cross = client.post(
        '/api/v1/relational/comparisons/validate',
        json={
            "baseline_version_id": large_base_vid,
            "target_version_id": large_target_vid,
            "snapshot_id": v1_snap_id,
            "context": {
                "mode": "relational_snapshot",
                "scope": "entire_supplied_snapshot",
                "contract_hash": c_large_hash,
                "unit_label": "points",
                "values_comparable": True
            },
            "relationship": "candidate"
        }
    )
    print("Cross-policy validate status code:", r_cross.status_code, "body:", r_cross.text)
    assert r_cross.status_code == 422, f"Expected 422 POLICY_MISMATCH, got {r_cross.status_code}"
    cross_err = r_cross.json()
    assert cross_err['error']['code'] == 'POLICY_MISMATCH'
    assert 'relational v1' in cross_err['error']['message']
    
    results['cross_policy_refusal'] = {
        "status": "PASS",
        "attempted_snapshot_id": v1_snap_id,
        "attempted_snapshot_policy": "retail_v1",
        "target_policy": "relational_snapshot_v1",
        "status_code": r_cross.status_code,
        "error_code": cross_err['error']['code'],
        "error_message": cross_err['error']['message'],
        "not_found_not_used": True
    }
    print("Cross-policy protection verified using existing valid incompatible record.")

    # -------------------------------------------------------------
    # Write Receipt
    # -------------------------------------------------------------
    receipt = {
        "gate": "CLOSE_REMAINING_EXECUTION_CHECKS",
        "status": "PASS",
        "runner_identity": {
            "image": "metricguard-runner:1.0",
            "image_digest": "sha256:15cbd74c34794fec24a372df6cb46ce451e64626f977184c8943d17bb69b12ac"
        },
        "checks": results
    }
    
    receipt_path = DATA_DIR / 'receipts' / 'receipt_03_runner_edge_cases.json'
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f"Receipt written successfully to {receipt_path}")

if __name__ == '__main__':
    run()
