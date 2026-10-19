"""COUNT(DISTINCT column) over one flat snapshot (policy flat-distinct-v1).

The evidence unit is the distinct value (the entity, e.g. one customer), not the
record. An entity counts 1 at an endpoint when at least one included record carries
it, so each entity changes by -1, 0 or +1 and the entity changes add up exactly to
the change in the distinct count. Records whose value is NULL are never counted, as
in SQL; they are reported separately. A per-record membership list (record -> entity,
included at each endpoint) travels beside the evidence so "which records kept this
customer in" is verifiable.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlglot import exp

from .common import CoreError

DISTINCT_POLICY = 'flat-distinct-v1'


def _error(code, message):
    raise CoreError(code, message)


def is_distinct_count(node) -> bool:
    return isinstance(node, exp.Count) and isinstance(node.this, exp.Distinct)


def distinct_column(node, contract) -> str:
    """Validate COUNT(DISTINCT <registered category>) and return the column name."""
    if contract['version'] not in (3, 4, 5, 8):
        _error('unsupported_sql', 'COUNT(DISTINCT ...) is not available on subtraction or CASE datasets')
    if any(v for k, v in node.args.items() if k not in ('this', 'big_int')):
        _error('unsupported_sql', 'Unsupported COUNT(DISTINCT ...) options')
    distinct = node.this
    if any(v for k, v in distinct.args.items() if k != 'expressions') or len(distinct.expressions) != 1:
        _error('unsupported_sql', 'COUNT(DISTINCT ...) takes exactly one column')
    column = distinct.expressions[0]
    if (not isinstance(column, exp.Column) or any(v for k, v in column.args.items() if k != 'this') or
            not isinstance(column.this, exp.Identifier) or column.this.args.get('quoted')):
        _error('unsupported_sql', 'COUNT(DISTINCT ...) takes one plain registered column name')
    name = column.this.this.lower()
    if name == contract['row_key']:
        _error('unsupported_sql', 'Every record has a unique key, so COUNT(DISTINCT key) equals COUNT(*); use COUNT(*).')
    if name == contract.get('measure') or name in {c['name'] for c in contract.get('decimal_columns') or []}:
        _error('unsupported_sql', 'Counting distinct amounts is not supported; count a column such as a customer or product.')
    if name not in contract['categories']:
        _error('unsupported_sql', f'COUNT(DISTINCT {name}): {name} is not a registered column of this dataset')
    return name


def canonical(value):
    """Entity key text, matching the snapshot's canonical cell text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def endpoint(rows):
    """rows: (record key, included, entity value) -> ({entity: supporting records}, null-entity rows)."""
    support, nulls = {}, 0
    for _, included, value in rows:
        entity = canonical(value)
        if entity is None:
            nulls += included is True
            continue
        support.setdefault(entity, 0)
        if included is True:
            support[entity] += 1
    return support, nulls


def compare_endpoints(column, baseline_rows, target_rows):
    """Exact entity evidence, the per-record membership list and summary counts."""
    from .disk_evidence import DiskRows
    if isinstance(baseline_rows, DiskRows):
        return _compare_disk(column, baseline_rows, target_rows)
    b_support, b_nulls = endpoint(baseline_rows)
    t_support, t_nulls = endpoint(target_rows)
    if b_support.keys() != t_support.keys():
        raise CoreError('reconciliation_failure', 'Both endpoints must read the same snapshot')
    evidence, counts = [], {'gained': 0, 'lost': 0, 'kept': 0}
    for entity in sorted(b_support):
        b, t = b_support[entity] > 0, t_support[entity] > 0
        membership = ('included_in_both' if b and t else 'baseline_only' if b else
                      'target_only' if t else 'included_in_neither')
        counts['kept' if b and t else 'lost' if b else 'gained' if t else 'kept'] += (b or t)
        evidence.append({'order_id': entity, 'baseline_included': b, 'target_included': t,
                         'baseline_contribution': '1' if b else '0', 'target_contribution': '1' if t else '0',
                         'delta': str(int(t) - int(b)), 'baseline_null_term': False, 'target_null_term': False,
                         'membership': membership,
                         'baseline_supporting_rows': b_support[entity],
                         'target_supporting_rows': t_support[entity]})
    baseline_value = sum(1 for v in b_support.values() if v)
    target_value = sum(1 for v in t_support.values() if v)
    targets = {key: included is True for key, included, _ in target_rows}
    members = [{'k': key, 'e': canonical(value), 'b': included is True, 't': targets[key]}
               for key, included, value in baseline_rows]
    summary = {'policy': DISTINCT_POLICY, 'entity_column': column, **counts,
               'baseline_null_entity_rows': b_nulls, 'target_null_entity_rows': t_nulls}
    return baseline_value, target_value, evidence, members, summary


