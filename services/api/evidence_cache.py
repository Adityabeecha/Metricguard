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
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Callable

MAX_ENTRIES = 16
MAX_CACHED_ROWS = 200_000
TOP_CHANGES = 20
DISK_INDEX_BYTES = 32 * 1024 * 1024


class _Entry:
    __slots__ = ('size', 'mtime_ns', 'offsets', 'line_hashes', 'summary', 'keys', 'magnitudes', 'categories', 'views', 'disk')

    def __init__(self, size, mtime_ns, offsets, line_hashes, summary, keys, magnitudes, categories=None):
        self.size, self.mtime_ns, self.offsets, self.line_hashes, self.summary = size, mtime_ns, offsets, line_hashes, summary
        self.keys, self.magnitudes = keys, magnitudes
        self.categories = categories or []
        self.views: dict[tuple, list[int]] = {}
        self.disk = None


# How a record moved between the two calculations. Two-load runs carry their own label; every other
# run is classified from its inclusion flags. Unchanged records have no category.
CATEGORY_ORDER = ('excluded', 'added', 'value_changed', 'removed', 'entered_filter', 'left_filter')
TWO_LOAD_CATEGORIES = {'added', 'removed', 'value_changed', 'entered_filter', 'left_filter'}


def category_of(row: dict) -> str | None:
    if _magnitude(row.get('delta')) == 0:
        return None
    label = row.get('change')
    if label is not None:
        return label if label in TWO_LOAD_CATEGORIES else None
    current, proposed = bool(row.get('baseline_included')), bool(row.get('target_included'))
    if current and not proposed:
        return 'excluded'
    if proposed and not current:
        return 'added'
    return 'value_changed'


class CategoryTotals:
    """Exact record counts and summed changes per category, built while the evidence is indexed."""

    def __init__(self):
        self.records: dict[str, int] = {}
        self.change: dict[str, Decimal] = {}

    def add(self, category, delta) -> None:
        if category is None:
            return
        self.records[category] = self.records.get(category, 0) + 1
        with localcontext() as exact:
            exact.prec = 100
            self.change[category] = self.change.get(category, Decimal(0)) + _dec(delta)

    def public(self) -> list[dict]:
        return [{'key': key, 'records': self.records[key], 'change': str(self.change[key])}
                for key in CATEGORY_ORDER if key in self.records]


_cache: 'OrderedDict[tuple, _Entry]' = OrderedDict()
_lock = threading.Lock()


def record_key(row: dict) -> str:
    for field in ('fact_key', 'row_key', 'order_id'):
        if row.get(field) is not None:
            return str(row[field])
    return ''


def _magnitude(value: str | None) -> Decimal:
    try:
        return Decimal(value).copy_abs() if value is not None else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _index(path: Path):
    offsets: list[int] = []
    hashes: list[str] = []
    keys: list[str] = []
    magnitudes: list[Decimal] = []
    categories: list = []
    totals = CategoryTotals()
    changed = blank = 0
    top: list[tuple[Decimal, int, dict]] = []
    content_hash = hashlib.sha256()
    with path.open('rb') as source:
        position = 0
        for number, line in enumerate(iter(source.readline, b'')):
            content_hash.update(line)
            offsets.append(position)
            hashes.append(hashlib.sha256(line).hexdigest())
            position += len(line)
            row = json.loads(line)
            if row.get('baseline_null_term') or row.get('target_null_term'):
                blank += 1
            magnitude = _magnitude(row.get('delta'))
            keys.append(record_key(row))
            magnitudes.append(magnitude)
            category = category_of(row)
            categories.append(category)
            totals.add(category, row.get('delta'))
            if magnitude:
                changed += 1
                top.append((magnitude, number, row))
                if len(top) > TOP_CHANGES * 4:
                    top.sort(key=lambda t: (-t[0], t[1]))
                    del top[TOP_CHANGES:]
    top.sort(key=lambda t: (-t[0], t[1]))
    summary = {'total': len(offsets), 'changed': changed, 'blank_amount_rows': blank,
               'top_changes': [row for _, _, row in top[:TOP_CHANGES]], 'categories': totals.public()}
    return offsets, hashes, summary, keys, magnitudes, categories, content_hash.hexdigest()


