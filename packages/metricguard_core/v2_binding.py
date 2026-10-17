"""Bind approved source identifiers to the existing restricted analytical roles."""

import sqlglot
from sqlglot import exp

from .common import CoreError, SCHEMA


def bind_sql(raw_sql: str, mapping: dict) -> str:
    """Return canonical SQL; the original text is retained by the caller."""
    columns = mapping.get('columns') if isinstance(mapping, dict) else None
    if not isinstance(columns, dict) or set(columns) != set(SCHEMA):
        raise CoreError('invalid_mapping', 'Approved source mapping is required')
    inverse = {}
    for canonical, source in columns.items():
        key = source.lower()
        if key in inverse:
            raise CoreError('invalid_mapping', 'Source identifiers differ only by case')
        inverse[key] = canonical
    try:
        statements = sqlglot.parse(raw_sql, read='duckdb')
    except Exception as exc:
        raise CoreError('unsupported_sql', 'Source SQL could not be parsed') from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise CoreError('unsupported_sql', 'One SELECT is required')
    tree = statements[0]
    if tree.args.get('with_') or list(tree.find_all(exp.CTE)):
        from .normalization import normalize_level1_cte
        ok, normalized_sql, diag, _ = normalize_level1_cte(raw_sql)
        if not ok:
            raise CoreError('unsupported_sql', diag or 'Source SQL has unsupported CTE')
        return bind_sql(normalized_sql, mapping)
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1 or tables[0].name.lower() != 'orders':
        raise CoreError('unsupported_sql', 'Mapped SQL must read the registered orders table')
    for column in tree.find_all(exp.Column):
        source = column.name
        key = source.lower()
        if key not in inverse or (column.this.args.get('quoted') and source != columns[inverse[key]]):
            raise CoreError('unsupported_sql', f'Column {source} is not in the approved mapping')
        column.set('this', exp.to_identifier(inverse[key], quoted=True))
    return tree.sql(dialect='duckdb')
