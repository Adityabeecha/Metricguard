"""Relational contract v2: one fact table and one to four lookup (dimension) tables.

A star schema: each lookup table joins the fact table on its own declared foreign
key (fact.fk = lookup.key, many-to-one). Calculations may join any subset of the
lookups, each INNER or LEFT, and filter on fact and joined lookup columns. Exactly as
in contract v1, every lookup key must be unique, so no join can multiply fact rows;
the import proves this per lookup before the snapshot is published, and the evidence
stays one row per fact record whose contributions add up exactly to the change.

Contract v1 (one dimension) keeps its own code path, IR, evidence and hashes; the
v1 modules dispatch here only when a contract or snapshot declares version 2.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from .common import CoreError, digest, file_hash
from .numeric import ScaledInteger
from .relational import (EXECUTION_POLICY, IDENT, MAX_BYTES, MAX_ROWS, MODE, SCOPE, HASH,
                         context_for, fail, quote)
from .snapshot import manifest_path

POLICY = 'relational-star-v1'
PREPARED_POLICY = 'relational-star-prepared-v1'

def row_limit(c):
    return 5_000_000 if c['version'] == 3 else MAX_ROWS

def byte_limit(c):
    return 512 * 1024 * 1024 if c['version'] == 3 else MAX_BYTES

MAX_DIMENSIONS = 4

# Text ranges (for example ISO dates or codes stored as text). DuckDB compares VARCHAR by
# byte, and UTF-8 byte order equals Python's code-point order, so both sides agree exactly.
RANGE_OPS = ('gt', 'gte', 'lt', 'lte')
RANGE_SQL = {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}
RANGE_NODES = {exp.GT: 'gt', exp.GTE: 'gte', exp.LT: 'lt', exp.LTE: 'lte'}


def compare_text(value, op, bound):
    return {'gt': value > bound, 'gte': value >= bound, 'lt': value < bound, 'lte': value <= bound}[op]
NULL_MARKER = '__METRICGUARD_RELATIONAL_NULL_54ce__'


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        fail('invalid_contract', f'{label} requires exactly: {", ".join(sorted(fields))}.')


def _label(value, maximum, field):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        fail('invalid_contract', f'{field} must contain 1–{maximum} characters.')


def _categories(table, label):
    cats = table['categories']
    if not isinstance(cats, list) or len(cats) > 8 or any(not isinstance(n, str) for n in cats) or len(set(cats)) != len(cats):
        fail('invalid_contract', f'{label} allows at most eight distinct string categories.')
    for name in cats:
        quote(name)
    if table['key'] in cats:
        fail('invalid_contract', 'Keys and categorical roles must be distinct.')


def validate_contract(payload):
    fields = {'version', 'mode', 'policy', 'name', 'fact', 'dimensions', 'measure', 'measure_meaning',
              'unit_label', 'values_comparable', 'scope', 'null_policy'}
    if not isinstance(payload, dict):
        fail('invalid_contract', 'A relational contract object is required.')
    value = {k: v for k, v in payload.items() if k != 'contract_hash'}
    _object(value, fields, 'Relational star contract')
    if (type(value['version']) is not int or value['version'] not in (2, 3) or value['mode'] != MODE or
            value['policy'] != (PREPARED_POLICY if value['version'] == 3 else POLICY) or value['scope'] != SCOPE or value['values_comparable'] is not True or
            value['null_policy'] not in ('blank_nullable', 'literal_NULL')):
        fail('invalid_contract', 'Use relational contract v2, whole-snapshot scope, comparable units and an explicit CSV NULL token.')
    for field, maximum in (('name', 160), ('measure_meaning', 300), ('unit_label', 80)):
        _label(value[field], maximum, field)
    fact = value['fact']
    _object(fact, {'table', 'key', 'grain', 'categories'}, 'fact')
    quote(fact['table']); quote(fact['key']); _label(fact['grain'], 300, 'fact grain')
    _categories(fact, 'The fact table')
    dims = value['dimensions']
    if not isinstance(dims, list) or not 1 <= len(dims) <= MAX_DIMENSIONS:
        fail('invalid_contract', f'Declare one to {MAX_DIMENSIONS} lookup tables.')
    roles, tables, foreign_keys = set(), {fact['table']}, set()
    for dim in dims:
        _object(dim, {'role', 'table', 'key', 'grain', 'categories', 'fact_foreign_key'}, 'Lookup table')
        quote(dim['role']); quote(dim['table']); quote(dim['key']); quote(dim['fact_foreign_key'])
        _label(dim['grain'], 300, dim['role'] + ' grain')
        _categories(dim, f'Lookup table {dim["role"]}')
        if dim['role'] in roles or dim['role'] == 'fact':
            fail('invalid_contract', 'Lookup roles must be distinct and cannot be named fact.')
        if dim['table'] in tables:
            fail('invalid_contract', 'Every table must have a different name.')
        if dim['fact_foreign_key'] in foreign_keys:
            fail('invalid_contract', 'Each lookup table needs its own fact foreign-key column.')
        roles.add(dim['role']); tables.add(dim['table']); foreign_keys.add(dim['fact_foreign_key'])
    measure = value['measure']
    _object(measure, {'column', 'precision', 'scale'}, 'Measure')
    quote(measure['column'])
    p, s = measure['precision'], measure['scale']
    if type(p) is not int or type(s) is not int or not 1 <= p <= 38 or not 0 <= s <= min(p, 18):
        fail('invalid_contract', 'Measure precision is 1–38; scale is 0–18 and cannot exceed precision.')
    identity = {fact['key'], *foreign_keys}
    if measure['column'] in identity or measure['column'] in fact['categories'] or foreign_keys.intersection(fact['categories']):
        fail('invalid_contract', 'Measure, foreign keys and categories must be different fact columns.')
    canonical = {**value, 'fact': {**fact, 'categories': sorted(fact['categories'])},
                 'dimensions': sorted(({**d, 'categories': sorted(d['categories'])} for d in dims), key=lambda d: d['role'])}
    sealed = digest(canonical)
    if 'contract_hash' in payload and payload['contract_hash'] != sealed:
        fail('contract_integrity', 'The relational contract hash does not match its contents.')
    return {**canonical, 'contract_hash': sealed}


def dims(c):
    return {d['role']: d for d in c['dimensions']}


def schemas(contract):
    c = validate_contract(contract)
    m = c['measure']
    fact = {c['fact']['key']: 'VARCHAR'}
    for d in c['dimensions']:
        fact[d['fact_foreign_key']] = 'VARCHAR'
    fact[m['column']] = f'DECIMAL({m["precision"]},{m["scale"]})'
    fact.update({name: 'VARCHAR' for name in c['fact']['categories']})
    out = {c['fact']['table']: fact}
    for d in c['dimensions']:
        out[d['table']] = {d['key']: 'VARCHAR', **{name: 'VARCHAR' for name in d['categories']}}
    return out


def alias(role):
    return 'd_' + role


# ---------- definitions ----------

def validate_ir(ir, contract):
    c = validate_contract(contract)
    roles = dims(c)
    fields = {'mode', 'policy', 'contract_version', 'contract_hash', 'aggregate', 'measure', 'joins', 'predicates', 'ir_hash'}
    if not isinstance(ir, dict) or set(ir) != fields:
        fail('invalid_ir', 'Relational definitions require the exact versioned IR shape.')
    expected = {'mode': MODE, 'policy': c['policy'], 'contract_version': c['version'], 'contract_hash': c['contract_hash'],
                'aggregate': 'sum', 'measure': {'table': 'fact', 'column': c['measure']['column']}}
    if any(ir.get(k) != v for k, v in expected.items()) or type(ir['contract_version']) is not int:
        fail('invalid_ir', 'Definition does not match the frozen relational measure and policy.')
    joins = ir['joins']
    if (not isinstance(joins, dict) or any(r not in roles or k not in ('inner', 'left') for r, k in joins.items()) or
            list(joins) != sorted(joins)):
        fail('invalid_ir', 'Joins must name declared lookup tables, each INNER or LEFT, in canonical order.')
    predicates = ir['predicates']
    if not isinstance(predicates, list) or len(predicates) > 16:
        fail('invalid_ir', 'At most 16 conjunctive categorical predicates are supported.')
    for pred in predicates:
        if not isinstance(pred, dict) or set(pred) != {'table', 'column', 'op', 'values'}:
            fail('invalid_ir', 'Malformed relational predicate.')
        role = pred['table']
        if not isinstance(role, str) or not isinstance(pred['column'], str):
            fail('invalid_ir', 'Predicate table and column must be registered string identifiers.')
        allowed = c['fact']['categories'] if role == 'fact' else roles[role]['categories'] if role in joins else None
        if allowed is None or pred['column'] not in allowed:
            fail('invalid_ir', 'Predicates may reference only registered categories of the fact or joined lookup tables.')
        values, op = pred['values'], pred['op']
        if (op not in ('eq', 'neq', 'in', 'not_in', 'is_null', 'is_not_null', *RANGE_OPS) or not isinstance(values, list) or
                any(not isinstance(v, str) or len(v) > 256 or '\x00' in v for v in values) or
                (op in ('eq', 'neq', *RANGE_OPS) and len(values) != 1) or (op in ('in', 'not_in') and not 1 <= len(values) <= 20) or
                (op in ('is_null', 'is_not_null') and values) or values != sorted(set(values))):
            fail('invalid_ir', 'Invalid bounded string predicate values.')
    if predicates != sorted(predicates, key=digest) or len({digest(p) for p in predicates}) != len(predicates):
        fail('invalid_ir', 'Predicates must be distinct and canonical.')
    if ir['ir_hash'] != digest({k: v for k, v in ir.items() if k != 'ir_hash'}):
        fail('invalid_ir', 'Relational definition hash does not match its contents.')
    return ir


def parse(raw_sql, contract):
    c = validate_contract(contract)
    roles = dims(c)
    by_table = {d['table']: d['role'] for d in c['dimensions']}
    if not isinstance(raw_sql, str) or not 1 <= len(raw_sql) <= 50_000:
        fail('unsupported_sql', 'Enter one bounded SELECT SUM query.')
    try:
        if any(token.token_type == sqlglot.tokens.TokenType.PLUS for token in sqlglot.tokenize(raw_sql, read='duckdb')):
            fail('unsupported_sql', 'Arithmetic operators are not supported, including unary plus.')
        statements = sqlglot.parse(raw_sql, read='duckdb')
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, RecursionError) as error:
        raise CoreError('unsupported_sql', 'SQL could not be parsed; keep one SELECT SUM with declared joins.') from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        fail('unsupported_sql', 'Exactly one SELECT is supported.')
    query = statements[0]
    if len(list(query.walk())) > 300:
        fail('unsupported_sql', 'Query exceeds the 300-node syntax limit.')
    if any(v for k, v in query.args.items() if k not in {'expressions', 'from_', 'joins', 'where'}):
        fail('unsupported_sql', 'No CTE, grouping, ordering, limits, distinct or additional SELECT clauses are supported.')
    source = query.args.get('from_')
    joins = query.args.get('joins') or []
    if not source or len(query.expressions) != 1 or len(joins) > len(roles):
        fail('unsupported_sql', f'Use the fact table, at most one join per lookup table ({", ".join(sorted(roles))}) and one SUM.')
    aliases = {}

    def table(node, expected_role=None):
        if not isinstance(node, exp.Table) or any(v for k, v in node.args.items() if k not in {'this', 'alias'}):
            fail('unsupported_sql', 'Only registered plain tables and optional aliases are supported.')
        if not isinstance(node.this, exp.Identifier) or node.this.args.get('quoted'):
            fail('unsupported_sql', 'Use unquoted registered table names.')
        role = 'fact' if node.name == c['fact']['table'] else by_table.get(node.name)
        if role is None or (expected_role == 'fact') != (role == 'fact'):
            fail('unsupported_sql', 'FROM must name the fact table; each JOIN must name a declared lookup table.')
        qualifier = node.name
        name_alias = node.args.get('alias')
        if name_alias:
            if name_alias.args.get('columns') or not isinstance(name_alias.this, exp.Identifier) or name_alias.this.args.get('quoted'):
                fail('unsupported_sql', 'Use plain table aliases without column renaming.')
            qualifier = name_alias.name
            quote(qualifier)
        if qualifier in aliases or role in aliases.values():
            fail('unsupported_sql', 'Each table may appear once, with a distinct alias.')
        aliases[qualifier] = role
        return role

    table(source.this, 'fact')

    def column(node):
        if not isinstance(node, exp.Column) or node.args.get('db') or node.args.get('catalog'):
            fail('unsupported_sql', 'Use qualified registered columns only.')
        if any(not isinstance(node.args.get(k), exp.Identifier) or node.args[k].args.get('quoted') for k in ('this', 'table')):
            fail('unsupported_sql', 'Every column must use its plain table name or alias.')
        if node.table not in aliases:
            fail('unsupported_sql', 'Column qualifier is not a joined table or alias.')
        return aliases[node.table], node.name

    join_kinds = {}
    for join in joins:
        role = table(join.this)
        if any(v for k, v in join.args.items() if k not in {'this', 'side', 'kind', 'on'}):
            fail('unsupported_sql', 'Joins support only ON equality; USING and other join clauses are refused.')
        side, kind = join.args.get('side') or '', join.args.get('kind') or ''
        if (side, kind) not in {('', ''), ('', 'INNER'), ('LEFT', ''), ('LEFT', 'OUTER')}:
            fail('unsupported_sql', 'Only INNER JOIN and LEFT JOIN are supported.')
        on = join.args.get('on')
        if not isinstance(on, exp.EQ):
            fail('unsupported_sql', 'Each join must be its single declared foreign-key equality.')
        if {column(on.this), column(on.expression)} != {('fact', roles[role]['fact_foreign_key']), (role, roles[role]['key'])}:
            fail('unsupported_sql', f'Join {role} on its declared foreign key: fact.{roles[role]["fact_foreign_key"]} = {role}.{roles[role]["key"]}.')
        join_kinds[role] = 'left' if side == 'LEFT' else 'inner'
    expression = query.expressions[0]
    if isinstance(expression, exp.Alias):
        if not isinstance(expression.args.get('alias'), exp.Identifier) or expression.args['alias'].args.get('quoted'):
            fail('unsupported_sql', 'Use a plain output alias.')
        expression = expression.this
    if not isinstance(expression, exp.Sum) or column(expression.this) != ('fact', c['measure']['column']):
        fail('unsupported_sql', 'Only SUM of the registered fact measure is supported.')
    predicates = []

    def lower(node):
        if isinstance(node, exp.Paren):
            return lower(node.this)
        if isinstance(node, exp.And):
            lower(node.this); lower(node.expression); return
        col = node.this
        if isinstance(node, exp.EQ):
            op, literals = 'eq', [node.expression]
        elif isinstance(node, exp.NEQ):
            op, literals = 'neq', [node.expression]
        elif type(node) in RANGE_NODES:
            op, literals = RANGE_NODES[type(node)], [node.expression]
        elif isinstance(node, exp.In) and not node.args.get('query') and not node.args.get('unnest'):
            op, literals = 'in', node.expressions
        elif (isinstance(node, exp.Not) and isinstance(node.this, exp.In) and not node.this.args.get('query')
              and not node.this.args.get('unnest')):
            op, literals, col = 'not_in', node.this.expressions, node.this.this
        elif isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
            op, literals = 'is_null', []
        elif isinstance(node, exp.Not) and isinstance(node.this, exp.Is) and isinstance(node.this.expression, exp.Null):
            op, literals, col = 'is_not_null', [], node.this.this
        else:
            fail('unsupported_sql', 'Filters support AND over =, <>, <, <=, >, >=, IN, NOT IN, IS NULL or IS NOT NULL on text columns.')
        role, name = column(col)
        allowed = c['fact']['categories'] if role == 'fact' else roles[role]['categories']
        if name not in allowed:
            fail('unsupported_sql', f'{role}.{name} is not a registered categorical field.')
        if len(literals) > 20:
            fail('unsupported_sql', 'IN lists allow at most 20 string values.')
        values = []
        for literal in literals:
            if not isinstance(literal, exp.Literal) or not literal.is_string or len(literal.this) > 256 or '\x00' in literal.this:
                fail('unsupported_sql', 'Categorical predicates require string literals of at most 256 characters.')
            values.append(literal.this)
        predicates.append({'table': role, 'column': name, 'op': op, 'values': sorted(set(values))})
        if len(predicates) > 16:
            fail('unsupported_sql', 'At most 16 categorical predicates are supported.')

    if query.args.get('where'):
        lower(query.args['where'].this)
    predicates = sorted({digest(p): p for p in predicates}.values(), key=digest)
    ir = {'mode': MODE, 'policy': c['policy'], 'contract_version': c['version'], 'contract_hash': c['contract_hash'],
          'aggregate': 'sum', 'measure': {'table': 'fact', 'column': c['measure']['column']},
          'joins': {role: join_kinds[role] for role in sorted(join_kinds)}, 'predicates': predicates}
    ir['ir_hash'] = digest(ir)
    return validate_ir(ir, c)


def compile_predicates(ir, contract):
    validate_ir(ir, contract)
    parts, params = [], []
    for p in ir['predicates']:
        col = ('f' if p['table'] == 'fact' else alias(p['table'])) + '.' + quote(p['column'])
        if p['op'] in ('is_null', 'is_not_null'):
            parts.append(col + (' IS NULL' if p['op'] == 'is_null' else ' IS NOT NULL'))
        elif p['op'] in ('eq', 'neq', *RANGE_OPS):
            parts.append(f"{col} {RANGE_SQL.get(p['op'], '=' if p['op'] == 'eq' else '<>')} ?"); params.extend(p['values'])
        else:
            # SQL semantics, matched by includes(): a NULL value satisfies neither IN nor NOT IN.
            parts.append(col + (' IN (' if p['op'] == 'in' else ' NOT IN (') + ','.join('?' for _ in p['values']) + ')')
            params.extend(p['values'])
    return '(' + ' AND '.join(parts or ['TRUE']) + ')', params


def joined(ir, matched):
    """A fact row survives the definition's joins when every INNER-joined lookup matched."""
    return all(matched[role] for role, kind in ir['joins'].items() if kind == 'inner')


