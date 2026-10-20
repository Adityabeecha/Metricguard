"""Hosted deployment: local runner (no Docker), durable file storage, and proxy/hosting switches."""
import gzip
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts.seed_demo import seed
from services import durable
from services.api.app import create_app, hosting_settings
from services.api.store import Store
from services.serve import WorkerPool
from services.supervisor.worker import DockerLauncher, LocalLauncher, Supervisor, launcher_from_environment


@pytest.fixture(autouse=True)
def reset_durable():
    yield
    durable.configure(None)


# ---------------------------------------------------------------- durable storage

def test_dir_backend_round_trip_with_sidecar(tmp_path):
    data, bucket = tmp_path / 'data', tmp_path / 'bucket'
    store = durable.Durable(data, durable.DirBackend(bucket))
    snapshot = data / 'snapshots' / 'a.duckdb'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b'frozen')
    snapshot.with_name('a.duckdb.manifest.json').write_text('{"m": 1}')

    store.push(snapshot)
    assert (bucket / 'snapshots/a.duckdb').read_bytes() == b'frozen'
    assert (bucket / 'snapshots/a.duckdb.manifest.json').read_text() == '{"m": 1}'

    snapshot.unlink()
    snapshot.with_name('a.duckdb.manifest.json').unlink()  # a redeploy wipes local disk
    assert store.ensure(snapshot)
    assert snapshot.read_bytes() == b'frozen'
    assert snapshot.with_name('a.duckdb.manifest.json').read_text() == '{"m": 1}'

    store.remove(snapshot)
    assert not snapshot.exists() and not (bucket / 'snapshots/a.duckdb').exists()
    assert not store.ensure(snapshot)


def test_files_outside_data_dir_and_local_mode_never_touch_storage(tmp_path):
    bucket = tmp_path / 'bucket'
    store = durable.Durable(tmp_path / 'data', durable.DirBackend(bucket))
    outside = tmp_path / 'elsewhere.txt'
    outside.write_text('x')
    store.push(outside)
    assert not bucket.exists()
    local = durable.Durable(tmp_path / 'data')
    assert not local.enabled
    assert local.ensure(outside) and not local.ensure(tmp_path / 'missing')


def test_environment_selects_backend(tmp_path, monkeypatch):
    monkeypatch.delenv('METRICGUARD_STORAGE', raising=False)
    assert not durable.from_environment(tmp_path).enabled
    monkeypatch.setenv('METRICGUARD_STORAGE', 'dir')
    monkeypatch.setenv('METRICGUARD_STORAGE_DIR', str(tmp_path / 'bucket'))
    assert isinstance(durable.from_environment(tmp_path).backend, durable.DirBackend)
    monkeypatch.setenv('METRICGUARD_STORAGE', 's3')
    with pytest.raises(durable.StorageError):
        durable.from_environment(tmp_path)  # credentials missing
    monkeypatch.setenv('METRICGUARD_STORAGE', 'ftp')
    with pytest.raises(durable.StorageError):
        durable.from_environment(tmp_path)


def test_s3_backend_signs_requests_and_streams_files(tmp_path):
    objects, seen = {}, []

    def handler(request):
        seen.append(request)
        key = request.url.raw_path.decode()
        if request.method == 'PUT':
            objects[key] = request.read()
            return httpx.Response(200)
        if request.method == 'GET':
            return httpx.Response(200, content=objects[key]) if key in objects else httpx.Response(404)
        if request.method == 'DELETE':
            objects.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(405)

    backend = durable.S3Backend('https://acct.r2.cloudflarestorage.com', 'mg-bucket', 'KEYID', 'SECRET',
                                prefix='prod/', client=httpx.Client(transport=httpx.MockTransport(handler)))
    source = tmp_path / 'evidence.jsonl'
    source.write_bytes(b'{"row": 1}\n' * 1000)
    backend.put('evidence/run 1.jsonl', source)

    put = seen[-1]
    assert put.url.raw_path == b'/mg-bucket/prod/evidence/run%201.jsonl'  # the signed, encoded path
    assert put.headers['host'] == 'acct.r2.cloudflarestorage.com'
    assert put.headers['x-amz-content-sha256'] == durable._sha256_file(source)
    auth = put.headers['authorization']
    assert auth.startswith('AWS4-HMAC-SHA256 Credential=KEYID/') and '/auto/s3/aws4_request' in auth
    assert 'SignedHeaders=host;x-amz-content-sha256;x-amz-date' in auth and len(auth.split('Signature=')[1]) == 64

    target = tmp_path / 'copy.jsonl'
    assert backend.get('evidence/run 1.jsonl', target) and target.read_bytes() == source.read_bytes()
    assert seen[-1].headers['x-amz-content-sha256'] == durable.EMPTY_SHA256
    assert not backend.get('evidence/missing.jsonl', tmp_path / 'missing.jsonl')
    assert not (tmp_path / 'missing.jsonl').exists()
    backend.delete('evidence/run 1.jsonl')
    assert not objects


