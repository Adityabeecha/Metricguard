"""Hosted reliability: server-wide run caps, lost-worker retries, health, guest expiry, DB backup."""
import gzip
import json
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from services import durable
from services.api import guests
from services.api.app import create_app
from services.api.store import (MAX_ATTEMPTS, DomainError, Store, definitions, guest_accounts, now, projects,
                                runs, snapshot_imports, snapshots, users, versions)
from scripts import backup_db
from tests.test_guest_accounts import sign_in
from tests.test_oauth import setup  # noqa: F401  (fixture)


@pytest.fixture
def store(tmp_path, monkeypatch):
    for name in ('MAX_ACTIVE_RUNS_PER_WORKSPACE', 'MAX_RUNS_PER_DAY', 'MAX_ACTIVE_RUNS_GLOBAL', 'MAX_RUNS_PER_DAY_GLOBAL', 'MAX_ROWS'):
        monkeypatch.delenv('METRICGUARD_' + name, raising=False)
    store = Store(f'sqlite:///{tmp_path / "app.db"}')
    with store.engine.begin() as conn:
        conn.execute(insert(projects).values(id='other', name='Other'))
        conn.execute(insert(definitions).values(id='d', project_id=store.project_id, name='Amount', source_label='CSV', owner_label='Analyst', created_at=now()))
        conn.execute(insert(versions).values(id='v', definition_id='d', version_number=1, data={'id': 'v'}))
        conn.execute(insert(snapshots).values(id='s', project_id=store.project_id, manifest_hash='hash', data={'id': 's', 'row_count': 2}))
    yield store
    store.engine.dispose()


REQUEST = {'baseline_version_id': 'v', 'target_version_id': 'v', 'snapshot_id': 's'}


def test_global_active_cap_spans_workspaces(store, monkeypatch):
    monkeypatch.setenv('METRICGUARD_MAX_ACTIVE_RUNS_GLOBAL', '1')
    first = store.queue(REQUEST, 'one', 'isolated')
    with pytest.raises(DomainError) as busy:
        store.queue(REQUEST, 'two', 'isolated', project_id='other')
    assert busy.value.code == 'SERVER_BUSY' and busy.value.status == 503
    assert store.queue(REQUEST, 'one', 'isolated')['id'] == first['id']  # idempotent retry still answers
    store.cancel(first['id'])
    assert store.queue(REQUEST, 'two', 'isolated', project_id='other')['state'] == 'queued'


def test_global_daily_cap(store, monkeypatch):
    monkeypatch.setenv('METRICGUARD_MAX_RUNS_PER_DAY_GLOBAL', '1')
    store.cancel(store.queue(REQUEST, 'one', 'isolated')['id'])
    with pytest.raises(DomainError, match="today's comparison limit"):
        store.queue(REQUEST, 'two', 'isolated', project_id='other')


def test_no_global_caps_by_default(store):
    for key in ('a', 'b', 'c'):
        assert store.queue(REQUEST, key, 'isolated')['state'] == 'queued'


def test_lost_worker_retries_then_fails(store):
    run = store.queue(REQUEST, 'one', 'isolated')
    for attempt in range(1, MAX_ATTEMPTS + 1):
        claimed = store.claim('w', lease_seconds=60)
        assert claimed['id'] == run['id'] and claimed['attempt'] == attempt
        with store.engine.begin() as conn:  # the worker vanished: its lease ran out
            conn.execute(update(runs).values(lease_expires_at=time.time() - 1))
    assert store.claim('w') is None
    failed = store.get_run(run['id'])
    assert failed['state'] == 'failed' and failed['error']['code'] == 'WORKER_LOST'


def test_lost_import_fails_after_max_attempts(store, tmp_path):
    with store.engine.begin() as conn:
        conn.execute(insert(snapshot_imports).values(id='imp', project_id=store.project_id, state='running',
            attempt=MAX_ATTEMPTS, lease_owner='w', lease_expires_at=time.time() - 1, source_path=str(tmp_path / 'x.csv'),
            data={}, created_at=now()))
    assert store.claim_import('w') is None
    with store.engine.connect() as conn:
        row = conn.execute(select(snapshot_imports).where(snapshot_imports.c.id == 'imp')).mappings().one()
    assert row['state'] == 'failed' and row['error']['code'] == 'WORKER_LOST'


def test_health_reports_database_and_sweep(setup, monkeypatch, tmp_path):
    client, store, _, _ = setup
    body = client.get('/api/v1/health').json()
    assert body['database'] == 'ok' and body['status'] == 'ok' and 'guest_sweep' not in body
    monkeypatch.setenv('METRICGUARD_FULL_ACCESS_EMAILS', 'owner@example.com')
    guests.sweep(store, tmp_path)
    sweep = client.get('/api/v1/health').json()['guest_sweep']
    assert sweep['at'] and sweep['erased'] == 0 and set(sweep) == {'at', 'erased', 'waiting'}
    monkeypatch.setattr(store, 'database_ok', lambda: (_ for _ in ()).throw(RuntimeError('db down')))
    down = client.get('/api/v1/health').json()
    assert down['database'] == 'unavailable' and down['status'] == 'degraded'


