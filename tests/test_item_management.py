"""Rename and delete datasets and saved cases: sealed evidence is never orphaned."""
import csv

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store
from services.supervisor.worker import Supervisor
from tests.test_distinct_and_ratio import _InProcess

ROWS = [['k', 'amount', 'status'], ['A', '10.00', 'paid'], ['B', '5.00', 'void'], ['C', '2.50', 'paid']]


def _setup(tmp_path, client, worker):
    source = tmp_path / 'orders.csv'
    with source.open('w', newline='', encoding='utf-8') as stream:
        csv.writer(stream).writerows(ROWS)
    contract = client.post('/api/v1/flat/contracts', json={
        'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'Orders', 'row_key': 'k',
        'measure': 'amount', 'precision': 18, 'scale': 2, 'categories': ['status'], 'dimension_types': {'status': 'string'},
        'nullable_categories': [], 'measure_meaning': 'Order amount', 'unit_label': 'INR', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}).json()
    with source.open('rb') as content:
        uploaded = client.post('/api/v1/flat/snapshots', files={'file': ('orders.csv', content, 'text/csv')},
                               data={'contract_id': contract['id'], 'name': 'Orders'}).json()
    assert worker.run_once()
    snapshot_id = client.get(f"/api/v1/snapshot-imports/{uploaded['id']}").json()['snapshot_id']
    ids = [client.post('/api/v1/flat/definitions', json={'name': name, 'raw_sql': sql, 'contract_id': contract['id']}).json()['versions'][0]['id']
           for name, sql in (('All', 'SELECT SUM(amount) FROM snapshot'), ('Paid', "SELECT SUM(amount) FROM snapshot WHERE status = 'paid'"))]
    context = {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
               'unit_label': 'INR', 'values_comparable': True}
    queued = client.post('/api/v1/flat/comparisons', headers={'Idempotency-Key': 'manage'}, json={
        'baseline_version_id': ids[0], 'target_version_id': ids[1], 'snapshot_id': snapshot_id, 'context': context}).json()
    assert worker.run_once()
    return snapshot_id, client.get(f"/api/v1/comparisons/{queued['id']}").json()


def test_rename_and_delete_datasets_and_cases(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), _InProcess(), tmp_path / 'jobs', 'manage-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        snapshot_id, run = _setup(tmp_path, client, worker)
        assert run['state'] == 'succeeded'
        evidence = list((tmp_path / 'evidence').glob(f"{run['id']}-*"))
        assert evidence

        renamed = client.patch(f'/api/v1/snapshots/{snapshot_id}', json={'name': '  September orders '})
        assert renamed.status_code == 200 and renamed.json()['name'] == 'September orders'
        assert client.patch(f'/api/v1/snapshots/{snapshot_id}', json={'name': '  '}).status_code == 422

        case = client.post('/api/v1/investigations', json={'title': 'Paid only', 'run_id': run['id'],
                                                           'decision': 'Void order B removed.', 'actor_label': 'Tester'}).json()
        refused = client.delete(f'/api/v1/snapshots/{snapshot_id}')
        assert refused.status_code == 409 and '“Paid only”' in refused.json()['error']['message']

        retitled = client.patch(f"/api/v1/investigations/{case['id']}", json={'title': 'Paid orders only'})
        assert retitled.status_code == 200
        assert retitled.json()['title'] == 'Paid orders only'
        assert retitled.json()['title_history'][0]['title'] == 'Paid only'

        assert client.delete(f"/api/v1/investigations/{case['id']}").status_code == 200
        assert client.get(f"/api/v1/investigations/{case['id']}").status_code == 404

        deleted = client.delete(f'/api/v1/snapshots/{snapshot_id}')
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()['comparisons_removed'] == 1
        assert client.get(f'/api/v1/snapshots/{snapshot_id}').status_code == 404
        assert client.get(f"/api/v1/comparisons/{run['id']}").status_code == 404
        assert not any(path.exists() for path in evidence)
        assert all(s['id'] != snapshot_id for s in client.get('/api/v1/bootstrap').json()['snapshots'])
        assert client.delete(f'/api/v1/snapshots/{snapshot_id}').status_code == 404


