"""V8 closeout check R defect: restoring the default workspace into a fresh installation."""
from pathlib import Path

from services.api.backup import backup_project, restore_project
from services.api.store import Store


def test_default_workspace_restores_into_fresh_store(tmp_path):
    source_dir, target_dir = tmp_path / 'source', tmp_path / 'target'
    source_dir.mkdir()
    target_dir.mkdir()
    source = Store(f"sqlite:///{(source_dir / 'app.db').as_posix()}")
    archive = tmp_path / 'backup.tar.gz'
    backup_project(source, source.project_id, source_dir, archive)

    # A fresh store seeds the same default project and local owner membership.
    target = Store(f"sqlite:///{(target_dir / 'app.db').as_posix()}")
    result = restore_project(target, archive, target_dir)

    assert result['status'] == 'restored'
    member = target.get_project_member(source.project_id, 'local-analyst')
    assert member and member['role'] == 'owner' and member['revoked_at'] is None
    # Restoring twice stays idempotent.
    assert restore_project(target, archive, target_dir)['status'] == 'restored'
    assert Path(archive).is_file()
