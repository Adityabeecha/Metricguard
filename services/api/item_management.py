"""Rename and delete datasets and saved cases from their lists.

Deleting is conservative: a dataset that a saved case, a metric approval or a dbt/Power BI
report relies on is refused (with the cases named), so sealed evidence is never orphaned.
Unsaved comparisons of the dataset, their drafts and their evidence files go with it.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, or_, select, update

from services import durable
from .account_deletion import _owned_path
from .store import DomainError, now


def _clean_name(value, label: str) -> str:
    name = (value or '').strip() if isinstance(value, str) else ''
    if not 1 <= len(name) <= 160:
        raise DomainError('INVALID_METADATA', f'A {label} name of 1–160 characters is required.')
    return name


def rename_snapshot(store, snapshot_id: str, name) -> dict:
    from .store import snapshots
    name = _clean_name(name, 'dataset')
    with store.engine.begin() as conn:
        row = conn.execute(select(snapshots).where(snapshots.c.id == snapshot_id).with_for_update()).mappings().first()
        if not row:
            raise DomainError('NOT_FOUND', 'Dataset not found.', 404)
        conn.execute(update(snapshots).where(snapshots.c.id == snapshot_id).values(data={**row['data'], 'name': name}))
    return store.snapshot(snapshot_id)


def rename_investigation(store, investigation_id: str, title=None, decision=None, editor: str | None = None) -> dict:
    """A case's title and conclusion are labels, not part of its seal; earlier versions stay on record."""
    from .store import records
    if title is None and decision is None:
        raise DomainError('INVALID_METADATA', 'Send a new title or conclusion.')
    title = _clean_name(title, 'case') if title is not None else None
    if decision is not None:
        decision = decision.strip() if isinstance(decision, str) else ''
        if not 1 <= len(decision) <= 4000:
            raise DomainError('INVALID_METADATA', 'A conclusion of 1–4000 characters is required.')
    with store.engine.begin() as conn:
        row = conn.execute(select(records).where(records.c.id == investigation_id, records.c.kind == 'investigation')
                           .with_for_update()).mappings().first()
        if not row:
            raise DomainError('NOT_FOUND', 'Case not found.', 404)
        data = dict(row['data'])
        changed = False
        if title is not None and data.get('title') != title:
            history = list(data.get('title_history') or [])
            history.append({'title': data.get('title'), 'renamed_at': now()})
            data.update(title=title, title_history=history[-20:])
            changed = True
        if decision is not None and data.get('decision') != decision:
            history = list(data.get('decision_history') or [])
            history.append({'decision': data.get('decision'), 'replaced_at': now(), 'replaced_by': editor})
            data.update(decision=decision, decision_history=history[-20:])
            changed = True
        if changed:
            data['updated_at'] = now()
            conn.execute(update(records).where(records.c.id == investigation_id).values(data=data))
    return store.investigation(investigation_id)


def delete_investigation(store, investigation_id: str) -> dict:
    """Remove a saved case from the list. Its comparison run and evidence stay (other cases or replays may use them)."""
    from .store import records
    with store.engine.begin() as conn:
        removed = conn.execute(delete(records).where(records.c.id == investigation_id, records.c.kind == 'investigation')).rowcount
    if not removed:
        raise DomainError('NOT_FOUND', 'Case not found.', 404)
    return {'deleted': True, 'id': investigation_id}


def _run_files(run: dict, root: Path) -> set[Path]:
    attempts = run.get('attempt') or 0
    return {root / 'evidence' / f"{run['id']}-{attempt}{suffix}"
            for attempt in range(1, attempts + 1) for suffix in ('.jsonl', '.members.jsonl', '.context.jsonl')}


