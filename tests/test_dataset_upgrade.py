from copy import deepcopy

import duckdb
import pytest
from fastapi.testclient import TestClient

from packages.metricguard_core.common import file_hash
from packages.metricguard_core.flat import ingest_flat, validate_contract
from packages.metricguard_core.snapshot import manifest_path
from services.api.app import create_app
from packages.metricguard_core.flat_upgrade import upgrade_contract, upgrade_frozen
from services.api.store import Store
from services.supervisor.runner import ingest_job, run_job
from services.supervisor.worker import Supervisor


def frozen(tmp_path):
    contract = validate_contract(dict(version=4, mode='flat_snapshot', name='Orders', row_key='id',
        measure='amount', precision=18, scale=2, categories=['active', 'day', 'stamp', 'label'],
        dimension_types={'active': 'boolean', 'day': 'date', 'stamp': 'timestamp', 'label': 'string'},
        measure_meaning='Order amount', unit_label='INR', values_comparable=True,
        scope='entire_supplied_snapshot', null_policy='blank_nullable'))
    source = tmp_path / 'orders.csv'
    source.write_text('id,amount,active,day,stamp,label\nA,10.25,true,2026-10-01,2026-10-01T12:30:00,ready\nB,,false,,,\nC,2.00,false,,,held\n', encoding='utf-8')
    database = tmp_path / 'snapshots' / 'old.duckdb'
    database.parent.mkdir()
    manifest = ingest_flat(source, database, contract)
    return contract, database, manifest


def metadata(contract, manifest, ident='old'):
    return {'flat_contract': upgrade_contract(contract), 'frozen_upgrade': {
        'snapshot_id': ident, 'manifest_hash': manifest['manifest_hash'],
        'database_hash': manifest['database_hash'], 'contract_hash': manifest['contract_hash'],
        'source_hash': manifest['source_hash']}}


def test_upgrade_preserves_every_physical_value_and_old_artifact(tmp_path):
    contract, source, manifest = frozen(tmp_path)
    before = source.read_bytes(), manifest_path(source).read_bytes()
    destination = tmp_path / 'new.duckdb'
    result = upgrade_frozen(source, destination, metadata(contract, manifest))
    assert result['contract_version'] == 5
    assert result['contract']['dimension_types'] == contract['dimension_types']
    assert result['null_policy'] == contract['null_policy']
    assert result['source_hash'] == manifest['database_hash']
    assert result['frozen_upgrade']['manifest_hash'] == manifest['manifest_hash']
    assert result['manifest_hash'] != manifest['manifest_hash']
    assert before == (source.read_bytes(), manifest_path(source).read_bytes())
    with duckdb.connect(str(destination), read_only=True) as con:
        assert con.execute('SELECT amount, active, day, stamp, label FROM snapshot ORDER BY id').fetchall()[1] == (None, False, None, None, None)
        assert dict((r[0], r[1]) for r in con.execute('DESCRIBE snapshot').fetchall()) == manifest['schema']


@pytest.mark.parametrize('version', [3, 5, 6, 7, 8])
def test_upgrade_only_accepts_v4(version):
    with pytest.raises(Exception, match='version 4'):
        upgrade_contract({'version': version})


def test_upgrade_refuses_changed_source_or_new_semantics(tmp_path):
    contract, source, manifest = frozen(tmp_path)
    declared = metadata(contract, manifest)
    changed = deepcopy(declared)
    changed['flat_contract'].pop('contract_hash')
    changed['flat_contract'] = validate_contract({**changed['flat_contract'], 'unit_label': 'USD'})
    with pytest.raises(Exception):
        upgrade_frozen(source, tmp_path / 'wrong.duckdb', changed)
    assert not (tmp_path / 'wrong.duckdb').exists()
    source.write_bytes(source.read_bytes() + b'tamper')
    with pytest.raises(Exception):
        upgrade_frozen(source, tmp_path / 'tampered.duckdb', declared)


class Launcher:
    def start(self, input_dir, output_dir, command=None):
        if command == 'ingest':
            ingest_job(input_dir, output_dir)
        else:
            assert command is None
            run_job(input_dir, output_dir)
        return self

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


