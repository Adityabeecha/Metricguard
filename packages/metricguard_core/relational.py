"""V8.1: one fact, one unique dimension, one exact additive measure.

This execution policy is deliberately independent of every flat/retail policy.
User SQL is lowered to a sealed allowlisted structure; only that structure is
compiled. Snapshot ingestion and evidence validation live in sibling modules.
"""

import re

import sqlglot
from sqlglot import exp

from .common import CoreError, digest

POLICY = 'relational-snapshot-v1'
EXECUTION_POLICY = 'relational_snapshot_v1'
MODE = 'relational_snapshot'
SCOPE = 'entire_supplied_snapshot'
MAX_ROWS = 100_000  # per table; evidence is one row per fact
MAX_BYTES = 25 * 1024 * 1024  # both sources combined
IDENT = re.compile(r'[a-z_][a-z0-9_]{0,63}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')


def fail(code, message):
    raise CoreError(code, message)


def quote(name):
    if not isinstance(name, str) or not IDENT.fullmatch(name):
        fail('invalid_identifier', 'Use registered lower-case SQL identifiers of at most 64 characters.')
    return '"' + name + '"'


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        fail('invalid_contract', f'{label} requires exactly: {", ".join(sorted(fields))}.')


def _label(value, maximum, field):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        fail('invalid_contract', f'{field} must contain 1–{maximum} characters.')


def _is_star(contract):
    return isinstance(contract, dict) and contract.get('version') in (2, 3)


def validate_contract(payload):
    if _is_star(payload):  # one fact and several lookup tables (relational_star.py)
        from .relational_star import validate_contract as validate_star
        return validate_star(payload)
    fields = {'version', 'mode', 'policy', 'name', 'fact', 'dimension', 'relationship',
              'measure', 'measure_meaning', 'unit_label', 'values_comparable', 'scope', 'null_policy'}
    if not isinstance(payload, dict):
        fail('invalid_contract', 'A relational contract object is required.')
    value = {k: v for k, v in payload.items() if k != 'contract_hash'}
    _object(value, fields, 'Relational contract')
    if (type(value['version']) is not int or value['version'] != 1 or value['mode'] != MODE or
            value['policy'] != POLICY or value['scope'] != SCOPE or value['values_comparable'] is not True or
            value['null_policy'] not in {'blank_nullable', 'literal_NULL'}):
        fail('invalid_contract', 'Use relational contract v1, whole-snapshot scope, comparable units and an explicit CSV NULL token.')
    for field, maximum in (('name', 160), ('measure_meaning', 300), ('unit_label', 80)):
        _label(value[field], maximum, field)
    for role in ('fact', 'dimension'):
        table = value[role]
        _object(table, {'table', 'key', 'grain', 'categories'}, role)
        quote(table['table']); quote(table['key']); _label(table['grain'], 300, role + ' grain')
        cats = table['categories']
        if not isinstance(cats, list) or len(cats) > 8 or any(not isinstance(n, str) for n in cats) or len(set(cats)) != len(cats):
            fail('invalid_contract', 'Each table allows at most eight distinct string categories.')
        for name in cats:
            quote(name)
        if table['key'] in cats:
            fail('invalid_contract', 'Keys and categorical roles must be distinct.')
    if value['fact']['table'] == value['dimension']['table']:
        fail('invalid_contract', 'Fact and dimension tables must have different names.')
    measure = value['measure']
    _object(measure, {'column', 'precision', 'scale'}, 'Measure')
    quote(measure['column'])
    p, s = measure['precision'], measure['scale']
    if type(p) is not int or type(s) is not int or not 1 <= p <= 38 or not 0 <= s <= min(p, 18):
        fail('invalid_contract', 'Measure precision is 1–38; scale is 0–18 and cannot exceed precision.')
    rel = value['relationship']
    _object(rel, {'fact_foreign_key', 'dimension_key', 'cardinality', 'missing_dimension_policy'}, 'Relationship')
    quote(rel['fact_foreign_key'])
    if (rel['dimension_key'] != value['dimension']['key'] or rel['cardinality'] != 'many_to_one' or
            rel['missing_dimension_policy'] != 'preserve_and_report'):
        fail('invalid_contract', 'Declare the dimension key, many_to_one cardinality and preserve_and_report missing keys.')
    identity_fields = {value['fact']['key'], rel['fact_foreign_key']}
    if (measure['column'] in identity_fields or measure['column'] in value['fact']['categories'] or
            identity_fields.intersection(value['fact']['categories'])):
        fail('invalid_contract', 'Measure and category roles must be distinct from key roles. The fact key may also be its foreign key.')
    canonical = {**value, 'fact': {**value['fact'], 'categories': sorted(value['fact']['categories'])},
                 'dimension': {**value['dimension'], 'categories': sorted(value['dimension']['categories'])}}
    sealed = digest(canonical)
    if 'contract_hash' in payload and payload['contract_hash'] != sealed:
        fail('contract_integrity', 'The relational contract hash does not match its contents.')
    return {**canonical, 'contract_hash': sealed}