def includes(ir, matched, fact_values, dimension_values):
    if not joined(ir, matched):
        return False
    for p in ir['predicates']:
        value = (fact_values if p['table'] == 'fact' else dimension_values[p['table']])[p['column']]
        if p['op'] == 'is_null':
            accepted = value is None
        elif p['op'] == 'is_not_null':
            accepted = value is not None
        elif p['op'] in ('neq', 'not_in'):
            accepted = value is not None and value not in p['values']
        elif p['op'] in RANGE_OPS:
            accepted = value is not None and compare_text(value, p['op'], p['values'][0])
        else:
            accepted = value is not None and value in p['values']
        if not accepted:
            return False
    return True


# ---------- snapshot ----------

def entry_name(role):
    return 'fact.csv' if role == 'fact' else f'dim_{role}.csv'


def join_sql(c):
    parts = [f'FROM {quote(c["fact"]["table"])} f']
    for d in c['dimensions']:
        parts.append(f'LEFT JOIN {quote(d["table"])} {alias(d["role"])} ON '
                     f'f.{quote(d["fact_foreign_key"])} = {alias(d["role"])}.{quote(d["key"])}')
    return ' '.join(parts)


def prove_grain(con, contract):
    c = validate_contract(contract)
    fact_total, distinct, missing = con.execute(
        f'SELECT COUNT(*), COUNT(DISTINCT {quote(c["fact"]["key"])}), '
        f'COUNT(*) FILTER (WHERE {quote(c["fact"]["key"])} IS NULL OR {quote(c["fact"]["key"])} = \'\') '
        f'FROM {quote(c["fact"]["table"])}').fetchone()
    if not 0 <= fact_total <= row_limit(c) or distinct != fact_total or missing:
        fail('invalid_keys', 'fact key must be unique, non-NULL and non-empty across the complete table.')
    per_dimension = {}
    for d in c['dimensions']:
        table, key, fk, a = quote(d['table']), quote(d['key']), quote(d['fact_foreign_key']), alias(d['role'])
        total, distinct, missing = con.execute(
            f'SELECT COUNT(*), COUNT(DISTINCT {key}), COUNT(*) FILTER (WHERE {key} IS NULL OR {key} = \'\') FROM {table}').fetchone()
        if not 0 <= total <= row_limit(c) or distinct != total or missing:
            fail('invalid_keys', f'{d["role"]} key must be unique, non-NULL and non-empty across the complete table.')
        rows, matched, unmatched, null_fk = con.execute(
            f'SELECT COUNT(*), COUNT({a}.{key}), COUNT(*) FILTER (WHERE {a}.{key} IS NULL), COUNT(*) FILTER (WHERE f.{fk} IS NULL) '
            f'FROM {quote(c["fact"]["table"])} f LEFT JOIN {table} {a} ON f.{fk} = {a}.{key}').fetchone()
        if rows != fact_total:
            fail('join_fanout', f'Joining {d["role"]} multiplied fact rows; this relationship cannot execute.')
        per_dimension[d['role']] = {'dimension_rows': total, 'dimension_key_unique': True, 'matched_fact_rows': matched,
                                    'unmatched_fact_rows': unmatched, 'null_foreign_key_rows': null_fk,
                                    'missing_nonnull_foreign_key_rows': unmatched - null_fk}
    all_rows = con.execute(f'SELECT COUNT(*) {join_sql(c)}').fetchone()[0]
    if all_rows != fact_total:
        fail('join_fanout', 'Joining the lookup tables multiplied fact rows; this schema cannot execute.')
    return {'fact_rows': fact_total, 'fact_key_unique': True, 'left_join_rows': all_rows, 'fanout_rows': 0,
            'grain_preserved': True, 'dimensions': per_dimension}


