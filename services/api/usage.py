"""Read-only workspace usage; no bucket listing or analytical work."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import func, select
from packages.metricguard_core.free_tier import configured_limit
from services import durable
from .store import records, runs, snapshots, snapshot_imports


def workspace_usage(store, pid, data_dir):
    root = Path(data_dir).resolve()
    paths = set()
    known = {}
    def add(value, size=0):
        if not value:
            return
        path = Path(value).resolve()
        if path.is_relative_to(root):
            paths.add(path)
            known[str(path)] = max(known.get(str(path), 0), size or 0)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with store.engine.connect() as conn:
        daily = conn.execute(select(func.count()).select_from(runs).where(
            runs.c.project_id == pid, runs.c.created_at >= cutoff)).scalar_one()
        for data in conn.execute(select(snapshots.c.data).where(snapshots.c.project_id == pid)).scalars():
            add(data.get('artifact_path'))
            if data.get('artifact_path'):
                add(str(data['artifact_path']) + durable.SIDECAR)
        for path in conn.execute(select(snapshot_imports.c.source_path).where(snapshot_imports.c.project_id == pid)).scalars():
            add(path)
        expired = set(conn.execute(select(records.c.data['run_id'].as_string()).where(
            records.c.project_id == pid, records.c.kind == 'evidence_expired')).scalars())
        for row in conn.execute(select(runs.c.id, runs.c.attempt, runs.c.result).where(runs.c.project_id == pid)):
            if row.id in expired:
                continue
            for attempt in range(1, row.attempt + 1):
                for suffix in ('.jsonl', '.members.jsonl', '.context.jsonl'):
                    size = (((row.result or {}).get('evidence_artifact') or {}).get('byte_count', 0)
                            if attempt == row.attempt and suffix == '.jsonl' else 0)
                    add(root / 'evidence' / f'{row.id}-{attempt}{suffix}', size)
    sizes = durable.storage().stored_sizes(paths)
    return {'max_rows': configured_limit('METRICGUARD_MAX_ROWS'),
            'max_upload_mb': configured_limit('METRICGUARD_MAX_UPLOAD_MB'),
            'max_runs_per_day': configured_limit('METRICGUARD_MAX_RUNS_PER_DAY'),
            'runs_last_24h': daily,
            'storage_bytes': sum(max(sizes.get(str(p), 0), known[str(p)]) for p in paths),
            'storage_basis': 'Uncompressed dataset, upload and evidence bytes referenced by this workspace; shared files count in each workspace.'}
