"""V3 Complete Project Backup and Atomic Restore.

Restores database records, snapshot DuckDB databases, and JSONL row-evidence
artifacts together. Fails atomically if any referenced artifact is missing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
from typing import Any
import uuid

from sqlalchemy import delete, insert, select, update

from services.api.store import (
    DomainError, Store, alerts, runs,
    decisions, versions, definitions,
    monitoring_policies, project_members, projects, records,
    review_requests, snapshots, users,
    dbt_import_revisions, dbt_resource_revisions,
    metrics, external_metric_references,
    metric_definition_bindings, metric_state_transitions,
    metric_impact_reports,
)


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def backup_project(store: Store, project_id: str, data_dir: Path, output_archive: Path) -> dict[str, Any]:
    """Hold the workspace lock until its backup archive has been published.

    Account deletion takes the same lock before scanning owned backup files. A
    backup cannot publish detached, staged private metadata after that scan.
    The implementation below only reads through its separate DB connection.
    """
    with store.engine.connect() as conn:
        if conn.dialect.name == 'sqlite':
            conn.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            conn.begin()
        try:
            project = conn.execute(select(projects.c.id).where(projects.c.id == project_id).with_for_update()).first()
            if not project:
                raise DomainError('NOT_FOUND', f"Project '{project_id}' not found.", 404)
            result = _backup_project(store, project_id, data_dir, output_archive)
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise


def _backup_project(store: Store, project_id: str, data_dir: Path, output_archive: Path) -> dict[str, Any]:
    """Create a complete backup archive of project metadata, snapshots, and evidence artifacts."""
    output_archive = Path(output_archive)
    output_archive.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix='mg-backup-') as tmp_dir:
        staging = Path(tmp_dir)
        snap_stage = staging / 'snapshots'
        snap_stage.mkdir(parents=True, exist_ok=True)
        ev_stage = staging / 'evidence'
        ev_stage.mkdir(parents=True, exist_ok=True)

        metadata: dict[str, Any] = {'project_id': project_id}
        with store.engine.connect() as conn:
            proj_row = conn.execute(select(projects).where(projects.c.id == project_id)).mappings().first()
            if not proj_row:
                raise DomainError('NOT_FOUND', f"Project '{project_id}' not found.", 404)
            metadata['project'] = dict(proj_row)
            metadata['members'] = [dict(r) for r in conn.execute(select(project_members).where(project_members.c.project_id == project_id)).mappings()]
            uids = [m['user_id'] for m in metadata['members']]
            metadata['users'] = [dict(r) for r in conn.execute(select(users).where(users.c.id.in_(uids))).mappings()] if uids else []
            metadata['definitions'] = [dict(r) for r in conn.execute(select(definitions).where(definitions.c.project_id == project_id)).mappings()]
            def_ids = [d['id'] for d in metadata['definitions']]
            metadata['versions'] = [dict(r) for r in conn.execute(select(versions).where(versions.c.definition_id.in_(def_ids))).mappings()] if def_ids else []
            metadata['snapshots'] = [dict(r) for r in conn.execute(select(snapshots).where(snapshots.c.project_id == project_id)).mappings()]
            metadata['runs'] = [dict(r) for r in conn.execute(select(runs).where(runs.c.project_id == project_id)).mappings()]
            metadata['records'] = [dict(r) for r in conn.execute(select(records).where(records.c.project_id == project_id)).mappings()]
            metadata['review_requests'] = [dict(r) for r in conn.execute(select(review_requests).where(review_requests.c.project_id == project_id)).mappings()]
            metadata['decisions'] = [dict(r) for r in conn.execute(select(decisions).where(decisions.c.project_id == project_id)).mappings()]
            metadata['monitoring_policies'] = [dict(r) for r in conn.execute(select(monitoring_policies).where(monitoring_policies.c.project_id == project_id)).mappings()]
            metadata['alerts'] = [dict(r) for r in conn.execute(select(alerts).where(alerts.c.project_id == project_id)).mappings()]
            metadata['dbt_import_revisions'] = [dict(r) for r in conn.execute(select(dbt_import_revisions).where(dbt_import_revisions.c.project_id == project_id)).mappings()]
            metadata['dbt_resource_revisions'] = [dict(r) for r in conn.execute(select(dbt_resource_revisions).where(dbt_resource_revisions.c.project_id == project_id)).mappings()]
            metadata['metrics'] = [dict(r) for r in conn.execute(select(metrics).where(metrics.c.project_id == project_id)).mappings()]
            metadata['external_metric_references'] = [dict(r) for r in conn.execute(select(external_metric_references).where(external_metric_references.c.project_id == project_id)).mappings()]
            metadata['metric_definition_bindings'] = [dict(r) for r in conn.execute(select(metric_definition_bindings).where(metric_definition_bindings.c.project_id == project_id)).mappings()]
            metadata['metric_state_transitions'] = [dict(r) for r in conn.execute(select(metric_state_transitions).where(metric_state_transitions.c.project_id == project_id)).mappings()]
            metadata['metric_impact_reports'] = [dict(r) for r in conn.execute(select(metric_impact_reports).where(metric_impact_reports.c.project_id == project_id)).mappings()]

        manifest_entries: dict[str, dict[str, Any]] = {}

        # 1. Copy snapshot files
        for s in metadata['snapshots']:
            data = s['data']
            art_path = Path(data['artifact_path'])
            if art_path.is_file():
                dest_file = snap_stage / art_path.name
                shutil.copy2(art_path, dest_file)
                rel = f"snapshots/{art_path.name}"
                manifest_entries[rel] = {'sha256': _file_hash(dest_file), 'size': dest_file.stat().st_size}

            sidecar = art_path.with_name(f"{art_path.name}.manifest.json")
            if sidecar.is_file():
                dest_side = snap_stage / sidecar.name
                shutil.copy2(sidecar, dest_side)
                rel = f"snapshots/{sidecar.name}"
                manifest_entries[rel] = {'sha256': _file_hash(dest_side), 'size': dest_side.stat().st_size}

        # 2. Copy evidence artifacts
        evidence_dir = data_dir / 'evidence'
        for r in metadata['runs']:
            run_id = r['id']
            if evidence_dir.is_dir():
                for ev_file in evidence_dir.glob(f"{run_id}-*.jsonl"):
                    dest_ev = ev_stage / ev_file.name
                    shutil.copy2(ev_file, dest_ev)
                    rel = f"evidence/{ev_file.name}"
                    manifest_entries[rel] = {'sha256': _file_hash(dest_ev), 'size': dest_ev.stat().st_size}

        # Write metadata & manifest
        (staging / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        manifest_entries['metadata.json'] = {
            'sha256': _file_hash(staging / 'metadata.json'),
            'size': (staging / 'metadata.json').stat().st_size,
        }
        (staging / 'backup_manifest.json').write_text(json.dumps(manifest_entries, indent=2), encoding='utf-8')

        with tarfile.open(output_archive, 'w:gz') as tar:
            for item in staging.iterdir():
                tar.add(item, arcname=item.name)

    return {
        'status': 'success',
        'project_id': project_id,
        'archive_path': str(output_archive),
        'files_archived': len(manifest_entries),
    }


def restore_project(store: Store, archive_path: Path, data_dir: Path) -> dict[str, Any]:
    """Atomically restore project metadata and physical artifacts, enforcing integrity."""
    archive_path = Path(archive_path)
    data_dir = Path(data_dir).resolve()
    if not archive_path.is_file():
        raise DomainError('NOT_FOUND', f"Backup archive '{archive_path}' not found.", 404)

    with tempfile.TemporaryDirectory(prefix='mg-restore-') as tmp_dir:
        staging = Path(tmp_dir)
        with tarfile.open(archive_path, 'r:gz') as tar:
            for member in tar.getmembers():
                target = (staging / member.name).resolve()
                if not target.is_relative_to(staging.resolve()):
                    raise DomainError('BACKUP_CORRUPTED', f"Unsafe archive path detected: '{member.name}'.", 422)
            # 'data' filter: no links, devices, absolute paths or permission bits from the archive.
            tar.extractall(staging, filter='data')

        manifest_file = staging / 'backup_manifest.json'
        if not manifest_file.is_file():
            raise DomainError('BACKUP_CORRUPTED', 'Missing backup_manifest.json in archive.', 422)

        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))

        # Verify all files in manifest exist and hashes match
        for rel_path, expected in manifest.items():
            fpath = staging / rel_path
            if not fpath.is_file():
                raise DomainError('BACKUP_CORRUPTED', f"Referenced artifact '{rel_path}' is missing from archive.", 422)
            actual_hash = _file_hash(fpath)
            if actual_hash != expected['sha256']:
                raise DomainError('BACKUP_CORRUPTED', f"Hash mismatch for '{rel_path}'. Expected {expected['sha256']}, got {actual_hash}.", 422)

        metadata_file = staging / 'metadata.json'
        if not metadata_file.is_file():
            raise DomainError('BACKUP_CORRUPTED', 'Missing metadata.json in archive.', 422)
        metadata = json.loads(metadata_file.read_text(encoding='utf-8'))
        project_id = metadata['project_id']

        # Enforce that every snapshot referenced in metadata has its DuckDB file present
        snap_stage = staging / 'snapshots'
        for s in metadata.get('snapshots', []):
            art_name = Path(s['data'].get('artifact_path', '')).name
            if not art_name or not (snap_stage / art_name).is_file():
                raise DomainError('BACKUP_CORRUPTED', f"Snapshot '{s['id']}' referenced artifact '{art_name}' not found in backup.", 422)
            # File location is local storage metadata, not part of the pinned manifest.
            s['data'] = {**s['data'], 'artifact_path': str(data_dir / 'snapshots' / art_name)}

        # Enforce that every run with evidence_artifact in result has its evidence.jsonl file present
        evidence_stage = staging / 'evidence'
        for r in metadata.get('runs', []):
            if r.get('result') and r['result'].get('evidence_artifact'):
                expected_ev_name = f"{r['id']}-{r['attempt']}.jsonl"
                if not (evidence_stage / expected_ev_name).is_file():
                    raise DomainError('BACKUP_CORRUPTED', f"Run '{r['id']}' has evidence artifact but '{expected_ev_name}' was not found in backup.", 422)

        # Verify storage quota before writing if quota is configured
        quota = store.get_project_quota(project_id)
        if quota and quota.get('max_storage_bytes'):
            total_incoming_bytes = 0
            if snap_stage.is_dir():
                total_incoming_bytes += sum(f.stat().st_size for f in snap_stage.glob('*.duckdb'))
            if evidence_stage.is_dir():
                total_incoming_bytes += sum(f.stat().st_size for f in evidence_stage.glob('*.jsonl'))
            if total_incoming_bytes > quota['max_storage_bytes']:
                raise DomainError('QUOTA_EXCEEDED', f"Restored project exceeds storage quota ({total_incoming_bytes} > {quota['max_storage_bytes']} bytes).", 413)

        # Restore database records
        with store.engine.begin() as conn:
            # Upsert project
            p = metadata['project']
            if not conn.execute(select(projects).where(projects.c.id == p['id'])).first():
                conn.execute(insert(projects).values(**p))

            # Helper for inserting if absent
            def _insert_records(table, items):
                for item in items:
                    if not conn.execute(select(table).where(table.c.id == item['id'])).first():
                        conn.execute(insert(table).values(**item))

            _insert_records(definitions, metadata.get('definitions', []))
            _insert_records(versions, metadata.get('versions', []))
            for item in metadata.get('snapshots', []):
                existing = conn.execute(select(snapshots).where(snapshots.c.id == item['id'])).mappings().first()
                if not existing:
                    conn.execute(insert(snapshots).values(**item))
                elif existing['project_id'] == item['project_id'] and existing['manifest_hash'] == item['manifest_hash']:
                    # Re-applying a backup also repairs paths written by older restores.
                    relocated = {**existing['data'], 'artifact_path': item['data']['artifact_path']}
                    conn.execute(update(snapshots).where(snapshots.c.id == item['id']).values(data=relocated))
            _insert_records(runs, metadata.get('runs', []))
            _insert_records(records, metadata.get('records', []))
            _insert_records(users, metadata.get('users', []))
            # Memberships are unique per (project, user). A fresh store already seeds the
            # default project's local owner, so keep an existing pair rather than fail.
            for item in metadata.get('members', []):
                if not conn.execute(select(project_members).where(
                        (project_members.c.id == item['id']) |
                        ((project_members.c.project_id == item['project_id']) & (project_members.c.user_id == item['user_id'])))).first():
                    conn.execute(insert(project_members).values(**item))
            _insert_records(review_requests, metadata.get('review_requests', []))
            _insert_records(decisions, metadata.get('decisions', []))
            _insert_records(monitoring_policies, metadata.get('monitoring_policies', []))
            _insert_records(alerts, metadata.get('alerts', []))
            _insert_records(dbt_import_revisions, metadata.get('dbt_import_revisions', []))
            _insert_records(dbt_resource_revisions, metadata.get('dbt_resource_revisions', []))
            _insert_records(metrics, metadata.get('metrics', []))
            _insert_records(external_metric_references, metadata.get('external_metric_references', []))
            _insert_records(metric_definition_bindings, metadata.get('metric_definition_bindings', []))
            _insert_records(metric_state_transitions, metadata.get('metric_state_transitions', []))
            _insert_records(metric_impact_reports, metadata.get('metric_impact_reports', []))

        # Restore physical artifacts to data_dir after DB insertion succeeds
        snap_dest = data_dir / 'snapshots'
        snap_dest.mkdir(parents=True, exist_ok=True)
        if (staging / 'snapshots').is_dir():
            for s_file in (staging / 'snapshots').iterdir():
                shutil.copy2(s_file, snap_dest / s_file.name)

        ev_dest = data_dir / 'evidence'
        ev_dest.mkdir(parents=True, exist_ok=True)
        if (staging / 'evidence').is_dir():
            for e_file in (staging / 'evidence').iterdir():
                shutil.copy2(e_file, ev_dest / e_file.name)

    return {
        'status': 'restored',
        'project_id': project_id,
        'snapshots_restored': len(metadata.get('snapshots', [])),
        'runs_restored': len(metadata.get('runs', [])),
        'definitions_restored': len(metadata.get('definitions', [])),
    }