def schemas(contract):
    if _is_star(contract):
        from .relational_star import schemas as star_schemas
        return star_schemas(contract)
    c = validate_contract(contract)
    m = c['measure']
    return {
        c['fact']['table']: {c['fact']['key']: 'VARCHAR', c['relationship']['fact_foreign_key']: 'VARCHAR',
                            m['column']: f'DECIMAL({m["precision"]},{m["scale"]})',
                            **{name: 'VARCHAR' for name in c['fact']['categories']}},
        c['dimension']['table']: {c['dimension']['key']: 'VARCHAR',
                                 **{name: 'VARCHAR' for name in c['dimension']['categories']}},
    }


def context_for(contract):
    c = validate_contract(contract)
    return {'mode': MODE, 'scope': SCOPE, 'contract_hash': c['contract_hash'],
            'unit_label': c['unit_label'], 'values_comparable': True}


def validate_ir(ir, contract):
    if _is_star(contract):
        from .relational_star import validate_ir as validate_star_ir
        return validate_star_ir(ir, contract)
    c = validate_contract(contract)
    fields = {'mode', 'policy', 'contract_version', 'contract_hash', 'aggregate', 'measure', 'join', 'predicates', 'ir_hash'}
    if not isinstance(ir, dict) or set(ir) != fields:
        fail('invalid_ir', 'Relational definitions require the exact versioned IR shape.')
    expected = {'mode': MODE, 'policy': POLICY, 'contract_version': 1, 'contract_hash': c['contract_hash'],
                'aggregate': 'sum', 'measure': {'table': 'fact', 'column': c['measure']['column']}}
    if any(ir.get(k) != v for k, v in expected.items()) or type(ir['contract_version']) is not int:
        fail('invalid_ir', 'Definition does not match the frozen relational measure and policy.')
    if ir['join'] not in ('inner', 'left'):
        fail('invalid_ir', 'Only the declared inner or left join is supported.')
    predicates = ir['predicates']
    if not isinstance(predicates, list) or len(predicates) > 16:
        fail('invalid_ir', 'At most 16 conjunctive categorical predicates are supported.')
    for pred in predicates:
        if not isinstance(pred, dict) or set(pred) != {'table', 'column', 'op', 'values'}:
            fail('invalid_ir', 'Malformed relational predicate.')
        role = pred['table']
        if role not in ('fact', 'dimension') or pred['column'] not in c[role]['categories']:
            fail('invalid_ir', 'Predicates may reference only registered categorical fields.')
        values, op = pred['values'], pred['op']
        if (op not in ('eq', 'in', 'is_null', 'is_not_null') or not isinstance(values, list) or
                any(not isinstance(v, str) or len(v) > 256 for v in values) or
                (op == 'eq' and len(values) != 1) or (op == 'in' and not 1 <= len(values) <= 20) or
                (op in ('is_null', 'is_not_null') and values) or values != sorted(set(values))):
            fail('invalid_ir', 'Invalid bounded string predicate values.')
    if predicates != sorted(predicates, key=digest) or len({digest(p) for p in predicates}) != len(predicates):
        fail('invalid_ir', 'Predicates must be distinct and canonical.')
    if ir['ir_hash'] != digest({k: v for k, v in ir.items() if k != 'ir_hash'}):
        fail('invalid_ir', 'Relational definition hash does not match its contents.')
    return ir


