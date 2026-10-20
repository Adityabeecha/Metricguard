"""Bounded context generation and visible optional-context failures."""
import json
import logging
from pathlib import Path

import duckdb
import pytest

from services.supervisor.row_context import validate_context, write_entity_context, write_flat_context
from services.supervisor.worker import Supervisor


@pytest.mark.parametrize('missing', [True, False])
def test_optional_context_drop_has_safe_reason_and_warning(tmp_path, caplog, missing):
    output = tmp_path / 'output'
    output.mkdir()
    evidence = tmp_path / 'evidence.jsonl'
    evidence.write_text('{"order_id":"R1"}\n', encoding='utf-8')
    if not missing:
        (output / 'context.jsonl').write_text('{"k":"wrong","v":{}}\n', encoding='utf-8')
    worker = Supervisor(object(), None, tmp_path / 'jobs', 'worker', data_dir=tmp_path)
    with caplog.at_level(logging.WARNING):
        info = worker._publish_context(output, evidence, {'categories': ['region']}, tmp_path, 'run', 1)
    assert info['status'] == 'unavailable'
    assert info['code'] == ('CONTEXT_MISSING' if missing else 'CONTEXT_INVALID')
    assert info['reason'] and 'wrong' not in info['reason']
    assert any('run' in record.message and info['code'] in record.message for record in caplog.records)
    assert not (tmp_path / 'run-1.context.jsonl').exists()


def test_context_generation_spools_lookup_and_preserves_order(tmp_path, monkeypatch):
    database = tmp_path / 'snapshot.duckdb'
    with duckdb.connect(str(database)) as connection:
        connection.execute('CREATE TABLE snapshot AS SELECT i::VARCHAR AS id, (i % 3)::VARCHAR AS region FROM range(3000) t(i)')
    real_connect = duckdb.connect

    class Cursor:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, *args, **kwargs):
            self.connection.execute(*args, **kwargs)
            return self

        def fetchall(self):
            raise AssertionError('Context must never collect the snapshot in memory')

        def fetchmany(self, size):
            assert size <= 1000
            return self.connection.fetchmany(size)

        def close(self):
            self.connection.close()

    monkeypatch.setattr(duckdb, 'connect', lambda *args, **kwargs: Cursor(real_connect(*args, **kwargs)))
    contract = {'row_key': 'id', 'categories': ['region']}
    evidence = [{'order_id': str(i)} for i in (2999, 5, 1000, 0)]
    context = tmp_path / 'context.jsonl'
    write_flat_context(database, contract, evidence, context)
    rows = [json.loads(line) for line in context.read_text().splitlines()]
    assert [row['k'] for row in rows] == ['2999', '5', '1000', '0']
    assert [row['v']['region'] for row in rows] == ['2', '2', '1', '0']
    entity_context = tmp_path / 'entity.jsonl'
    write_entity_context(database, contract, 'id', evidence, entity_context)
    assert entity_context.read_bytes() == context.read_bytes()


def test_entity_context_rejects_multivalued_column_across_whole_snapshot(tmp_path):
    database = tmp_path / 'snapshot.duckdb'
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE snapshot AS SELECT * FROM (VALUES ('A', 'north'), ('B', 'east'), ('A', 'south')) t(entity, region)")
    context = tmp_path / 'context.jsonl'
    info = write_entity_context(database, {'categories': ['region']}, 'entity', [{'order_id': 'B'}], context)
    assert info['excluded_columns'] == {'region': 'multi_valued_per_entity'}
    assert json.loads(context.read_text()) == {'k': 'B', 'v': {}}


def test_context_validator_limits_each_read(tmp_path, monkeypatch):
    import io
    from services.supervisor import row_context
    evidence, context = tmp_path / 'evidence.jsonl', tmp_path / 'context.jsonl'
    evidence.write_text('{"order_id":"A"}\n')
    context.write_text('{"k":"A","v":{"region":"north"}}\n')
    original_open = Path.open

    class BoundedReader(io.BytesIO):
        def readline(self, size=-1):
            assert 0 < size <= 65537, 'An untrusted line must never be read without a byte bound'
            return super().readline(size)

    # Capture bytes before replacing Path.open (read_bytes itself uses it).
    content = {path: path.read_bytes() for path in (evidence, context)}
    monkeypatch.setattr(Path, 'open', lambda path, mode='r', *args, **kwargs:
                        BoundedReader(content[path]) if path in content and mode == 'rb'
                        else original_open(path, mode, *args, **kwargs))
    assert row_context.validate_context(context, evidence, ['region'])['row_count'] == 1