_building: dict[tuple, threading.Lock] = {}


def _cached(path: Path, key: tuple) -> _Entry | None:
    stat = path.stat()
    with _lock:
        entry = _cache.get(key)
        if entry and entry.size == stat.st_size and entry.mtime_ns == stat.st_mtime_ns:
            _cache.move_to_end(key)
            return entry
    return None


def is_ready(path: Path, key: tuple) -> bool:
    """True when the artifact is verified and indexed, or its verification is already running."""
    with _lock:
        if key in _building and _building[key].locked():
            return True
    return _cached(Path(path), key) is not None


def _fresh(path: Path, key: tuple, verify_all: Callable[[], Any]) -> _Entry:
    entry = _cached(path, key)
    if entry is not None:
        return entry
    with _lock:
        building = _building.setdefault(key, threading.Lock())
    # One full verification per artifact: a concurrent request waits for it instead of repeating it.
    with building:
        entry = _cached(path, key)
        if entry is not None:
            return entry
        stat = path.stat()
        verify_all()  # full V8.3 verification; raises ValueError on any mismatch
        disk = None
        if stat.st_size > DISK_INDEX_BYTES:
            from .evidence_disk_index import DiskIndex
            disk = DiskIndex(path, record_key)
            offsets, hashes, summary, keys, magnitudes, categories, indexed_hash = disk, [], disk.summary, [], [], [], disk.digest
        else:
            offsets, hashes, summary, keys, magnitudes, categories, indexed_hash = _index(path)
        after = path.stat()
        # The index must describe the bytes whose digest was frozen by the
        # supervisor, including when the file changes between verification/indexing.
        if (indexed_hash != key[-1] or stat.st_size != after.st_size or
                stat.st_mtime_ns != after.st_mtime_ns):
            raise ValueError('EVIDENCE_INVALID')
        entry = _Entry(stat.st_size, stat.st_mtime_ns, offsets, hashes, summary, keys, magnitudes, categories)
        entry.disk = disk
        with _lock:
            _cache[key] = entry
            _cache.move_to_end(key)
            while len(_cache) > 1 and (len(_cache) > MAX_ENTRIES or
                    sum(len(e.offsets) for e in _cache.values()) > MAX_CACHED_ROWS):
                _cache.popitem(last=False)
            if len(_building) > 4 * MAX_ENTRIES:
                for stale in [k for k, lock in _building.items() if not lock.locked() and k != key]:
                    _building.pop(stale, None)
        return entry


def _drop(key: tuple) -> None:
    with _lock:
        _cache.pop(key, None)


def _view(entry: _Entry, changed_only: bool, query: str, order: str, category: str = '') -> list[int] | None:
    """Row numbers for a filtered/sorted view, or None for the plain file order."""
    query = query.strip().lower()
    if not changed_only and not query and order != 'impact' and not category:
        return None
    view_key = (changed_only, query, order, category)
    if view_key not in entry.views:
        rows = [i for i in range(len(entry.offsets))
                if (not changed_only or entry.magnitudes[i]) and (not query or query in entry.keys[i].lower())
                and (not category or entry.categories[i] == category)]
        if order == 'impact':
            rows.sort(key=lambda i: (-entry.magnitudes[i], i))
        if len(entry.views) > 8:
            entry.views.clear()
        entry.views[view_key] = rows
    return entry.views[view_key]


def page(path: Path, key: tuple, verify_all: Callable[[], Any], offset: int, limit: int,
         changed_only: bool = False, query: str = '', order: str = 'file', category: str = '') -> tuple[list[dict], int]:
    """Rows of a (optionally filtered/sorted) view and its total, served only from verified bytes."""
    path = Path(path)
    entry = _fresh(path, key, verify_all)
    if entry.disk is not None:
        positions, count = entry.disk.select(offset, limit, changed_only, query, order, category)
        rows = []
        with path.open('rb') as source:
            for position, expected in positions:
                source.seek(position)
                line = source.readline()
                if hashlib.sha256(line).hexdigest() != expected:
                    _drop(key)
                    raise ValueError('EVIDENCE_INVALID')
                rows.append(json.loads(line))
        return rows, count
    view = _view(entry, changed_only, query, order, category)
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
    expected_hashes = iter(entry.disk.hashes() if entry.disk is not None else entry.line_hashes)
    with path.open('rb') as source:
        count = 0
        for i, line in enumerate(iter(source.readline, b'')):
            if hashlib.sha256(line).hexdigest() != next(expected_hashes, None):
                _drop(key)
                raise ValueError('EVIDENCE_INVALID')
            count += 1
            yield json.loads(line)
        if count != len(entry.offsets):
            _drop(key)
            raise ValueError('EVIDENCE_INVALID')


