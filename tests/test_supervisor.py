import hashlib
import json
from pathlib import Path

import pytest

from services.supervisor.worker import DockerLauncher, Supervisor, validate_result, isolated_ingest
from services.supervisor.runner import run_job, ingest_job


class FakeProcess:
    def __init__(self, result=None, code=0):
        self.args = None
        self.code = code
        self.result = result
        self.killed = False

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.code


class FakeStore:
    def __init__(self, snapshot, run):
        self._snapshot = snapshot
        self._run = run
        self.finished = []
        self.heartbeats = []

    def claim(self, worker_id, lease_seconds=90):
        return self._run

    def snapshot(self, id):
        return self._snapshot

    def version(self, id):
        return {'id': id, 'ir': {'metric': id}}

    def heartbeat(self, run_id, worker_id, attempt, lease_seconds=90):
        self.heartbeats.append((run_id, worker_id, attempt))
        return True

    def get_run(self, id):
        return self._run

    def finish(self, run_id, worker_id, attempt, result=None, error=None):
        self.finished.append((run_id, worker_id, attempt, result, error))
        return True


def test_docker_launch_enforces_isolation_at_process_boundary(monkeypatch, tmp_path):
    process = FakeProcess()
    captured = []

    def popen(args, **kwargs):
        captured.append((args, kwargs))
        return process

    monkeypatch.setattr('services.supervisor.worker.subprocess.Popen', popen)
    launcher = DockerLauncher(image='metricguard-runner:1.0')
    launcher.start(tmp_path / 'input', tmp_path / 'output')
    args, kwargs = captured[0]
    assert args[:3] == ['docker', 'run', '--rm']
    for pair in (['--network', 'none'], ['--read-only'], ['--user', '10001:10001'],
                 ['--cap-drop', 'ALL'], ['--security-opt', 'no-new-privileges'],
                 ['--memory', '1g'], ['--cpus', '1'], ['--pids-limit', '64']):
        assert all(item in args for item in pair)
    assert 'metricguard-runner:1.0' in args
    assert not any('docker.sock' in item for item in args)
    assert kwargs['stdout'] == -1 and kwargs['stderr'] == -1
    assert args[args.index('--name') + 1].startswith('metricguard-')


def test_supervisor_rejects_changed_snapshot_before_launch(tmp_path):
    db = tmp_path / 'snapshot.duckdb'
    db.write_bytes(b'changed')
    run = {'id': 'r1', 'attempt': 3, 'snapshot_id': 's1', 'baseline_version_id': 'v1',
           'target_version_id': 'v2', 'execution_mode': 'isolated', 'context': {}}
    store = FakeStore({'artifact_path': str(db), 'manifest': {'database_hash': '0' * 64}}, run)

    class NeverLaunch:
        def start(self, *_):
            raise AssertionError('corrupt snapshot must not launch')

    Supervisor(store, NeverLaunch(), tmp_path / 'jobs', worker_id='w1').run_once()
    assert store.finished[0][4]['code'] == 'SNAPSHOT_HASH_MISMATCH'


def test_supervisor_skips_demo_job(tmp_path):
    store = FakeStore({}, {'id': 'demo', 'attempt': 1, 'execution_mode': 'demo'})
    class NeverLaunch:
        def start(self, *_):
            raise AssertionError('demo must not launch')
    assert Supervisor(store, NeverLaunch(), tmp_path, worker_id='w1').run_once() is False
    assert store.finished == []


def test_invalid_reconciliation_is_rejected():
    result = {'baseline_value': '10', 'target_value': '13',
              'delta_target_minus_baseline': '4', 'absolute_gap': '3',
              'residual': '0', 'steps': [], 'evidence': [], 'provenance': {}}
    with pytest.raises(ValueError, match='delta'):
        validate_result(result)


def test_runner_rechecks_snapshot_hash_before_core_execution(monkeypatch, tmp_path):
    source = tmp_path / 'input'
    output = tmp_path / 'output'
    source.mkdir()
    output.mkdir()
    (source / 'snapshot.duckdb').write_bytes(b'tampered')
    (source / 'job.json').write_text(json.dumps({'snapshot_hash': '0' * 64,
        'baseline_ir': {}, 'target_ir': {}, 'context': {}}))
    monkeypatch.setattr('packages.metricguard_core.compare',
                        lambda *_: (_ for _ in ()).throw(AssertionError('core called')),
                        raising=False)
    with pytest.raises(ValueError, match='SNAPSHOT_HASH_MISMATCH'):
        run_job(source, output)


