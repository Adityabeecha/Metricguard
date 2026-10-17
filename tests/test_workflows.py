from pathlib import Path
import json
import hashlib
import duckdb

from fastapi.testclient import TestClient

from scripts.seed_demo import seed
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.runner import ingest_job, run_job
from services.supervisor.worker import Supervisor
from packages.metricguard_core import ingest_csv


FIXTURE = Path(__file__).resolve().parents[1] / 'MetricGuard_Planning_Pack/examples/orders_golden.csv'


class Completed:
    def __init__(self, code=0):
        self.code = code

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code


class InProcessLauncher:
    def start(self, input_dir, output_dir, command=None):
        from packages.metricguard_core import CoreError
        assert command in (None, 'ingest')
        try:
            if command == 'ingest':
                ingest_job(input_dir, output_dir)
            else:
                run_job(input_dir, output_dir)
        except CoreError as error:
            (Path(output_dir) / 'error.json').write_text(json.dumps({'code': error.code, 'message': error.message}))
            return Completed(2)
        return Completed()


def test_browser_upload_is_queued_and_published_only_after_worker(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    app = create_app(database_url=url, data_dir=tmp_path, mode='isolated')
    with TestClient(app) as client:
        response = client.post('/api/v1/snapshots', files={'file': ('orders.csv', FIXTURE.read_bytes())},
                               data={'name': 'Uploaded fixture', 'scope': 'v2_test'})
        assert response.status_code == 202, response.text
        queued = response.json()
        assert queued['state'] == 'queued'
        assert 'source_path' not in queued
        duplicate = client.post('/api/v1/snapshots', files={'file': ('orders.csv', FIXTURE.read_bytes())},
                                data={'name': 'Second label', 'scope': 'v2_test'}).json()
        assert duplicate['id'] == queued['id']
        assert duplicate['reused'] is True
        assert len(list((tmp_path / 'uploads').glob('*.csv'))) == 1
        assert client.get('/api/v1/bootstrap').json()['snapshots'] == []
        worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'workflow-worker', data_dir=tmp_path)
        assert worker.run_once()
        completed = client.get(f"/api/v1/snapshot-imports/{queued['id']}").json()
        assert completed['state'] == 'validated'
        assert completed['snapshot_id']
        assert client.get('/api/v1/bootstrap').json()['snapshots'][0]['id'] == completed['snapshot_id']
        assert list((tmp_path / 'uploads').glob('*.csv')) == []


def test_parquet_upload_runs_through_isolated_ingestion_and_comparison(tmp_path):
    source_db = tmp_path / 'source.duckdb'
    ingest_csv(FIXTURE, source_db)
    parquet = tmp_path / 'orders.parquet'
    with duckdb.connect(str(source_db)) as con:
        con.execute('COPY orders TO ? (FORMAT PARQUET)', [str(parquet)])

    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        response = client.post('/api/v1/snapshots', files={'file': ('orders.parquet', parquet.read_bytes())})
        assert response.status_code == 202, response.text
        queued = response.json()
        worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'parquet-worker', data_dir=tmp_path)
        assert worker.run_once()
        completed = client.get(f"/api/v1/snapshot-imports/{queued['id']}").json()
        assert completed['state'] == 'validated', completed.get('error')
        accepted = next(item for item in client.get('/api/v1/bootstrap').json()['snapshots']
                        if item['id'] == completed['snapshot_id'])
        assert accepted['row_count'] == 8
        assert accepted['source_hash'] == hashlib.sha256(parquet.read_bytes()).hexdigest()
        assert list((tmp_path / 'uploads').glob('*.parquet')) == []

        definitions = client.get('/api/v1/bootstrap').json()['definitions']
        context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
                   'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
                   'scope': 'synthetic_retail_all_orders'}
        run = client.post('/api/v1/comparisons', json={
            'baseline_version_id': definitions[0]['versions'][0]['id'],
            'target_version_id': definitions[2]['versions'][0]['id'],
            'snapshot_id': accepted['id'], 'context': context,
            'relationship': 'expected_agreement'}, headers={'Idempotency-Key': 'parquet-golden'})
        assert run.status_code == 202, run.text
        assert worker.run_once()
        finished = client.get(f"/api/v1/comparisons/{run.json()['id']}").json()
        assert finished['state'] == 'succeeded', finished.get('error')
        assert finished['result']['delta_target_minus_baseline'] == '-6000000.00'