def delete_snapshot(store, snapshot_id: str, data_dir) -> dict:
    from .store import (metric_diff_reports, metric_impact_reports, metric_state_transitions, metrics, postgres_extractions,
                        records, runs, snapshot_imports, snapshots)
    root = Path(data_dir).resolve()
    with store.engine.begin() as conn:
        row = conn.execute(select(snapshots).where(snapshots.c.id == snapshot_id).with_for_update()).mappings().first()
        if not row:
            raise DomainError('NOT_FOUND', 'Dataset not found.', 404)
        pid = row['project_id']
        project_runs = [dict(r) for r in conn.execute(select(runs).where(runs.c.project_id == pid)).mappings()]
        used = [r for r in project_runs if r['snapshot_id'] == snapshot_id
                or (r.get('request') or {}).get('target_snapshot_id') == snapshot_id]
        run_ids = {r['id'] for r in used}
        if any(r['state'] in ('queued', 'running') for r in used):
            raise DomainError('DATASET_BUSY', 'A comparison on this dataset is still running. Try again when it finishes.', 409)
        if conn.execute(select(snapshot_imports.c.id).where(snapshot_imports.c.snapshot_id == snapshot_id,
                                                            snapshot_imports.c.state.in_(('queued', 'running')))).first():
            raise DomainError('DATASET_BUSY', 'This dataset is still being imported. Try again when it finishes.', 409)
        active_imports = conn.execute(select(snapshot_imports.c.data).where(
            snapshot_imports.c.project_id == pid, snapshot_imports.c.state.in_(('queued', 'running')))).scalars()
        if any(item.get('metadata', {}).get('frozen_upgrade', {}).get('snapshot_id') == snapshot_id
               for item in active_imports):
            raise DomainError('DATASET_BUSY', 'Averages and ratios are being enabled from this frozen dataset. Try again when it finishes.', 409)
        cases = [r['data'] for r in conn.execute(select(records).where(records.c.project_id == pid, records.c.kind == 'investigation')).mappings()
                 if r['data'].get('snapshot_id') == snapshot_id or r['data'].get('target_snapshot_id') == snapshot_id
                 or r['data'].get('run_id') in run_ids]
        if cases:
            titles = ', '.join(f"'{c.get('title') or 'Untitled'}'" for c in cases[:3])
            raise DomainError('DATASET_IN_USE', f"Used by the case {titles}. Delete or change that first.", 409)
        if run_ids:
            names = list(conn.execute(select(metrics.c.display_name).join(metric_state_transitions,
                metrics.c.id == metric_state_transitions.c.metric_id).where(
                metric_state_transitions.c.comparison_run_id.in_(run_ids)).distinct().limit(3)).scalars())
            if names:
                raise DomainError('DATASET_IN_USE', f"Used by the metric approval {', '.join(repr(name) for name in names)}. Delete or change that first.", 409)
        for table in (metric_impact_reports, metric_diff_reports):
            reports = list(conn.execute(select(table.c.id, table.c.data).where(table.c.snapshot_id == snapshot_id).limit(3)).mappings())
            if reports:
                names = [r['data'].get('name') or r['data'].get('title') or r['id'] for r in reports]
                raise DomainError('DATASET_IN_USE', f"Used by the report {', '.join(repr(name) for name in names)}. Delete or change that first.", 409)

        files: set[Path] = set()
        artifact = _owned_path(row['data'].get('artifact_path'), root)
        shared = artifact and conn.execute(select(snapshots.c.id).where(
            snapshots.c.id != snapshot_id, snapshots.c.data['artifact_path'].as_string() == row['data'].get('artifact_path'))).first()
        if artifact and not shared:
            files.update({artifact, artifact.with_name(artifact.name + durable.SIDECAR), artifact.with_suffix('.lineage.jsonl')})
        for run in used:
            files.update(_run_files(run, root))
        imports = [dict(r) for r in conn.execute(select(snapshot_imports).where(snapshot_imports.c.snapshot_id == snapshot_id)).mappings()]
        for item in imports:
            # Upgrades borrow the older frozen artifact; it is never a disposable upload.
            if 'frozen_upgrade' in item.get('data', {}).get('metadata', {}):
                continue
            source = _owned_path(item.get('source_path'), root)
            if source:
                files.update({source, source.with_name(source.name + durable.SIDECAR)})

        if run_ids:
            linked = [r['id'] for r in conn.execute(select(records).where(records.c.project_id == pid)).mappings()
                      if r['kind'] != 'investigation' and (r['data'].get('run_id') in run_ids or r['data'].get('comparison_run_id') in run_ids)]
            if linked:
                conn.execute(delete(records).where(records.c.id.in_(linked)))
            conn.execute(delete(runs).where(runs.c.id.in_(run_ids)))
        conn.execute(update(postgres_extractions).where(postgres_extractions.c.snapshot_id == snapshot_id).values(snapshot_id=None))
        conn.execute(delete(snapshot_imports).where(snapshot_imports.c.snapshot_id == snapshot_id))
        conn.execute(delete(snapshots).where(snapshots.c.id == snapshot_id))
        try:
            for path in sorted(files):
                if _owned_path(str(path), root) == path:
                    durable.remove(path, sidecar=False)
        except Exception as exc:
            raise DomainError('DELETE_STORAGE_FAILED', 'Storage cleanup could not finish. Try deleting the dataset again.', 503) from exc
    return {'deleted': True, 'id': snapshot_id, 'comparisons_removed': len(run_ids)}