def test_undefined_sum_result_stays_unavailable():
    result = {'baseline_value': None, 'target_value': '5',
              'delta_target_minus_baseline': None, 'absolute_gap': None,
              'residual': None, 'steps': [], 'evidence': [], 'provenance': {},
              'value_status': 'unavailable'}
    assert validate_result(result) is result


def test_partial_bridge_retains_valid_endpoint_and_residual():
    result = {'baseline_value': '10', 'target_value': '5',
              'delta_target_minus_baseline': '-5', 'absolute_gap': '5',
              'residual': '-3', 'explanation_status': 'partial',
              'steps': [{'previous_value': '10', 'next_value': '8', 'contribution': '-2'}],
              'evidence': [], 'provenance': {}}
    assert validate_result(result) is result


def test_ingest_runner_writes_manifest_for_verified_artifact(monkeypatch, tmp_path):
    source = tmp_path / 'input'
    output = tmp_path / 'output'
    source.mkdir()
    output.mkdir()
    (source / 'source.csv').write_text('order_id\n1\n')
    (source / 'metadata.json').write_text('{}')
    def fake_ingest(csv_path, db_path, metadata):
        db_path.write_bytes(b'database')
        return {'database_hash': hashlib.sha256(b'database').hexdigest(), 'row_count': 1}
    monkeypatch.setattr('packages.metricguard_core.ingest_csv', fake_ingest, raising=False)
    manifest = ingest_job(source, output)
    assert manifest['row_count'] == 1
    assert (output / 'snapshot.duckdb').read_bytes() == b'database'


def test_isolated_ingest_rejects_runner_artifact_with_false_hash(monkeypatch, tmp_path):
    source = tmp_path / 'source.csv'
    source.write_text('order_id\n1\n')
    destination = tmp_path / 'saved.duckdb'
    def popen(args, **kwargs):
        mount = next(arg for arg in args if 'dst=/job/output' in arg)
        output = Path(mount.split('src=')[1].split(',dst=')[0])
        (output / 'snapshot.duckdb').write_bytes(b'tampered')
        (output / 'manifest.json').write_text(json.dumps({'database_hash': '0' * 64}))
        (output / 'snapshot.duckdb.manifest.json').write_text(json.dumps({'database_hash': '0' * 64}))
        return FakeProcess()
    monkeypatch.setattr('services.supervisor.worker.subprocess.Popen', popen)
    with pytest.raises(ValueError, match='INGEST_HASH_MISMATCH'):
        isolated_ingest(source, destination, {}, DockerLauncher(), tmp_path / 'jobs')
    assert not destination.exists()


def test_isolated_ingest_keeps_manifest_sidecar_for_replay(tmp_path):
    from packages.metricguard_core import inspect_snapshot
    fixture = Path(__file__).resolve().parents[1] / 'MetricGuard_Planning_Pack/examples/orders_golden.csv'

    class InProcessRunner:
        def start(self, input_dir, output_dir, command=None):
            assert command == 'ingest'
            ingest_job(input_dir, output_dir)
            return FakeProcess()

    destination = tmp_path / 'golden.duckdb'
    manifest = isolated_ingest(fixture, destination, {}, InProcessRunner(), tmp_path / 'jobs')
    assert inspect_snapshot(destination)['manifest_hash'] == manifest['manifest_hash']


def test_isolated_ingest_rejects_tampered_manifest_metadata(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / 'MetricGuard_Planning_Pack/examples/orders_golden.csv'

    class TamperedRunner:
        def start(self, input_dir, output_dir, command=None):
            ingest_job(input_dir, output_dir)
            path = Path(output_dir) / 'manifest.json'
            manifest = json.loads(path.read_text())
            manifest['scope'] = 'unexpected_scope'
            path.write_text(json.dumps(manifest))
            return FakeProcess()

    destination = tmp_path / 'not-accepted.duckdb'
    with pytest.raises(ValueError, match='INGEST_MANIFEST_MISMATCH'):
        isolated_ingest(fixture, destination, {'scope': 'declared_scope'}, TamperedRunner(), tmp_path / 'jobs')
    assert not destination.exists()