def test_invalid_long_context_line_is_rejected(tmp_path):
    evidence, context = tmp_path / 'evidence.jsonl', tmp_path / 'context.jsonl'
    evidence.write_text('{"order_id":"A"}\n')
    context.write_bytes(b'x' * (9 * 1024))
    with pytest.raises(ValueError, match='CONTEXT_INVALID'):
        validate_context(context, evidence, ['region'])


@pytest.mark.parametrize('reject', [False, True])
def test_succeeded_run_records_unavailable_context_without_altering_evidence(tmp_path, caplog, reject):
    from packages.metricguard_core.flat import ingest_flat, parse_flat, validate_contract
    from services.supervisor.runner import run_job

    contract = validate_contract({'version': 3, 'mode': 'flat_snapshot', 'name': 'Context',
        'row_key': 'id', 'measure': 'amount', 'precision': 18, 'scale': 2, 'categories': ['region'],
        'measure_meaning': 'Revenue', 'unit_label': 'INR', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
    source = tmp_path / 'source.csv'
    source.write_text('id,amount,region\nA,2.00,north\nB,3.00,south\n')
    database = tmp_path / 'snapshot.duckdb'
    manifest = ingest_flat(source, database, contract)
    versions = {'base': {'ir': parse_flat('SELECT SUM(amount) FROM snapshot', contract)},
                'target': {'ir': parse_flat("SELECT SUM(amount) FROM snapshot WHERE region='north'", contract)}}
    run = {'id': 'run', 'attempt': 1, 'snapshot_id': 'snapshot', 'baseline_version_id': 'base',
           'target_version_id': 'target', 'execution_policy': 'flat_snapshot_v1', 'project_id': 'project',
           'context': {'mode': 'flat_snapshot', 'scope': contract['scope'], 'contract_hash': contract['contract_hash'],
                       'unit_label': 'INR', 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}}

    class Store:
        def __init__(self):
            self.records = []
            self.finished = None

        def snapshot(self, ident):
            return {'artifact_path': str(database), 'manifest': manifest}

        def version(self, ident):
            return versions[ident]

        def finish(self, *args, result=None):
            self.finished = result
            return True

        def record(self, kind, info, **kwargs):
            self.records.append((kind, info))

    class Launcher:
        def start(self, input_dir, output_dir):
            run_job(input_dir, output_dir)
            path = output_dir / 'context.jsonl'
            if reject:
                path.write_text('{"k":"wrong","v":{}}\n')
            else:
                path.unlink()
            return self

        def poll(self):
            return 0

        def wait(self, **kwargs):
            return 0

    store = Store()
    worker = Supervisor(store, Launcher(), tmp_path / 'jobs', 'worker', data_dir=tmp_path)
    with caplog.at_level(logging.WARNING):
        worker._execute(run)
    assert store.finished['delta_target_minus_baseline'] == '-3.00'
    assert store.finished['residual'] == '0'
    kind, context = store.records[0]
    assert kind == 'evidence_context' and context['status'] == 'unavailable'
    assert context['code'] == ('CONTEXT_INVALID' if reject else 'CONTEXT_MISSING')
    assert (tmp_path / 'evidence/run-1.jsonl').is_file()
    assert any(context['code'] in record.message for record in caplog.records)


def test_runner_launchers_pass_only_validated_admission_settings(monkeypatch, tmp_path):
    from services.supervisor.worker import DockerLauncher, LocalLauncher
    monkeypatch.setenv('METRICGUARD_MAX_ROWS', '123')
    monkeypatch.setenv('METRICGUARD_MAX_UPLOAD_MB', '7')
    env = LocalLauncher().environment(tmp_path, tmp_path, tmp_path)
    assert env['METRICGUARD_MAX_ROWS'] == '123'
    assert env['METRICGUARD_MAX_UPLOAD_MB'] == '7'
    args, _ = DockerLauncher().arguments(tmp_path, tmp_path, ['image'])
    assert 'METRICGUARD_MAX_ROWS=123' in args and 'METRICGUARD_MAX_UPLOAD_MB=7' in args


def test_ingest_runner_limit_message_is_preserved(tmp_path):
    from services.supervisor.worker import IngestValidationError, isolated_ingest
    source = tmp_path / 'source.csv'
    source.write_text('id\nA\nB\n')
    message = 'Free beta limit: 1 rows per dataset (this file has 2).'

    class Launcher:
        def start(self, input_dir, output_dir, command=None):
            (output_dir / 'error.json').write_text(json.dumps({'code': 'FREE_TIER_ROWS', 'message': message}))
            return self

        def wait(self, **kwargs):
            return 2

    with pytest.raises(IngestValidationError) as error:
        isolated_ingest(source, tmp_path / 'snapshot.duckdb', {}, Launcher(), tmp_path / 'jobs')
    assert error.value.code == 'FREE_TIER_ROWS' and error.value.message == message
