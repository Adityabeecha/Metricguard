"""Repeating a guided comparison reuses its calculations and draft instead of adding duplicates."""
from fastapi.testclient import TestClient

from services.api.app import create_app
from tests.test_flat_api import CONTRACT


def _client(tmp_path):
    return TestClient(create_app(database_url=f"sqlite:///{tmp_path / 'app.db'}", data_dir=tmp_path, mode='isolated'))


def test_identical_calculation_reuses_the_existing_definition(tmp_path):
    with _client(tmp_path) as client:
        contract = client.post('/api/v1/flat/contracts', json=CONTRACT).json()
        body = {'name': 'All values', 'contract_id': contract['id'], 'raw_sql': 'SELECT SUM(recorded_value) FROM snapshot'}
        first = client.post('/api/v1/flat/definitions', json=body).json()
        again = client.post('/api/v1/flat/definitions', json={**body, 'name': 'Renamed by the form'}).json()
        assert again['id'] == first['id']
        assert again['versions'][0]['id'] == first['versions'][0]['id']
        other = client.post('/api/v1/flat/definitions', json={**body, 'raw_sql': "SELECT SUM(recorded_value) FROM snapshot WHERE state = 'CA'"}).json()
        assert other['id'] != first['id']
        assert len(client.get('/api/v1/bootstrap').json()['definitions']) == 2


def test_repeated_comparison_creates_no_duplicate_draft(tmp_path):
    with _client(tmp_path) as client:
        pid = client.get('/api/v1/bootstrap').json()['project']['id']
        url = f'/api/v1/projects/{pid}/investigation-drafts'
        draft = {'title': 'Values: CA only', 'snapshot_id': 'snapshot-1', 'baseline_version_id': 'base',
                 'target_version_id': 'target', 'comparison_run_id': 'run-1', 'status': 'needs_decision'}
        first = client.post(url, json={**draft, 'idempotency_key': 'guided-abc'})
        assert first.status_code == 201, first.text
        # The browser retrying with the same key, and a fresh page re-running the same comparison
        # (same server run, new key), both resume the one draft.
        retried = client.post(url, json={**draft, 'idempotency_key': 'guided-abc'}).json()
        rerun = client.post(url, json={**draft, 'idempotency_key': 'guided-other-page'}).json()
        assert retried['id'] == first.json()['id'] == rerun['id']
        assert len(client.get(url).json()['items']) == 1
        # A different comparison still gets its own draft.
        second = client.post(url, json={**draft, 'comparison_run_id': 'run-2', 'idempotency_key': 'guided-def'}).json()
        assert second['id'] != first.json()['id']
        assert len(client.get(url).json()['items']) == 2


def test_draft_retry_key_is_bounded(tmp_path):
    with _client(tmp_path) as client:
        pid = client.get('/api/v1/bootstrap').json()['project']['id']
        response = client.post(f'/api/v1/projects/{pid}/investigation-drafts', json={'title': 'x', 'idempotency_key': 'k' * 161})
        assert response.status_code == 422
