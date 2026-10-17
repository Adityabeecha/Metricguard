"""Fast, integrity-preserving access to verified evidence artifacts.

`verify_artifact` re-reads and re-verifies the whole JSONL file on every request.
That is the V8.3 guarantee, and the first read here still performs it unchanged.
After a successful full verification this module remembers, per artifact:

  * the byte offset of every row, so a page is read without scanning the file;
  * the SHA-256 of every row line, re-checked on every page read, so a page can
    only be served if its exact bytes are the bytes that passed full verification;
  * the file size and modification time; any change forces full re-verification;
  * a change summary (changed rows, blank-amount rows, largest changes).

Entries are kept in a small LRU and keyed by the run attempt and manifest hash.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

MAX_ENTRIES = 16
TOP_CHANGES = 20


class _Entry:
    __slots__ = ('size', 'mtime_ns', 'offsets', 'line_hashes', 'summary', 'keys', 'magnitudes', 'views')

    def __init__(self, size, mtime_ns, offsets, line_hashes, summary, keys, magnitudes):
        self.size, self.mtime_ns, self.offsets, self.line_hashes, self.summary = size, mtime_ns, offsets, line_hashes, summary
        self.keys, self.magnitudes = keys, magnitudes
        self.views: dict[tuple, list[int]] = {}


_cache: 'OrderedDict[tuple, _Entry]' = OrderedDict()
_lock = threading.Lock()


def record_key(row: dict) -> str:
    for field in ('fact_key', 'row_key', 'order_id'):
        if row.get(field) is not None:
            return str(row[field])
    return ''


def _magnitude(value: str | None) -> Decimal:
    try:
        return abs(Decimal(value)) if value is not None else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _index(path: Path):
    offsets: list[int] = []
    hashes: list[str] = []
    keys: list[str] = []
    magnitudes: list[Decimal] = []
    changed = blank = 0
    top: list[tuple[Decimal, int, dict]] = []
    with path.open('rb') as source:
        position = 0
        for number, line in enumerate(iter(source.readline, b'')):
            offsets.append(position)
            hashes.append(hashlib.sha256(line).hexdigest())
            position += len(line)
            row = json.loads(line)
            if row.get('baseline_null_term') or row.get('target_null_term'):
                blank += 1
            magnitude = _magnitude(row.get('delta'))
            keys.append(record_key(row))
            magnitudes.append(magnitude)
            if magnitude:
                changed += 1
                top.append((magnitude, number, row))
                if len(top) > TOP_CHANGES * 4:
                    top.sort(key=lambda t: (-t[0], t[1]))
                    del top[TOP_CHANGES:]
    top.sort(key=lambda t: (-t[0], t[1]))
    summary = {'total': len(offsets), 'changed': changed, 'blank_amount_rows': blank,
               'top_changes': [row for _, _, row in top[:TOP_CHANGES]]}
    return offsets, hashes, summary, keys, magnitudes


def _fresh(path: Path, key: tuple, verify_all: Callable[[], Any]) -> _Entry:
    stat = path.stat()
    with _lock:
        entry = _cache.get(key)
        if entry and entry.size == stat.st_size and entry.mtime_ns == stat.st_mtime_ns:
            _cache.move_to_end(key)
            return entry
    verify_all()  # full V8.3 verification; raises ValueError on any mismatch
    offsets, hashes, summary, keys, magnitudes = _index(path)
    entry = _Entry(stat.st_size, stat.st_mtime_ns, offsets, hashes, summary, keys, magnitudes)
    with _lock:
        _cache[key] = entry
        _cache.move_to_end(key)
        while len(_cache) > MAX_ENTRIES:
            _cache.popitem(last=False)
    return entry


def _drop(key: tuple) -> None:
    with _lock:
        _cache.pop(key, None)


def _view(entry: _Entry, changed_only: bool, query: str, order: str) -> list[int] | None:
    """Row numbers for a filtered/sorted view, or None for the plain file order."""
    query = query.strip().lower()
    if not changed_only and not query and order != 'impact':
        return None
    view_key = (changed_only, query, order)
    if view_key not in entry.views:
        rows = [i for i in range(len(entry.offsets))
                if (not changed_only or entry.magnitudes[i]) and (not query or query in entry.keys[i].lower())]
        if order == 'impact':
            rows.sort(key=lambda i: (-entry.magnitudes[i], i))
        if len(entry.views) > 8:
            entry.views.clear()
        entry.views[view_key] = rows
    return entry.views[view_key]


def page(path: Path, key: tuple, verify_all: Callable[[], Any], offset: int, limit: int,
         changed_only: bool = False, query: str = '', order: str = 'file') -> tuple[list[dict], int]:
    """Rows of a (optionally filtered/sorted) view and its total, served only from verified bytes."""
    path = Path(path)
    entry = _fresh(path, key, verify_all)
    view = _view(entry, changed_only, query, order)
    count = len(entry.offsets) if view is None else len(view)
    numbers = range(offset, min(offset + limit, count))
    wanted = numbers if view is None else [view[i] for i in numbers]
    rows: list[dict] = []
    with path.open('rb') as source:
        for i in wanted:
            source.seek(entry.offsets[i])
            line = source.readline()
            if hashlib.sha256(line).hexdigest() != entry.line_hashes[i]:
                _drop(key)
                raise ValueError('EVIDENCE_INVALID')
            rows.append(json.loads(line))
    return rows, count


def summary(path: Path, key: tuple, verify_all: Callable[[], Any]) -> dict[str, Any]:
    return _fresh(Path(path), key, verify_all).summary


def iter_rows(path: Path, key: tuple, verify_all: Callable[[], Any]):
    """Every verified row in order (for full exports); each line is re-checked."""
    path = Path(path)
    entry = _fresh(path, key, verify_all)
    with path.open('rb') as source:
        for i, line in enumerate(iter(source.readline, b'')):
            if i >= len(entry.line_hashes) or hashlib.sha256(line).hexdigest() != entry.line_hashes[i]:
                _drop(key)
                raise ValueError('EVIDENCE_INVALID')
            yield json.loads(line)


def clear() -> None:
    with _lock:
        _cache.clear()