@pytest.mark.parametrize('evict', [False, True])
def test_api_queues_reuses_and_publishes_without_mutating_old_snapshot(tmp_path, monkeypatch, evict):
    contract, source, manifest = frozen(tmp_path)
    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    old_import = store.queue_import(tmp_path / 'deleted.csv', manifest['source_hash'], 'October', {'flat_contract': contract})
    claimed = store.claim_import('setup')
    store.finish_import(old_import['id'], 'setup', claimed['attempt'], manifest=manifest, artifact_path=source)
    old_id = store.get_import(old_import['id'])['snapshot_id']
    old_snapshot = store.snapshot(old_id)
    worker = Supervisor(store, Launcher(), tmp_path / 'jobs', 'upgrade-worker')
    calls = []
    from services import durable
    original_ensure = durable.ensure
    monkeypatch.setattr(durable, 'ensure', lambda path, **kw: (calls.append(str(path)), original_ensure(path, **kw))[1])
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        registered = store.record_flat_contract(contract)
        versions = [client.post('/api/v1/flat/definitions', json={'name': name, 'raw_sql': sql, 'contract_id': registered['id']}).json()['versions'][0]['id']
            for name, sql in [('All', 'SELECT SUM(amount) FROM snapshot'), ('Active', 'SELECT SUM(amount) FROM snapshot WHERE active = TRUE')]]
        context = {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
                   'unit_label': 'INR', 'values_comparable': True}
        run = client.post('/api/v1/flat/comparisons', json={'baseline_version_id': versions[0], 'target_version_id': versions[1],
            'snapshot_id': old_id, 'context': context}, headers={'Idempotency-Key': 'old-case'}).json()
        assert worker.run_once()
        original_result = store.get_run(run['id'])
        assert original_result['state'] == 'succeeded', original_result.get('error')
        saved = client.post('/api/v1/investigations', json={'title': 'Original inputs', 'run_id': run['id'],
            'decision': 'Keep evidence', 'actor_label': 'Tester'}).json()
        original_case = store.investigation(saved['id'])
        reader, key = store.create_user('Reader', 'upgrade-reader@example.com')
        store.add_project_member(store.project_id, reader['id'], 'reader')
        assert client.post(f'/api/v1/snapshots/{old_id}/enable-averages-ratios', headers={'X-API-Key': key}).status_code == 403
        assert client.post(f'/api/v1/snapshots/{old_id}/enable-averages-ratios', headers={'X-Project-Id': 'another-workspace'}).status_code == 403
        if evict:
            durable.configure(durable.Durable(tmp_path, durable.DirBackend(tmp_path / 'bucket')))
            durable.push(source)
            source.unlink()
            manifest_path(source).unlink()
        queued = client.post(f'/api/v1/snapshots/{old_id}/enable-averages-ratios')
        assert queued.status_code == 202, queued.text
        second = client.post(f'/api/v1/snapshots/{old_id}/enable-averages-ratios')
        assert second.json()['id'] == queued.json()['id']
        assert client.delete(f'/api/v1/snapshots/{old_id}').status_code == 409
        assert worker.run_once()
        completed = store.get_import(queued.json()['id'])
        assert completed['state'] == 'validated', completed['error']
        upgraded = store.snapshot(completed['snapshot_id'])
        assert upgraded['id'] != old_id
        assert upgraded['manifest']['contract_version'] == 5
        assert client.post(f"/api/v1/snapshots/{upgraded['id']}/enable-averages-ratios").status_code == 422
        assert upgraded['row_count'] == old_snapshot['row_count']
        assert store.snapshot(old_id) == old_snapshot
        assert store.investigation(saved['id']) == original_case
        assert source.exists() and file_hash(source) == manifest['database_hash']
        assert str(source) in calls
        third = client.post(f'/api/v1/snapshots/{old_id}/enable-averages-ratios')
        assert third.status_code == 200
        assert third.json()['id'] == upgraded['id']
        upgraded_contract = store.record_flat_contract(upgraded['manifest']['contract'])
        average = client.post('/api/v1/flat/definitions', json={'name': 'Average', 'raw_sql': 'SELECT AVG(amount) FROM snapshot', 'contract_id': upgraded_contract['id']})
        assert average.status_code == 201, average.text
        replay = client.post(f"/api/v1/investigations/{saved['id']}/replays", headers={'Idempotency-Key': 'after-upgrade'})
        assert replay.status_code == 202, replay.text
        assert worker.run_once()
        replayed = store.get_run(replay.json()['id'])
        assert replayed['state'] == 'succeeded', replayed.get('error')
        assert replayed['result']['delta_target_minus_baseline'] == '-2.00'
        assert replayed['snapshot_id'] == old_id
        assert client.delete(f"/api/v1/snapshots/{upgraded['id']}").status_code == 200
        assert source.exists() and file_hash(source) == manifest['database_hash']
    durable.configure(None)


@pytest.mark.parametrize('problem,code', [('missing', 'UPGRADE_UNAVAILABLE'), ('tampered', 'SOURCE_HASH_MISMATCH')])
def test_failed_upgrade_never_deletes_borrowed_source(tmp_path, problem, code):
    from services import durable
    durable.configure(durable.Durable(tmp_path))
    contract, source, manifest = frozen(tmp_path)
    store = Store(f"sqlite:///{tmp_path / 'app.db'}")
    queued = store.queue_import(source, manifest['database_hash'], 'Upgrade', metadata(contract, manifest))
    if problem == 'missing':
        source.unlink()
    else:
        source.write_bytes(source.read_bytes() + b'tamper')
    worker = Supervisor(store, Launcher(), tmp_path / 'jobs', 'failed-upgrade')
    assert worker.run_once()
    failed = store.get_import(queued['id'])
    assert failed['state'] == 'failed'
    assert failed['error']['code'] == code
    assert failed['snapshot_id'] is None
    assert manifest_path(source).exists()
    assert source.exists() == (problem == 'tampered')
    durable.configure(None)