def clear() -> None:
    with _lock:
        _cache.clear()
        _contexts.clear()


# ---------- record context and breakdowns ----------

_contexts: 'OrderedDict[str, tuple[int, int, str, dict[str, dict]]]' = OrderedDict()


def load_context(path: Path, info: dict) -> dict[str, dict]:
    """Record key -> column values from a registered context file, verified by SHA-256 and row count."""
    path = Path(path)
    stat = path.stat()
    with _lock:
        hit = _contexts.get(str(path))
        if hit and hit[0] == stat.st_size and hit[1] == stat.st_mtime_ns and hit[2] == info.get('sha256'):
            _contexts.move_to_end(str(path))
            return hit[3]
    digest = hashlib.sha256()
    values: dict[str, dict] = {}
    with path.open('rb') as source:
        for line in iter(source.readline, b''):
            digest.update(line)
            item = json.loads(line)
            values[str(item['k'])] = item['v']
    if digest.hexdigest() != info.get('sha256') or len(values) != info.get('row_count'):
        raise ValueError('CONTEXT_INVALID')
    with _lock:
        _contexts[str(path)] = (stat.st_size, stat.st_mtime_ns, info['sha256'], values)
        while len(_contexts) > 1 and (len(_contexts) > MAX_ENTRIES or
                sum(len(v[3]) for v in _contexts.values()) > MAX_CACHED_ROWS):
            _contexts.popitem(last=False)
    return values


# Group value for lookup columns of a record whose link found no lookup row: kept apart from a
# lookup row whose own value is blank, so "no matching row" is never shown as "blank".
NO_MATCH = '\u0000no-match'


def row_values(row: dict, context: dict[str, dict] | None) -> dict:
    """Column values for one evidence row: from the context file (single table) or the row itself (two tables).

    Two tables also expose each link's key as ``link.<role>`` (the main table's value that points to
    the lookup), so changes can be split by it, e.g. to see which keys are missing from a lookup.
    """
    if context is not None:
        return context.get(record_key(row), {})
    out = {f'fact.{name}': value for name, value in (row.get('fact_values') or {}).items()}
    if 'links' in row:  # several lookup tables: dimension_values is {role: {column: value}}
        for role, link in (row.get('links') or {}).items():
            out[f'link.{role}'] = (link or {}).get('foreign_key')
        for role, values in (row.get('dimension_values') or {}).items():
            matched = ((row.get('links') or {}).get(role) or {}).get('matched', True)
            for name, value in (values or {}).items():
                out[f'{role}.{name}'] = value if matched else NO_MATCH
    else:
        if 'foreign_key' in row:
            out['link.dimension'] = row.get('foreign_key')
        matched = row.get('dimension_matched', True)
        for name, value in (row.get('dimension_values') or {}).items():
            out[f'dimension.{name}'] = value if matched else NO_MATCH
    return out


def _group_value(row: dict, context: dict[str, dict] | None, column: str, bucket: str | None):
    value = row_values(row, context).get(column)
    if bucket == 'day' and isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return value


def _public_group(group: dict) -> dict:
    if group['value'] == NO_MATCH:
        return {**group, 'value': None, 'no_match': True}
    return group


def _dec(value) -> Decimal:
    try:
        return Decimal(value) if value not in (None, '') else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def ratio_effect(share: str | None, ratio: dict) -> tuple[str | None, str | None]:
    """A record's (or group's) exact effect on the ratio and its rounded display, from its scaled share."""
    from packages.metricguard_core import flat_ratio
    if share is None or ratio.get('status') != 'defined':
        return None, None
    value = flat_ratio.to_fraction(share) / flat_ratio.to_fraction(ratio['scale_divisor'])
    return flat_ratio.fraction_text(value), flat_ratio.display(value, ratio['display_places'])


