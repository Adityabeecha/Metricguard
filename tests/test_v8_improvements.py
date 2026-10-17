"""V8 improvement pass: access hardening, sessions, record types, evidence tools."""
import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.delenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', raising=False)
    from scripts.seed_demo import seed
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    with TestClient(app) as client:
        yield app, client


def _run(client):
    data = client.get('/api/v1/bootstrap').json()
    body = {'baseline_version_id': data['definitions'][0]['versions'][0]['id'],
            'target_version_id': data['definitions'][2]['versions'][0]['id'], 'snapshot_id': data['snapshots'][0]['id'],
            'context': {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata', 'currency': 'INR',
                        'unit': 'currency', 'grain': 'one_row_per_order_id', 'scope': 'synthetic_retail_all_orders'},
            'relationship': 'expected_agreement'}
    response = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'improve-1'})
    assert response.status_code == 202, response.text
    return response.json()


def test_issuing_keys_requires_a_verified_owner(demo):
    app, client = demo
    store = app.state.store
    body = {'name': 'Mallory', 'email': 'm@example.com'}
    assert client.post('/api/v1/users', json=body).status_code == 403  # unverified local caller
    reader, reader_key = store.create_user('Reader', 'r@example.com')
    store.add_project_member(store.project_id, reader['id'], 'reader')
    assert client.post('/api/v1/users', json=body, headers={'X-API-Key': reader_key}).status_code == 403
    owner, owner_key = store.create_user('Owner', 'o@example.com')
    store.add_project_member(store.project_id, owner['id'], 'owner')
    created = client.post('/api/v1/users', json=body, headers={'X-API-Key': owner_key})
    assert created.status_code == 201 and created.json()['api_key'].startswith('mg_')


def test_workspace_listing_never_discloses_unopenable_workspaces(demo):
    app, client = demo
    app.state.store.create_project('Hidden', project_id='hidden-team')
    ids = [w['id'] for w in client.get('/api/v1/workspaces').json()]
    assert 'hidden-team' not in ids and ids == [w['id'] for w in client.get('/api/v1/projects').json()]


def test_session_cookie_survives_without_key_and_is_forgery_guarded(demo, monkeypatch):
    app, client = demo
    store = app.state.store
    user, key = store.create_user('Asha', 'asha@example.com')
    store.add_project_member(store.project_id, user['id'], 'owner')
    monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'false')
    assert client.get('/api/v1/bootstrap').status_code == 401

    assert client.post('/api/v1/auth/session').status_code == 401  # a key is required to start one
    started = client.post('/api/v1/auth/session', headers={'X-API-Key': key})
    assert started.status_code == 200
    cookie = started.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'path=/api' in cookie

    identity = client.get('/api/v1/auth/identity').json()  # cookie only, no key header
    assert identity['verified'] and identity['session'] and identity['user']['id'] == user['id']
    assert client.get('/api/v1/bootstrap').status_code == 200
    # State-changing cookie requests need the web-client header (forged cross-site posts lack it).
    body = {'name': 'X', 'email': 'x@example.com'}
    assert client.post('/api/v1/users', json=body).status_code == 403
    assert client.post('/api/v1/users', json=body, headers={'X-MetricGuard-Client': 'web'}).status_code == 201

    store.deactivate_users_by_email('asha@example.com')
    assert client.get('/api/v1/bootstrap').status_code == 401  # deactivation ends sessions
    assert client.delete('/api/v1/auth/session').json() == {'signed_in': False}


def test_investigation_detail_declares_its_record_type(demo):
    _, client = demo
    run = _run(client)
    saved = client.post('/api/v1/investigations', json={'title': 'T', 'run_id': run['id'], 'decision': 'D', 'actor_label': 'A'}).json()
    detail = client.get(f"/api/v1/investigations/{saved['id']}").json()
    assert detail['record_type'] == 'investigation' and detail['is_draft'] is False


def test_evidence_summary_filters_and_full_csv(demo):
    _, client = demo
    run = _run(client)
    summary = client.get(f"/api/v1/comparisons/{run['id']}/evidence-summary").json()
    assert summary['total'] == 8 and summary['changed'] >= 1
    assert len(summary['top_changes']) == min(summary['changed'], 20)
    changed = client.get(f"/api/v1/comparisons/{run['id']}/evidence?changed_only=true&order=impact").json()
    assert changed['total'] == summary['changed']
    magnitudes = [abs(float(r['delta'])) for r in changed['items']]
    assert magnitudes == sorted(magnitudes, reverse=True)
    first_key = changed['items'][0]['order_id']
    found = client.get(f"/api/v1/comparisons/{run['id']}/evidence", params={'q': first_key}).json()
    assert found['total'] >= 1 and any(r['order_id'] == first_key for r in found['items'])
    csv = client.get(f"/api/v1/comparisons/{run['id']}/evidence.csv")
    assert csv.status_code == 200 and csv.headers['content-type'].startswith('text/csv')
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith('record,current_included') and len(lines) == 1 + summary['total']