def test_invalid_parquet_fails_without_publishing_snapshot(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        queued = client.post('/api/v1/snapshots',
                             files={'file': ('broken.parquet', b'not a parquet file')})
        assert queued.status_code == 202
        worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'parquet-worker', data_dir=tmp_path)
        assert worker.run_once()
        result = client.get(f"/api/v1/snapshot-imports/{queued.json()['id']}").json()
        assert result['state'] == 'failed'
        assert result['error']['code'] == 'INVALID_PARQUET'
        assert client.get('/api/v1/bootstrap').json()['snapshots'] == []


def test_import_lease_recovery_rejects_stale_worker(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'app.db'}")
    pending = store.queue_import(tmp_path / 'staged.csv', 'a' * 64, 'Recovery', {'currency': 'INR'})
    first = store.claim_import('worker-one', lease_seconds=-1)
    second = store.claim_import('worker-two')
    assert first['id'] == pending['id'] == second['id']
    assert second['attempt'] == first['attempt'] + 1
    assert not store.finish_import(pending['id'], 'worker-one', first['attempt'],
                                   error={'code': 'STALE', 'message': 'Stale attempt'})
    assert store.finish_import(pending['id'], 'worker-two', second['attempt'],
                               error={'code': 'VALIDATION', 'message': 'Invalid CSV'})
    assert store.get_import(pending['id'])['state'] == 'failed'


def test_bad_csv_fails_without_snapshot_and_demo_rejects_new_bytes(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        queued = client.post('/api/v1/snapshots', files={'file': ('orders.csv', b'wrong,header\n1,2\n')}).json()
        worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'workflow-worker', data_dir=tmp_path)
        assert worker.run_once()
        result = client.get(f"/api/v1/snapshot-imports/{queued['id']}").json()
        assert result['state'] == 'failed'
        assert result['error']['code'] == 'INVALID_SCHEMA'
        assert 'exact ordered schema' in result['error']['message']
        assert client.get('/api/v1/bootstrap').json()['snapshots'] == []
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='demo')) as client:
        response = client.post('/api/v1/snapshots', files={'file': ('orders.csv', b'new,data\n1,2\n')})
        assert response.json()['error']['code'] == 'ISOLATION_REQUIRED'


def test_candidates_are_deterministic_and_review_is_explicit(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='demo')) as client:
        first = client.get('/api/v1/candidates').json()['items']
        assert first == client.get('/api/v1/candidates').json()['items']
        assert first and first[0]['reasons']
        assert all(item['review_status'] == 'unreviewed' for item in first)
        pair = first[0]
        response = client.post('/api/v1/pair-reviews', json={'left_definition_id': pair['left_definition_id'],
            'right_definition_id': pair['right_definition_id'], 'relation': 'intentional_variant',
            'reason': 'Reviewed', 'actor_label': 'Analyst'})
        assert response.status_code == 201
        updated = client.get('/api/v1/candidates').json()['items']
        assert next(x for x in updated if x['left_definition_id'] == pair['left_definition_id']
                    and x['right_definition_id'] == pair['right_definition_id'])['review_status'] == 'intentional_variant'


def test_definition_replay_keeps_one_snapshot_and_labels_purpose(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='demo')) as client:
        data = client.get('/api/v1/bootstrap').json()
        original, other = data['definitions'][0], data['definitions'][1]
        new = client.post(f"/api/v1/definitions/{original['id']}/versions", json={
            'raw_sql': other['versions'][0]['raw_sql'], 'time_column': 'order_date',
            'source_changed_at': '2026-08-15', 'effective_from': '2026-08-16'}).json()
        assert new['source_changed_at'] == '2026-08-15'
        assert new['effective_from'] == '2026-08-16'
        assert new['observed_at'] != new['source_changed_at']
        body = {'baseline_version_id': original['versions'][0]['id'],
                'target_version_id': new['id'], 'snapshot_id': data['snapshots'][0]['id'],
                'relationship': 'candidate', 'context': {'start': '2026-08-01',
                    'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata', 'currency': 'INR',
                    'unit': 'currency', 'grain': 'one_row_per_order_id', 'scope': 'synthetic_retail_all_orders'}}
        response = client.post(f"/api/v1/definitions/{original['id']}/replays", json=body,
                               headers={'Idempotency-Key': 'replay-one'})
        assert response.status_code == 202, response.text
        run = response.json()
        assert run['purpose'] == 'definition_replay'
        assert run['snapshot_id'] == data['snapshots'][0]['id']
        assert run['result']['delta_target_minus_baseline'] == '700000.00'
        body['target_version_id'] = other['versions'][0]['id']
        invalid = client.post(f"/api/v1/definitions/{original['id']}/replays", json=body,
                              headers={'Idempotency-Key': 'replay-invalid'})
        assert invalid.json()['error']['code'] == 'INVALID_REPLAY'