def test_expired_guest_is_refused_before_the_sweep_and_signs_in_fresh(setup, monkeypatch):
    client, store, _, profile = setup
    monkeypatch.setenv('METRICGUARD_FULL_ACCESS_EMAILS', 'owner@example.com')
    profile['email'] = 'stranger@example.com'
    pid = sign_in(client)
    guest = store.get_user_by_session(client.cookies.get('mg_session'))
    identity = client.get('/api/v1/auth/identity').json()
    assert identity['guest_storage_bytes'] == 25 * 1024 * 1024
    usage = client.get('/api/v1/usage', headers={'x-project-id': pid}).json()
    assert usage['storage_quota_bytes'] == 25 * 1024 * 1024 and usage['guest_expires_at'] == pytest.approx(identity['guest_expires_at'])
    with store.engine.begin() as conn:
        conn.execute(update(guest_accounts).values(expires_at=time.time() - 1))
    refused = client.get('/api/v1/auth/identity')
    assert refused.status_code == 401 and refused.json()['error']['code'] == 'GUEST_EXPIRED'
    sign_in(client)  # the callback erases the expired account first
    fresh = store.get_user_by_session(client.cookies.get('mg_session'))
    assert fresh['id'] != guest['id'] and store.guest_expiry(fresh['id']) > time.time()
    with pytest.raises(DomainError):
        store.get_user(guest['id'])


def test_full_accounts_have_no_guest_fields(setup):
    client, _, _, _ = setup
    pid = sign_in(client)
    assert client.get('/api/v1/auth/identity').json()['guest_storage_bytes'] is None
    usage = client.get('/api/v1/usage', headers={'x-project-id': pid}).json()
    assert usage['storage_quota_bytes'] is None and usage['guest_expires_at'] is None


def test_backup_round_trip_and_retention(store, tmp_path):
    with store.engine.begin() as conn:
        conn.execute(insert(users).values(id='u1', email='a@example.com', name='A', api_key_hash='h', is_active=True, created_at=now()))
    remote = tmp_path / 'remote'
    storage = durable.Durable(tmp_path / 'data', durable.DirBackend(remote))
    old = remote / 'backups' / 'db' / '2026-10-18.jsonl.gz'
    kept = remote / 'backups' / 'db' / '2026-10-19.jsonl.gz'
    for path in (old, kept):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x')
    key = backup_db.backup(str(store.engine.url), storage, today=date(2026, 11, 1))
    assert key == 'backups/db/2026-11-01.jsonl.gz' and not old.exists() and kept.exists()
    copy = remote / key
    lines = gzip.open(copy, 'rt', encoding='utf-8').read().splitlines()
    assert json.loads(lines[0]) == {'format': backup_db.FORMAT}
    assert not any('"auth_sessions"' in line for line in lines)
    target = Store(f'sqlite:///{tmp_path / "restored.db"}')
    with target.engine.begin() as conn:  # a fresh Store seeds its default project; start truly empty
        for table in reversed(backup_db.metadata.sorted_tables):
            conn.execute(table.delete())
    counts = backup_db.restore(copy, target.engine)
    assert counts['users'] >= 1 and counts.get('runs', 0) == 0
    with target.engine.connect() as conn:
        assert conn.execute(select(users.c.email).where(users.c.id == 'u1')).scalar_one() == 'a@example.com'
        assert conn.execute(select(versions.c.data).where(versions.c.id == 'v')).scalar_one() == {'id': 'v'}
    with pytest.raises(SystemExit, match='already have rows'):
        backup_db.restore(copy, target.engine)
    target.engine.dispose()


def test_backup_needs_durable_storage(store, tmp_path):
    with pytest.raises(SystemExit, match='Durable storage is not configured'):
        backup_db.backup(str(store.engine.url), durable.Durable(tmp_path))


def test_health_counts_server_errors(setup, monkeypatch):
    from fastapi.testclient import TestClient
    from services.api import oauth
    client, _, _, _ = setup
    before = client.get('/api/v1/health').json()['server_errors']['since_start']
    monkeypatch.setattr(oauth, 'settings', lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    with TestClient(client.app, raise_server_exceptions=False, base_url='https://testserver') as raw:
        assert raw.get('/api/v1/auth/providers').status_code == 500
        errors = raw.get('/api/v1/health').json()['server_errors']
    assert errors['since_start'] == before + 1 and errors['last_at']


def test_global_import_cap(store, tmp_path, monkeypatch):
    monkeypatch.setenv('METRICGUARD_MAX_ACTIVE_IMPORTS_GLOBAL', '1')
    store.queue_import(tmp_path / 'a.csv', 'hash-a', 'A', {'purpose': 'preview'})
    assert store.queue_import(tmp_path / 'a.csv', 'hash-a', 'A', {'purpose': 'preview'})['reused']  # same file still answers
    with pytest.raises(DomainError) as busy:
        store.queue_import(tmp_path / 'b.csv', 'hash-b', 'B', {'purpose': 'preview'}, project_id='other')
    assert busy.value.code == 'SERVER_BUSY' and busy.value.status == 503
