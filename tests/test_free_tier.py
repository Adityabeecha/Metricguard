"""Hosted admission/retention behavior; all limits are opt-in outside the image."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, update, select
from services import durable
from services.api.store import Store, DomainError, definitions, versions, snapshots, runs, records, projects, now
from services.maintenance import expire_evidence, maintain


@pytest.fixture
def case(tmp_path, monkeypatch):
    for name in ('MAX_ROWS', 'MAX_UPLOAD_MB', 'MAX_ACTIVE_RUNS_PER_WORKSPACE', 'MAX_RUNS_PER_DAY', 'CACHE_MAX_MB', 'UNSAVED_EVIDENCE_DAYS'):
        monkeypatch.delenv('METRICGUARD_' + name, raising=False)
    root = tmp_path / 'data'
    store = Store(f'sqlite:///{root / "app.db"}')
    with store.engine.begin() as conn:
        conn.execute(insert(definitions).values(id='d', project_id=store.project_id, name='Amount', source_label='CSV', owner_label='Analyst', created_at=now()))
        conn.execute(insert(versions).values(id='v', definition_id='d', version_number=1, data={'id': 'v'}))
        conn.execute(insert(snapshots).values(id='s', project_id=store.project_id, manifest_hash='hash', data={'id': 's', 'row_count': 2}))
    storage = durable.Durable(root, durable.DirBackend(tmp_path / 'remote'))
    durable.configure(storage)
    request = {'baseline_version_id': 'v', 'target_version_id': 'v', 'snapshot_id': 's'}
    yield store, root, storage, request
    durable.configure(None)
    store.engine.dispose()


def test_limits_unset_allow_multiple_queued_runs(case):
    store, _, _, request = case
    assert store.queue(request, 'one', 'isolated')['state'] == 'queued'
    assert store.queue(request, 'two', 'isolated')['state'] == 'queued'


def test_active_limit_409_and_idempotent_retry(case, monkeypatch):
    store, _, _, request = case
    monkeypatch.setenv('METRICGUARD_MAX_ACTIVE_RUNS_PER_WORKSPACE', '1')
    first = store.queue(request, 'one', 'isolated')
    assert store.queue(request, 'one', 'isolated')['id'] == first['id']
    with pytest.raises(DomainError) as err:
        store.queue(request, 'two', 'isolated')
    assert err.value.status == 409
    assert err.value.message == 'One comparison at a time on the free beta. It will start when the current one finishes.'
    store.cancel(first['id'])
    assert store.queue(request, 'two', 'isolated')['state'] == 'queued'


def test_concurrent_admission_is_atomic(case, monkeypatch):
    store, _, _, request = case
    monkeypatch.setenv('METRICGUARD_MAX_ACTIVE_RUNS_PER_WORKSPACE', '1')
    def queue(key):
        try:
            return store.queue(request, key, 'isolated')['state']
        except DomainError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(queue, ['one', 'two']), key=str) == [409, 'queued']


def test_daily_limit_counts_last_24_hours_and_retries(case, monkeypatch):
    store, _, _, request = case
    monkeypatch.setenv('METRICGUARD_MAX_RUNS_PER_DAY', '1')
    first = store.queue(request, 'old', 'isolated')
    store.cancel(first['id'])
    assert store.queue(request, 'old', 'isolated')['id'] == first['id']
    with pytest.raises(DomainError, match='1 comparisons per workspace in 24 hours') as error:
        store.queue(request, 'new', 'isolated')
    assert error.value.status == 429
    with store.engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.id == first['id']).values(created_at=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()))
    assert store.queue(request, 'new', 'isolated')['state'] == 'queued'


def test_queue_and_prepared_snapshot_cap(case, monkeypatch):
    from packages.metricguard_core.free_tier import FreeTierLimit
    store, _, _, request = case
    monkeypatch.setenv('METRICGUARD_MAX_ROWS', '1')
    with pytest.raises(FreeTierLimit, match='1 rows per dataset'):
        store.queue(request, 'over', 'isolated')
    with pytest.raises(FreeTierLimit):
        store.record_snapshot('Prepared', {'row_count': 2}, 'prepared.duckdb')


def old_run(case, name):
    store, root, storage, request = case
    run = store.queue(request, name, 'isolated')
    with store.engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.id == run['id']).values(state='succeeded', attempt=1,
            completed_at=(datetime.now(timezone.utc)-timedelta(days=9)).isoformat(),
            result={'baseline_value': '3.00', 'target_value': '2.00', 'evidence_artifact': {'sha256': 'immutable', 'byte_count': 4}}))
    files = []
    for suffix in ('.jsonl', '.context.jsonl', '.members.jsonl'):
        path = root / 'evidence' / f"{run['id']}-1{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{}\n')
        storage.push(path, sidecar=False)
        files.append(path)
    return run['id'], files


def test_retention_keeps_saved_and_preserves_summary_and_identity(case, monkeypatch):
    store, root, storage, request = case
    unsaved, files = old_run(case, 'unsaved')
    saved, protected = old_run(case, 'saved')
    draft, draft_files = old_run(case, 'draft')
    store.record('investigation', {'run_id': saved})
    store.record('investigation_draft', {'comparison_run_id': draft})
    before = store._row(runs, unsaved)
    assert expire_evidence(store, root) == 0
    monkeypatch.setenv('METRICGUARD_UNSAVED_EVIDENCE_DAYS', '7')
    assert expire_evidence(store, root) == 1
    assert expire_evidence(store, root) == 0
    for path in files:
        assert not path.exists()
        assert not storage.ensure(path, sidecar=False)
    assert all(path.exists() for path in protected + draft_files)
    after = store._row(runs, unsaved)
    assert before['result'] == after['result']
    assert before['request_hash'] == after['request_hash']
    assert store.get_run(unsaved)['evidence_state'] == 'expired'
    assert store.get_run(saved).get('evidence_state') is None


def test_expired_detail_api_and_usage(case, monkeypatch):
    from services.api.app import create_app
    from services.api.usage import workspace_usage
    store, root, storage, _ = case
    ident, paths = old_run(case, 'old')
    monkeypatch.setenv('METRICGUARD_UNSAVED_EVIDENCE_DAYS', '7')
    expire_evidence(store, root)
    app = create_app(database_url=str(store.engine.url), data_dir=root, mode='isolated')
    durable.configure(storage)
    with TestClient(app) as client:
        detail = client.get(f'/api/v1/comparisons/{ident}')
        assert detail.status_code == 200, detail.text
        assert detail.json()['evidence_state'] == 'expired'
        for endpoint in ['evidence', 'evidence-summary', 'evidence-breakdown']:
            response = client.get(f'/api/v1/comparisons/{ident}/{endpoint}')
            assert response.status_code == 200, response.text
            assert response.json()['evidence_state'] == 'expired'
            assert response.json()['reason'] == 'Detailed records expired \u2014 rerun to see them'
        response = client.get('/api/v1/usage')
        assert response.status_code == 200, response.text
        assert response.json()['runs_last_24h'] == 1
        assert response.json()['storage_bytes'] == 0
    with pytest.raises(DomainError) as error:
        store.record('investigation', {'run_id': ident})
    assert error.value.status == 410


def test_content_length_refuses_before_body_read(case, monkeypatch):
    from services.api.app import create_app
    store, root, _, _ = case
    monkeypatch.setenv('METRICGUARD_MAX_UPLOAD_MB', '1')
    with TestClient(create_app(database_url=str(store.engine.url), data_dir=root, mode='isolated')) as client:
        response = client.post('/api/v1/flat/snapshots', content=b'x', headers={'content-length': str(2*1024*1024)})
        assert response.status_code == 413
        assert response.json()['error']['code'] == 'FREE_TIER_UPLOAD'
        assert 'Free beta limit: 1 MB' in response.json()['error']['message']


def test_maintenance_throttles(monkeypatch, case):
    from services import maintenance
    store, root, _, _ = case
    maintenance._clock.clear()
    calls = []
    monkeypatch.setenv('METRICGUARD_CACHE_MAX_MB', '600')
    monkeypatch.setenv('METRICGUARD_UNSAVED_EVIDENCE_DAYS', '7')
    monkeypatch.setattr(maintenance.durable, 'cleanup_cache', lambda: calls.append('cache'))
    monkeypatch.setattr(maintenance, 'expire_evidence', lambda *_: calls.append('retention'))
    monkeypatch.setattr(maintenance.time, 'monotonic', lambda: 100)
    maintain(store, root); maintain(store, root)
    assert calls == ['cache', 'retention']
    monkeypatch.setattr(maintenance.time, 'monotonic', lambda: 161)
    maintain(store, root)
    assert calls == ['cache', 'retention', 'cache']


def test_shared_files_counts_null_workspace(case):
    from services.api.account_deletion import _shared_files
    from sqlalchemy import Column, JSON, MetaData, String, Table, create_engine
    # Production snapshots currently disallow NULL; legacy metadata may contain
    # unowned rows. Exercise the actual query against that legacy shape.
    import services.api.store as module
    store, root, _, _ = case
    db = create_engine('sqlite://')
    meta = MetaData()
    table = Table('snapshots', meta, Column('id', String), Column('project_id', String), Column('data', JSON))
    imports = Table('snapshot_imports', meta, Column('id', String), Column('project_id', String), Column('source_path', String))
    meta.create_all(db)
    path = root / 'snapshots' / 'shared.duckdb'
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, 'snapshots', table); patch.setattr(module, 'snapshot_imports', imports)
        with db.begin() as conn:
            conn.execute(insert(table).values(id='legacy', project_id=None, data={'artifact_path': str(path)}))
            assert path in _shared_files(conn, {store.project_id}, {path}, root)
    db.dispose()


def test_breakdown_reason_matches_why_details_are_missing(case):
    """Two-load runs never have record details; only a real drop asks the analyst to rerun."""
    from services.api.app import create_app
    store, root, storage, _ = case
    legacy, _ = old_run(case, 'legacy')
    two_loads, _ = old_run(case, 'two-loads')
    dropped, _ = old_run(case, 'dropped')
    with store.engine.begin() as conn:
        request = store._row(runs, two_loads)['request']
        conn.execute(update(runs).where(runs.c.id == two_loads).values(
            request={**request, 'context': {**(request.get('context') or {}), 'comparison': 'snapshot_change'}}))
    store.record('evidence_context', {'run_id': dropped, 'attempt': 1, 'status': 'unavailable',
                                      'reason': 'Record details could not be saved.'})
    app = create_app(database_url=str(store.engine.url), data_dir=root, mode='isolated')
    durable.configure(storage)
    with TestClient(app) as client:
        reasons = {name: client.get(f'/api/v1/comparisons/{ident}/evidence-breakdown').json()['reason']
                   for name, ident in (('legacy', legacy), ('two_loads', two_loads), ('dropped', dropped))}
    assert reasons['two_loads'] == 'This comparison was run before two-load breakdowns existed. Replay it to enable them.'
    assert reasons['dropped'].startswith("Breakdown isn't available for this run (the record details could not be saved)")
    assert reasons['legacy'] == 'This comparison was run before column breakdowns existed. Replay it to enable them.'