def parse_relational(raw_sql, contract):
    if _is_star(contract):
        from .relational_star import parse as parse_star
        return parse_star(raw_sql, contract)
    c = validate_contract(contract)
    if not isinstance(raw_sql, str) or not 1 <= len(raw_sql) <= 50_000:
        fail('unsupported_sql', 'Enter one bounded SELECT SUM query.')
    try:
        if any(token.token_type == sqlglot.tokens.TokenType.PLUS for token in sqlglot.tokenize(raw_sql, read='duckdb')):
            fail('unsupported_sql', 'Arithmetic operators are not supported, including unary plus.')
        statements = sqlglot.parse(raw_sql, read='duckdb')
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, RecursionError) as error:
        raise CoreError('unsupported_sql', 'SQL could not be parsed; keep one SELECT SUM with the declared join.') from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        fail('unsupported_sql', 'Exactly one SELECT is supported.')
    query = statements[0]
    if len(list(query.walk())) > 200:
        fail('unsupported_sql', 'Query exceeds the 200-node syntax limit.')
    if any(v for k, v in query.args.items() if k not in {'expressions', 'from_', 'joins', 'where'}):
        fail('unsupported_sql', 'No CTE, grouping, ordering, limits, distinct or additional SELECT clauses are supported.')
    source = query.args.get('from_')
    joins = query.args.get('joins') or []
    if not source or len(joins) != 1 or len(query.expressions) != 1:
        fail('unsupported_sql', 'Use one fact table, one dimension join and one SUM.')
    aliases = {}
    def table(node, role):
        if not isinstance(node, exp.Table) or any(v for k, v in node.args.items() if k not in {'this', 'alias'}):
            fail('unsupported_sql', 'Only registered plain tables and optional aliases are supported.')
        if not isinstance(node.this, exp.Identifier) or node.this.args.get('quoted') or node.name != c[role]['table']:
            fail('unsupported_sql', f'Use the registered {role} table {c[role]["table"]}.')
        alias = node.args.get('alias')
        qualifier = node.name
        if alias:
            if alias.args.get('columns') or not isinstance(alias.this, exp.Identifier) or alias.this.args.get('quoted'):
                fail('unsupported_sql', 'Use plain table aliases without column renaming.')
            qualifier = alias.name
            quote(qualifier)
        if qualifier in aliases:
            fail('unsupported_sql', 'Fact and dimension aliases must differ.')
        aliases[qualifier] = role
    table(source.this, 'fact')
    join = joins[0]
    table(join.this, 'dimension')
    if any(v for k, v in join.args.items() if k not in {'this', 'side', 'kind', 'on'}):
        fail('unsupported_sql', 'Join supports only ON equality; USING and other join clauses are refused.')
    side, kind = join.args.get('side') or '', join.args.get('kind') or ''
    if (side, kind) not in {('', ''), ('', 'INNER'), ('LEFT', ''), ('LEFT', 'OUTER')}:
        fail('unsupported_sql', 'Only INNER JOIN and LEFT JOIN are supported.')
    join_kind = 'left' if side == 'LEFT' else 'inner'
    def column(node):
        if not isinstance(node, exp.Column) or node.args.get('db') or node.args.get('catalog'):
            fail('unsupported_sql', 'Use qualified registered columns only.')
        if any(not isinstance(node.args.get(k), exp.Identifier) or node.args[k].args.get('quoted') for k in ('this', 'table')):
            fail('unsupported_sql', 'Every column must use its plain table name or alias.')
        if node.table not in aliases:
            fail('unsupported_sql', 'Column qualifier is not a registered table alias.')
        return aliases[node.table], node.name
    on = join.args.get('on')
    if not isinstance(on, exp.EQ):
        fail('unsupported_sql', 'Join must be the single declared foreign-key equality.')
    expected_keys = {('fact', c['relationship']['fact_foreign_key']), ('dimension', c['dimension']['key'])}
    if {column(on.this), column(on.expression)} != expected_keys:
        fail('unsupported_sql', 'Join columns differ from the declared foreign-key relationship.')
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
        op, values = None, []
        col = node.this
        if isinstance(node, exp.EQ):
            op, literals = 'eq', [node.expression]
        elif isinstance(node, exp.In) and not node.args.get('query') and not node.args.get('unnest'):
            op, literals = 'in', node.expressions
        elif isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
            op, literals = 'is_null', []
        elif isinstance(node, exp.Not) and isinstance(node.this, exp.Is) and isinstance(node.this.expression, exp.Null):
            op, literals, col = 'is_not_null', [], node.this.this
        else:
            fail('unsupported_sql', 'Filters support AND over string equality, IN, IS NULL or IS NOT NULL only.')
        role, name = column(col)
        if name not in c[role]['categories']:
            fail('unsupported_sql', f'{role}.{name} is not a registered categorical field.')
        if len(literals) > 20:
            fail('unsupported_sql', 'IN lists allow at most 20 string values.')
        for literal in literals:
            if not isinstance(literal, exp.Literal) or not literal.is_string or len(literal.this) > 256:
                fail('unsupported_sql', 'Categorical predicates require string literals of at most 256 characters.')
            values.append(literal.this)
        predicates.append({'table': role, 'column': name, 'op': op, 'values': sorted(set(values))})
        if len(predicates) > 16:
            fail('unsupported_sql', 'At most 16 categorical predicates are supported.')
    if query.args.get('where'):
        lower(query.args['where'].this)
    predicates = sorted({digest(p): p for p in predicates}.values(), key=digest)
    ir = {'mode': MODE, 'policy': POLICY, 'contract_version': 1, 'contract_hash': c['contract_hash'],
          'aggregate': 'sum', 'measure': {'table': 'fact', 'column': c['measure']['column']},
          'join': join_kind, 'predicates': predicates}
    ir['ir_hash'] = digest(ir)
    return validate_ir(ir, c)


def compile_predicates(ir, contract):
    if _is_star(contract):
        from .relational_star import compile_predicates as compile_star
        return compile_star(ir, contract)
    validate_ir(ir, contract)
    parts, params = [], []
    for p in ir['predicates']:
        col = ('f' if p['table'] == 'fact' else 'd') + '.' + quote(p['column'])
        if p['op'] in ('is_null', 'is_not_null'):
            parts.append(col + (' IS NULL' if p['op'] == 'is_null' else ' IS NOT NULL'))
        elif p['op'] == 'eq':
            parts.append(col + ' = ?'); params.extend(p['values'])
        else:
            parts.append(col + ' IN (' + ','.join('?' for _ in p['values']) + ')'); params.extend(p['values'])
    return '(' + ' AND '.join(parts or ['TRUE']) + ')', params


def includes(ir, matched, fact_values, dimension_values):
    if ir['join'] == 'inner' and not matched:
        return False
    for p in ir['predicates']:
        value = (fact_values if p['table'] == 'fact' else dimension_values)[p['column']]
        if p['op'] == 'is_null':
            accepted = value is None
        elif p['op'] == 'is_not_null':
            accepted = value is not None
        else:
            accepted = value is not None and value in p['values']
        if not accepted:
            return False
    return True