def test_s3_signature_is_deterministic_for_fixed_time():
    from datetime import datetime, timezone
    backend = durable.S3Backend('https://h.example', 'b', 'AK', 'SK', region='us-east-1')
    at = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    one = backend._headers('GET', '/b/k', durable.EMPTY_SHA256, now=at)
    two = backend._headers('GET', '/b/k', durable.EMPTY_SHA256, now=at)
    other = durable.S3Backend('https://h.example', 'b', 'AK', 'OTHER', region='us-east-1')._headers(
        'GET', '/b/k', durable.EMPTY_SHA256, now=at)
    assert one == two and one['x-amz-date'] == '20261020T120000Z'
    assert one['authorization'] != other['authorization']
    assert 'Credential=AK/20261020/us-east-1/s3/aws4_request' in one['authorization']


# ---------------------------------------------------------------- local runner, end to end with a redeploy

def test_local_runner_comparison_survives_a_redeploy(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    data = tmp_path / 'data'
    data.mkdir()
    seed(url, data)
    app = create_app(database_url=url, data_dir=data, mode='isolated')
    bucket = tmp_path / 'bucket'
    durable.configure(durable.Durable(data, durable.DirBackend(bucket)))
    with TestClient(app) as client:
        boot = client.get('/api/v1/bootstrap').json()
        snapshot = boot['snapshots'][0]
        # Seeded data is written directly; hosted imports are pushed by the worker.
        store = Store(url)
        durable.push(store.snapshot(snapshot['id'])['artifact_path'])
        context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
                   'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
                   'scope': 'synthetic_retail_all_orders'}
        body = {'baseline_version_id': boot['definitions'][0]['versions'][0]['id'],
                'target_version_id': boot['definitions'][2]['versions'][0]['id'],
                'snapshot_id': snapshot['id'], 'context': context, 'relationship': 'expected_agreement'}
        queued = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'local-runner'}).json()

        worker = Supervisor(store, LocalLauncher(), tmp_path / 'jobs', 'hosted-worker', data_dir=data)
        assert worker.run_once()
        finished = client.get(f"/api/v1/comparisons/{queued['id']}").json()
        assert finished['state'] == 'succeeded', finished['error']
        assert finished['result']['delta_target_minus_baseline'] == '-6000000.00'
        evidence = data / 'evidence' / f"{queued['id']}-{finished['attempt']}.jsonl"
        assert gzip.decompress((bucket / 'evidence' / (evidence.name + '.gz')).read_bytes()) == evidence.read_bytes()

        # Redeploy: the container's disk is wiped, the bucket and database remain.
        for folder in ('evidence', 'snapshots'):
            for path in (data / folder).iterdir():
                path.unlink()
        page = client.get(f"/api/v1/comparisons/{queued['id']}/evidence?offset=0&limit=5")
        assert page.status_code == 200, page.text
        assert page.json()['total'] > 0 and evidence.is_file()

        replay = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'local-runner-replay'}).json()
        assert worker.run_once()  # fetches the frozen dataset back from storage
        again = client.get(f"/api/v1/comparisons/{replay['id']}").json()
        assert again['state'] == 'succeeded', again['error']
        assert again['result']['evidence_artifact']['sha256'] == finished['result']['evidence_artifact']['sha256']


def test_local_launcher_passes_no_secrets_to_the_engine(tmp_path, monkeypatch):
    monkeypatch.setenv('METRICGUARD_DATABASE_URL', 'postgresql://user:password@db/app')
    monkeypatch.setenv('METRICGUARD_S3_SECRET_ACCESS_KEY', 'secret')
    env = LocalLauncher().environment(tmp_path / 'in', tmp_path / 'out', tmp_path / 'scratch')
    assert 'METRICGUARD_DATABASE_URL' not in env and 'METRICGUARD_S3_SECRET_ACCESS_KEY' not in env
    assert env['METRICGUARD_JOB_INPUT'] == str(tmp_path / 'in') and env['TMPDIR'] == str(tmp_path / 'scratch')


def test_runner_choice_comes_from_environment(monkeypatch):
    monkeypatch.delenv('METRICGUARD_RUNNER', raising=False)
    assert isinstance(launcher_from_environment(), DockerLauncher)
    monkeypatch.setenv('METRICGUARD_RUNNER', 'local')
    assert isinstance(launcher_from_environment(), LocalLauncher)
    monkeypatch.setenv('METRICGUARD_RUNNER', 'kubernetes')
    with pytest.raises(ValueError):
        launcher_from_environment()


