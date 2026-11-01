"""Nightly database backup to durable storage (Cloudflare R2), and restore into an empty database.

    python -m scripts.backup_db                      # back up METRICGUARD_DATABASE_URL
    python -m scripts.backup_db --restore FILE --into postgresql://...   # restore a downloaded copy

Writes backups/db/YYYY-MM-DD.jsonl.gz through the same storage settings the server uses
(METRICGUARD_STORAGE=s3 and METRICGUARD_S3_*), then deletes copies older than KEEP_DAYS.

It is a logical export of every application table (one JSON line per row), not pg_dump: the
server image has no PostgreSQL client, and a client older than Neon's server would refuse to
dump. Files (datasets, evidence) already live in R2 and are not copied. Short-lived sign-in
rows (sessions, OAuth states) are left out; people simply sign in again after a restore.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine, func, insert, select

from services import durable
from services.api.store import auth_sessions, metadata, oauth_states

KEEP_DAYS = 14
SKIPPED = {auth_sessions.name, oauth_states.name}
FORMAT = 'metricguard-db-backup/1'


def dump(engine, output: Path) -> dict[str, int]:
    """Write every table to a gzip JSON-lines file; parents come before children."""
    counts = {}
    with engine.connect() as conn, gzip.open(output, 'wt', encoding='utf-8') as out:
        out.write(json.dumps({'format': FORMAT}) + '\n')
        for table in metadata.sorted_tables:
            if table.name in SKIPPED:
                continue
            counts[table.name] = 0
            for row in conn.execute(select(table)).mappings():
                out.write(json.dumps({'table': table.name, 'row': dict(row)}, separators=(',', ':')) + '\n')
                counts[table.name] += 1
    return counts


def restore(source: Path, engine) -> dict[str, int]:
    """Load a backup into a database whose application tables are all empty."""
    metadata.create_all(engine)
    tables = {table.name: table for table in metadata.sorted_tables}
    with engine.begin() as conn:
        occupied = [t.name for t in tables.values()
                    if t.name not in SKIPPED and conn.execute(select(func.count()).select_from(t)).scalar_one()]
        if occupied:
            raise SystemExit(f'Refusing to restore: these tables already have rows: {", ".join(occupied[:5])}')
        counts, batch, current = {}, [], None
        with gzip.open(source, 'rt', encoding='utf-8') as lines:
            header = json.loads(next(lines))
            if header.get('format') != FORMAT:
                raise SystemExit('Not a MetricGuard database backup.')
            for line in lines:
                item = json.loads(line)
                if item['table'] != current and batch:
                    conn.execute(insert(tables[current]), batch)
                    batch = []
                current = item['table']
                if current not in tables:
                    raise SystemExit(f'Unknown table in backup: {current}')
                batch.append(item['row'])
                counts[current] = counts.get(current, 0) + 1
                if len(batch) >= 500:
                    conn.execute(insert(tables[current]), batch)
                    batch = []
            if batch:
                conn.execute(insert(tables[current]), batch)
    return counts


def backup(database_url: str, store: durable.Durable, today: date | None = None) -> str:
    today = today or date.today()
    if store.backend is None:
        raise SystemExit('Durable storage is not configured (set METRICGUARD_STORAGE=s3 and METRICGUARD_S3_*).')
    key = f'backups/db/{today.isoformat()}.jsonl.gz'
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with tempfile.TemporaryDirectory(prefix='metricguard-db-backup-') as folder:
            path = Path(folder) / 'backup.jsonl.gz'
            counts = dump(engine, path)
            store.backend.put(key, path)
    finally:
        engine.dispose()
    # Keys are dated, so retention needs no bucket listing: drop the week past the window.
    for age in range(KEEP_DAYS, KEEP_DAYS + 7):
        store.backend.delete(f'backups/db/{(today - timedelta(days=age)).isoformat()}.jsonl.gz')
    print(f'backup ok: {key} ({sum(counts.values()):,} rows in {len(counts)} tables)')
    return key


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--restore', type=Path, help='a downloaded backup file to load')
    parser.add_argument('--into', help='database URL to restore into (must be empty)')
    args = parser.parse_args(argv)
    if args.restore:
        if not args.into:
            parser.error('--restore needs --into DATABASE_URL')
        counts = restore(args.restore, create_engine(args.into))
        print(f'restore ok: {sum(counts.values()):,} rows in {len(counts)} tables')
        return 0
    url = os.environ.get('METRICGUARD_DATABASE_URL')
    if not url:
        print('Set METRICGUARD_DATABASE_URL.', file=sys.stderr)
        return 1
    backup(url, durable.from_environment(tempfile.gettempdir()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