def _amount(value, c):
    from .relational_snapshot import amount
    return amount(value, c)


def ingest(source, destination, payload):
    c = validate_contract(payload)
    source, destination = Path(source), Path(destination)
    if destination.exists() or manifest_path(destination).exists():
        fail('immutable_snapshot', 'Snapshot destination already exists.')
    if source.stat().st_size > byte_limit(c) + 4096 * (1 + len(c['dimensions'])):
        fail('file_too_large', f'Relational upload exceeds {byte_limit(c) // (1024 * 1024)} MiB plus bundle metadata.')
    source_hash = file_hash(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    roles = ['fact', *[d['role'] for d in c['dimensions']]]
    table_of = {'fact': c['fact'], **dims(c)}
    source_hashes, row_counts = {}, {}
    table_schemas = schemas(c)
    try:
        with zipfile.ZipFile(source) as bundle, tempfile.TemporaryDirectory(prefix='relational-', dir=destination.parent) as folder:
            entries = bundle.infolist()
            if ({e.filename for e in entries} != {entry_name(r) for r in roles} or len(entries) != len(roles) or
                    any(e.flag_bits & 1 or e.file_size > byte_limit(c) for e in entries) or sum(e.file_size for e in entries) > byte_limit(c)):
                fail('invalid_bundle', 'The internal bundle requires one bounded, unencrypted CSV per table.')
            for role in roles:
                entry = bundle.getinfo(entry_name(role))
                raw_path = Path(folder) / ('raw_' + entry_name(role))
                with bundle.open(entry) as stream, raw_path.open('wb') as raw_output:
                    copied = 0
                    while chunk := stream.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > byte_limit(c):
                            fail('file_too_large', 'CSV exceeds the policy byte limit.')
                        raw_output.write(chunk)
                if copied != entry.file_size or not copied:
                    fail('invalid_bundle', 'CSV byte counts are inconsistent or empty.')
                source_hashes[role] = file_hash(raw_path)
                spec = table_of[role]
                schema = table_schemas[spec['table']]
                columns = list(schema)
                # The source handle must close on every refusal: Windows cannot remove the
                # temporary folder while a file in it is open, which would mask the real error.
                try:
                    with raw_path.open('r', encoding='utf-8-sig', newline='') as raw_input:
                        reader = csv.DictReader(raw_input)
                        if (reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)) or
                                len(reader.fieldnames) > 64 or any(len(n) > 128 for n in reader.fieldnames) or
                                not set(columns).issubset(reader.fieldnames)):
                            fail('invalid_schema', f'{role} CSV must contain every registered column and distinct headings (max 64).')
                        token = '' if c['null_policy'] == 'blank_nullable' else 'NULL'
                        keys, count = set(), 0
                        with (Path(folder) / entry_name(role)).open('w', encoding='utf-8', newline='') as output:
                            writer = csv.writer(output); writer.writerow(columns)
                            for line, raw in enumerate(reader, 2):
                                if count >= row_limit(c):
                                    fail('too_many_rows', f'{role} CSV exceeds {row_limit(c)} rows.')
                                if None in raw or any(v is None for v in raw.values()):
                                    fail('invalid_row', f'{role} CSV has an inconsistent field count at line {line}.')
                                values = {name: None if raw[name] == token else raw[name] for name in columns}
                                for name, value in values.items():
                                    if value is not None and (len(value) > 256 or '\x00' in value or value == NULL_MARKER):
                                        fail('invalid_row', f'{role}.{name} exceeds 256 characters or contains a reserved value at line {line}.')
                                key = values[spec['key']]
                                if not key or key in keys:
                                    fail('invalid_keys', f'{role} key is missing or duplicated at line {line}; no deduplication is performed.')
                                if c['version'] == 2:
                                    keys.add(key)
                                if role == 'fact':
                                    values[c['measure']['column']] = _amount(values[c['measure']['column']], c)
                                    for d in c['dimensions']:
                                        if values[d['fact_foreign_key']] == '':
                                            fail('invalid_keys', f'An empty foreign key must use the declared NULL token at line {line}.')
                                writer.writerow(NULL_MARKER if values[n] is None else values[n] for n in columns)
                                count += 1
                    row_counts[role] = count
                except (UnicodeError, csv.Error) as error:
                    raise CoreError('invalid_csv', f'{role} source is not a supported UTF-8 CSV.') from error
            with duckdb.connect(str(destination), config={'threads': '1', 'memory_limit': '256MB'}) as con:
                for role in roles:
                    spec = table_of[role]
                    schema = table_schemas[spec['table']]
                    con.execute(f'CREATE TABLE {quote(spec["table"])} (' + ', '.join(quote(n) + ' ' + t for n, t in schema.items()) + ')')
                    con.execute(f"COPY {quote(spec['table'])} FROM ? (FORMAT CSV, HEADER TRUE, NULLSTR '{NULL_MARKER}')",
                                [str(Path(folder) / entry_name(role))])
                proof = prove_grain(con, c)
                if proof['fact_rows'] != row_counts['fact'] or any(
                        proof['dimensions'][d['role']]['dimension_rows'] != row_counts[d['role']] for d in c['dimensions']):
                    fail('source_changed', 'Loaded table counts differ from the validated sources.')
            if file_hash(source) != source_hash:
                fail('source_changed', 'Source bundle changed during import.')
            manifest = {'mode': MODE, 'policy': c['policy'], 'execution_policy': EXECUTION_POLICY,
                        'contract_version': c['version'], 'contract': c, 'contract_hash': c['contract_hash'],
                        'source_hash': source_hash, 'source_hashes': source_hashes, 'database_hash': file_hash(destination),
                        'schema': {table + '.' + col: kind for table, cols in table_schemas.items() for col, kind in cols.items()},
                        'table_schemas': table_schemas, 'primary_key': c['fact']['key'], 'row_count': proof['fact_rows'],
                        'join_proof': proof, 'scope': c['scope'], 'grain': c['fact']['grain'],
                        'unit_label': c['unit_label'], 'measure_meaning': c['measure_meaning'],
                        'null_policy': c['null_policy'], 'values_comparable': True,
                        'currency': None, 'timezone': None, 'cutoff_at': None,
                        'ingestion_version': c['policy'], 'created_at': datetime.now(timezone.utc).isoformat(),
                        'transformations': ['Only registered fields copied; exact measure validation; no deduplication, key coercion or amount imputation.'],
                        'null_semantics': 'NULL fact foreign keys never match; LEFT keeps unmatched facts with NULL lookup fields; INNER excludes them. NULL measures are ignored by SUM; empty/all-NULL SUM is NULL.'}
            manifest['manifest_hash'] = digest(manifest)
            manifest_path(destination).write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
            return manifest
    except zipfile.BadZipFile as error:
        destination.unlink(missing_ok=True); manifest_path(destination).unlink(missing_ok=True)
        raise CoreError('invalid_bundle', 'Internal source bundle could not be read.') from error
    except Exception:
        destination.unlink(missing_ok=True); manifest_path(destination).unlink(missing_ok=True)
        raise