def _compare_disk(column, baseline_rows, target_rows):
    from itertools import zip_longest
    from .disk_evidence import DiskRows, bounded_database
    members = DiskRows(budget=baseline_rows.budget)
    evidence = DiskRows(budget=baseline_rows.budget)
    nulls = [0, 0]
    counts = {'gained': 0, 'lost': 0, 'kept': 0}
    av = bv = 0
    with bounded_database() as con:
        con.execute('CREATE TABLE support(e TEXT PRIMARY KEY, b INTEGER, t INTEGER) WITHOUT ROWID')
        for br, tr in zip_longest(baseline_rows, target_rows):
            if br is None or tr is None or br[0] != tr[0] or canonical(br[2]) != canonical(tr[2]):
                raise CoreError('reconciliation_failure', 'Distinct endpoints must read the same ordered snapshot')
            entity, b, t = canonical(br[2]), br[1] is True, tr[1] is True
            members.append({'k': br[0], 'e': entity, 'b': b, 't': t})
            if entity is None:
                nulls[0] += b
                nulls[1] += t
            else:
                con.execute('INSERT INTO support VALUES (?, ?, ?) ON CONFLICT(e) DO UPDATE '
                            'SET b=b+excluded.b, t=t+excluded.t', (entity, b, t))
        for entity, bc, tc in con.execute('SELECT e,b,t FROM support ORDER BY e'):
            b, t = bc > 0, tc > 0
            av += b
            bv += t
            counts['gained'] += t and not b
            counts['lost'] += b and not t
            counts['kept'] += b and t
            evidence.append({'order_id': entity, 'baseline_included': b, 'target_included': t,
                             'baseline_contribution': '1' if b else '0', 'target_contribution': '1' if t else '0',
                             'delta': str(int(t)-int(b)), 'baseline_null_term': False, 'target_null_term': False,
                             'membership': 'included_in_both' if b and t else 'baseline_only' if b else
                             'target_only' if t else 'included_in_neither',
                             'baseline_supporting_rows': bc, 'target_supporting_rows': tc})
    return av, bv, evidence, members, {'policy': DISTINCT_POLICY, 'entity_column': column, **counts,
                                     'baseline_null_entity_rows': nulls[0], 'target_null_entity_rows': nulls[1]}


def functional_columns(rows, columns):
    """Columns with exactly one value per entity across the whole snapshot.

    rows: (entity, {column: value}) for every record with a non-NULL entity.
    Returns ({entity: {column: value}} for functional columns, {column: reason} for the rest).
    """
    seen: dict[str, dict] = {}
    excluded = {}
    for entity, values in rows:
        current = seen.setdefault(entity, {})
        for column in columns:
            if column in excluded:
                continue
            value = values.get(column)
            if column in current and current[column] != value:
                excluded[column] = 'multi_valued_per_entity'
            current[column] = value
    kept = [c for c in columns if c not in excluded]
    return {entity: {c: v[c] for c in kept if c in v} for entity, v in seen.items()}, excluded