def annotate_ratio(rows: list[dict], ratio: dict | None) -> list[dict]:
    """Add each ratio record's effect (exact and rounded) for display; other runs pass through."""
    if not ratio:
        return rows
    out = []
    for row in rows:
        exact, rounded = ratio_effect(row.get('delta'), ratio)
        out.append({**row, 'effect': rounded, 'effect_exact': exact})
    return out


def entity_records(path: Path, info: dict, entity: str) -> list[dict]:
    """Records carrying one distinct value, from a registered membership file (verified by SHA-256)."""
    digest = hashlib.sha256()
    found, count = [], 0
    with Path(path).open('rb') as source:
        for line in iter(source.readline, b''):
            digest.update(line)
            count += 1
            item = json.loads(line)
            if item.get('e') == entity:
                found.append({'record': item['k'], 'current_included': item['b'], 'proposed_included': item['t']})
    if digest.hexdigest() != info.get('sha256') or count != info.get('row_count'):
        raise ValueError('MEMBERS_INVALID')
    return found


def entity_records_page(path: Path, info: dict, entity: str, offset: int = 0, limit: int = 500) -> dict:
    """Hash the complete membership file while retaining only one bounded page."""
    from packages.metricguard_core.flat_distinct_evidence import MAX_MEMBER_LINE_BYTES, MAX_MEMBERS_BYTES
    if offset < 0 or not 1 <= limit <= 500:
        raise ValueError('MEMBERS_INVALID')
    digest = hashlib.sha256()
    found, count, total, size, previous = [], 0, 0, 0, None
    with Path(path).open('rb') as source:
        while line := source.readline(MAX_MEMBER_LINE_BYTES + 1):
            size += len(line)
            if len(line) > MAX_MEMBER_LINE_BYTES or size > MAX_MEMBERS_BYTES or not line.endswith(b'\n'):
                raise ValueError('MEMBERS_INVALID')
            digest.update(line)
            item = json.loads(line)
            if (not isinstance(item, dict) or set(item) != {'k', 'e', 'b', 't'} or
                    not isinstance(item['k'], str) or (previous is not None and item['k'] <= previous) or
                    type(item['b']) is not bool or type(item['t']) is not bool):
                raise ValueError('MEMBERS_INVALID')
            previous = item['k']
            count += 1
            if item['e'] == entity:
                if offset <= total < offset + limit:
                    found.append({'record': item['k'], 'current_included': item['b'], 'proposed_included': item['t']})
                total += 1
    if (digest.hexdigest() != info.get('sha256') or count != info.get('row_count') or
            size != info.get('byte_count')):
        raise ValueError('MEMBERS_INVALID')
    next_offset = offset + len(found)
    return {'items': found, 'total': total, 'offset': offset, 'limit': limit,
            'next_offset': next_offset if next_offset < total else None}