def validate_manifest(manifest, contract, source_hash=None):
    c = validate_contract(contract)
    ts = schemas(c)
    expected = {'mode': MODE, 'policy': c['policy'], 'execution_policy': EXECUTION_POLICY,
                'contract_version': c['version'], 'contract': c, 'contract_hash': c['contract_hash'],
                'table_schemas': ts, 'schema': {t + '.' + n: typ for t, cols in ts.items() for n, typ in cols.items()},
                'primary_key': c['fact']['key'], 'scope': c['scope'], 'grain': c['fact']['grain'],
                'unit_label': c['unit_label'], 'measure_meaning': c['measure_meaning'],
                'null_policy': c['null_policy'], 'values_comparable': True,
                'currency': None, 'timezone': None, 'cutoff_at': None}
    if (not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in expected.items()) or
            manifest.get('manifest_hash') != digest({k: v for k, v in manifest.items() if k != 'manifest_hash'}) or
            (source_hash is not None and manifest.get('source_hash') != source_hash)):
        fail('snapshot_integrity', 'Frozen relational snapshot manifest does not match the approved contract or source.')
    roles = {'fact', *dims(c)}
    if (not isinstance(manifest.get('source_hashes'), dict) or set(manifest['source_hashes']) != roles or
            any(not isinstance(v, str) or not HASH.fullmatch(v) for v in manifest['source_hashes'].values()) or
            any(not isinstance(manifest.get(k), str) or not HASH.fullmatch(manifest[k]) for k in ('source_hash', 'database_hash'))):
        fail('snapshot_integrity', 'Source lineage hashes are missing or invalid.')
    proof = manifest.get('join_proof')
    if (not isinstance(proof, dict) or set(proof) != {'fact_rows', 'fact_key_unique', 'left_join_rows', 'fanout_rows', 'grain_preserved', 'dimensions'} or
            proof['fact_key_unique'] is not True or proof['grain_preserved'] is not True or
            type(proof['fanout_rows']) is not int or proof['fanout_rows'] != 0 or
            type(proof['fact_rows']) is not int or not 0 <= proof['fact_rows'] <= row_limit(c) or
            type(proof['left_join_rows']) is not int or proof['left_join_rows'] != proof['fact_rows'] or
            not isinstance(proof['dimensions'], dict) or set(proof['dimensions']) != set(dims(c)) or
            type(manifest.get('row_count')) is not int or manifest['row_count'] != proof['fact_rows']):
        fail('snapshot_integrity', 'The frozen grain proof is incomplete or does not reconcile.')
    for item in proof['dimensions'].values():
        if (not isinstance(item, dict) or set(item) != {'dimension_rows', 'dimension_key_unique', 'matched_fact_rows', 'unmatched_fact_rows',
                                                       'null_foreign_key_rows', 'missing_nonnull_foreign_key_rows'} or
                item['dimension_key_unique'] is not True or
                any(type(item[k]) is not int or not 0 <= item[k] <= row_limit(c) for k in item if k != 'dimension_key_unique') or
                item['matched_fact_rows'] + item['unmatched_fact_rows'] != proof['fact_rows'] or
                item['null_foreign_key_rows'] + item['missing_nonnull_foreign_key_rows'] != item['unmatched_fact_rows'] or
                (item['matched_fact_rows'] > 0 and item['dimension_rows'] == 0)):
            fail('snapshot_integrity', 'The frozen grain proof does not reconcile.')
    return c


