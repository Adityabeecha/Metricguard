"""Record context: each evidence row's filter-column values, in evidence order.

Single-table evidence rows carry only the record key and contributions. To break a
difference down by a column, or show columns beside each key, the isolated runner
writes this separate JSONL file from the frozen snapshot it already reads. The
evidence artifact, its hash and replay identity are unchanged; this file has its own
SHA-256, is validated line-by-line against the evidence keys by the supervisor, and
is optional (older runs simply have none).

Line format: {"k": <record key>, "v": {<column>: <string|bool|null>, ...}}
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


def _encode(key: str, values: dict) -> bytes:
    return (json.dumps({'k': key, 'v': values}, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')


def write_flat_context(snapshot_path: Path, contract: dict, evidence: list[dict], path: Path) -> dict:
    """Runner side: read the frozen snapshot's filter columns and write them in evidence order."""
    import duckdb

    columns = [c for c in contract.get('categories', [])][:MAX_CONTEXT_COLUMNS]
    key_column = contract['row_key']
    quoted = ', '.join('"' + c.replace('"', '""') + '"' for c in [key_column, *columns])
    # Keep only the evidence records (on large snapshots the evidence lists changed records only).
    wanted = {record_key(row) for row in evidence}
    lookup = {}
    # Identical definitions on a large snapshot have no changed-record evidence.
    # They must not scan millions of records solely to produce an empty sidecar.
    if wanted:
        connection = duckdb.connect(str(snapshot_path), read_only=True, config={
            'enable_external_access': 'false', 'autoinstall_known_extensions': 'false',
            'autoload_known_extensions': 'false', 'memory_limit': '512MB', 'threads': '1'})
        try:
            cursor = connection.execute(f'SELECT {quoted} FROM snapshot')
            while batch := cursor.fetchmany(10_000):
                for row in batch:
                    if str(row[0]) in wanted:
                        lookup[str(row[0])] = {c: _plain(v) for c, v in zip(columns, row[1:])}
                if len(lookup) == len(wanted):
                    break
        finally:
            connection.close()
        if len(lookup) != len(wanted):
            raise ValueError('CONTEXT_INVALID')
    digest = hashlib.sha256()
    count = size = 0
    with Path(path).open('xb', buffering=4 * 1024 * 1024) as out:
        for row in evidence:
            line = _encode(record_key(row), lookup.get(record_key(row), {}))
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
    import duckdb
    from packages.metricguard_core.flat_distinct import canonical, functional_columns

    columns = [c for c in contract.get('categories', []) if c != entity_column][:MAX_CONTEXT_COLUMNS]
    quoted = ', '.join('"' + c.replace('"', '""') + '"' for c in [entity_column, *columns])
    connection = duckdb.connect(str(snapshot_path), read_only=True)
    try:
        records = [(canonical(row[0]), {c: _plain(v) for c, v in zip(columns, row[1:])})
                   for row in connection.execute(f'SELECT {quoted} FROM snapshot').fetchall() if row[0] is not None]
    finally:
        connection.close()
    lookup, excluded = functional_columns(records, columns)
    digest = hashlib.sha256()
    count = size = 0
    with Path(path).open('xb', buffering=4 * 1024 * 1024) as out:
        for row in evidence:
            line = _encode(record_key(row), lookup.get(record_key(row), {}))
            if len(line) > MAX_LINE_BYTES or size + len(line) > MAX_CONTEXT_BYTES:
                raise ValueError('CONTEXT_LIMIT')
            out.write(line)
            digest.update(line)
            size += len(line)
            count += 1
    return {'sha256': digest.hexdigest(), 'row_count': count, 'byte_count': size,
            'columns': [c for c in columns if c not in excluded], 'excluded_columns': excluded}


def validate_context(context_path: Path, evidence_path: Path, allowed_columns: list[str]) -> dict:
    """Supervisor side: every line matches the evidence key in the same position and holds only allowed scalar values."""
    allowed = set(allowed_columns)
    digest = hashlib.sha256()
    count = size = 0
    columns: list[str] = []
    with Path(context_path).open('rb') as ctx, Path(evidence_path).open('rb') as ev:
        for line in iter(ctx.readline, b''):
            size += len(line)
            if len(line) > MAX_LINE_BYTES or size > MAX_CONTEXT_BYTES or not line.endswith(b'\n'):
                raise ValueError('CONTEXT_INVALID')
            evidence_line = ev.readline()
            if not evidence_line:
                raise ValueError('CONTEXT_INVALID')
            item = json.loads(line)
            if set(item) != {'k', 'v'} or item['k'] != record_key(json.loads(evidence_line)) or not isinstance(item['v'], dict):
                raise ValueError('CONTEXT_INVALID')
            for name, value in item['v'].items():
                if name not in allowed or not (value is None or isinstance(value, bool) or (isinstance(value, str) and len(value) <= MAX_VALUE_LENGTH)):
                    raise ValueError('CONTEXT_INVALID')
                if name not in columns:
                    columns.append(name)
            digest.update(line)
            count += 1
        if ev.readline():
            raise ValueError('CONTEXT_INVALID')  # fewer context lines than evidence rows
    return {'sha256': digest.hexdigest(), 'row_count': count, 'byte_count': size,
            'columns': [c for c in allowed_columns if c in columns] or list(allowed_columns)}
