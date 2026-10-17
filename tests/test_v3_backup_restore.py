"""V3 Complete Project Backup, Atomic Restore, Quotas & Audit Log Tests.

Verifies:
- Complete project backup bundles DB records, DuckDB snapshots, and JSONL row evidence with SHA-256 digests.
- Atomic restore into a clean store reconstructs definitions, runs, snapshots, evidence, and governance objects.
- Missing or corrupted evidence artifacts cause restore to fail atomically (422 BACKUP_CORRUPTED).
- Storage quota limits prevent exceeding project bounds (413 QUOTA_EXCEEDED).
- Operational audit logs redact credentials, tokens, and secrets.
"""
import json
from pathlib import Path
import shutil
import tarfile
import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.backup import backup_project, restore_project
from services.api.store import DomainError, Store
from scripts.seed_demo import seed


@pytest.fixture
def backup_env(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    store = app.state.store

    owner, owner_key = store.create_user('Backup Owner', 'bowner@metricguard.internal')
    proj = store.create_project('Archival Project', owner_id=owner['id'], project_id='proj-archive')

    # Create definition
    defn = store.record_definition(
        name='Orders Measure', source_label='Warehouse', owner_label='Ops',
        raw_sql='SELECT SUM(order_amount) FROM orders', time_column='order_date',
        project_id='proj-archive'
    )

    # Register snapshot with real file and distinct manifest hash for proj-archive
    snap_seed = store.bootstrap('local-retail')['snapshots'][0]
    snap_path = Path(snap_seed['artifact_path'])
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    if not snap_path.is_file():
        snap_path.write_bytes(b'MOCK DUCKDB SNAPSHOT DATA')
    snap_manifest = {**snap_seed['manifest'], 'manifest_hash': 'a' * 64}
    snap = store.record_snapshot(
        name='Orders Snap', manifest=snap_manifest,
        artifact_path=str(snap_path), project_id='proj-archive'
    )

    # Create a completed run with an evidence artifact JSONL
    ev_dir = tmp_path / 'evidence'
    ev_dir.mkdir(parents=True, exist_ok=True)
    run_id = 'run-with-evidence-1'
    ev_file = ev_dir / f"{run_id}-0.jsonl"
    ev_file.write_text('{"row_id": "r1", "delta": "100.00"}\n{"row_id": "r2", "delta": "-100.00"}\n', encoding='utf-8')

    mock_manifest = {
        'row_count': 2,
        'sha256': 'mock_sha',
        'execution_policy': 'v1_direct'
    }

    run_req = {
        'baseline_version_id': defn['versions'][0]['id'],
        'target_version_id': defn['versions'][0]['id'],
        'snapshot_id': snap['id'],
        'context': {
            'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
            'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
            'scope': 'synthetic_retail_all_orders'
        },
        'relationship': 'expected_agreement'
    }
    run = store.queue(run_req, idempotency_key='backup-run-idemp', execution_mode='demo', project_id='proj-archive')
    mock_result = {
        'baseline_value': '500.00', 'target_value': '500.00', 'delta_target_minus_baseline': '0.00',
        'explanation_status': 'complete_ordered_bridge', 'residual': '0.00',
        'evidence_artifact': mock_manifest,
        'provenance': {'snapshot_manifest_hash': snap['manifest_hash']}
    }
    # Link run id
    with store.engine.begin() as conn:
        from services.api.store import runs
        from sqlalchemy import update
        conn.execute(update(runs).where(runs.c.id == run['id']).values(id=run_id))
    run = store.complete_demo(run_id, mock_result)

    # Create governance objects: review request, decision, monitoring policy, alert
    rr = store.create_review_request('proj-archive', owner['id'], {
        'rationale': 'Periodic review for archival project',
        'is_intentional_variant': False
    })
    dec = store.record_decision('proj-archive', owner['id'], {
        'review_request_id': rr['id'],
        'approved_version_id': defn['versions'][0]['id'],
        'decision': 'approved',
        'rationale': 'Approved in full'
    })
    pol = store.create_monitoring_policy('proj-archive', owner['id'], {
        'name': 'Daily check',
        'baseline_version_id': defn['versions'][0]['id'],
        'target_version_id': defn['versions'][0]['id'],
        'snapshot_id': snap['id'],
        'absolute_threshold': '50.00'
    })
    alert, _ = store.record_alert('proj-archive', pol['id'], 'hash123', {'delta': '60.00'})

    with TestClient(app) as client:
        yield {
            'client': client,
            'store': store,
            'tmp_path': tmp_path,
            'owner_token': owner_key,
            'proj': proj,
            'defn': defn,
            'snap': snap,
            'run_id': run_id,
            'ev_file': ev_file,
            'policy': pol,
            'alert': alert,
        }


def test_complete_project_backup_and_atomic_restore(backup_env):
    """Backup bundles all DB records, DuckDB snapshots, and JSONL evidence; restore restores everything together."""
    store = backup_env['store']
    data_dir = backup_env['tmp_path']
    archive_out = data_dir / 'backups' / 'archive_test.tar.gz'

    # 1. Backup Project
    res = backup_project(store, 'proj-archive', data_dir, archive_out)
    assert res['status'] == 'success'
    assert archive_out.is_file()

    # Verify tarball contents
    with tarfile.open(archive_out, 'r:gz') as tar:
        names = tar.getnames()
        assert 'metadata.json' in names
        assert 'backup_manifest.json' in names
        assert any(n.startswith('snapshots/') for n in names)
        assert f"evidence/{backup_env['run_id']}-0.jsonl" in names

    # 2. Restore into a completely clean target store
    clean_dir = data_dir / 'clean_store'
    clean_dir.mkdir(parents=True, exist_ok=True)
    clean_db_url = f"sqlite:///{clean_dir / 'clean.db'}"
    target_store = Store(clean_db_url)

    restore_res = restore_project(target_store, archive_out, clean_dir)
    assert restore_res['status'] == 'restored'
    assert restore_res['project_id'] == 'proj-archive'
    assert restore_res['snapshots_restored'] >= 1
    assert restore_res['runs_restored'] >= 1
    assert restore_res['definitions_restored'] >= 1

    # 3. Verify restored items in target store
    bootstrap = target_store.bootstrap('proj-archive')
    assert len(bootstrap['definitions']) >= 1
    assert bootstrap['definitions'][0]['name'] == 'Orders Measure'
    assert len(bootstrap['snapshots']) >= 1
    assert len(bootstrap['runs']) >= 1
    assert bootstrap['runs'][0]['id'] == backup_env['run_id']

    # Verify physical files restored
    restored_ev = clean_dir / 'evidence' / f"{backup_env['run_id']}-0.jsonl"
    assert restored_ev.is_file()
    assert restored_ev.read_text(encoding='utf-8') == backup_env['ev_file'].read_text(encoding='utf-8')

    # Verify governance records restored
    decisions = target_store.list_decisions('proj-archive')
    assert len(decisions) >= 1
    policies = target_store.list_monitoring_policies('proj-archive')
    assert len(policies) >= 1
    alerts = target_store.list_alerts('proj-archive')
    assert len(alerts) >= 1


@pytest.mark.parametrize('legacy_location', [False, True])
def test_restore_uses_destination_snapshot_without_source_files(tmp_path, legacy_location):
    """A restored snapshot must remain usable after its original file disappears."""
    import zipfile
    from sqlalchemy import update
    from packages.metricguard_core.relational import context_for, parse_relational, validate_contract
    from packages.metricguard_core.relational_snapshot import ingest_relational
    from packages.metricguard_core.relational_compare import compare_relational
    from services.api.store import snapshots

    original = tmp_path / 'original'
    original.mkdir()
    source_store = Store(f"sqlite:///{original / 'app.db'}")
    owner, _ = source_store.create_user('Restore owner', 'restore@example.test')
    source_store.create_project('Restore project', owner_id=owner['id'], project_id='restore-case')
    contract = validate_contract({
        'version': 1, 'mode': 'relational_snapshot', 'policy': 'relational-snapshot-v1',
        'name': 'Restore test', 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable',
        'fact': {'table': 'facts', 'key': 'fact_id', 'grain': 'one fact', 'categories': []},
        'dimension': {'table': 'groups', 'key': 'group_id', 'grain': 'one group', 'categories': ['segment']},
        'relationship': {'fact_foreign_key': 'group_id', 'dimension_key': 'group_id',
                         'cardinality': 'many_to_one', 'missing_dimension_policy': 'preserve_and_report'},
        'measure': {'column': 'amount', 'precision': 18, 'scale': 2},
        'measure_meaning': 'Declared points', 'unit_label': 'points', 'values_comparable': True,
    })
    bundle = original / 'source.zip'
    with zipfile.ZipFile(bundle, 'w') as z:
        z.writestr('fact.csv', 'fact_id,group_id,amount\nA,G1,10.00\nB,G2,20.00\n')
        z.writestr('dimension.csv', 'group_id,segment\nG1,north\nG2,south\n')
    original_path = original / 'snapshots' / 'points.duckdb'
    manifest = ingest_relational(bundle, original_path, contract)
    source = source_store.record_snapshot('Points', manifest, original_path, project_id='restore-case')
    original_bytes = original_path.read_bytes()
    archive = tmp_path / 'relocation.tar.gz'
    backup_project(source_store, 'restore-case', original, archive)
    destination = tmp_path / 'relocated'
    destination.mkdir()
    target = Store(f"sqlite:///{destination / 'app.db'}")
    try:
        if legacy_location:
            restore_project(target, archive, destination)
            # Reproduce records retained by the previous restore implementation.
            with target.engine.begin() as conn:
                conn.execute(update(snapshots).where(snapshots.c.id == source['id']).values(data=source))

        original_path.unlink()
        restore_project(target, archive, destination)
        restored = target.snapshot(source['id'])
        restored_path = Path(restored['artifact_path'])
        assert restored_path.is_absolute()
        assert restored_path == destination / 'snapshots' / original_path.name
        assert restored_path.read_bytes() == original_bytes
        assert restored['manifest'] == source['manifest']
        assert restored['manifest_hash'] == source['manifest_hash']
        sql = 'SELECT SUM(f.amount) FROM facts f LEFT JOIN groups d ON f.group_id = d.group_id'
        result = compare_relational(restored_path, parse_relational(sql, contract),
                                    parse_relational(sql + " WHERE d.segment = 'north'", contract), context_for(contract))
        assert (result['baseline_value'], result['target_value'],
                result['delta_target_minus_baseline'], result['residual']) == ('30.00', '10.00', '-20.00', '0.00')
    finally:
        target.engine.dispose()
        source_store.engine.dispose()


def test_atomic_rejection_of_corrupted_backup(backup_env):
    """Restore must fail atomically with BACKUP_CORRUPTED if any artifact is missing or tampered."""
    store = backup_env['store']
    data_dir = backup_env['tmp_path']
    archive_out = data_dir / 'backups' / 'archive_valid.tar.gz'
    backup_project(store, 'proj-archive', data_dir, archive_out)

    # Create a corrupted archive where an evidence file is missing
    corrupt_dir = data_dir / 'corrupt_staging'
    corrupt_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_out, 'r:gz') as tar:
        tar.extractall(corrupt_dir)

    # Delete the evidence file from staging while leaving it referenced in metadata
    ev_files = list((corrupt_dir / 'evidence').glob('*.jsonl'))
    assert len(ev_files) > 0
    ev_files[0].unlink()

    corrupt_archive = data_dir / 'backups' / 'archive_corrupt.tar.gz'
    with tarfile.open(corrupt_archive, 'w:gz') as tar:
        for item in corrupt_dir.iterdir():
            tar.add(item, arcname=item.name)

    # Attempt restore into clean store -> must raise BACKUP_CORRUPTED
    clean_dir = data_dir / 'clean_store2'
    clean_dir.mkdir(parents=True, exist_ok=True)
    target_store = Store(f"sqlite:///{clean_dir / 'clean2.db'}")

    with pytest.raises(DomainError) as exc_info:
        restore_project(target_store, corrupt_archive, clean_dir)
    assert exc_info.value.code == 'BACKUP_CORRUPTED'