def verify_database(con, contract, manifest):
    for table, columns in schemas(contract).items():
        actual = {r[0]: r[1] for r in con.execute('DESCRIBE ' + quote(table)).fetchall()}
        if actual != columns:
            fail('snapshot_integrity', 'Relational table schema differs from the frozen contract.')
    if prove_grain(con, contract) != manifest['join_proof']:
        fail('snapshot_integrity', 'Current table keys or join cardinality differ from the frozen proof.')


# ---------- comparison ----------

def compare(database, c, manifest, baseline, target, context):
    if manifest['row_count'] > MAX_ROWS:
        fail('preparation_required', 'Prepare this relational snapshot for flat analytics before comparing more than 100,000 facts.')
    baseline, target = validate_ir(baseline, c), validate_ir(target, c)
    if context != context_for(c):
        fail('not_comparable', 'Use the exact frozen whole-snapshot scope and unit context.')
    started = time.monotonic()
    scale = c['measure']['scale']
    bp, bparams = compile_predicates(baseline, c)
    tp, tparams = compile_predicates(target, c)

    def join_condition(ir):
        parts = [f'{alias(r)}.{quote(dims(c)[r]["key"])} IS NOT NULL' for r, kind in ir['joins'].items() if kind == 'inner']
        return '(' + ' AND '.join(parts or ['TRUE']) + ')'

    fields = ['f.' + quote(c['fact']['key']), 'f.' + quote(c['measure']['column']),
              f'({join_condition(baseline)} AND {bp}) IS TRUE', f'({join_condition(target)} AND {tp}) IS TRUE']
    for d in c['dimensions']:
        fields += ['f.' + quote(d['fact_foreign_key']), alias(d['role']) + '.' + quote(d['key'])]
    fields += ['f.' + quote(n) for n in c['fact']['categories']]
    for d in c['dimensions']:
        fields += [alias(d['role']) + '.' + quote(n) for n in d['categories']]
    sql = 'SELECT ' + ', '.join(fields) + ' ' + join_sql(c) + ' ORDER BY f.' + quote(c['fact']['key'])
    evidence, sums, terms = [], [ScaledInteger.zero(scale) for _ in range(2)], [0, 0]
    with duckdb.connect(str(database), read_only=True, config={'threads': '1', 'memory_limit': '256MB',
                        'enable_external_access': 'false', 'autoinstall_known_extensions': 'false', 'autoload_known_extensions': 'false'}) as con:
        con.execute('SET lock_configuration = true')
        verify_database(con, c, manifest)
        cursor = con.execute(sql, [*bparams, *tparams])
        while batch := cursor.fetchmany(1000):
            for raw in batch:
                key, measure, bi, ti = raw[:4]
                position = 4
                links, matched = {}, {}
                for d in c['dimensions']:
                    fk, dk = raw[position], raw[position + 1]
                    position += 2
                    matched[d['role']] = dk is not None
                    links[d['role']] = {'foreign_key': fk, 'dimension_key': dk, 'matched': dk is not None,
                                        'membership': 'matched' if dk is not None else 'null_foreign_key' if fk is None else 'missing_dimension'}
                fact_values = dict(zip(c['fact']['categories'], raw[position:position + len(c['fact']['categories'])]))
                position += len(c['fact']['categories'])
                dimension_values = {}
                for d in c['dimensions']:
                    dimension_values[d['role']] = dict(zip(d['categories'], raw[position:position + len(d['categories'])]))
                    position += len(d['categories'])
                measure_str = None if measure is None else format(measure, 'f')
                n = ScaledInteger.zero(scale) if measure is None else ScaledInteger.parse(measure_str)
                bc, tc = (n if flag and measure is not None else ScaledInteger.zero(scale) for flag in (bi, ti))
                for i, flag in enumerate((bi, ti)):
                    if flag and measure is not None:
                        sums[i] += n; terms[i] += 1
                evidence.append({'order_id': key, 'fact_key': key, 'links': links,
                                 'baseline_join_included': joined(baseline, matched),
                                 'target_join_included': joined(target, matched),
                                 'fact_values': fact_values, 'dimension_values': dimension_values,
                                 'measure_value': measure_str, 'baseline_included': bi, 'target_included': ti,
                                 'baseline_null_term': bi and measure is None, 'target_null_term': ti and measure is None,
                                 'baseline_contribution': bc.to_fixed_string(), 'target_contribution': tc.to_fixed_string(),
                                 'delta': (tc - bc).to_fixed_string(),
                                 'membership': 'included_in_both' if bi and ti else 'baseline_only' if bi else 'target_only' if ti else 'included_in_neither'})
    for total in sums:
        if abs(total.coeff) >= 10 ** 38:
            fail('arithmetic_overflow', 'Endpoint SUM exceeds the declared DECIMAL(38, scale) capacity.')
    av, bv = (total if count else None for total, count in zip(sums, terms))
    gap = bv - av if av is not None and bv is not None else None
    number = lambda n: None if n is None else n.to_fixed_string()
    step = {'rule': 'join_and_filter_change', 'position': 1,
            'baseline': {'joins': baseline['joins'], 'predicates': baseline['predicates']},
            'target': {'joins': target['joins'], 'predicates': target['predicates']}, 'changed': baseline != target,
            'previous_value': number(av), 'next_value': number(bv), 'value_before': number(av), 'value_after': number(bv),
            'contribution': number(gap)}
    return {'baseline_value': number(av), 'target_value': number(bv), 'delta_target_minus_baseline': number(gap),
            'absolute_gap': number(abs(gap)) if gap is not None else None, 'residual': number(ScaledInteger.zero(scale)) if gap is not None else None,
            'relative_change_percent_display': None, 'currency': None, 'unit_label': c['unit_label'], 'measure_meaning': c['measure_meaning'],
            'numeric_tolerance': '0', 'method': 'relational_join_filter_change',
            'definition_status': 'same_supported_representation' if baseline == target else 'different',
            'value_status': 'defined' if gap is not None else 'undefined',
            'explanation_status': 'complete_join_filter_change' if gap is not None else 'unavailable',
            'steps': [step] if gap is not None else [], 'definition_differences': [step], 'shapley': None, 'evidence': evidence,
            'join_proof': manifest['join_proof'],
            'provenance': {'execution_policy': EXECUTION_POLICY, 'engine_version': c['policy'], 'contract_version': c['version'],
                           'contract_hash': c['contract_hash'], 'baseline_ir_hash': baseline['ir_hash'], 'target_ir_hash': target['ir_hash'],
                           'snapshot_manifest_hash': manifest['manifest_hash'], 'database_hash': manifest['database_hash'],
                           'source_hash': manifest['source_hash'], 'source_hashes': manifest['source_hashes'], 'context': context,
                           'query_hash': digest({'sql': sql, 'params': [*bparams, *tparams]}), 'query_count': 1,
                           'parser_version': sqlglot.__version__, 'duckdb_version': duckdb.__version__,
                           'elapsed_ms': round((time.monotonic() - started) * 1000), 'evidence_unit': 'fact_key'},
            'limitations': ['Key uniqueness is proven per lookup table within this snapshot, so no join multiplies fact rows; business grain and source authenticity are not independently established.',
                            'Missing lookup records are reported; they do not establish a zero measure or a valid foreign-key match.',
                            'One whole-definition bridge explains the join/filter change; it is not causal attribution across individual predicates or joins.',
                            'Units are declared by the user. Unknown currency stays unknown; no verified financial loss is claimed.'] +
                           (['An empty or all-NULL endpoint SUM is NULL; gap and residual are not applicable.'] if gap is None else [])}


