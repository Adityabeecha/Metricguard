"""Run synthetic pilot scenarios through the actual MetricGuard flat-snapshot workflow.

Exercises the real API, store, supervisor, runner, and evidence path in-process
(no Docker required). Validates deterministic oracle values, evidence reconciliation,
save -> reopen -> replay in a clean session, and an unsupported/refusal case.

All timings are **machine execution timings** on this host, not analyst completion
times. This synthetic pilot does not satisfy the V2-06 requirement for two
independently sourced sanitized real cases with a second analyst.
"""

import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store
from services.supervisor.runner import ingest_job, run_job
from services.supervisor.worker import Supervisor


ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# In-process launcher (same pattern as test suite -- no Docker dependency)
# ---------------------------------------------------------------------------

class CompletedProcess:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class InProcessLauncher:
    """Calls the runner entry points directly, matching the test suite pattern."""

    def start(self, input_dir, output_dir, command=None):
        if command == 'ingest':
            ingest_job(input_dir, output_dir)
        elif command is None:
            run_job(input_dir, output_dir)
        else:
            raise AssertionError(f'Unexpected command: {command}')
        return CompletedProcess()


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def _csv_and_manifest(scenario_dir):
    """Resolve the CSV and manifest paths for a scenario directory."""
    for candidate in ('orders-snapshot.csv', 'sales-snapshot.csv', 'transactions-snapshot.csv'):
        path = scenario_dir / candidate
        if path.is_file():
            return path, scenario_dir / 'manifest.json'
    raise FileNotFoundError(f'No snapshot CSV found in {scenario_dir}')


def _schema_for(scenario_dir):
    """Return (row_key, measure, categories) for each scenario by directory name."""
    name = scenario_dir.name
    if 'returns' in name:
        return 'order_id', 'revenue', ['order_status']
    elif 'regions' in name:
        return 'sale_id', 'sale_amount', ['region']
    elif 'status' in name:
        return 'transaction_id', 'amount', ['status']
    raise ValueError(f'Unknown scenario: {name}')


