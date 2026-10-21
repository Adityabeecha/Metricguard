"""Record context: each evidence row's filter-column values, in evidence order.

Single-table evidence rows carry only the record key and contributions. To break a
difference down by a column, or show columns beside each key, the isolated runner
writes this separate JSONL file from the frozen snapshot it already reads. The
evidence artifact, its hash and replay identity are unchanged; this file has its own
SHA-256, is validated line-by-line against the evidence keys by the supervisor, and
is optional (older runs simply have none).

Line format: {"k": <record key>, "v": {<column>: <string|bool|null>, ...}}
Two-load runs add "b": the earlier load's values (null when the record is new). Their "v"
holds the later load's values, or the earlier load's for a record that was removed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

MAX_CONTEXT_COLUMNS = 8
MAX_VALUE_LENGTH = 256
MAX_CONTEXT_BYTES = 128 * 1024 * 1024
MAX_LINE_BYTES = 8 * 1024
CONTEXT_BATCH_ROWS = 1000

UNAVAILABLE_REASONS = {
    'CONTEXT_MISSING': 'The runner did not provide column context for this comparison.',
    'CONTEXT_INVALID': 'Column context failed integrity validation and was discarded.',
    'CONTEXT_LIMIT': 'Column context exceeded its bounded storage limit.',
    'CONTEXT_GENERATION_FAILED': 'Column context could not be generated within the runner resources.',
    'CONTEXT_PUBLISH_FAILED': 'Column context could not be published.',
}


def unavailable_context(code):
    code = code if code in UNAVAILABLE_REASONS else 'CONTEXT_GENERATION_FAILED'
    return {'status': 'unavailable', 'code': code, 'reason': UNAVAILABLE_REASONS[code]}


def record_key(row: dict) -> str:
    for field in ('fact_key', 'row_key', 'order_id'):
        if row.get(field) is not None:
            return str(row[field])
    return ''


def _plain(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    text = str(value)
    return text[:MAX_VALUE_LENGTH]


def _encode(key: str, values: dict, earlier=None, two_loads: bool = False) -> bytes:
    item = {'k': key, 'v': values, **({'b': earlier} if two_loads else {})}
    return (json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')


def _connect(snapshot_path):
    import duckdb
    return duckdb.connect(str(snapshot_path), read_only=True, config={
        'enable_external_access': 'false', 'autoinstall_known_extensions': 'false',
        'autoload_known_extensions': 'false', 'memory_limit': '64MB', 'threads': '1'})


def _write_context(index, evidence, path, columns, excluded=None):
    digest = hashlib.sha256()
    count = size = 0
    with Path(path).open('xb', buffering=1024 * 1024) as out:
        for row in evidence:
            key = record_key(row)
            stored = index.execute('SELECT v FROM context WHERE k=?', (key,)).fetchone()
            if stored is None or stored[0] is None:
                raise ValueError('CONTEXT_INVALID')
            values = json.loads(stored[0])
            line = _encode(key, {c: values[c] for c in columns if c in values})
            if len(line) > MAX_LINE_BYTES or size + len(line) > MAX_CONTEXT_BYTES:
                raise ValueError('CONTEXT_LIMIT')
            out.write(line)
            digest.update(line)
            size += len(line)
            count += 1
    info = {'sha256': digest.hexdigest(), 'row_count': count, 'byte_count': size, 'columns': columns}
    if excluded is not None:
        info['excluded_columns'] = excluded
    return info


def write_flat_context(snapshot_path: Path, contract: dict, evidence, path: Path) -> dict:
    """Runner side: read the frozen snapshot's filter columns and write them in evidence order."""
    from packages.metricguard_core.disk_evidence import bounded_database

    columns = [c for c in contract.get('categories', [])][:MAX_CONTEXT_COLUMNS]
    key_column = contract['row_key']
    quoted = ', '.join('"' + c.replace('"', '""') + '"' for c in [key_column, *columns])
    # Keep only the evidence records (on large snapshots the evidence lists changed records only).
    # Identical definitions on a large snapshot have no changed-record evidence.
    # They must not scan millions of records solely to produce an empty sidecar.
    with bounded_database() as index:
        index.execute('CREATE TABLE context(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID')
        index.executemany('INSERT OR IGNORE INTO context(k) VALUES (?)', ((record_key(row),) for row in evidence))
        wanted = index.execute('SELECT COUNT(*) FROM context').fetchone()[0]
        found = 0
        if wanted:
            connection = _connect(snapshot_path)
            try:
                cursor = connection.execute(f'SELECT {quoted} FROM snapshot')
                while batch := cursor.fetchmany(CONTEXT_BATCH_ROWS):
                    keys = [str(row[0]) for row in batch]
                    needed = {row[0] for row in index.execute(
                        'SELECT k FROM context WHERE v IS NULL AND k IN (' + ','.join('?' for _ in keys) + ')', keys)}
                    updates = [(json.dumps({c: _plain(v) for c, v in zip(columns, row[1:])}, ensure_ascii=False), str(row[0]))
                               for row in batch if str(row[0]) in needed]
                    index.executemany('UPDATE context SET v=? WHERE k=?', updates)
                    found += len(updates)
                    if found == wanted:
                        break
            finally:
                connection.close()
            if found != wanted:
                raise ValueError('CONTEXT_INVALID')
        return _write_context(index, evidence, path, columns)