def _ratio_breakdown(path, key, verify_all, column, context, ratio, limit, bucket=None):
    from fractions import Fraction
    from packages.metricguard_core import flat_ratio
    groups: dict[Any, dict[str, Any]] = {}
    total = Fraction(0)
    places = ratio['display_places']
    for row in iter_rows(path, key, verify_all):
        value = _group_value(row, context, column, bucket)
        group = groups.setdefault(value, {'value': value, 'records': 0, 'changed_records': 0,
                                          'n0': Fraction(0), 'n1': Fraction(0), 'd0': Fraction(0), 'd1': Fraction(0),
                                          'share': Fraction(0)})
        share = flat_ratio.to_fraction(row['delta'])
        group['records'] += 1
        group['changed_records'] += 1 if share else 0
        for name, field in (('n0', 'baseline_numerator'), ('n1', 'target_numerator'),
                            ('d0', 'baseline_denominator'), ('d1', 'target_denominator')):
            group[name] += flat_ratio.to_fraction(row[field])
        group['share'] += share
        total += share
    divisor = flat_ratio.to_fraction(ratio['scale_divisor']) if ratio.get('status') == 'defined' else None

    def shown(g):
        effect = g['share'] / divisor if divisor else None
        rate = lambda n, d: flat_ratio.display(n / d, places) if d else None
        return {'value': g['value'], 'records': g['records'], 'changed_records': g['changed_records'],
                'change': flat_ratio.display(effect, places) if effect is not None else None,
                'change_exact': flat_ratio.fraction_text(effect),
                'current': rate(g['n0'], g['d0']), 'proposed': rate(g['n1'], g['d1']),
                'current_numerator': flat_ratio.fixed(g['n0'], ratio['numerator_scale']),
                'proposed_numerator': flat_ratio.fixed(g['n1'], ratio['numerator_scale']),
                'current_denominator': flat_ratio.fixed(g['d0'], ratio['denominator_scale']),
                'proposed_denominator': flat_ratio.fixed(g['d1'], ratio['denominator_scale'])}

    ordered = sorted(groups.values(), key=lambda g: (-abs(g['share']), str(g['value'])))
    rest = ordered[limit:]
    rest_share = sum((g['share'] for g in rest), Fraction(0))
    absolute = sum((abs(g['share']) for g in ordered), Fraction(0))
    return {'column': column, 'ratio': True,
            'total_records': sum(g['records'] for g in ordered), 'other_records': sum(g['records'] for g in rest),
            'other_changed_records': sum(g['changed_records'] for g in rest),
            'abs_change_total': flat_ratio.fraction_text(absolute / divisor) if divisor else None,
            'total_change': flat_ratio.display(total / divisor, places) if divisor else None,
            'total_change_exact': flat_ratio.fraction_text(total / divisor) if divisor else None,
            'groups': [_public_group(shown(g)) for g in ordered[:limit]], 'other_groups': len(rest),
            'other_change': flat_ratio.display(rest_share / divisor, places) if divisor else None}


def breakdown(path: Path, key: tuple, verify_all: Callable[[], Any], column: str,
              context: dict[str, dict] | None, limit: int = 50, ratio: dict | None = None,
              bucket: str | None = None) -> dict[str, Any]:
    """Exact split of the change by the values of one column, largest change first.

    ``bucket='day'`` groups timestamps by their date, so a split by time reads per day instead
    of per distinct timestamp.
    """
    entry = _fresh(Path(path), key, verify_all)
    cache_key = ('breakdown', column, id(context), bucket, limit)
    if cache_key in entry.views:
        return entry.views[cache_key]  # type: ignore[return-value]
    if ratio:
        result = _ratio_breakdown(path, key, verify_all, column, context, ratio, limit, bucket)
        entry.views[cache_key] = result  # type: ignore[assignment]
        return result
    groups: dict[Any, dict[str, Any]] = {}
    with localcontext() as exact:
        exact.prec = 100  # exact sums for 38-digit decimals
        total_change = Decimal(0)
        for row in iter_rows(path, key, verify_all):
            value = _group_value(row, context, column, bucket)
            group = groups.setdefault(value, {'value': value, 'records': 0, 'changed_records': 0,
                                              'current': Decimal(0), 'proposed': Decimal(0), 'change': Decimal(0)})
            change = _dec(row.get('delta'))
            group['records'] += 1
            group['changed_records'] += 1 if change else 0
            group['current'] += _dec(row.get('baseline_contribution'))
            group['proposed'] += _dec(row.get('target_contribution'))
            group['change'] += change
            total_change += change
        ordered = sorted(groups.values(), key=lambda g: (-abs(g['change']), str(g['value'])))
        rest = ordered[limit:]
        result = {
            'column': column,
            'total_change': str(total_change),
            'total_records': sum(g['records'] for g in ordered),
            'other_records': sum(g['records'] for g in rest),
            'other_changed_records': sum(g['changed_records'] for g in rest),
            'abs_change_total': str(sum((abs(g['change']) for g in ordered), Decimal(0))),
            'groups': [_public_group({**g, 'current': str(g['current']), 'proposed': str(g['proposed']), 'change': str(g['change'])})
                       for g in ordered[:limit]],
            'other_groups': len(rest),
            'other_change': str(sum((g['change'] for g in rest), Decimal(0))),
        }
    if len(entry.views) > 8:
        entry.views.clear()
    entry.views[cache_key] = result  # type: ignore[assignment]
    return result