def run_scenario(scenario_name, scenario_dir, work):
    """Run one scenario through the complete MetricGuard flat-snapshot workflow."""

    oracle = json.loads((scenario_dir / 'oracle.json').read_text(encoding='utf-8'))
    csv_path, manifest_path = _csv_and_manifest(scenario_dir)
    row_key, measure, categories = _schema_for(scenario_dir)

    print(f"\n{'=' * 70}")
    print(f"SCENARIO: {scenario_name}")
    print(f"{'=' * 70}")
    print(f"Business context: {oracle['business_context']}")
    print(f"Expected confusion: {oracle['expected_confusion']}")
    print(f"Rows: {oracle['row_count']}")
    print()

    url = f"sqlite:///{work / 'app.db'}"
    store = Store(url)
    worker = Supervisor(store, InProcessLauncher(), work / 'jobs',
                        f'pilot-{scenario_name}', data_dir=work)

    timings = {}
    errors = []

    with TestClient(create_app(database_url=url, data_dir=work, mode='isolated')) as client:
        def post(path, **kwargs):
            resp = client.post('/api/v1' + path, **kwargs)
            if resp.status_code not in {200, 201, 202}:
                errors.append(f"{path}: {resp.status_code} {resp.text}")
                raise Exception(f"Request failed: {resp.text}")
            return resp.json()

        def get(path):
            resp = client.get('/api/v1' + path)
            if resp.status_code != 200:
                raise Exception(f"Request failed: {resp.text}")
            return resp.json()

        # --- Step 1: Register contract ---
        t0 = time.perf_counter()
        contract = post('/flat/contracts', json={
            'version': 3, 'mode': 'flat_snapshot',
            'name': oracle['scenario'],
            'row_key': row_key, 'measure': measure,
            'precision': 18, 'scale': 2,
            'categories': categories,
            'measure_meaning': oracle['business_context'][:200],
            'unit_label': 'USD', 'values_comparable': True,
            'scope': 'entire_supplied_snapshot',
            'null_policy': 'blank_nullable',
        })
        timings['contract_registration'] = time.perf_counter() - t0
        print(f"[OK] Contract registered: {contract['id'][:8]}... "
              f"({timings['contract_registration']:.3f}s)")

        # --- Step 2: Upload snapshot with preparation manifest ---
        t0 = time.perf_counter()
        upload = post('/flat/snapshots',
                      files={
                          'file': (csv_path.name, csv_path.read_bytes()),
                          'preparation_manifest': ('manifest.json',
                                                   manifest_path.read_bytes()),
                      },
                      data={'name': f'{scenario_name} snapshot',
                            'contract_id': contract['id']})
        assert worker.run_once(), 'Worker failed to process upload'
        imported = store.get_import(upload['id'])
        assert imported['state'] == 'validated', \
            f"Import failed: {imported.get('error')}"
        snapshot = store.snapshot(imported['snapshot_id'])
        timings['upload_and_ingestion'] = time.perf_counter() - t0
        print(f"[OK] Snapshot validated: {snapshot['row_count']} rows "
              f"({timings['upload_and_ingestion']:.3f}s)")

        # --- Step 3: Register baseline definition ---
        t0 = time.perf_counter()
        baseline_def = post('/flat/definitions', json={
            'name': oracle['baseline']['name'],
            'raw_sql': oracle['baseline']['sql'],
            'contract_id': contract['id'],
        })
        bv = baseline_def['versions'][-1]
        assert bv['support_state'] == 'executable', \
            f"Baseline refused: {bv.get('support_reason')}"
        timings['baseline_registration'] = time.perf_counter() - t0
        print(f"[OK] Baseline registered ({timings['baseline_registration']:.3f}s)")

        # --- Step 4: Register target definition ---
        t0 = time.perf_counter()
        target_def = post('/flat/definitions', json={
            'name': oracle['target']['name'],
            'raw_sql': oracle['target']['sql'],
            'contract_id': contract['id'],
        })
        tv = target_def['versions'][-1]
        assert tv['support_state'] == 'executable', \
            f"Target refused: {tv.get('support_reason')}"
        timings['target_registration'] = time.perf_counter() - t0
        print(f"[OK] Target registered ({timings['target_registration']:.3f}s)")

        # --- Step 5: Run comparison ---
        t0 = time.perf_counter()
        ctx = {
            'mode': 'flat_snapshot',
            'scope': 'entire_supplied_snapshot',
            'contract_hash': contract['contract_hash'],
            'unit_label': 'USD',
            'values_comparable': True,
        }
        run = post('/flat/comparisons', json={
            'baseline_version_id': bv['id'],
            'target_version_id': tv['id'],
            'snapshot_id': snapshot['id'],
            'context': ctx,
            'relationship': 'candidate',
        }, headers={'Idempotency-Key': f'pilot-{scenario_name}'})

        elapsed = 0
        while run['state'] in ('queued', 'running', 'pending') and elapsed < 60:
            assert worker.run_once(), 'Worker failed during comparison'
            time.sleep(0.2)
            elapsed += 0.2
            run = get(f"/comparisons/{run['id']}")
        assert run['state'] == 'succeeded', f"Comparison failed: {run.get('error')}"
        result = run['result']
        timings['comparison'] = time.perf_counter() - t0
        print(f"[OK] Comparison completed ({timings['comparison']:.3f}s)")

        # --- Step 6: Oracle verification ---
        baseline_match = result['baseline_value'] == oracle['baseline']['expected']
        target_match = result['target_value'] == oracle['target']['expected']
        gap_match = result['delta_target_minus_baseline'] == oracle['gap']
        residual = result.get('residual', 'MISSING')

        print(f"\nOracle verification:")
        print(f"  Expected baseline: {oracle['baseline']['expected']}")
        print(f"  Actual baseline:   {result['baseline_value']}")
        print(f"  Expected target:   {oracle['target']['expected']}")
        print(f"  Actual target:     {result['target_value']}")
        print(f"  Expected gap:      {oracle['gap']}")
        print(f"  Actual gap:        {result['delta_target_minus_baseline']}")
        print(f"  Residual:          {residual}")

        if baseline_match and target_match and gap_match:
            print(f"  [OK] All values match oracle")
        else:
            errors.append(f'Oracle mismatch in {scenario_name}')
            print(f"  [X] VALUES DO NOT MATCH ORACLE")

        residual_zero = residual == '0.00' or residual == '0'

        # --- Step 7: Evidence coverage ---
        t0 = time.perf_counter()
        evidence_p1 = get(f"/comparisons/{run['id']}/evidence?offset=0&limit=100")
        evidence_total = evidence_p1['total']
        evidence_page_1_count = len(evidence_p1['items'])
        evidence_delta_sum = sum(Decimal(row['delta']) for row in evidence_p1['items'])

        # If there are more rows, fetch remaining pages
        fetched_rows = list(evidence_p1['items'])
        offset = 100
        while offset < evidence_total:
            page = get(f"/comparisons/{run['id']}/evidence?offset={offset}&limit=100")
            fetched_rows.extend(page['items'])
            offset += 100

        full_evidence_delta = sum(Decimal(row['delta']) for row in fetched_rows)
        evidence_reconciles = str(full_evidence_delta) == result['delta_target_minus_baseline']
        timings['evidence_verification'] = time.perf_counter() - t0

        print(f"\nEvidence coverage:")
        print(f"  Total evidence rows: {evidence_total}")
        print(f"  Fetched rows: {len(fetched_rows)}")
        print(f"  Evidence delta sum: {full_evidence_delta}")
        print(f"  Reconciles with gap: {'[OK]' if evidence_reconciles else '[X]'}")
        print(f"  ({timings['evidence_verification']:.3f}s)")

        if not evidence_reconciles:
            errors.append(f'Evidence does not reconcile in {scenario_name}')

        # --- Step 8: Export check ---
        t0 = time.perf_counter()
        export_resp = client.get(f"/api/v1/comparisons/{run['id']}/export?format=json")
        assert export_resp.status_code == 200
        export_data = export_resp.json()
        timings['export_check'] = time.perf_counter() - t0
        print(f"\n[OK] JSON export retrieved ({timings['export_check']:.3f}s)")

        # --- Step 9: Save investigation ---
        t0 = time.perf_counter()
        investigation = post('/investigations', json={
            'title': f'{scenario_name} - {oracle["correct_conclusion"]}'[:160],
            'run_id': run['id'],
            'decision': oracle['correct_conclusion'],
            'actor_label': 'synthetic-pilot-simulation',
        })
        investigation_id = investigation['id']
        timings['save_investigation'] = time.perf_counter() - t0
        print(f"\n[OK] Investigation saved: {investigation_id[:8]}... "
              f"({timings['save_investigation']:.3f}s)")

        # --- Step 10: Reopen investigation ---
        t0 = time.perf_counter()
        reopened = get(f"/investigations/{investigation_id}")
        assert reopened['run']['result']['baseline_value'] == oracle['baseline']['expected']
        assert reopened['replay_available'] is True
        timings['reopen'] = time.perf_counter() - t0
        print(f"[OK] Reopened investigation ({timings['reopen']:.3f}s)")

        # --- Step 11: Replay (same session) ---
        t0 = time.perf_counter()
        replay_run = post(f"/investigations/{investigation_id}/replays",
                          json={},
                          headers={'Idempotency-Key': f'pilot-replay-{scenario_name}'})
        elapsed = 0
        while replay_run['state'] in ('queued', 'running', 'pending') and elapsed < 60:
            assert worker.run_once(), 'Worker failed during replay'
            time.sleep(0.2)
            elapsed += 0.2
            replay_run = get(f"/comparisons/{replay_run['id']}")
        assert replay_run['state'] == 'succeeded', \
            f"Replay failed: {replay_run.get('error')}"
        replay_result = replay_run['result']
        replay_match = (replay_result['baseline_value'] == oracle['baseline']['expected'] and
                        replay_result['target_value'] == oracle['target']['expected'] and
                        replay_result['delta_target_minus_baseline'] == oracle['gap'])
        timings['replay_same_session'] = time.perf_counter() - t0
        print(f"[OK] Same-session replay {'matches' if replay_match else 'MISMATCH'} "
              f"({timings['replay_same_session']:.3f}s)")

    # --- Step 12: Clean-session replay ---
    # Create a FRESH TestClient and Store pointing at the same database
    # to simulate closing and reopening the application.
    t0 = time.perf_counter()
    clean_store = Store(url)
    clean_worker = Supervisor(clean_store, InProcessLauncher(), work / 'jobs',
                              f'pilot-clean-{scenario_name}', data_dir=work)

    with TestClient(create_app(database_url=url, data_dir=work, mode='isolated')) as client2:
        # List investigations from clean session
        inv_list = client2.get('/api/v1/investigations').json()
        found = any(i['id'] == investigation_id for i in inv_list['items'])
        assert found, 'Investigation not found in clean session'

        # Reopen from clean session
        reopened2 = client2.get(f'/api/v1/investigations/{investigation_id}').json()
        assert reopened2['run']['result']['baseline_value'] == oracle['baseline']['expected']
        assert reopened2['replay_available'] is True

        # Replay from clean session
        clean_replay = client2.post(
            f'/api/v1/investigations/{investigation_id}/replays',
            json={},
            headers={'Idempotency-Key': f'pilot-clean-replay-{scenario_name}'}).json()
        elapsed = 0
        while clean_replay['state'] in ('queued', 'running', 'pending') and elapsed < 60:
            assert clean_worker.run_once(), 'Worker failed during clean replay'
            time.sleep(0.2)
            elapsed += 0.2
            clean_replay = client2.get(f"/api/v1/comparisons/{clean_replay['id']}").json()
        assert clean_replay['state'] == 'succeeded', \
            f"Clean replay failed: {clean_replay.get('error')}"
        clean_result = clean_replay['result']
        clean_replay_match = (
            clean_result['baseline_value'] == oracle['baseline']['expected'] and
            clean_result['target_value'] == oracle['target']['expected'] and
            clean_result['delta_target_minus_baseline'] == oracle['gap']
        )

    timings['replay_clean_session'] = time.perf_counter() - t0
    print(f"[OK] Clean-session replay {'matches' if clean_replay_match else 'MISMATCH'} "
          f"({timings['replay_clean_session']:.3f}s)")

    total_time = sum(timings.values())
    print(f"\nTotal machine execution time: {total_time:.3f}s")

    return {
        'scenario': scenario_name,
        'business_context': oracle['business_context'],
        'correct_conclusion': oracle['correct_conclusion'],
        'status': 'PASSED' if not errors else 'FAILED',
        'row_count': oracle['row_count'],
        'oracle_match': {
            'baseline': baseline_match,
            'target': target_match,
            'gap': gap_match,
            'all_match': baseline_match and target_match and gap_match,
        },
        'residual': residual,
        'residual_is_zero': residual_zero,
        'evidence': {
            'total_rows': evidence_total,
            'fetched_rows': len(fetched_rows),
            'delta_sum_reconciles': evidence_reconciles,
        },
        'replay': {
            'same_session_match': replay_match,
            'clean_session_match': clean_replay_match,
        },
        'machine_execution_timings_seconds': {
            k: round(v, 4) for k, v in timings.items()
        },
        'total_machine_execution_seconds': round(total_time, 4),
        'errors': errors,
    }


