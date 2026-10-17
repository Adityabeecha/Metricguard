"""Verify the inventory case through isolated flat snapshot workflow.

This tests whether flat snapshot mode generalizes beyond Olist by using:
- Different column names (sku_id, inventory_cost vs order_id, payment_value)
- Different categories (warehouse_location, item_condition vs order_status)
- Different domain (inventory management vs e-commerce orders)
"""

import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store
from services.supervisor.worker import DockerLauncher, Supervisor


ROOT = Path(__file__).resolve().parents[1]


def verify_inventory_case():
    """Run the inventory case through isolated workflow and verify against oracle."""

    # Load case data
    case_dir = ROOT / 'data' / 'evaluation' / 'inventory-case'
    csv_path = case_dir / 'inventory-snapshot.csv'
    manifest_path = case_dir / 'inventory-manifest.json'
    oracle_path = case_dir / 'oracle.json'

    if not csv_path.exists():
        raise FileNotFoundError(f"Run scripts.create_inventory_case first")

    oracle = json.loads(oracle_path.read_text(encoding='utf-8'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

    print(f"Testing inventory case:")
    print(f"  Rows: {manifest['sku_count']}")
    print(f"  Expected baseline: {oracle['baseline']['expected']}")
    print(f"  Expected target: {oracle['target']['expected']}")
    print(f"  Expected gap: {oracle['gap']}")
    print()

    # Create isolated workspace
    (ROOT / '.test-tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='inventory-case-', dir=ROOT / '.test-tmp',
                                     ignore_cleanup_errors=True) as directory:
        work = Path(directory)
        url = f"sqlite:///{work / 'app.db'}"
        store = Store(url)
        worker = Supervisor(store, DockerLauncher(), work / 'jobs', 'inventory-verify', data_dir=work)

        with TestClient(create_app(database_url=url, data_dir=work, mode='isolated')) as client:
            def post(path, **kwargs):
                response = client.post('/api/v1' + path, **kwargs)
                if response.status_code not in {200, 201, 202}:
                    print(f"Error: {response.status_code} {response.text}")
                assert response.status_code in {200, 201, 202}, response.text
                return response.json()

            def get(path):
                response = client.get('/api/v1' + path)
                assert response.status_code == 200, response.text
                return response.json()

            # Step 1: Register flat contract with inventory schema
            print("Step 1: Registering flat contract...")
            contract = post('/flat/contracts', json={
                'version': 3,
                'mode': 'flat_snapshot',
                'name': 'Inventory warehouse snapshot',
                'row_key': 'sku_id',
                'measure': 'inventory_cost',
                'precision': 18,
                'scale': 2,
                'categories': ['warehouse_location', 'item_condition'],
                'measure_meaning': 'total inventory cost at snapshot date',
                'unit_label': 'USD',
                'values_comparable': True,
                'scope': 'entire_supplied_snapshot',
                'null_policy': 'blank_nullable'
            })
            print(f"  Contract ID: {contract['id']}")
            print(f"  Contract hash: {contract['contract_hash'][:12]}...")
            print()

            # Step 2: Upload CSV and validate through isolated runner
            print("Step 2: Uploading CSV for isolated validation...")
            upload_started = time.perf_counter()
            upload = post('/flat/snapshots',
                         files={'file': ('inventory-snapshot.csv', csv_path.read_bytes()),
                               'preparation_manifest': ('inventory-manifest.json', manifest_path.read_bytes())},
                         data={'name': 'Inventory snapshot', 'contract_id': contract['id']})

            # Run supervisor to process upload
            assert worker.run_once(), "Supervisor failed to process upload"

            imported = store.get_import(upload['id'])
            upload_elapsed = time.perf_counter() - upload_started

            assert imported['state'] == 'validated', f"Import failed: {imported.get('error')}"
            snapshot = store.snapshot(imported['snapshot_id'])
            assert snapshot['row_count'] == manifest['sku_count']
            print(f"  Snapshot validated: {snapshot['id']}")
            print(f"  Row count: {snapshot['row_count']}")
            print(f"  Upload + validation: {upload_elapsed:.2f}s")
            print()

            # Step 3: Register baseline definition (exclude DAMAGED)
            print("Step 3: Registering baseline definition...")
            baseline_def = post('/flat/definitions', json={
                'name': 'Exclude DAMAGED items',
                'raw_sql': oracle['baseline']['sql'],
                'contract_id': contract['id']
            })
            baseline_version = baseline_def['versions'][-1]
            assert baseline_version['support_state'] == 'executable', \
                f"Baseline SQL refused: {baseline_version.get('support_reason')}"
            print(f"  Baseline version ID: {baseline_version['id']}")
            print(f"  SQL: {oracle['baseline']['sql']}")
            print()

            # Step 4: Register target definition (NORTH and SOUTH warehouses)
            print("Step 4: Registering target definition...")
            target_def = post('/flat/definitions', json={
                'name': 'NORTH and SOUTH warehouses only',
                'raw_sql': oracle['target']['sql'],
                'contract_id': contract['id']
            })
            target_version = target_def['versions'][-1]
            assert target_version['support_state'] == 'executable', \
                f"Target SQL refused: {target_version.get('support_reason')}"
            print(f"  Target version ID: {target_version['id']}")
            print(f"  SQL: {oracle['target']['sql']}")
            print()

            # Step 5: Run comparison through isolated runner
            print("Step 5: Running comparison...")
            comparison_started = time.perf_counter()
            run = post('/flat/comparisons', json={
                'baseline_version_id': baseline_version['id'],
                'target_version_id': target_version['id'],
                'snapshot_id': snapshot['id'],
                'context': {
                    'mode': 'flat_snapshot',
                    'scope': 'entire_supplied_snapshot',
                    'contract_hash': contract['contract_hash'],
                    'unit_label': contract['unit_label'],
                    'values_comparable': True
                },
                'relationship': 'candidate'
            }, headers={'Idempotency-Key': 'inventory-case-verify'})

            # Poll until comparison completes
            max_wait = 60
            elapsed = 0
            while run['state'] in ['queued', 'running', 'pending'] and elapsed < max_wait:
                assert worker.run_once(), "Supervisor failed during comparison"
                time.sleep(0.5)
                elapsed += 0.5
                run = get(f'/comparisons/{run["id"]}')

            comparison_elapsed = time.perf_counter() - comparison_started

            assert run['state'] == 'succeeded', f"Comparison failed: {run.get('error')}"
            result = run['result']
            print(f"  Comparison completed: {comparison_elapsed:.2f}s")
            print()

            # Step 6: Verify against oracle
            print("Step 6: Verifying results against oracle...")
            print(f"  Expected baseline: {oracle['baseline']['expected']}")
            print(f"  Actual baseline:   {result['baseline_value']}")
            print(f"  Expected target:   {oracle['target']['expected']}")
            print(f"  Actual target:     {result['target_value']}")
            print(f"  Expected gap:      {oracle['gap']}")
            print(f"  Actual gap:        {result['delta_target_minus_baseline']}")
            print(f"  Residual:          {result['residual']}")
            print()

            # Assertions
            assert result['baseline_value'] == oracle['baseline']['expected'], \
                f"Baseline mismatch: expected {oracle['baseline']['expected']}, got {result['baseline_value']}"
            assert result['target_value'] == oracle['target']['expected'], \
                f"Target mismatch: expected {oracle['target']['expected']}, got {result['target_value']}"
            assert result['delta_target_minus_baseline'] == oracle['gap'], \
                f"Gap mismatch: expected {oracle['gap']}, got {result['delta_target_minus_baseline']}"
            assert str(result['residual']) in ['0', '0.00', '0.0'], \
                f"Non-zero residual: {result['residual']}"

            # Step 7: Check evidence
            print("Step 7: Checking evidence...")
            evidence_page = get(f'/comparisons/{run["id"]}/evidence?offset=0&limit=100')
            print(f"  Total evidence rows: {evidence_page['total']}")
            print(f"  Page 1 rows: {len(evidence_page['items'])}")
            assert evidence_page['total'] == manifest['sku_count']
            print()

            # Step 8: Save investigation
            print("Step 8: Saving investigation...")
            investigation = post('/investigations', json={
                'title': 'Inventory case verification',
                'run_id': run['id'],
                'decision': 'Verified generalization beyond Olist case',
                'actor_label': 'automated-test',
                'snapshot_id': snapshot['id']
            })
            print(f"  Investigation ID: {investigation['id']}")
            print(f"  Preparation manifest hash: {investigation.get('preparation_manifest_hash', 'none')[:12]}...")
            print()

            # Step 9: Replay
            print("Step 9: Testing replay...")
            replay_started = time.perf_counter()
            replay = post(f'/investigations/{investigation["id"]}/replays',
                         json={}, headers={'Idempotency-Key': 'inventory-replay'})

            # Process replay
            while replay['state'] in ['queued', 'running', 'pending']:
                assert worker.run_once(), "Supervisor failed during replay"
                time.sleep(0.5)
                replay = get(f'/comparisons/{replay["id"]}')

            replay_elapsed = time.perf_counter() - replay_started

            assert replay['state'] == 'succeeded', f"Replay failed: {replay.get('error')}"
            assert replay['result']['delta_target_minus_baseline'] == oracle['gap']
            print(f"  Replay succeeded: {replay_elapsed:.2f}s")
            print(f"  Replay gap: {replay['result']['delta_target_minus_baseline']}")
            print()

            # Summary
            print("=" * 60)
            print("VERIFICATION SUCCESSFUL")
            print("=" * 60)
            print(f"Flat snapshot mode successfully handled:")
            print(f"  [OK] Different column names (sku_id, inventory_cost)")
            print(f"  [OK] Different categories (warehouse_location, item_condition)")
            print(f"  [OK] Different domain (inventory vs e-commerce)")
            print(f"  [OK] Matched independent oracle totals")
            print(f"  [OK] Zero residual")
            print(f"  [OK] Complete evidence (500 rows)")
            print(f"  [OK] Saved investigation with provenance")
            print(f"  [OK] Successful replay")
            print()

            return {
                'status': 'PASSED',
                'csv_hash': oracle['csv_sha256'],
                'manifest_hash': oracle['manifest_sha256'],
                'row_count': manifest['sku_count'],
                'baseline': result['baseline_value'],
                'target': result['target_value'],
                'gap': result['delta_target_minus_baseline'],
                'residual': str(result['residual']),
                'upload_seconds': round(upload_elapsed, 2),
                'comparison_seconds': round(comparison_elapsed, 2),
                'replay_seconds': round(replay_elapsed, 2),
                'investigation_id': investigation['id'],
                'preparation_manifest_pinned': investigation.get('preparation_manifest_hash') is not None
            }


if __name__ == '__main__':
    import sys
    try:
        result = verify_inventory_case()
        output_dir = ROOT / 'data' / 'evaluation' / 'inventory-case'
        report_path = output_dir / 'verification-report.json'
        report_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(f"Report written to: {report_path}")
        sys.exit(0)
    except Exception as e:
        print(f"VERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
