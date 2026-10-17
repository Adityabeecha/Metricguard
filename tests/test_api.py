from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from services.api.app import create_app
    from scripts.seed_demo import seed
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='demo')) as test_client:
        yield test_client


def request_for(client):
    data = client.get('/api/v1/bootstrap').json()
    return {'baseline_version_id': data['definitions'][0]['versions'][0]['id'],
            'target_version_id': data['definitions'][2]['versions'][0]['id'], 'snapshot_id': data['snapshots'][0]['id'],
            'context': {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
                        'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id', 'scope': 'synthetic_retail_all_orders'},
            'relationship': 'expected_agreement'}


def test_complete_demo_api_returns_verified_exact_evidence_and_export(client):
    response = client.post('/api/v1/comparisons', json=request_for(client), headers={'Idempotency-Key': 'test-golden'})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run['result']['delta_target_minus_baseline'] == '-6000000.00'
    assert run['execution_mode'] == 'demo'
    assert client.get(f"/api/v1/comparisons/{run['id']}/evidence?limit=2").json()['total'] == 8
    report = client.get(f"/api/v1/comparisons/{run['id']}/export?format=json").json()
    assert report['result']['provenance']['snapshot_manifest_hash']
    assert report['result']['residual'] == '0.00'


def test_demo_shows_different_definitions_with_equal_value(client):
    data = client.get('/api/v1/bootstrap').json()
    definitions = {item['name']: item for item in data['definitions']}
    baseline = definitions['Dashboard A']['versions'][0]
    variant = definitions['Dashboard A (equal-value variant)']['versions'][0]
    assert baseline['ir_hash'] != variant['ir_hash']
    body = request_for(client)
    body['target_version_id'] = variant['id']
    response = client.post('/api/v1/comparisons', json=body,
                           headers={'Idempotency-Key': 'equal-value-demo'})
    assert response.status_code == 202, response.text
    result = response.json()['result']
    assert result['baseline_value'] == result['target_value'] == '104000000.00'
    assert result['definition_status'] == 'different'
    assert result['value_status'] == 'equal_on_snapshot'


def test_demo_replays_changed_date_rule_on_fixed_snapshot(client):
    data = client.get('/api/v1/bootstrap').json()
    definition = next(item for item in data['definitions'] if item['name'] == 'Dashboard A')
    assert len(definition['versions']) == 2
    original, changed = definition['versions']
    assert original['time_column'] == 'order_date'
    assert changed['time_column'] == 'payment_date'
    body = request_for(client)
    body['target_version_id'] = changed['id']
    response = client.post(f"/api/v1/definitions/{definition['id']}/replays", json=body,
                           headers={'Idempotency-Key': 'date-replay-demo'})
    assert response.status_code == 202, response.text
    assert response.json()['purpose'] == 'definition_replay'
    assert response.json()['result']['delta_target_minus_baseline'] == '-1680000.00'


def test_demo_blocks_uncomputed_context_without_executing(client):
    body = request_for(client)
    body['context']['currency'] = 'USD'
    response = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'mismatch'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'NOT_COMPARABLE'


def test_unsupported_import_remains_visible_and_cannot_be_run(client):
    definition = client.post('/api/v1/definitions', json={'name': 'Unsafe scan', 'source_label': 'Test', 'owner_label': 'Local',
        'raw_sql': "SELECT SUM(order_amount) FROM read_csv('/etc/passwd')", 'time_column': 'order_date'}).json()
    assert definition['versions'][0]['support_state'] == 'unsupported'
    body = request_for(client)
    body['target_version_id'] = definition['versions'][0]['id']
    response = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'unsupported'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'UNSUPPORTED_CONSTRUCT'


def test_html_export_escapes_imported_names(client):
    bootstrap = client.get('/api/v1/bootstrap').json()
    original = bootstrap['definitions'][0]['versions'][0]
    definition = client.post('/api/v1/definitions', json={'name': '<script>alert(1)</script>', 'source_label': 'Test', 'owner_label': 'Local',
        'raw_sql': original['raw_sql'], 'time_column': original['time_column']}).json()
    body = request_for(client)
    body['baseline_version_id'] = definition['versions'][0]['id']
    response = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'escape'})
    assert response.status_code == 202, response.text
    html = client.get(f"/api/v1/comparisons/{response.json()['id']}/export?format=html").text
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def test_api_rejects_missing_idempotency_and_keeps_retries_stable(client):
    body = request_for(client)
    assert client.post('/api/v1/comparisons', json=body).status_code == 422
    one = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'repeat'})
    two = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'repeat'})
    assert one.json()['id'] == two.json()['id']
    body['relationship'] = 'intentional_variant'
    assert client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'repeat'}).status_code == 409


def test_snapshot_files_and_local_paths_are_not_exposed(client):
    data = client.get('/api/v1/bootstrap').json()
    assert all('artifact_path' not in snapshot for snapshot in data['snapshots'])
    assert all('manifest' not in snapshot for snapshot in data['snapshots'])
    assert client.post('/api/v1/snapshots', files={'file': ('bad.zip', b'not-a-csv')}).status_code == 422