# ---------------------------------------------------------------------------
# Refusal / unsupported case
# ---------------------------------------------------------------------------

def run_refusal_case(work):
    """Verify that an unsupported SQL definition is correctly refused."""
    print(f"\n{'=' * 70}")
    print("SCENARIO: Unsupported SQL refusal")
    print(f"{'=' * 70}")
    print("Testing that unsupported SQL patterns are correctly refused")
    print()

    url = f"sqlite:///{work / 'app.db'}"
    store = Store(url)

    timings = {}
    errors = []
    refused_sqls = []

    with TestClient(create_app(database_url=url, data_dir=work, mode='isolated')) as client:
        # Register a contract
        t0 = time.perf_counter()
        contract = client.post('/api/v1/flat/contracts', json={
            'version': 3, 'mode': 'flat_snapshot',
            'name': 'Refusal test', 'row_key': 'item_id',
            'measure': 'amount', 'precision': 18, 'scale': 2,
            'categories': ['category'],
            'measure_meaning': 'Test measure for refusal validation',
            'unit_label': 'USD', 'values_comparable': True,
            'scope': 'entire_supplied_snapshot',
            'null_policy': 'blank_nullable',
        }).json()
        timings['contract_registration'] = time.perf_counter() - t0

        unsupported_patterns = [
            {
                'label': 'JOIN query',
                'sql': 'SELECT SUM(amount) FROM snapshot JOIN other ON snapshot.item_id = other.id',
                'reason': 'Joins are not supported in flat-snapshot SQL',
            },
            {
                'label': 'Subquery',
                'sql': 'SELECT SUM(amount) FROM (SELECT * FROM snapshot)',
                'reason': 'Subqueries are not supported in flat-snapshot SQL',
            },
            {
                'label': 'AVG aggregate',
                'sql': 'SELECT AVG(amount) FROM snapshot',
                'reason': 'Only SUM aggregate is supported in flat-snapshot SQL',
            },
            {
                'label': 'Wrong table name',
                'sql': 'SELECT SUM(amount) FROM orders WHERE category = \'test\'',
                'reason': 'Only the registered snapshot table is allowed',
            },
            {
                'label': 'Undeclared category field',
                'sql': 'SELECT SUM(amount) FROM snapshot WHERE region = \'NORTH\'',
                'reason': 'Only declared categories can be filtered',
            },
        ]

        for pattern in unsupported_patterns:
            t0 = time.perf_counter()
            resp = client.post('/api/v1/flat/definitions', json={
                'name': pattern['label'],
                'raw_sql': pattern['sql'],
                'contract_id': contract['id'],
            })
            elapsed = time.perf_counter() - t0

            if resp.status_code == 201:
                body = resp.json()
                version = body['versions'][-1]
                if version['support_state'] != 'executable':
                    print(f"[OK] Refused: {pattern['label']} -> "
                          f"{version['support_state']}: {version.get('support_reason', '')} "
                          f"({elapsed:.3f}s)")
                    refused_sqls.append({
                        'label': pattern['label'],
                        'sql': pattern['sql'],
                        'support_state': version['support_state'],
                        'support_reason': version.get('support_reason'),
                        'machine_seconds': round(elapsed, 4),
                    })
                else:
                    # It was accepted -- this SQL should have been refused
                    errors.append(f"Expected refusal for '{pattern['label']}' but got executable")
                    print(f"[X] UNEXPECTED: {pattern['label']} was accepted as executable")
            elif resp.status_code == 422:
                # 422 is also an acceptable refusal (validation error)
                error_body = resp.json()
                error_msg = error_body.get('error', {}).get('message', str(error_body))
                print(f"[OK] Refused (422): {pattern['label']} -> {error_msg[:80]} "
                      f"({elapsed:.3f}s)")
                refused_sqls.append({
                    'label': pattern['label'],
                    'sql': pattern['sql'],
                    'support_state': 'rejected_422',
                    'support_reason': error_msg[:200],
                    'machine_seconds': round(elapsed, 4),
                })
            else:
                errors.append(f"Unexpected status {resp.status_code} for '{pattern['label']}'")
                print(f"[X] Unexpected status {resp.status_code}: {pattern['label']}")

    refusal_count = len(refused_sqls)
    total_patterns = len(unsupported_patterns)
    status = 'PASSED' if refusal_count == total_patterns and not errors else 'FAILED'

    print(f"\nRefusal coverage: {refusal_count}/{total_patterns} patterns correctly refused")
    print(f"Status: {status}")

    return {
        'scenario': 'unsupported_sql_refusal',
        'business_context': 'Validation that unsupported SQL patterns are correctly refused',
        'status': status,
        'patterns_tested': total_patterns,
        'patterns_refused': refusal_count,
        'refused_details': refused_sqls,
        'errors': errors,
    }


