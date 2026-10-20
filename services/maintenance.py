"""Bounded worker maintenance. Admission limits never change frozen run identities."""
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import threading
import time
from uuid import uuid4

from sqlalchemy import insert, select, update
from packages.metricguard_core.free_tier import configured_limit
from services import durable
from services.api.store import projects, records, runs, now

LOG = logging.getLogger(__name__)
EXPIRED_MESSAGE = 'Detailed records expired \u2014 rerun to see them'
_clock = {}
_guard = threading.Lock()


def expire_evidence(store, data_dir):
    days = configured_limit('METRICGUARD_UNSAVED_EVIDENCE_DAYS')
    if days is None:
        return 0
    root = Path(data_dir).resolve()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    count = 0
    # Read only ids initially; handle one workspace/run at a time, without
    # materializing evidence or the database's complete result history.
    with store.engine.connect() as conn:
        candidates = list(conn.execute(select(runs.c.id, runs.c.project_id).where(
            runs.c.state == 'succeeded', runs.c.completed_at < cutoff)))
    for rid, pid in candidates:
        try:
            with store.engine.begin() as conn:
                # Saving cases and admission take the same lock, so a save
                # cannot enter between the reference check and expiration.
                conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
                run = conn.execute(select(runs).where(runs.c.id == rid,
                    runs.c.state == 'succeeded', runs.c.completed_at < cutoff).with_for_update()).mappings().first()
                if not run:
                    continue
                existing = conn.execute(select(records.c.id).where(records.c.kind == 'evidence_expired',
                    records.c.data['run_id'].as_string() == rid)).first()
                saved = conn.execute(select(records.c.id).where(records.c.project_id == pid,
                    records.c.kind.in_(['investigation', 'investigation_draft']),
                    (records.c.data['run_id'].as_string() == rid) |
                    (records.c.data['comparison_run_id'].as_string() == rid))).first()
                if existing or saved:
                    continue
                # Only server-constructed evidence names, including old attempts.
                if not all(c.isalnum() or c in '_-' for c in rid):
                    continue
                for attempt in range(1, run['attempt'] + 1):
                    for suffix in ('.jsonl', '.context.jsonl', '.members.jsonl'):
                        durable.remove(root / 'evidence' / f'{rid}-{attempt}{suffix}', sidecar=False)
                expired_at = now()
                conn.execute(insert(records).values(id=str(uuid4()), project_id=pid,
                    kind='evidence_expired', created_at=expired_at,
                    data={'run_id': rid, 'attempt': run['attempt'], 'expired_at': expired_at}))
                count += 1
        except Exception:
            LOG.warning('Evidence retention failed for run %s; cleanup will retry', rid, exc_info=True)
    return count


def maintain(store, data_dir):
    """At most once/minute cache sweep and once/hour retention per worker process."""
    if not (configured_limit('METRICGUARD_CACHE_MAX_MB') or configured_limit('METRICGUARD_UNSAVED_EVIDENCE_DAYS')):
        return
    moment = time.monotonic()
    identity = (str(store.engine.url), str(Path(data_dir).resolve()))
    due = []
    with _guard:
        for name, interval, env in [('cache', 60, 'METRICGUARD_CACHE_MAX_MB'),
                                     ('retention', 3600, 'METRICGUARD_UNSAVED_EVIDENCE_DAYS')]:
            if configured_limit(env) is None:
                continue
            key = (*identity, name)
            if moment - _clock.get(key, float('-inf')) >= interval:
                _clock[key] = moment
                due.append(name)
    for name in due:
        try:
            if name == 'cache':
                durable.cleanup_cache()
            else:
                expire_evidence(store, data_dir)
        except Exception:
            LOG.warning('%s maintenance failed; retry on the next scheduled sweep', name, exc_info=True)