# ---------- evidence ----------

BASE_FIELDS = {'order_id', 'baseline_included', 'target_included', 'baseline_contribution',
               'target_contribution', 'delta', 'baseline_null_term', 'target_null_term', 'membership'}
FIELDS = {'fact_key', 'links', 'baseline_join_included', 'target_join_included', 'fact_values', 'dimension_values', 'measure_value'}


def validate_result(result, context):
    c, manifest = context['contract'], context['manifest']
    a, b = context['baseline'], context['target']
    expected = {'execution_policy': EXECUTION_POLICY, 'engine_version': c['policy'], 'contract_version': c['version'],
                'contract_hash': c['contract_hash'], 'snapshot_manifest_hash': manifest['manifest_hash'],
                'database_hash': manifest['database_hash'], 'source_hash': manifest['source_hash'],
                'source_hashes': manifest['source_hashes'], 'baseline_ir_hash': a['ir_hash'], 'target_ir_hash': b['ir_hash'],
                'context': context_for(c), 'evidence_unit': 'fact_key'}
    if (any(result.get('provenance', {}).get(k) != v for k, v in expected.items()) or
            result.get('join_proof') != manifest['join_proof'] or result.get('currency') is not None or
            result.get('unit_label') != c['unit_label'] or result.get('measure_meaning') != c['measure_meaning'] or
            result.get('method') != 'relational_join_filter_change'):
        raise ValueError('RELATIONAL_RESULT_INVALID')
    defined = result['baseline_value'] is not None and result['target_value'] is not None
    if (result.get('explanation_status') != ('complete_join_filter_change' if defined else 'unavailable') or
            result.get('value_status') != ('defined' if defined else 'undefined') or
            result.get('definition_status') != ('same_supported_representation' if a == b else 'different') or
            result.get('numeric_tolerance') != '0'):
        raise ValueError('RELATIONAL_RESULT_INVALID')
    if not defined and (any(result.get(k) is not None for k in ('delta_target_minus_baseline', 'absolute_gap', 'residual')) or result.get('steps') != []):
        raise ValueError('RELATIONAL_RESULT_INVALID')
    if defined:
        steps = result['steps']
        if len(steps) != 1 or steps[0].get('rule') != 'join_and_filter_change':
            raise ValueError('RELATIONAL_RESULT_INVALID')
        for side, ir in (('baseline', a), ('target', b)):
            if steps[0].get(side) != {'joins': ir['joins'], 'predicates': ir['predicates']}:
                raise ValueError('RELATIONAL_RESULT_INVALID')
        av, bv, delta = (ScaledInteger.parse(result[k]) for k in ('baseline_value', 'target_value', 'delta_target_minus_baseline'))
        if (bv - av != delta or ScaledInteger.parse(result['absolute_gap']) != abs(delta) or ScaledInteger.parse(result['residual']) != 0 or
                ScaledInteger.parse(steps[0]['contribution']) != delta or
                steps[0]['previous_value'] != result['baseline_value'] or steps[0]['next_value'] != result['target_value']):
            raise ValueError('RELATIONAL_RESULT_INVALID')