def _collect(snapshot_path, key_column: str, columns: list[str], keys: set[str]) -> dict[str, dict]:
    """Filter-column values of the given records, read from one frozen snapshot."""
    quoted = ', '.join('"' + c.replace('"', '""') + '"' for c in [key_column, *columns])
    found: dict[str, dict] = {}
    if not keys:
        return found
    connection = _connect(snapshot_path)
    try:
        cursor = connection.execute(f'SELECT {quoted} FROM snapshot')
        while batch := cursor.fetchmany(CONTEXT_BATCH_ROWS):
            for row in batch:
                key = str(row[0])
                if key in keys:
                    found[key] = {c: _plain(v) for c, v in zip(columns, row[1:])}
            if len(found) == len(keys):
                break
    finally:
        connection.close()
    return found


def write_change_context(baseline_path: Path, target_path: Path, contract: dict, evidence: list[dict], path: Path) -> dict:
    """Runner side, two-load runs: each record's values in the later load (earlier if removed), plus the earlier values.

    Two-load runs are bounded (100,000 records per load), so both lookups fit in memory.
    """
    columns = [c for c in contract.get('categories', [])][:MAX_CONTEXT_COLUMNS]
    target_manifest = Path(target_path).with_name(Path(target_path).name + '.manifest.json')
    if target_manifest.is_file():
        # Two separate sources may differ in their other filter columns: keep the shared ones.
        shared = set(json.loads(target_manifest.read_text(encoding='utf-8')).get('contract', {}).get('categories', []))
        columns = [c for c in columns if c in shared]
    key_column = contract['row_key']
    earlier = _collect(baseline_path, key_column, columns, {record_key(r) for r in evidence if r.get('baseline_present') is not False})
    later = _collect(target_path, key_column, columns, {record_key(r) for r in evidence if r.get('target_present') is not False})
    digest = hashlib.sha256()
    count = size = 0
    with Path(path).open('xb', buffering=1024 * 1024) as out:
        for row in evidence:
            key = record_key(row)
            before = earlier.get(key) if row.get('baseline_present') is not False else None
            after = later.get(key) if row.get('target_present') is not False else None
            if (row.get('baseline_present') is not False and before is None) or (row.get('target_present') is not False and after is None):
                raise ValueError('CONTEXT_INVALID')
            line = _encode(key, after if after is not None else before, before, two_loads=True)
            if len(line) > MAX_LINE_BYTES or size + len(line) > MAX_CONTEXT_BYTES:
                raise ValueError('CONTEXT_LIMIT')
            out.write(line)
            digest.update(line)
            size += len(line)
            count += 1
    return {'sha256': digest.hexdigest(), 'row_count': count, 'byte_count': size, 'columns': columns}


