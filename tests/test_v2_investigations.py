from fastapi.testclient import TestClient

from scripts.seed_demo import CONTEXT, seed
from services.api.app import create_app


def test_saved_investigation_pins_run_and_can_replay_same_inputs(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='demo')) as client:
        initial = client.get('/api/v1/bootstrap').json()
        versions = [item['versions'][0]['id'] for item in initial['definitions'][:3]]
        snapshot = initial['snapshots'][0]['id']
        request = {'baseline_version_id': versions[0], 'target_version_id': versions[2],
                   'snapshot_id': snapshot, 'context': CONTEXT,
                   'relationship': 'expected_agreement'}
        run = client.post('/api/v1/comparisons', json=request,
                          headers={'Idempotency-Key': 'case-original'})
        assert run.status_code == 202, run.text
        assert run.json()['state'] == 'succeeded'
        saved = client.post('/api/v1/investigations', json={
            'title': 'August revenue gap', 'run_id': run.json()['id'],
            'decision': 'Check payment-date cutoff with finance',
            'actor_label': 'Local analyst'})
        assert saved.status_code == 201, saved.text
        case = client.get(f"/api/v1/investigations/{saved.json()['id']}")
        assert case.status_code == 200
        assert case.json()['run']['id'] == run.json()['id']
        assert case.json()['snapshot']['id'] == snapshot
        assert case.json()['baseline_version']['id'] == versions[0]
        assert case.json()['target_version']['id'] == versions[2]
        assert case.json()['context'] == CONTEXT
        assert case.json()['run']['result']['delta_target_minus_baseline'] == '-6000000.00'

        replay = client.post(f"/api/v1/investigations/{saved.json()['id']}/replays",
                             headers={'Idempotency-Key': 'case-replay'})
        assert replay.status_code == 202, replay.text
        assert replay.json()['snapshot_id'] == snapshot
        assert replay.json()['baseline_version_id'] == versions[0]
        assert replay.json()['target_version_id'] == versions[2]
        assert replay.json()['result']['delta_target_minus_baseline'] == '-6000000.00'


def test_investigation_refuses_run_from_another_snapshot(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='demo')) as client:
        data = client.get('/api/v1/bootstrap').json()
        run = client.post('/api/v1/comparisons', json={
            'baseline_version_id': data['definitions'][0]['versions'][0]['id'],
            'target_version_id': data['definitions'][2]['versions'][0]['id'],
            'snapshot_id': data['snapshots'][0]['id'], 'context': CONTEXT},
            headers={'Idempotency-Key': 'case-mismatch'}).json()
        response = client.post('/api/v1/investigations', json={
            'title': 'Wrong snapshot', 'run_id': run['id'],
            'snapshot_id': 'not-the-run-snapshot',
            'decision': 'Test', 'actor_label': 'Local analyst'})
        assert response.status_code == 422