class EvidenceValidator:
    def __init__(self, context, *, include_join_evidence=False):
        self.context = context
        self.contract = context['contract']
        self.roles = dims(self.contract)
        self.keys = set()
        self.dimensions = {role: {} for role in self.roles}
        self.missing = {role: set() for role in self.roles}
        self.matched = {role: 0 for role in self.roles}
        self.null_fk = {role: 0 for role in self.roles}
        self.rows = 0
        scale = self.contract['measure']['scale']
        self.sums = [ScaledInteger.zero(scale) for _ in range(2)]
        self.terms = [0, 0]
        self.explanation = JoinEvidenceSummary(context) if include_join_evidence else None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.keys.clear(); self.dimensions.clear(); self.missing.clear()

    def add(self, row):
        try:
            self._add(row)
        except (CoreError, KeyError, TypeError, ValueError) as error:
            raise ValueError('EVIDENCE_INVALID') from error

    def _add(self, row):
        if not isinstance(row, dict) or set(row) != BASE_FIELDS | FIELDS:
            raise ValueError('EVIDENCE_INVALID')
        c = self.contract
        key = row['fact_key']
        if not isinstance(key, str) or not key or len(key) > 256 or '\x00' in key or key == NULL_MARKER or key != row['order_id'] or key in self.keys:
            raise ValueError('EVIDENCE_INVALID')
        links, values = row['links'], row['dimension_values']
        if not isinstance(links, dict) or set(links) != set(self.roles) or not isinstance(values, dict) or set(values) != set(self.roles):
            raise ValueError('EVIDENCE_INVALID')
        fact_values = row['fact_values']
        if (not isinstance(fact_values, dict) or set(fact_values) != set(c['fact']['categories']) or
                any(v is not None and (not isinstance(v, str) or len(v) > 256 or '\x00' in v) for v in fact_values.values())):
            raise ValueError('EVIDENCE_INVALID')
        matched = {}
        for role, spec in self.roles.items():
            link = links[role]
            if not isinstance(link, dict) or set(link) != {'foreign_key', 'dimension_key', 'matched', 'membership'}:
                raise ValueError('EVIDENCE_INVALID')
            fk, dk = link['foreign_key'], link['dimension_key']
            for value in (fk, dk):
                if value is not None and (not isinstance(value, str) or not value or len(value) > 256 or '\x00' in value or value == NULL_MARKER):
                    raise ValueError('EVIDENCE_INVALID')
            is_matched = dk is not None
            if type(link['matched']) is not bool or link['matched'] != is_matched or (is_matched and fk != dk):
                raise ValueError('EVIDENCE_INVALID')
            if link['membership'] != ('matched' if is_matched else 'null_foreign_key' if fk is None else 'missing_dimension'):
                raise ValueError('EVIDENCE_INVALID')
            dim_values = values[role]
            if (not isinstance(dim_values, dict) or set(dim_values) != set(spec['categories']) or
                    any(v is not None and (not isinstance(v, str) or len(v) > 256 or '\x00' in v) for v in dim_values.values())):
                raise ValueError('EVIDENCE_INVALID')
            if not is_matched and any(v is not None for v in dim_values.values()):
                raise ValueError('EVIDENCE_INVALID')
            if is_matched:
                seen = self.dimensions[role]
                if dk in self.missing[role] or (dk in seen and seen[dk] != dim_values):
                    raise ValueError('EVIDENCE_INVALID')
                seen[dk] = dim_values
            elif fk is not None:
                if fk in self.dimensions[role]:
                    raise ValueError('EVIDENCE_INVALID')
                self.missing[role].add(fk)
            matched[role] = is_matched
            self.matched[role] += int(is_matched)
            self.null_fk[role] += int(fk is None)
        raw = row['measure_value']
        if _amount(raw, c) != raw:
            raise ValueError('EVIDENCE_INVALID')
        scale = c['measure']['scale']
        n = ScaledInteger.parse(raw) if raw is not None else ScaledInteger.zero(scale)
        for i, side in enumerate(('baseline', 'target')):
            ir = self.context[side]
            inc = includes(ir, matched, fact_values, values)
            flags = {side + '_join_included': joined(ir, matched), side + '_included': inc, side + '_null_term': inc and raw is None}
            if any(type(row[k]) is not bool or row[k] != v for k, v in flags.items()):
                raise ValueError('EVIDENCE_INVALID')
            expected = n if inc and raw is not None else ScaledInteger.zero(scale)
            if ScaledInteger.parse(row[side + '_contribution']) != expected:
                raise ValueError('EVIDENCE_INVALID')
            self.sums[i] += expected
            if inc and raw is not None:
                self.terms[i] += 1
        bi, ti = row['baseline_included'], row['target_included']
        if row['membership'] != ('included_in_both' if bi and ti else 'baseline_only' if bi else 'target_only' if ti else 'included_in_neither'):
            raise ValueError('EVIDENCE_INVALID')
        if ScaledInteger.parse(row['target_contribution']) - ScaledInteger.parse(row['baseline_contribution']) != ScaledInteger.parse(row['delta']):
            raise ValueError('EVIDENCE_INVALID')
        self.keys.add(key); self.rows += 1
        if self.rows > self.context['manifest']['row_count']:
            raise ValueError('EVIDENCE_INVALID')
        if self.explanation is not None:
            self.explanation.add(row)

    def finish(self, result):
        validate_result(result, self.context)
        proof = self.context['manifest']['join_proof']
        if self.rows != proof['fact_rows']:
            raise ValueError('EVIDENCE_INVALID')
        for role, item in proof['dimensions'].items():
            if (self.matched[role] != item['matched_fact_rows'] or self.null_fk[role] != item['null_foreign_key_rows'] or
                    len(self.dimensions[role]) > item['dimension_rows']):
                raise ValueError('EVIDENCE_INVALID')
        for i, side in enumerate(('baseline', 'target')):
            total = result[side + '_value']
            if (total is None) != (self.terms[i] == 0):
                raise ValueError('EVIDENCE_INVALID')
            if total is not None and (ScaledInteger.parse(total) != self.sums[i] or abs(self.sums[i].coeff) >= 10 ** 38):
                raise ValueError('EVIDENCE_INVALID')