def test_readers_cannot_rename_or_delete(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    worker = Supervisor(store, _InProcess(), tmp_path / 'jobs', 'manage-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        snapshot_id, _ = _setup(tmp_path, client, worker)
        pid = client.get('/api/v1/bootstrap').json()['project']['id']
        reader, key = store.create_user('Reader', 'reader@example.com')
        store.add_project_member(pid, reader['id'], 'reader')
        headers = {'X-API-Key': key, 'X-Project-Id': pid}
        assert client.patch(f'/api/v1/snapshots/{snapshot_id}', json={'name': 'x'}, headers=headers).status_code == 403
        assert client.delete(f'/api/v1/snapshots/{snapshot_id}', headers=headers).status_code == 403


def test_a_saved_case_can_become_an_approved_metric(tmp_path):
    """The case page's "Save as metric": create, attach the proposed calculation, approve it with the case as evidence."""
    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), _InProcess(), tmp_path / 'jobs', 'manage-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        snapshot_id, run = _setup(tmp_path, client, worker)
        pid = client.get('/api/v1/bootstrap').json()['project']['id']
        version = client.get(f"/api/v1/comparisons/{run['id']}").json()['target_version_id']
        definition = next(d for d in client.get('/api/v1/bootstrap').json()['definitions']
                          if any(v['id'] == version for v in d['versions']))
        metric = client.post(f'/api/v1/projects/{pid}/metrics', json={'display_name': 'Paid revenue'})
        assert metric.status_code == 201, metric.text
        metric_id = metric.json()['id']
        bound = client.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/bindings',
                            json={'definition_id': definition['id'], 'status': 'candidate', 'approved_version_id': version})
        assert bound.status_code == 201, bound.text
        promoted = client.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/promote',
                               json={'definition_id': definition['id'], 'approved_version_id': version, 'comparison_run_id': run['id']})
        assert promoted.status_code == 200, promoted.text
        active = [b for b in client.get(f'/api/v1/projects/{pid}/metrics/{metric_id}/bindings').json()['items'] if b['status'] == 'active']
        assert [b['approved_version_id'] for b in active] == [version]
        # The approval relies on the comparison, so its dataset can no longer be deleted.
        assert client.delete(f'/api/v1/snapshots/{snapshot_id}').status_code == 409


def test_bundled_sample_orders_import_through_the_normal_path(tmp_path, monkeypatch):
    """apps/web/src/sampleDataset.ts SAMPLE_CONTRACT + public/samples/sample_orders.csv, within the free-beta limits."""
    from pathlib import Path
    monkeypatch.setenv('METRICGUARD_MAX_ROWS', '50000')
    monkeypatch.setenv('METRICGUARD_MAX_UPLOAD_MB', '25')
    sample = Path(__file__).resolve().parents[1] / 'apps' / 'web' / 'public' / 'samples' / 'sample_orders.csv'
    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), _InProcess(), tmp_path / 'jobs', 'sample-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/flat/contracts', json={
            'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'Sample orders',
            'row_key': 'order_id', 'measure': 'amount', 'precision': 18, 'scale': 2,
            'categories': ['status', 'region', 'channel', 'order_date', 'customer_id'],
            'dimension_types': {'status': 'string', 'region': 'string', 'channel': 'string', 'order_date': 'date', 'customer_id': 'string'},
            'nullable_categories': [], 'measure_meaning': 'Order amount (synthetic sample)', 'unit_label': 'INR',
            'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
        assert contract.status_code == 201, contract.text
        with sample.open('rb') as content:
            uploaded = client.post('/api/v1/flat/snapshots', files={'file': ('sample_orders.csv', content, 'text/csv')},
                                   data={'contract_id': contract.json()['id'], 'name': 'Sample orders'})
        assert uploaded.status_code == 202, uploaded.text
        assert worker.run_once()
        done = client.get(f"/api/v1/snapshot-imports/{uploaded.json()['id']}").json()
        assert done['state'] == 'validated', done.get('error')
        assert client.get(f"/api/v1/snapshots/{done['snapshot_id']}").json()['row_count'] == 5000
