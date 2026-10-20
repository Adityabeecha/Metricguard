"""Account erasure with conservative workspace ownership and retryable storage cleanup.

Metadata remains available for retry until every local/durable deletion succeeds.
Shared workspace evidence stays intact; historical required user references become
an inactive, anonymous actor rather than granting another member ownership.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from sqlalchemy import delete, func, insert, or_, select, update

from services import durable
from .store import DomainError


class AccountDeletionError(DomainError):
    @property
    def status_code(self):
        return self.status


def _cancel_is_draining(row) -> bool:
    if row['state'] != 'canceled' or not row.get('lease_owner'):
        return False
    # Cancel invalidates the lease before the worker has necessarily stopped.
    # Retain evidence through the configured worker time limit and a grace period.
    horizon = max(1200, int(os.environ.get('METRICGUARD_RUN_SECONDS', '600')),
                  int(os.environ.get('METRICGUARD_INGEST_SECONDS', '1200'))) + 60
    stamps = []
    for name in ('started_at', 'completed_at', 'heartbeat_at'):
        value = row.get(name)
        if value is not None:
            try:
                stamps.append(float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(value).timestamp())
            except (TypeError, ValueError):
                return True  # Unknown worker age requires acknowledgement, not deletion.
    return not stamps or max(stamps) + horizon >= time.time()


def _owned_path(value, root: Path) -> Path | None:
    """Only recorded files in application artifact folders may be removed."""
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    if len(relative.parts) < 2 or relative.parts[0] not in {'snapshots', 'uploads', 'evidence', 'backups', 'staging'}:
        return None
    # Do not follow directories or symlinks into another artifact's location.
    lexical = Path(value).absolute()
    if lexical.is_symlink() or any(parent.is_symlink() for parent in lexical.parents if parent != root):
        return None
    return path


def _files(rows, root: Path) -> set[Path]:
    result = set()
    for table, row in rows:
        # These are server-owned storage fields. Arbitrary investigation/context
        # JSON must never become authority to delete another local file.
        stored_path = (row['data'].get('artifact_path') if table.name == 'snapshots' else
                       row.get('source_path') if table.name == 'snapshot_imports' else None)
        path = _owned_path(stored_path, root)
        if path:
            result.update((path, path.with_name(path.name + durable.SIDECAR)))
            if table.name == 'snapshots':
                result.add(path.with_suffix('.lineage.jsonl'))
        if table.name == 'comparison_runs':
            ident, attempts = row['id'], row['attempt'] or 0
            # Construct exact names, including remote-only files from earlier attempts.
            if re.fullmatch(r'[A-Za-z0-9_-]+', ident):
                for attempt in range(1, attempts + 1):
                    for suffix in ('.jsonl', '.members.jsonl', '.context.jsonl'):
                        result.add(root / 'evidence' / f'{ident}-{attempt}{suffix}')
        elif table.name == 'snapshot_imports':
            ident, attempts = row['id'], row['attempt'] or 0
            if re.fullmatch(r'[A-Za-z0-9_-]+', ident):
                for attempt in range(1, attempts + 1):
                    path = root / 'snapshots' / f'{ident}-{attempt}.duckdb'
                    result.update((path, path.with_name(path.name + durable.SIDECAR)))
    return result


def _shared_files(conn, erase, paths: set[Path], root: Path) -> set[Path]:
    """Find other-workspace references only to candidate artifact filenames.

    Store.record_snapshot/finish_import/queue_import keep str(path), without
    canonicalising it. Exact spelling checks cover normal writes; a filename
    prefilter also finds relative, separator and '..' aliases. Resolve only those
    matching rows, never all other workspaces' metadata. Run evidence names are
    unique to a run id, so comparison_runs need no preservation query.
    """
    from .store import snapshots, snapshot_imports

    shared = set()
    candidates = sorted(paths)
    for table, column in ((snapshots, snapshots.c.data['artifact_path'].as_string()),
                          (snapshot_imports, snapshot_imports.c.source_path)):
        normalized = func.replace(column, '\\', '/')
        if os.name == 'nt':
            normalized = func.lower(normalized)
        # Bound bind counts / expression depth even for accounts with many files.
        for start in range(0, len(candidates), 100):
            batch = candidates[start:start + 100]
            spellings = {spelling for path in batch for spelling in (str(path), path.as_posix())}
            names = {path.name.lower() if os.name == 'nt' else path.name for path in batch}
            matches = [column.in_(spellings), normalized.in_(names)]
            for name in names:
                escaped = name.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                matches.append(normalized.like('%/' + escaped, escape='\\'))
            query = select(table).where(table.c.project_id.not_in(erase), or_(*matches))
            for row in conn.execute(query).mappings():
                stored = row['data'].get('artifact_path') if table is snapshots else row['source_path']
                if _owned_path(stored, root) in paths:
                    shared.update(_files([(table, dict(row))], root))
    return shared


def delete_account(store, user_id: str, data_dir: Path) -> dict:
    """Erase sole-owned workspaces; leave shared workspaces without a role transfer.

    A queued/running comparison, import or extraction returns 409 before touching
    any files. Storage failures return 503 and roll back database changes so the
    same authenticated account can retry (already removed files are idempotent).
    SQLite's write lock and PostgreSQL's project row locks prevent new FK-backed
    jobs/memberships from entering a workspace during the cleanup transaction.
    """
    from .store import metadata, projects, project_members, users, now

    root = Path(data_dir).resolve()
    with store.engine.connect() as conn:
        if conn.dialect.name == 'sqlite':
            conn.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            conn.begin()
        try:
            user = conn.execute(select(users).where(users.c.id == user_id).with_for_update()).mappings().first()
            if not user:
                raise AccountDeletionError('NOT_FOUND', 'Account is no longer available.', 404)
            memberships = conn.execute(select(project_members).where(project_members.c.user_id == user_id)).mappings().all()
            member_pids = {row['project_id'] for row in memberships}
            if member_pids:
                conn.execute(select(projects).where(projects.c.id.in_(member_pids)).order_by(projects.c.id).with_for_update()).all()
                # Lock existing memberships too: updates do not acquire a project FK lock.
                conn.execute(select(project_members).where(project_members.c.project_id.in_(member_pids))
                             .order_by(project_members.c.id).with_for_update()).all()
            owned = {row['project_id'] for row in memberships if row['role'] == 'owner' and row['revoked_at'] is None}
            other_members = conn.execute(select(project_members.c.project_id).where(
                project_members.c.project_id.in_(owned), project_members.c.user_id != user_id,
                project_members.c.revoked_at.is_(None))).scalars().all() if owned else []
            # Preserve even an inactive user's unrevoked membership conservatively.
            erase = owned - set(other_members)
            tables = list(metadata.sorted_tables)
            conditions = {}
            for table in tables:
                if table is projects:
                    conditions[table] = table.c.id.in_(erase)
                elif 'project_id' in table.c:
                    conditions[table] = table.c.project_id.in_(erase)
                else:
                    # Some child tables (definition_versions) have no project_id.
                    inherited = [fk.parent.in_(select(fk.column).where(conditions[fk.column.table]))
                                 for fk in table.foreign_keys if fk.column.table in conditions]
                    if inherited:
                        conditions[table] = or_(*inherited)
            owned_rows = []
            for table, condition in conditions.items():
                owned_rows.extend((table, dict(row)) for row in conn.execute(select(table).where(condition).with_for_update()).mappings())
            for table, row in owned_rows:
                if (table.name in {'comparison_runs', 'snapshot_imports', 'postgres_extractions'} and
                        (row['state'] in {'queued', 'running'} or _cancel_is_draining(row))):
                    raise AccountDeletionError('ACCOUNT_DELETION_BUSY', 'Wait for workspace jobs to finish before deleting your account.', 409)
            paths = _files(owned_rows, root)
            paths.difference_update(_shared_files(conn, erase, paths, root))
            backup_dir = root / 'backups'
            if backup_dir.is_dir():
                for path in backup_dir.iterdir():
                    if any(re.fullmatch(rf'backup-{re.escape(pid)}-[0-9a-f]{{8}}\.tar\.gz', path.name) for pid in erase):
                        safe = _owned_path(str(path), root)
                        if safe:
                            paths.add(safe)
            # Child-first deletion works with FK enforcement on SQLite and PostgreSQL.
            for table in reversed(tables):
                if table in conditions:
                    conn.execute(delete(table).where(conditions[table]))
            # Preserve shared historical records with required actor FKs.
            references = [(table, fk.parent) for table in tables for fk in table.foreign_keys
                          if fk.column.table is users and table in conditions and table is not project_members]
            needs_actor = any(conn.execute(select(column).where(column == user_id).limit(1)).first()
                              for _, column in references)
            if needs_actor:
                actor = f'deleted-account-{uuid4().hex}'
                conn.execute(insert(users).values(id=actor, email=f'{actor}@deleted.invalid', name='Deleted account',
                    api_key_hash=hashlib.sha256(uuid4().bytes).hexdigest(), is_active=False, created_at=now()))
                for table, column in references:
                    conn.execute(update(table).where(column == user_id).values({column.name: actor}))
            # Sessions, identities and memberships have no independent shared history.
            for table in reversed(tables):
                if table in conditions and table is not project_members:
                    continue
                for fk in table.foreign_keys:
                    if fk.column.table is users:
                        conn.execute(delete(table).where(fk.parent == user_id))
            conn.execute(delete(users).where(users.c.id == user_id))
            try:
                for path in sorted(paths):
                    # Check derived paths too; never follow a symlink outside root.
                    if _owned_path(str(path), root) == path:
                        durable.remove(path, sidecar=False)
            except Exception as exc:
                raise AccountDeletionError('ACCOUNT_DELETION_STORAGE_FAILED',
                    'Storage cleanup could not finish. Retry account deletion to complete it.', 503) from exc
            conn.commit()
            return {'deleted': True, 'deleted_project_ids': sorted(erase),
                    'preserved_project_ids': sorted(member_pids - erase)}
        except BaseException:
            conn.rollback()
            raise
