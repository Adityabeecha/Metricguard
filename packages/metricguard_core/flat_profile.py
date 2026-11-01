"""Read-only look-ups on a frozen flat snapshot that help an analyst write filters.

Neither function changes evidence or results: they read the immutable snapshot with the
same hardened read-only connection as the comparison engine. ``match_count`` only runs a
calculation's WHERE clause after ``parse_flat`` has validated it into typed IR, and the
predicate is compiled from those typed leaves, exactly as a comparison would.
"""
import threading

from .common import CoreError
from .flat import _connect, _predicates_sql, _quote, parse_flat, validate_contract
from .flat_boolean import compile_boolean

MAX_VALUES = 200
# The hardened connection locks DuckDB's configuration, and DuckDB shares one instance per file
# between open connections in a process, so a second concurrent connection to the same snapshot
# fails. Look-ups are short (well under a second on a million rows), so they run one at a time.
_lookup_lock = threading.Lock()


def column_values(database, contract, column, limit=MAX_VALUES):
    """The most common values of a declared filter column, with how many records hold each.

    ``complete`` is true when every distinct value is listed, so a value missing from the
    list is known not to occur in the snapshot.
    """
    contract = validate_contract(contract)
    if column not in contract['categories']:
        raise CoreError('unknown_column', f'{column} is not a declared filter column of this dataset')
    limit = max(1, min(int(limit), MAX_VALUES))
    with _lookup_lock:
        return _column_values(database, column, limit)


def _column_values(database, column, limit):
    con = _connect(database)
    try:
        q = _quote(column)
        distinct, blanks = con.execute(
            f'SELECT COUNT(DISTINCT {q}), COUNT(*) FILTER (WHERE {q} IS NULL) FROM snapshot').fetchone()
        rows = con.execute(
            f'SELECT {q} AS v, COUNT(*) AS n FROM snapshot WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY n DESC, 1 LIMIT ?',
            [limit]).fetchall()
    finally:
        con.close()
    return {'column': column, 'distinct_count': int(distinct), 'blank_count': int(blanks),
            'complete': int(distinct) <= len(rows),
            'values': [{'value': _text(value), 'count': int(count)} for value, count in rows]}


def match_count(database, contract, sql):
    """How many records a calculation's conditions include (its WHERE clause only).

    Returns None for CASE datasets, whose conditions live inside the CASE expression.
    """
    contract = validate_contract(contract)
    if contract['version'] == 7:
        return None
    ir = parse_flat(sql, contract)
    if 'predicate_tree' in ir:
        predicate, params = compile_boolean(ir['predicate_tree'], lambda p: _predicates_sql({'predicates': [p]}, contract))
    else:
        predicate, params = _predicates_sql(ir, contract)
    with _lookup_lock:
        counts = _match_count(database, predicate, params)
        if counts['matched'] == 0 and counts['total'] > 0:
            # Nothing matches: count each condition alone so the editor can name the one that
            # removes every record, with an existing value differing only in capitals.
            counts['conditions'] = _condition_counts(database, contract, _leaves(ir))
        return counts


def _leaves(ir):
    if 'predicates' in ir:
        return list(ir['predicates'])
    found = []

    def walk(node):
        if isinstance(node, dict):
            if 'field' in node and 'op' in node:
                found.append(node)
            else:
                for value in node.values():
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(ir.get('predicate_tree'))
    return found[:8]


_OPS = {'eq': '=', 'neq': '≠', 'in': 'is one of', 'not_in': 'is not one of', 'gt': '>', 'gte': '≥', 'lt': '<', 'lte': '≤',
        'is_null': 'is blank', 'is_not_null': 'is not blank', 'between': 'is between'}


def _condition_counts(database, contract, leaves):
    out = []
    con = _connect(database)
    try:
        for leaf in leaves:
            predicate, params = _predicates_sql({'predicates': [leaf]}, contract)
            matched = con.execute(f'SELECT COUNT(*) FILTER (WHERE ({predicate}) IS TRUE) FROM snapshot', params).fetchone()[0]
            values = [v for v in leaf.get('values') or [] if isinstance(v, str)]
            label = ' '.join([leaf['field'], _OPS.get(leaf['op'], leaf['op']), ', '.join(repr(v) for v in values)]).strip()
            item = {'condition': label, 'field': leaf['field'], 'matched': int(matched)}
            if not matched and leaf['op'] in ('eq', 'in') and values:
                q = _quote(leaf['field'])
                for value in values:
                    hit = con.execute(f'SELECT CAST({q} AS VARCHAR) FROM snapshot WHERE lower(trim(CAST({q} AS VARCHAR))) = lower(trim(?)) '
                                      f'AND CAST({q} AS VARCHAR) <> ? LIMIT 1', [value, value]).fetchone()
                    if hit:
                        item['suggestion'] = {'typed': value, 'existing': _text(hit[0])}
                        break
            out.append(item)
    finally:
        con.close()
    return out


def _match_count(database, predicate, params):
    con = _connect(database)
    try:
        matched, total = con.execute(
            f'SELECT COUNT(*) FILTER (WHERE ({predicate}) IS TRUE), COUNT(*) FROM snapshot', params).fetchone()
    finally:
        con.close()
    return {'matched': int(matched), 'total': int(total)}


def _text(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)