def test_storage_quota_enforcement(backup_env):
    """Project storage quota rejects additions exceeding limit with 413 QUOTA_EXCEEDED."""
    store = backup_env['store']
    data_dir = backup_env['tmp_path']

    # Set storage quota to 1,000,000 bytes (current existing snapshot + evidence is ~536 KB)
    store.set_project_quota('proj-archive', {'max_storage_bytes': 1_000_000})
    quota = store.get_project_quota('proj-archive')
    assert quota['max_storage_bytes'] == 1_000_000

    # Checking 50 bytes passes (~536 KB + 50 B <= 1 MB)
    store.check_storage_quota('proj-archive', new_bytes=50, data_dir=data_dir)

    # Checking 600,000 bytes exceeds 1,000,000 bytes (~536 KB + 600 KB = 1.13 MB > 1 MB) -> raises QUOTA_EXCEEDED (413)
    with pytest.raises(DomainError) as exc:
        store.check_storage_quota('proj-archive', new_bytes=600_000, data_dir=data_dir)
    assert exc.value.code == 'QUOTA_EXCEEDED'
    assert exc.value.status == 413


def test_redacted_audit_logs(backup_env):
    """Audit logs redact sensitive tokens, api keys, and passwords."""
    store = backup_env['store']

    log = store.record_audit_log(
        project_id='proj-archive',
        actor_id='user-1',
        action='rotate_keys',
        target_type='credentials',
        target_id='cred-99',
        data={
            'provider': 'snowflake',
            'api_key': 'super_secret_api_key_12345',
            'database_token': 'secret_bearer_token',
            'password': 'my_plain_password',
            'public_info': 'us-east-1'
        }
    )

    assert log['data']['public_info'] == 'us-east-1'
    assert log['data']['api_key'] == '[REDACTED]'
    assert log['data']['database_token'] == '[REDACTED]'
    assert log['data']['password'] == '[REDACTED]'

    # Verify retrieval preserves redaction
    logs = store.list_audit_logs('proj-archive')
    assert len(logs) >= 1
    latest = next(l for l in logs if l['id'] == log['id'])
    assert latest['data']['api_key'] == '[REDACTED]'
