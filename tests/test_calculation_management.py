from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store
from services.supervisor.worker import Supervisor
from tests.test_distinct_and_ratio import _InProcess
from tests.test_item_management import _setup


def test_unused_calculation_can_be_renamed_and_deleted(tmp_path):
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        item = client.post('/api/v1/definitions', json={'name': 'Scratch', 'source_label': 'SQL',
            'owner_label': 'Tester', 'raw_sql': 'SELECT SUM(amount) FROM orders'}).json()
        renamed = client.patch(f"/api/v1/definitions/{item['id']}", json={'name': 'My amount'})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()['name'] == 'My amount'
        usage = client.get('/api/v1/definitions/usage').json()['items']
        assert next(u for u in usage if u['definition_id'] == item['id'])['can_delete']
        assert client.delete(f"/api/v1/definitions/{item['id']}").status_code == 200
        assert client.get(f"/api/v1/definitions/{item['id']}").status_code == 404


def test_run_and_case_references_preserved_but_only_case_retains_in_default_list(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    worker = Supervisor(store, _InProcess(), tmp_path / 'jobs', 'calculations-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        _, run = _setup(tmp_path, client, worker)
        definition = next(d for d in client.get('/api/v1/bootstrap').json()['definitions']
                          if any(v['id'] == run['target_version_id'] for v in d['versions']))
        ident = definition['id']
        usage = next(u for u in client.get('/api/v1/definitions/usage').json()['items'] if u['definition_id'] == ident)
        assert not usage['retained'] and not usage['can_delete']
        refused = client.delete(f'/api/v1/definitions/{ident}')
        assert refused.status_code == 409 and run['id'] in refused.json()['error']['message']
        case = client.post('/api/v1/investigations', json={'title': 'Paid case', 'run_id': run['id'],
            'decision': 'Keep paid orders', 'actor_label': 'Tester'}).json()
        usage = next(u for u in client.get('/api/v1/definitions/usage').json()['items'] if u['definition_id'] == ident)
        assert usage['retained']
        assert any(u['kind'] == 'case' and u['id'] == case['id'] for u in usage['used_by'])
        assert 'Paid case' in client.delete(f'/api/v1/definitions/{ident}').json()['error']['message']


def test_calculation_mutations_enforce_workspace_roles(tmp_path):
    app = create_app(data_dir=tmp_path)
    store = app.state.store
    with TestClient(app) as client:
        item = client.post('/api/v1/definitions', json={'name': 'Scratch', 'source_label': 'SQL',
            'owner_label': 'Tester', 'raw_sql': 'SELECT SUM(amount) FROM orders'}).json()
        reader, key = store.create_user('Reader', 'reader@example.com')
        store.add_project_member(store.project_id, reader['id'], 'reader')
        headers = {'X-API-Key': key, 'X-Project-Id': store.project_id}
        assert client.patch(f"/api/v1/definitions/{item['id']}", json={'name': 'Other'}, headers=headers).status_code == 403
        assert client.delete(f"/api/v1/definitions/{item['id']}", headers=headers).status_code == 403


def test_dataset_linked_metric_baseline_and_candidate_compare(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    worker = Supervisor(store, _InProcess(), tmp_path / 'jobs', 'metric-sql-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        sid, run = _setup(tmp_path, client, worker)
        pid = store.project_id
        definitions = client.get('/api/v1/bootstrap').json()['definitions']
        before = next(d for d in definitions if any(v['id'] == run['baseline_version_id'] for v in d['versions']))
        after = next(d for d in definitions if any(v['id'] == run['target_version_id'] for v in d['versions']))
        metric = client.post(f'/api/v1/projects/{pid}/metrics', json={'display_name': 'Revenue'}).json()
        prefix = f"/api/v1/projects/{pid}/metrics/{metric['id']}"
        promoted = client.post(prefix + '/promote', json={'definition_id': before['id'], 'approved_version_id': run['baseline_version_id']})
        assert promoted.status_code == 200, promoted.text
        bound = client.post(prefix + '/bindings', json={'definition_id': after['id'], 'approved_version_id': run['target_version_id'], 'status': 'candidate'})
        assert bound.status_code == 201, bound.text
        metric_summary = next(m for m in client.get('/api/v1/bootstrap').json()['metrics'] if m['id'] == metric['id'])
        assert metric_summary['candidate_count'] == 1
        usage = next(u for u in client.get('/api/v1/definitions/usage').json()['items'] if u['definition_id'] == after['id'])
        assert usage['retained'] and any(u['id'] == metric['id'] for u in usage['used_by'] if u['kind'] == 'metric')
        snapshot = store.snapshot(sid)
        queued = client.post('/api/v1/flat/comparisons', headers={'Idempotency-Key': 'metric-compare'}, json={
            'snapshot_id': sid, 'baseline_version_id': run['baseline_version_id'], 'target_version_id': run['target_version_id'],
            'context': {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': snapshot['contract_hash'], 'unit_label': 'INR', 'values_comparable': True}})
        assert queued.status_code == 202, queued.text
        assert worker.run_once()
        done = client.get(f"/api/v1/comparisons/{queued.json()['id']}").json()
        assert done['state'] == 'succeeded', done.get('error')
        assert done['result']['delta_target_minus_baseline'] == '-5.00'
        assert not client.get('/api/v1/definitions/usage').json()['items'][0]['can_delete']