# ---------------------------------------------------------------------------
# Main pilot runner
# ---------------------------------------------------------------------------

def run_pilot():
    """Run all pilot scenarios and the refusal case, producing a summary report."""

    scenarios = [
        ('returns', ROOT / 'data' / 'pilot' / 'scenario-returns'),
        ('regions', ROOT / 'data' / 'pilot' / 'scenario-regions'),
        ('status', ROOT / 'data' / 'pilot' / 'scenario-status'),
    ]

    # Verify scenario data exists
    for name, path in scenarios:
        if not path.is_dir():
            print(f"Scenario data not found: {path}")
            print("Run `python -m scripts.generate_pilot_scenarios` first.")
            return 1

    print('=' * 70)
    print('METRICGUARD V2 SYNTHETIC PILOT REHEARSAL')
    print('=' * 70)
    print()
    print('This is a synthetic pilot rehearsal with deterministic oracle values.')
    print('All timings are machine execution timings, not analyst completion times.')
    print('This does NOT satisfy the V2-06 requirement for real cases and a')
    print('second analyst.')
    print()

    (ROOT / '.test-tmp').mkdir(exist_ok=True)
    pilot_start = time.perf_counter()

    # --- Supported scenarios ---
    supported_results = []
    for scenario_name, scenario_dir in scenarios:
        with tempfile.TemporaryDirectory(
                prefix=f'pilot-{scenario_name}-',
                dir=ROOT / '.test-tmp',
                ignore_cleanup_errors=True) as directory:
            work = Path(directory)
            result = run_scenario(scenario_name, scenario_dir, work)
            supported_results.append(result)

    # --- Refusal case ---
    with tempfile.TemporaryDirectory(
            prefix='pilot-refusal-',
            dir=ROOT / '.test-tmp',
            ignore_cleanup_errors=True) as directory:
        refusal_result = run_refusal_case(Path(directory))

    pilot_elapsed = time.perf_counter() - pilot_start

    # --- Summary ---
    print(f"\n\n{'=' * 70}")
    print('SYNTHETIC PILOT REHEARSAL SUMMARY')
    print(f"{'=' * 70}")

    passed = sum(1 for r in supported_results if r['status'] == 'PASSED')
    total = len(supported_results)

    print(f"\nSupported scenarios: {passed}/{total} passed")

    print(f"\nMachine execution timing breakdown:")
    for r in supported_results:
        print(f"\n  {r['scenario']} ({r['row_count']} rows):")
        for step, secs in r['machine_execution_timings_seconds'].items():
            print(f"    {step}: {secs}s")
        print(f"    TOTAL: {r['total_machine_execution_seconds']}s")

    print(f"\nOracle match results:")
    for r in supported_results:
        om = r['oracle_match']
        print(f"  {r['scenario']}: baseline={'[OK]' if om['baseline'] else '[X]'} "
              f"target={'[OK]' if om['target'] else '[X]'} "
              f"gap={'[OK]' if om['gap'] else '[X]'} "
              f"residual={r['residual']}")

    print(f"\nEvidence reconciliation:")
    for r in supported_results:
        ev = r['evidence']
        print(f"  {r['scenario']}: {ev['total_rows']} rows, "
              f"reconciles={'[OK]' if ev['delta_sum_reconciles'] else '[X]'}")

    print(f"\nReplay results:")
    for r in supported_results:
        rp = r['replay']
        print(f"  {r['scenario']}: same-session={'[OK]' if rp['same_session_match'] else '[X]'} "
              f"clean-session={'[OK]' if rp['clean_session_match'] else '[X]'}")

    print(f"\nRefusal case: {refusal_result['status']} "
          f"({refusal_result['patterns_refused']}/{refusal_result['patterns_tested']} refused)")

    avg_time = sum(r['total_machine_execution_seconds'] for r in supported_results) / len(supported_results)
    print(f"\nAverage machine execution time per scenario: {avg_time:.3f}s")
    print(f"Total pilot machine execution time: {pilot_elapsed:.3f}s")

    # --- Write report ---
    now = datetime.now(timezone.utc).isoformat()
    report = {
        'pilot_type': 'synthetic_rehearsal',
        'classification': 'synthetic_development_rehearsal',
        'timestamp': now,
        'scenarios_run': total,
        'scenarios_passed': passed,
        'refusal_case': {
            'status': refusal_result['status'],
            'patterns_tested': refusal_result['patterns_tested'],
            'patterns_refused': refusal_result['patterns_refused'],
            'details': refusal_result['refused_details'],
        },
        'supported_results': supported_results,
        'average_machine_execution_seconds': round(avg_time, 4),
        'total_pilot_machine_execution_seconds': round(pilot_elapsed, 4),
        'execution_method': 'in-process (InProcessLauncher, no Docker)',
        'timing_note': (
            'All timings are machine execution timings measured with '
            'time.perf_counter() on the host running this script. They are '
            'NOT analyst completion times and do not reflect human investigation, '
            'understanding, or decision-making effort.'
        ),
        'limitations': [
            'Synthetic pilot rehearsal with deterministic oracle values, not real business investigations.',
            'All supported scenarios are designed to pass; no wrong-conclusion measurement.',
            'No independent analyst participated; no second-analyst replay observation.',
            'No independently sourced sanitized real business datasets.',
            'Machine execution timings are not analyst completion times.',
            'Refusal tests cover parser-level rejection, not analyst-discovered unsupported cases.',
            'This rehearsal does not close the V2-06 release gate.',
        ],
        'v2_06_status': 'V2 engineering/product requirements complete; V2-06 external real-world validation remains open',
    }

    report_path = ROOT / 'data' / 'pilot' / 'simulation-report.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f"\nReport written: {report_path}")

    all_passed = (passed == total and
                  refusal_result['status'] == 'PASSED' and
                  all(r['replay']['clean_session_match'] for r in supported_results))

    if all_passed:
        print('\n[OK] All synthetic scenarios, replays, and refusal checks passed')
        return 0
    else:
        failed_count = total - passed + (0 if refusal_result['status'] == 'PASSED' else 1)
        print(f'\n[X] {failed_count} scenario(s) or checks failed')
        return 1


if __name__ == '__main__':
    import sys
    try:
        sys.exit(run_pilot())
    except Exception as e:
        print(f'\nPILOT SIMULATION FAILED: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)
