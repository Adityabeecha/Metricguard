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
    connection = duckdb.connect(str(snapshot_path), read_only=True)
    try:
        lookup = {str(row[0]): {c: _plain(v) for c, v in zip(columns, row[1:])}
                  for row in connection.execute(f'SELECT {quoted} FROM snapshot').fetchall()}
    finally:
        connection.close()
    digest = hashlib.sha256()
    count = size = 0
    with Path(path).open('xb') as out:
        for row in evidence:
            line = _encode(record_key(row), lookup.get(record_key(row), {}))
            if len(line) > MAX_LINE_BYTES or size + len(line) > MAX_CONTEXT_BYTES:
                raise ValueError('CONTEXT_LIMIT')
            out.write(line)
            digest.update(line)
            size += len(line)
            count += 1
    return {'sha256': digest.hexdigest(), 'row_count': count, 'byte_count': size, 'columns': columns}


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