# ---------- explanations ----------

EXPLANATION_POLICY = 'relational-join-evidence-v2'
SIDES = ('baseline', 'target')
OUTCOMES = ('included_value', 'included_null_measure', 'excluded_join', 'excluded_filter_false', 'excluded_filter_unknown')
MEMBERSHIPS = ('included_in_both', 'baseline_only', 'target_only', 'included_in_neither')


def predicate_states(ir, row, contract):
    items = []
    tables = {'fact': contract['fact']['table'], **{r: d['table'] for r, d in dims(contract).items()}}
    for predicate in ir['predicates']:
        role, column, op = predicate['table'], predicate['column'], predicate['op']
        value = (row['fact_values'] if role == 'fact' else row['dimension_values'][role])[column]
        if op == 'is_null':
            state = 'true' if value is None else 'false'
        elif op == 'is_not_null':
            state = 'false' if value is None else 'true'
        elif value is None:
            state = 'unknown'
        elif op in ('neq', 'not_in'):
            state = 'true' if value not in predicate['values'] else 'false'
        elif op in RANGE_OPS:
            state = 'true' if compare_text(value, op, predicate['values'][0]) else 'false'
        else:
            state = 'true' if value in predicate['values'] else 'false'
        items.append({'table': role, 'field': tables[role] + '.' + column, 'column': column, 'op': op,
                      'values': predicate['values'], 'observed': value, 'state': state})
    states = {item['state'] for item in items}
    return ('false' if 'false' in states else 'unknown' if 'unknown' in states else 'true'), items


def explain_row(context, row):
    explanation = {'fact_key': row['fact_key'], 'links': row['links']}
    for side in SIDES:
        ir = context[side]
        if row[side + '_join_included']:
            where_state, predicates = predicate_states(ir, row, context['contract'])
            outcome = ('excluded_filter_false' if where_state == 'false' else
                       'excluded_filter_unknown' if where_state == 'unknown' else
                       'included_null_measure' if row['measure_value'] is None else 'included_value')
            missing = []
        else:
            where_state, predicates, outcome = 'not_evaluated', [], 'excluded_join'
            missing = [r for r, kind in ir['joins'].items() if kind == 'inner' and not row['links'][r]['matched']]
        if ((outcome in ('included_value', 'included_null_measure')) != row[side + '_included'] or
                (outcome == 'included_null_measure') != row[side + '_null_term']):
            raise ValueError('EVIDENCE_INVALID')
        explanation[side] = {'joins': ir['joins'], 'joined': row[side + '_join_included'], 'unmatched_inner_joins': missing,
                             'where_state': where_state, 'outcome': outcome, 'predicates': predicates}
    return explanation


class JoinEvidenceSummary:
    def __init__(self, context):
        self.context = context
        scale = context['contract']['measure']['scale']
        self.sides = {side: {outcome: 0 for outcome in OUTCOMES} for side in SIDES}
        self.memberships = {m: 0 for m in MEMBERSHIPS}
        self.relationships = {role: {'matched': 0, 'null_foreign_key': 0, 'missing_dimension': 0} for role in dims(context['contract'])}
        self.sums = {side: ScaledInteger.zero(scale) for side in SIDES}
        self.delta = ScaledInteger.zero(scale)
        self.changed_rows = self.nonzero_rows = self.rows = 0

    def add(self, row):
        explanation = explain_row(self.context, row)
        for side in SIDES:
            self.sides[side][explanation[side]['outcome']] += 1
            self.sums[side] += ScaledInteger.parse(row[side + '_contribution'])
        delta = ScaledInteger.parse(row['delta'])
        self.delta += delta
        self.rows += 1
        self.changed_rows += int(row['baseline_included'] != row['target_included'])
        self.nonzero_rows += int(delta != 0)
        self.memberships[row['membership']] += 1
        for role, link in row['links'].items():
            self.relationships[role][link['membership']] += 1

    def report(self, result, artifact):
        c, manifest = self.context['contract'], self.context['manifest']
        defined = result['baseline_value'] is not None and result['target_value'] is not None
        if self.sums['target'] - self.sums['baseline'] != self.delta:
            raise ValueError('EVIDENCE_INVALID')
        proof = manifest['join_proof']
        if self.rows != proof['fact_rows'] or any(
                self.relationships[r]['matched'] != item['matched_fact_rows'] or
                self.relationships[r]['null_foreign_key'] != item['null_foreign_key_rows'] or
                self.relationships[r]['missing_dimension'] != item['missing_nonnull_foreign_key_rows']
                for r, item in proof['dimensions'].items()):
            raise ValueError('EVIDENCE_INVALID')
        if defined and self.delta != ScaledInteger.parse(result['delta_target_minus_baseline']):
            raise ValueError('EVIDENCE_INVALID')
        report = {
            'schema_version': 2, 'policy': EXPLANATION_POLICY,
            'pins': {'execution_policy': manifest['execution_policy'], 'contract_version': c['version'],
                     'contract_hash': c['contract_hash'], 'snapshot_manifest_hash': manifest['manifest_hash'],
                     'database_hash': manifest['database_hash'], 'source_hashes': manifest['source_hashes'],
                     'context_hash': digest(context_for(c)),
                     'baseline_ir_hash': self.context['baseline']['ir_hash'], 'target_ir_hash': self.context['target']['ir_hash'],
                     'evidence_sha256': artifact['sha256'], 'relational_binding': artifact['relational_binding']},
            'contract': {key: c[key] for key in ('fact', 'dimensions', 'measure', 'measure_meaning', 'unit_label', 'scope', 'null_policy')},
            'context': context_for(c),
            'definitions': {side: {'joins': self.context[side]['joins'], 'predicates': self.context[side]['predicates']} for side in SIDES},
            'join_proof': proof, 'fact_rows': self.rows, 'relationship_counts': self.relationships,
            'membership_counts': self.memberships, 'side_counts': self.sides,
            'changed_inclusion_rows': self.changed_rows, 'nonzero_contribution_rows': self.nonzero_rows,
            'reconciliation': {'status': 'reconciled' if defined else 'not_applicable',
                               'baseline_value': result['baseline_value'], 'target_value': result['target_value'],
                               'delta_target_minus_baseline': result['delta_target_minus_baseline'],
                               'residual': result['residual'],
                               'baseline_bookkeeping_sum': self.sums['baseline'].to_fixed_string(),
                               'target_bookkeeping_sum': self.sums['target'].to_fixed_string(),
                               'row_delta_sum': self.delta.to_fixed_string()},
            'null_semantics': manifest['null_semantics'],
            'limitations': [*result['limitations'],
                            'Filter states explain inclusion, not a causal share of the gap. A row excluded by an INNER join has no evaluated WHERE state.',
                            'All-row counts and bookkeeping sums use the complete verified artifact; exported supporting records remain bounded.']}
        if not defined:
            report['limitations'].append('Row bookkeeping sums are not endpoint SUMs when no non-NULL terms exist; their difference is not a defined metric gap.')
        return {**report, 'summary_hash': digest(report)}