# ---------------------------------------------------------------- hosting switches

def hosted_client(tmp_path, monkeypatch, **env):
    defaults = {'METRICGUARD_ALLOWED_HOSTS': 'api.example.run', 'METRICGUARD_PUBLIC_ORIGINS': 'https://mg.example',
                'METRICGUARD_ORIGIN_SECRET': 'proxy-secret'}
    for key, value in {**defaults, **env}.items():
        monkeypatch.setenv(key, value)
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    return TestClient(create_app(database_url=url, data_dir=tmp_path, mode='demo'), base_url='http://api.example.run')


def test_proxy_secret_is_required_except_for_health(tmp_path, monkeypatch):
    with hosted_client(tmp_path, monkeypatch) as client:
        assert client.get('/api/v1/health').status_code == 200
        blocked = client.get('/api/v1/bootstrap')
        assert blocked.status_code == 403 and blocked.json()['error']['code'] == 'FORBIDDEN'
        assert client.get('/api/v1/bootstrap', headers={'x-origin-secret': 'wrong'}).status_code == 403
        ok = client.get('/api/v1/bootstrap', headers={'x-origin-secret': 'proxy-secret'})
        assert ok.status_code == 200
        assert ok.headers['cache-control'] == 'no-store'


def test_public_origin_may_change_data_and_others_may_not(tmp_path, monkeypatch):
    with hosted_client(tmp_path, monkeypatch) as client:
        secret = {'x-origin-secret': 'proxy-secret'}
        foreign = client.delete('/api/v1/auth/session', headers={**secret, 'origin': 'https://evil.example'})
        assert foreign.status_code == 403 and foreign.json()['error']['code'] == 'LOCAL_ONLY'
        own = client.delete('/api/v1/auth/session', headers={**secret, 'origin': 'https://mg.example'})
        assert own.status_code != 403 or own.json()['error']['code'] != 'LOCAL_ONLY'


def test_unknown_host_is_rejected(tmp_path, monkeypatch):
    with hosted_client(tmp_path, monkeypatch) as client:
        response = client.get('/api/v1/health', headers={'host': 'attacker.example'})
        assert response.status_code == 400


def test_hosting_settings_are_off_by_default():
    settings = hosting_settings({})
    assert settings == {'hosts': [], 'origins': set(), 'origin_secret': '', 'secure_cookies': False}
    assert hosting_settings({'METRICGUARD_SECURE_COOKIES': 'true',
                             'METRICGUARD_PUBLIC_ORIGINS': 'https://a.example/, https://b.example'})['origins'] == \
        {'https://a.example', 'https://b.example'}


# ---------------------------------------------------------------- one container: API + worker pool

def test_worker_pool_restarts_a_worker_that_exits():
    pool = WorkerPool(1, command=[sys.executable, '-c', 'import sys; sys.exit(3)'], restart_delay=0.05)
    pool.start()
    deadline = time.monotonic() + 15
    while pool.restarts < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    pool.stop()
    assert pool.restarts >= 2


def test_worker_pool_stops_its_workers():
    pool = WorkerPool(2, command=[sys.executable, '-c', 'import time; time.sleep(60)'])
    pool.start()
    deadline = time.monotonic() + 10
    while any(p is None for p in pool.processes) and time.monotonic() < deadline:
        time.sleep(0.05)
    pool.stop(timeout=5)
    assert all(p is not None and p.poll() is not None for p in pool.processes)


def test_engine_killed_for_memory_gets_a_clear_message(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    data = tmp_path / 'data'
    data.mkdir()
    seed(url, data)
    app = create_app(database_url=url, data_dir=data, mode='isolated')
    with TestClient(app) as client:
        boot = client.get('/api/v1/bootstrap').json()
        context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
                   'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
                   'scope': 'synthetic_retail_all_orders'}
        body = {'baseline_version_id': boot['definitions'][0]['versions'][0]['id'],
                'target_version_id': boot['definitions'][2]['versions'][0]['id'],
                'snapshot_id': boot['snapshots'][0]['id'], 'context': context, 'relationship': 'expected_agreement'}
        queued = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'oom'}).json()

        class Killed:
            def poll(self):
                return -9

            def wait(self, timeout=None):
                return -9

        class OutOfMemoryLauncher:
            def start(self, input_dir, output_dir, command=None):
                return Killed()

        assert Supervisor(Store(url), OutOfMemoryLauncher(), tmp_path / 'jobs', 'oom-worker', data_dir=data).run_once()
        failed = client.get(f"/api/v1/comparisons/{queued['id']}").json()
        assert failed['state'] == 'failed'
        assert failed['error']['code'] == 'RUNNER_OUT_OF_MEMORY'
        assert 'ran out of memory' in failed['error']['message']
