import json
import tarfile
from pathlib import Path
import pytest

from services.api.store import Store, DomainError
from services.api.backup import backup_project, restore_project
from scripts.seed_demo import seed


def test_storage_quota_accounting_and_restore_enforcement(tmp_path):
    """Verify storage quota dynamic accounting and restore rejection on quota overflow."""
    url = f"sqlite:///{tmp_path / 'quota.db'}"
    seed(url, tmp_path)
    store = Store(url)

    owner, key_o = store.create_user("Owner", "owner@quota.org")
    proj = store.create_project("Quota Project", owner_id=owner['id'], project_id="proj-quota")

    # Set quota of 1000 bytes
    store.set_project_quota("proj-quota", {"max_storage_bytes": 1000})

    # Checking 500 bytes passes
    store.check_storage_quota("proj-quota", 500, tmp_path)

    # Checking 1001 bytes fails
    with pytest.raises(DomainError) as exc:
        store.check_storage_quota("proj-quota", 1001, tmp_path)
    assert exc.value.code == 'QUOTA_EXCEEDED'


def test_backup_restore_adversarial_tampering(tmp_path):
    """Verify atomic rejection of path traversal, missing snapshot, and corrupted files."""
    url = f"sqlite:///{tmp_path / 'tamper.db'}"
    seed(url, tmp_path)
    store = Store(url)

    owner, _ = store.create_user("Owner", "owner@tamper.org")
    proj = store.create_project("Project Tamper", owner_id=owner['id'], project_id="proj-tamper")

    # Backup original clean project
    clean_archive = tmp_path / "clean_backup.tar.gz"
    backup_project(store, "proj-tamper", tmp_path, clean_archive)

    # 1. Adversarial Path Traversal Archive: member attempts to write outside staging (e.g., ../evil.txt)
    traversal_archive = tmp_path / "traversal.tar.gz"
    evil_file = tmp_path / "evil.txt"
    evil_file.write_text("pwned", encoding='utf-8')
    with tarfile.open(traversal_archive, "w:gz") as tar:
        tar.add(evil_file, arcname="../evil.txt")

    with pytest.raises(DomainError) as exc_trav:
        restore_project(store, traversal_archive, tmp_path)
    assert exc_trav.value.code == 'BACKUP_CORRUPTED'
    assert "unsafe archive path" in exc_trav.value.message.lower()

    # 2. Missing Snapshot DB in archive: metadata references snapshot, but file is absent
    corrupt_snap_archive = tmp_path / "missing_snap.tar.gz"
    staging = tmp_path / "staging_corrupt"
    staging.mkdir()
    with tarfile.open(clean_archive, "r:gz") as tar:
        tar.extractall(staging)

    # Alter metadata to reference a phantom snapshot
    meta = json.loads((staging / "metadata.json").read_text(encoding='utf-8'))
    meta['snapshots'] = [{'id': 'snap-phantom', 'data': {'artifact_path': 'snapshots/missing.duckdb'}}]
    (staging / "metadata.json").write_text(json.dumps(meta), encoding='utf-8')

    # Update manifest to match modified metadata.json hash
    import hashlib
    m_json = json.loads((staging / "backup_manifest.json").read_text(encoding='utf-8'))
    m_json['metadata.json']['sha256'] = hashlib.sha256((staging / "metadata.json").read_bytes()).hexdigest()
    m_json['metadata.json']['size'] = (staging / "metadata.json").stat().st_size
    (staging / "backup_manifest.json").write_text(json.dumps(m_json), encoding='utf-8')

    with tarfile.open(corrupt_snap_archive, "w:gz") as tar:
        for item in staging.iterdir():
            tar.add(item, arcname=item.name)

    with pytest.raises(DomainError) as exc_snap:
        restore_project(store, corrupt_snap_archive, tmp_path)
    assert exc_snap.value.code == 'BACKUP_CORRUPTED'
    assert "not found in backup" in exc_snap.value.message.lower()


def test_comprehensive_log_redaction():
    """Verify that operational log redaction sanitizes keys, tokens, connection strings, and query params."""
    sensitive_data = {
        # Various key spellings/casings
        "X-API-KEY": "secret-12345",
        "access_token": "token-abcde",
        "Client_Secret": "supersecret",
        "DB_PASSWORD": "mypassword1",
        "Authorization": "Bearer sensitive_auth_token",
        "dsn": "postgres://usr:secret_pwd@db.host.internal:5432/analytics",
        # Generic keys containing sensitive strings
        "remote_url": "https://api.internal/v1/export?api_key=token999&format=csv",
        "connection_uri": "mongodb://admin:adminpass@mongo.internal:27017/db",
        "header_value": "Bearer my_secret_bearer_token",
        "nested": {
            "token": "nested-token",
            "safe_key": "safe_value"
        },
        "list_items": [
            "safe_item",
            "Bearer array_token",
            {"password": "item_password"}
        ]
    }

    redacted = Store._redact_dict(sensitive_data)

    # 1. Key-level redaction
    assert redacted["X-API-KEY"] == '[REDACTED]'
    assert redacted["access_token"] == '[REDACTED]'
    assert redacted["Client_Secret"] == '[REDACTED]'
    assert redacted["DB_PASSWORD"] == '[REDACTED]'
    assert redacted["Authorization"] == '[REDACTED]'
    assert redacted["dsn"] == '[REDACTED]'

    # 2. String value redactions (Bearer, connection strings, query params)
    assert "token999" not in redacted["remote_url"]
    assert "api_key=[REDACTED]" in redacted["remote_url"]

    assert "adminpass" not in redacted["connection_uri"]
    assert "[REDACTED]" in redacted["connection_uri"]

    assert "my_secret_bearer_token" not in redacted["header_value"]
    assert "Bearer [REDACTED]" in redacted["header_value"]

    # 3. Nested dict & list recursion
    assert redacted["nested"]["token"] == '[REDACTED]'
    assert redacted["nested"]["safe_key"] == 'safe_value'

    assert redacted["list_items"][0] == 'safe_item'
    assert redacted["list_items"][1] == 'Bearer [REDACTED]'
    assert redacted["list_items"][2]["password"] == '[REDACTED]'
