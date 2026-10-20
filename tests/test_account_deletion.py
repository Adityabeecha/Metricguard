"""Deletion fixtures exercise real FK enforcement and a local durable mirror."""
import pytest
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from sqlalchemy import event, insert, select, update

from services import durable
from services.api.account_deletion import AccountDeletionError, delete_account
from services.api.store import (Store, auth_sessions, decisions, definitions, metadata, oauth_identities,
                               now, project_members, projects, records, review_requests,
                               runs, snapshot_imports, snapshots, users, versions)


@pytest.fixture
def account(tmp_path):
    root = tmp_path / 'data'
    store = Store(f'sqlite:///{root / "metricguard.db"}')
    storage = durable.Durable(root, durable.DirBackend(tmp_path / 'remote'))
    durable.configure(storage)
    user, _ = store.create_user('Owner', 'owner@example.test')
    project = store.create_project('Personal', user['id'])
    yield store, user['id'], project['id'], root, storage
    durable.configure(None)
    store.engine.dispose()


def artifact(root, storage, name, content=b'private bytes'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    durable.push(path, sidecar=False)
    return path


def seed_project(store, pid, artifact_path, prefix='p', state='succeeded'):
    did, vid, sid, rid = (f'{prefix}-{suffix}' for suffix in ('definition', 'version', 'snapshot', 'run'))
    with store.engine.begin() as conn:
        conn.execute(insert(definitions).values(id=did, project_id=pid, name='Revenue', source_label='SQL', owner_label='Owner', created_at=now()))
        conn.execute(insert(versions).values(id=vid, definition_id=did, version_number=1, data={'id': vid}))
        conn.execute(insert(snapshots).values(id=sid, project_id=pid, manifest_hash=prefix, data={'artifact_path': str(artifact_path)}))
        conn.execute(insert(runs).values(id=rid, project_id=pid, baseline_version_id=vid, target_version_id=vid,
            snapshot_id=sid, idempotency_key=f'{prefix}-idem', request_hash='hash', state=state, execution_mode='isolated',
            attempt=2, cancel_requested=False, created_at=now(), request={}, result={}))
        conn.execute(insert(records).values(id=f'{prefix}-case', project_id=pid, kind='investigation', created_at=now(), data={'private': True}))
    return rid


def test_sole_owner_erases_metadata_sessions_and_local_and_durable_artifacts(account):
    store, uid, pid, root, storage = account
    snap = artifact(root, storage, 'snapshots/private.duckdb')
    side = artifact(root, storage, 'snapshots/private.duckdb.manifest.json')
    rid = seed_project(store, pid, snap)
    evidence = [artifact(root, storage, f'evidence/{rid}-{attempt}{suffix}')
                for attempt in (1, 2) for suffix in ('.jsonl', '.members.jsonl', '.context.jsonl')]
    # Remote-only objects must also be erased; globbing local files is insufficient.
    evidence[0].unlink()
    backup = artifact(root, storage, f'backups/backup-{pid}-12abcdef.tar.gz')
    unrelated = artifact(root, storage, 'snapshots/unrelated.duckdb')
    store.create_session(uid, 60)
    with store.engine.begin() as conn:
        conn.execute(insert(oauth_identities).values(provider='google', subject='subject', user_id=uid))
    result = delete_account(store, uid, root)
    assert result['deleted_project_ids'] == [pid]
    assert result['preserved_project_ids'] == []
    for path in [snap, side, backup, *evidence]:
        assert not path.exists()
        assert not (storage.backend.root / storage.key(path)).exists()
        if path.name.endswith('.jsonl'):
            assert not (storage.backend.root / (storage.key(path) + '.gz')).exists()
    assert unrelated.exists()
    assert (root / 'metricguard.db').exists()
    with store.engine.connect() as conn:
        assert not conn.execute(select(users).where(users.c.id == uid)).first()
        assert not conn.execute(select(auth_sessions).where(auth_sessions.c.user_id == uid)).first()
        assert not conn.execute(select(oauth_identities).where(oauth_identities.c.user_id == uid)).first()
        assert not conn.execute(select(versions).where(versions.c.id == 'p-version')).first()
        for table in metadata.sorted_tables:
            if 'project_id' in table.c:
                assert not conn.execute(select(table).where(table.c.project_id == pid)).first(), table.name


def test_shared_owner_leaves_without_role_transfer_and_preserves_history(account):
    store, uid, pid, root, storage = account
    other, _ = store.create_user('Reader', 'reader@example.test')
    store.add_project_member(pid, other['id'], 'reader')
    snap = artifact(root, storage, 'snapshots/shared.duckdb')
    seed_project(store, pid, snap)
    with store.engine.begin() as conn:
        conn.execute(insert(review_requests).values(id='review', project_id=pid, requester_id=uid,
            status='pending', data={'preserved': True}, created_at=now(), updated_at=now()))
        conn.execute(insert(decisions).values(id='decision', project_id=pid, decider_id=uid, data={'preserved': True}, created_at=now()))
    token = store.create_session(uid, 60)
    result = delete_account(store, uid, root)
    assert result['deleted_project_ids'] == []
    assert result['preserved_project_ids'] == [pid]
    assert snap.exists()
    assert store.get_project_member(pid, other['id'])['role'] == 'reader'
    assert store.get_project_member(pid, uid) is None
    assert store.get_user_by_session(token) is None
    with store.engine.connect() as conn:
        row = conn.execute(select(decisions).where(decisions.c.id == 'decision')).mappings().one()
        actor = conn.execute(select(users).where(users.c.id == row['decider_id'])).mappings().one()
        assert actor['id'] != uid and actor['is_active'] is False
        assert row['data'] == {'preserved': True}
        assert conn.execute(select(review_requests.c.requester_id)).scalar_one() == actor['id']
        assert not conn.execute(select(users).where(users.c.id == uid)).first()


def test_shared_snapshot_bytes_remain_referenced_by_another_project(account):
    store, uid, pid, root, storage = account
    other, _ = store.create_user('Other', 'other@example.test')
    other_pid = store.create_project('Other workspace', other['id'])['id']
    shared = artifact(root, storage, 'snapshots/hash-shared.duckdb')
    lineage = artifact(root, storage, 'snapshots/hash-shared.lineage.jsonl')
    seed_project(store, pid, shared)
    seed_project(store, other_pid, shared, prefix='other')
    delete_account(store, uid, root)
    assert shared.exists()
    assert lineage.exists()
    assert (storage.backend.root / (storage.key(lineage) + '.gz')).exists()
    assert (storage.backend.root / storage.key(shared)).exists()
    assert store.get_project(other_pid)


@pytest.mark.parametrize('path_form', ['absolute', 'relative', 'parent', 'forward_slashes'])
def test_other_owners_snapshot_preserves_same_artifact_path_alias(account, monkeypatch, path_form):
    store, uid, pid, root, storage = account
    other, _ = store.create_user('Other', 'other@example.test')
    other_pid = store.create_project('Other workspace', other['id'])['id']
    shared = artifact(root, storage, 'snapshots/shared_%_bytes.duckdb')
    sidecar = artifact(root, storage, 'snapshots/shared_%_bytes.duckdb.manifest.json')
    lineage = artifact(root, storage, 'snapshots/shared_%_bytes.lineage.jsonl')
    seed_project(store, pid, shared)
    monkeypatch.chdir(root.parent)
    if path_form == 'relative':
        stored_path = str(shared.relative_to(root.parent))
    elif path_form == 'parent':
        (root / 'snapshots' / 'alias').mkdir()
        stored_path = str(root / 'snapshots' / 'alias' / '..' / shared.name)
    elif path_form == 'forward_slashes':
        stored_path = shared.as_posix()
    else:
        stored_path = str(shared)
    store.record_snapshot('Other snapshot', {'manifest_hash': 'other-shared'}, stored_path, project_id=other_pid)

    delete_account(store, uid, root)

    for path in (shared, sidecar, lineage):
        assert path.is_file()
        assert (storage.backend.root / (storage.key(path) + ('.gz' if path.name.endswith('.jsonl') else ''))).is_file()
    assert store.get_project(other_pid)


def test_deletion_does_not_select_unrelated_workspace_rows(account):
    store, uid, pid, root, storage = account
    snap = artifact(root, storage, 'snapshots/private.duckdb')
    seed_project(store, pid, snap)
    other, _ = store.create_user('Other', 'other@example.test')
    other_pid = store.create_project('Other workspace', other['id'])['id']
    seed_project(store, other_pid, root / 'snapshots' / 'other.duckdb', prefix='other')
    with store.engine.begin() as conn:
        conn.execute(insert(decisions).values(id='other-decision', project_id=other_pid,
            decider_id=other['id'], data={'private': True}, created_at=now()))
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith('select'):
            statements.append(' '.join(statement.lower().split()))

    event.listen(store.engine, 'before_cursor_execute', capture)
    try:
        delete_account(store, uid, root)
    finally:
        event.remove(store.engine, 'before_cursor_execute', capture)

    unrelated_selects = [sql for sql in statements
                         if 'from definition_versions' in sql or 'from decisions' in sql]
    assert unrelated_selects, 'Listener must observe the owned-row / audit lookups'
    # Owned rows and bounded actor lookups remain necessary; inverse workspace
    # queries over these non-file tables are the database-wide scaling bug.
    assert all('not in' not in sql for sql in unrelated_selects), unrelated_selects
    assert not any('from comparison_runs' in sql and 'not in' in sql for sql in statements)


def test_shared_import_source_is_preserved_but_owned_attempt_artifacts_are_erased(account, monkeypatch):
    store, uid, pid, root, storage = account
    other, _ = store.create_user('Other', 'other@example.test')
    other_pid = store.create_project('Other workspace', other['id'])['id']
    source = artifact(root, storage, 'uploads/shared_%_source.csv')
    attempt = artifact(root, storage, 'snapshots/owned-import-1.duckdb')
    sidecar = artifact(root, storage, 'snapshots/owned-import-1.duckdb.manifest.json')
    unrelated = artifact(root, storage, 'uploads/unrelated.csv')
    monkeypatch.chdir(root.parent)
    with store.engine.begin() as conn:
        for ident, project_id, stored_path in (
            ('owned-import', pid, str(source)),
            ('other-import', other_pid, str(source.relative_to(root.parent))),
            ('unrelated-import', other_pid, str(unrelated)),
        ):
            conn.execute(insert(snapshot_imports).values(id=ident, project_id=project_id,
                state='validated', attempt=1, created_at=now(), source_path=stored_path, data={}))
    delete_account(store, uid, root)
    assert source.is_file()
    assert (storage.backend.root / storage.key(source)).is_file()
    assert unrelated.is_file()
    for path in (attempt, sidecar):
        assert not path.exists()
        assert not (storage.backend.root / storage.key(path)).exists()


def test_prepared_snapshot_lineage_is_deleted_even_when_only_durable_copy_exists(account):
    store, uid, pid, root, storage = account
    snap = artifact(root, storage, 'snapshots/prepared.duckdb')
    lineage = artifact(root, storage, 'snapshots/prepared.lineage.jsonl')
    lineage.unlink()
    seed_project(store, pid, snap)
    delete_account(store, uid, root)
    assert not (storage.backend.root / (storage.key(lineage) + '.gz')).exists()


def test_deletion_waits_for_backup_publication_and_then_erases_archive(account, monkeypatch):
    import services.api.backup as backup_module

    store, uid, pid, root, storage = account
    snap = artifact(root, storage, 'snapshots/private.duckdb')
    seed_project(store, pid, snap)
    archive = root / 'backups' / f'backup-{pid}-abcdef12.tar.gz'
    publishing, release, deleting = Event(), Event(), Event()
    original = backup_module.tarfile.open

    def pause_publication(*args, **kwargs):
        if args[0] == archive and (args[1] if len(args) > 1 else kwargs.get('mode')) == 'w:gz':
            publishing.set()
            assert release.wait(5), 'Backup publication was not released'
        return original(*args, **kwargs)

    def erase():
        deleting.set()
        return delete_account(store, uid, root)

    monkeypatch.setattr(backup_module.tarfile, 'open', pause_publication)
    with ThreadPoolExecutor(max_workers=2) as pool:
        backup = pool.submit(backup_module.backup_project, store, pid, root, archive)
        try:
            if not publishing.wait(5):
                backup.result(timeout=1)  # Surface errors before the publication barrier.
                pytest.fail('Backup did not reach publication')
            deletion = pool.submit(erase)
            assert deleting.wait(5)
            with pytest.raises(TimeoutError):
                deletion.result(timeout=0.15)
        finally:
            release.set()
        assert backup.result(timeout=5)['status'] == 'success'
        assert deletion.result(timeout=5)['deleted_project_ids'] == [pid]
    assert not archive.exists()


def test_durable_failure_preserves_retry_metadata_even_after_local_removal(account, monkeypatch):
    store, uid, pid, root, storage = account
    snap = artifact(root, storage, 'snapshots/private.duckdb')
    seed_project(store, pid, snap)
    token = store.create_session(uid, 60)
    original = storage.backend.delete

    def fail(key):
        raise durable.StorageError('Simulated remote outage')

    monkeypatch.setattr(storage.backend, 'delete', fail)
    with pytest.raises(AccountDeletionError) as error:
        delete_account(store, uid, root)
    assert error.value.status == 503
    assert store.get_project(pid)
    assert store.get_user_by_session(token)['id'] == uid
    assert store.snapshot('p-snapshot')['artifact_path'] == str(snap)
    monkeypatch.setattr(storage.backend, 'delete', original)
    delete_account(store, uid, root)
    assert not (storage.backend.root / storage.key(snap)).exists()


@pytest.mark.parametrize('kind,state', [('run', 'queued'), ('run', 'running'), ('import', 'queued'), ('import', 'running'), ('extraction', 'running')])
def test_active_jobs_reject_before_any_file_deletion(account, kind, state):
    store, uid, pid, root, storage = account
    snap = artifact(root, storage, 'snapshots/private.duckdb')
    seed_project(store, pid, snap, state=state if kind == 'run' else 'succeeded')
    if kind == 'import':
        source = artifact(root, storage, 'uploads/input.csv')
        with store.engine.begin() as conn:
            conn.execute(insert(snapshot_imports).values(id='import', project_id=pid, state=state,
                attempt=1, created_at=now(), source_path=str(source), data={}))
    elif kind == 'extraction':
        store.record_extraction(pid, 'extraction', {'cutoff_value': '2026-10-20', 'cutoff_column': 'day',
            'schema_name': 'public', 'table_name': 'orders', 'contract_id': 'contract'})
    with pytest.raises(AccountDeletionError) as error:
        delete_account(store, uid, root)
    assert error.value.status == 409
    assert snap.exists()
    assert store.get_project(pid)
    assert store.get_user(uid)


def test_outside_and_database_paths_are_never_deleted(account):
    store, uid, pid, root, storage = account
    outside = root.parent / 'unrelated.duckdb'
    outside.write_bytes(b'outside')
    seed_project(store, pid, outside)
    unrelated = artifact(root, storage, 'snapshots/unrelated.duckdb')
    with store.engine.begin() as conn:
        conn.execute(insert(snapshots).values(id='db-reference', project_id=pid, manifest_hash='db',
            data={'artifact_path': str(root / 'metricguard.db')}))
        conn.execute(insert(records).values(id='untrusted-context', project_id=pid, kind='investigation',
            created_at=now(), data={'context': {'artifact_path': str(unrelated)}}))
    delete_account(store, uid, root)
    assert outside.read_bytes() == b'outside'
    assert (root / 'metricguard.db').is_file()
    assert unrelated.is_file()


def test_revoked_membership_does_not_preserve_sole_owner_workspace(account):
    store, uid, pid, root, storage = account
    other, _ = store.create_user('Former reader', 'former@example.test')
    store.add_project_member(pid, other['id'], 'reader')
    store.revoke_project_member(pid, other['id'])
    assert delete_account(store, uid, root)['deleted_project_ids'] == [pid]


def test_recently_canceled_worker_keeps_evidence_until_drain_window(account):
    store, uid, pid, root, storage = account
    snap = artifact(root, storage, 'snapshots/private.duckdb')
    rid = seed_project(store, pid, snap, state='running')
    with store.engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.id == rid).values(lease_owner='worker', heartbeat_at=time.time(), started_at=now()))
    store.cancel(rid)
    with pytest.raises(AccountDeletionError) as error:
        delete_account(store, uid, root)
    assert error.value.status == 409
    assert snap.exists()


def test_queued_cancellation_without_a_worker_does_not_block_deletion(account):
    store, uid, pid, root, storage = account
    snap = artifact(root, storage, 'snapshots/private.duckdb')
    rid = seed_project(store, pid, snap, state='queued')
    store.cancel(rid)
    assert delete_account(store, uid, root)['deleted_project_ids'] == [pid]
