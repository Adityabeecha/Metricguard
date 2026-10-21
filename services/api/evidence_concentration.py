"""Where the difference concentrates across columns: single values and pairs of values.

"Where it sits" splits the change by one chosen column. This reader looks across all record
columns at once and lists the values (and two-column combinations) that hold a much larger
share of the difference than of the records, e.g. "channel = app × region = west: 41% of the
difference, 9% of records". Shares are exact fractions of the absolute record changes; the
work is bounded (MAX_COLUMNS columns, pairs only over columns with at most MAX_VALUES values).
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import combinations
from typing import Any, Callable

from . import evidence_cache

MAX_COLUMNS = 8
MAX_VALUES = 50
MIN_SHARE = Fraction(1, 10)
MIN_LIFT = Fraction(5, 4)  # at least 1.25x its share of the records, so random spread is not reported
TOP = 8


def _key(value) -> str:
    return 'null' if value is None else repr(value)


def concentration(path, key: tuple, verify_all: Callable[[], Any], columns: list[str], context,
                  buckets: dict[str, str | None], ratio: dict | None = None) -> dict:
    """Exact single-value and two-value concentrations of the absolute record changes."""
    considered = columns[:MAX_COLUMNS]
    capped = []
    if len(columns) > MAX_COLUMNS:
        capped.append(f'Only the first {MAX_COLUMNS} columns were considered.')
    singles: dict[tuple, dict] = {}
    totals_records = 0
    abs_total = Decimal(0)
    distinct: dict[str, set] = {c: set() for c in considered}
    rows_values: list[tuple[list, Decimal, Decimal]] = []
    with localcontext() as exact:
        exact.prec = 100
        for row in evidence_cache.iter_rows(path, key, verify_all):
            totals_records += 1
            values = [evidence_cache._group_value(row, context, c, buckets.get(c)) for c in considered]
            delta = evidence_cache._dec(row.get('delta'))
            magnitude = abs(delta)
            abs_total += magnitude
            for column, value in zip(considered, values):
                if len(distinct[column]) <= MAX_VALUES:
                    distinct[column].add(_key(value))
                item = singles.setdefault((column, _key(value)), {'columns': [column], 'values': [value], 'records': 0,
                                                                  'changed_records': 0, 'change': Decimal(0), 'abs': Decimal(0)})
                item['records'] += 1
                if magnitude:
                    item['changed_records'] += 1
                    item['change'] += delta
                    item['abs'] += magnitude
            rows_values.append((values, delta, magnitude))
        if not abs_total:
            return {'available': False, 'reason': 'No record changes the total, so nothing concentrates.',
                    'columns': considered, 'items': [], 'pairs': []}
        narrow = [i for i, c in enumerate(considered) if len(distinct[c]) <= MAX_VALUES]
        if len(narrow) < len(considered):
            capped.append('Columns with more than 50 different values are left out of the pairs.')
        pairs: dict[tuple, dict] = {}
        for values, delta, magnitude in rows_values:
            for i, j in combinations(narrow, 2):
                item = pairs.setdefault((i, _key(values[i]), j, _key(values[j])), {
                    'columns': [considered[i], considered[j]], 'values': [values[i], values[j]], 'records': 0,
                    'changed_records': 0, 'change': Decimal(0), 'abs': Decimal(0)})
                item['records'] += 1
                if magnitude:
                    item['changed_records'] += 1
                    item['change'] += delta
                    item['abs'] += magnitude

    def finish(items):
        out = []
        for item in items.values():
            share = Fraction(item['abs']) / Fraction(abs_total)
            record_share = Fraction(item['records'], totals_records)
            if share < MIN_SHARE or share < record_share * MIN_LIFT:
                continue
            change = str(item['change'])
            entry = {'columns': item['columns'], 'values': [None if v == evidence_cache.NO_MATCH else v for v in item['values']],
                     'no_match': [v == evidence_cache.NO_MATCH for v in item['values']],
                     'records': item['records'], 'changed_records': item['changed_records'], 'change': change,
                     'difference_share': float(share), 'record_share': float(record_share),
                     'lift': float(share / record_share) if record_share else None}
            if ratio:
                exact_effect, rounded = evidence_cache.ratio_effect(change, ratio)
                entry.update(change=rounded, change_exact=exact_effect)
            out.append((share / record_share if record_share else share, share, entry))
        out.sort(key=lambda t: (-t[0], -t[1], [str(v) for v in t[2]['values']]))
        return [entry for _, _, entry in out[:TOP]]

    return {'available': True, 'columns': considered, 'total_records': totals_records, 'abs_change_total': str(abs_total),
            'ratio': bool(ratio), 'items': finish(singles), 'pairs': finish(pairs), 'capped': capped}


def public_concentration(value: dict) -> dict:
    """Allowlisted shape for public share links."""
    keep = ('columns', 'values', 'no_match', 'records', 'changed_records', 'change', 'change_exact',
            'difference_share', 'record_share', 'lift')
    out = {k: value[k] for k in ('available', 'reason', 'columns', 'total_records', 'abs_change_total', 'ratio', 'capped') if k in value}
    for group in ('items', 'pairs'):
        out[group] = [{k: item[k] for k in keep if k in item} for item in value.get(group, [])]
    return out