def write_entity_context(snapshot_path: Path, contract: dict, entity_column: str, evidence: list[dict], path: Path) -> dict:
    """Runner side, COUNT(DISTINCT) runs: one line per entity (the evidence unit).

    A column is included only when it has one value per entity across the whole
    snapshot; otherwise a per-entity breakdown would count an entity in two groups
    and the groups would not add up. Such columns are listed in `excluded_columns`.
    """
    from packages.metricguard_core.disk_evidence import bounded_database
    from packages.metricguard_core.flat_distinct import canonical

    columns = [c for c in contract.get('categories', []) if c != entity_column][:MAX_CONTEXT_COLUMNS]
    quoted = ', '.join('"' + c.replace('"', '""') + '"' for c in [entity_column, *columns])
    with bounded_database() as index:
        index.execute('CREATE TABLE context(k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID')
        excluded = {}
        connection = _connect(snapshot_path)
        try:
            cursor = connection.execute(f'SELECT {quoted} FROM snapshot')
            while batch := cursor.fetchmany(CONTEXT_BATCH_ROWS):
                # Only this page of entities is materialized. Whole-snapshot functional
                # dependency checks retain their state in the bounded disk index.
                keys = [canonical(row[0]) for row in batch if row[0] is not None]
                if not keys:
                    continue
                seen = {key: json.loads(value) for key, value in index.execute(
                    'SELECT k,v FROM context WHERE k IN (' + ','.join('?' for _ in keys) + ')', keys)}
                for row in batch:
                    key = canonical(row[0])
                    if key is None:
                        continue
                    values = {c: _plain(v) for c, v in zip(columns, row[1:])}
                    previous = seen.get(key)
                    if previous is not None:
                        for c in columns:
                            if previous[c] != values[c]:
                                excluded[c] = 'multi_valued_per_entity'
                    seen[key] = values
                index.executemany('INSERT OR REPLACE INTO context(k,v) VALUES (?,?)',
                                  ((key, json.dumps(values, ensure_ascii=False)) for key, values in seen.items()))
        finally:
            connection.close()
        return _write_context(index, evidence, path, [c for c in columns if c not in excluded], excluded)


def validate_context(context_path: Path, evidence_path: Path, allowed_columns: list[str]) -> dict:
    """Supervisor side: every line matches the evidence key in the same position and holds only allowed scalar values."""
    allowed = set(allowed_columns)
    digest = hashlib.sha256()
    count = size = 0
    columns: list[str] = []
    with Path(context_path).open('rb') as ctx, Path(evidence_path).open('rb') as ev:
        while line := ctx.readline(MAX_LINE_BYTES + 1):
            size += len(line)
            if len(line) > MAX_LINE_BYTES or size > MAX_CONTEXT_BYTES or not line.endswith(b'\n'):
                raise ValueError('CONTEXT_INVALID')
            evidence_line = ev.readline(64 * 1024 + 1)
            if not evidence_line or len(evidence_line) > 64 * 1024 or not evidence_line.endswith(b'\n'):
                raise ValueError('CONTEXT_INVALID')
            item = json.loads(line)
            if set(item) not in ({'k', 'v'}, {'k', 'v', 'b'}) or item['k'] != record_key(json.loads(evidence_line)) or not isinstance(item['v'], dict):
                raise ValueError('CONTEXT_INVALID')
            earlier = item.get('b')
            if earlier is not None and (not isinstance(earlier, dict) or set(earlier) - set(item['v']) or set(item['v']) - set(earlier)):
                raise ValueError('CONTEXT_INVALID')
            for name, value in [*item['v'].items(), *((earlier or {}).items())]:
                if name not in allowed or not (value is None or isinstance(value, bool) or (isinstance(value, str) and len(value) <= MAX_VALUE_LENGTH)):
                    raise ValueError('CONTEXT_INVALID')
                if name not in columns:
                    columns.append(name)
            digest.update(line)
            count += 1
        if ev.readline(1):
            raise ValueError('CONTEXT_INVALID')  # fewer context lines than evidence rows
    return {'sha256': digest.hexdigest(), 'row_count': count, 'byte_count': size,
            'columns': [c for c in allowed_columns if c in columns] or list(allowed_columns)}
