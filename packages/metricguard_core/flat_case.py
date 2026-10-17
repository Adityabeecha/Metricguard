"""Restricted searched CASE policy, independent IR checks and native row semantics.

Evidence verification lives in flat_case_evidence and reuses the same compiled
predicate SQL. This module deliberately has no second predicate evaluator.
"""

import re

from sqlglot import exp

from .common import CoreError, digest
from .flat_boolean import PREDICATE_POLICY, compile_boolean, lower_boolean, validate_boolean
from .numeric import ScaledInteger

MEASURE_POLICY = 'flat-case-v1'
MAX_COMBINED_NODES = 96
MAX_COMBINED_LEAVES = 24
MAX_SQL_AST_NODES = 250
ZERO_LITERAL = re.compile(r'(?:0+(?:\.0*)?|\.0+)\Z')


def _fail(message, code='invalid_ir'):
    raise CoreError(code, message)


def validate_case_ast(tree):
    """Allowlist active AST fields before lowering any v7 SQL.

    Typed date/timestamp literals use SQLGlot Cast/DataType nodes; the existing
    typed-leaf parser still restricts those to canonical literal values. Neg is
    only meaningful for a decimal predicate literal, never a CASE branch.
    """
    fields = {
        exp.Select: {'expressions', 'from_', 'where'},
        exp.From: {'this'}, exp.Table: {'this'}, exp.Where: {'this'},
        exp.Sum: {'this'}, exp.Case: {'ifs', 'default'}, exp.If: {'this', 'true'},
        exp.Column: {'this'}, exp.Identifier: {'this', 'quoted'},
        exp.Literal: {'this', 'is_string'}, exp.Boolean: {'this'}, exp.Null: set(),
        exp.And: {'this', 'expression'}, exp.Or: {'this', 'expression'},
        exp.Not: {'this'}, exp.Paren: {'this'}, exp.Neg: {'this'},
        exp.EQ: {'this', 'expression'}, exp.NEQ: {'this', 'expression'},
        exp.GT: {'this', 'expression'}, exp.GTE: {'this', 'expression'},
        exp.LT: {'this', 'expression'}, exp.LTE: {'this', 'expression'},
        exp.Is: {'this', 'expression'}, exp.In: {'this', 'expressions'},
        exp.Between: {'this', 'low', 'high'},
        exp.Cast: {'this', 'to'}, exp.DataType: {'this'},
    }
    for count, node in enumerate(tree.walk(), 1):
        allowed = fields.get(type(node))
        if count > MAX_SQL_AST_NODES or allowed is None:
            _fail('CASE SQL contains an unsupported expression or exceeds 250 AST nodes', 'unsupported_sql')
        for key, value in node.args.items():
            # SQLGlot includes absent optional slots as None, False or [].
            absent = value is None or value is False or (isinstance(value, list) and not value)
            if key not in allowed and not absent:
                _fail(f'Unsupported CASE SQL field {key} on {type(node).__name__}', 'unsupported_sql')


def parse_case(node, where, contract):
    from .flat import POLICY, _identifier, _typed_predicates

    if type(node) is not exp.Case or node.args.get('this') is not None:
        _fail('Only searched CASE with one WHEN and ELSE zero is supported', 'unsupported_sql')
    validate_case_ast(node)
    if where is not None:
        if type(where) is not exp.Where:
            _fail('Unsupported outer WHERE structure', 'unsupported_sql')
        validate_case_ast(where)
    branches = node.args.get('ifs')
    zero = node.args.get('default')
    if not isinstance(branches, list) or len(branches) != 1 or type(branches[0]) is not exp.If:
        _fail('CASE requires exactly one WHEN branch', 'unsupported_sql')
    branch = branches[0]
    measure = branch.args.get('true')
    if type(measure) is not exp.Column or _identifier(measure.this) != contract['measure']:
        _fail('THEN must be the registered decimal measure', 'unsupported_sql')
    if (type(zero) is not exp.Literal or zero.is_string or len(str(zero.this)) > 256
            or not ZERO_LITERAL.fullmatch(str(zero.this))):
        _fail('CASE requires mandatory bounded ELSE numeric zero', 'unsupported_sql')
    try:
        when_tree = lower_boolean(branch.this, lambda n: _typed_predicates(n, contract)[0])
        where_tree = lower_boolean(where.this if where is not None else None,
                                   lambda n: _typed_predicates(n, contract)[0])
        ir = {'mode': 'flat_snapshot', 'policy': POLICY, 'contract_version': 7,
              'contract_hash': contract['contract_hash'], 'measure_policy': MEASURE_POLICY,
              'predicate_policy': PREDICATE_POLICY, 'table': 'snapshot', 'aggregate': 'sum',
              'measure_expression': {'op': 'case', 'when': when_tree, 'then': contract['measure'], 'else': '0'},
              'predicate_tree': where_tree}
        ir['ir_hash'] = digest(ir)
        return check_case_ir(ir, contract)
    except CoreError as exc:
        raise CoreError('unsupported_sql', str(exc)) from exc


def check_case_ir(ir, contract):
    """Validate semantics, canonical trees and bounds before accepting any hash."""
    from .flat import POLICY, SHA256, _check_flat_predicates, validate_contract

    contract = validate_contract(contract)
    fields = {'mode', 'policy', 'contract_version', 'contract_hash', 'measure_policy',
              'predicate_policy', 'table', 'aggregate', 'measure_expression', 'predicate_tree', 'ir_hash'}
    if not isinstance(ir, dict) or set(ir) != fields:
        _fail('CASE IR shape is invalid')
    expected = {'mode': 'flat_snapshot', 'policy': POLICY, 'contract_version': 7,
                'contract_hash': contract['contract_hash'], 'measure_policy': MEASURE_POLICY,
                'predicate_policy': PREDICATE_POLICY, 'table': 'snapshot', 'aggregate': 'sum'}
    if contract['version'] != 7 or type(ir['contract_version']) is not int or any(ir[k] != v for k, v in expected.items()):
        _fail('CASE IR policy or contract differs')
    expression = ir['measure_expression']
    if (not isinstance(expression, dict) or set(expression) != {'op', 'when', 'then', 'else'}
            or expression['op'] != 'case' or expression['then'] != contract['measure']
            or expression['else'] != '0' or expression['when'] is None):
        _fail('CASE expression must use its registered measure and ELSE zero')
    counts = [validate_boolean(tree, lambda p: _check_flat_predicates([p], contract))
              for tree in (ir['predicate_tree'], expression['when'])]
    if sum(nodes for nodes, _ in counts) > MAX_COMBINED_NODES or sum(leaves for _, leaves in counts) > MAX_COMBINED_LEAVES:
        _fail('CASE WHERE and WHEN together exceed 96 nodes or 24 leaves')
    if not isinstance(ir['ir_hash'], str) or not SHA256.fullmatch(ir['ir_hash']):
        _fail('CASE IR hash is invalid')
    if digest({k: v for k, v in ir.items() if k != 'ir_hash'}) != ir['ir_hash']:
        _fail('CASE IR hash differs')
    return ir


def case_query(ir, contract):
    from .flat import _quote, _predicates_sql

    check_case_ir(ir, contract)
    compile_leaf = lambda p: _predicates_sql({'predicates': [p]}, contract)
    where, where_params = compile_boolean(ir['predicate_tree'], compile_leaf)
    when, when_params = compile_boolean(ir['measure_expression']['when'], compile_leaf)
    measure = _quote(contract['measure'])
    columns = [contract['measure'], *contract['categories']]
    # WHERE is a value, not a row-eliminating clause: diagnostic predicate states
    # and native CASE terms are retained even for outer-excluded source rows.
    sql = (f'SELECT {_quote(contract["row_key"])}, {where}, {when}, '
           f'CASE WHEN {when} THEN {measure} ELSE CAST(0 AS DECIMAL({contract["precision"]},{contract["scale"]})) END, '
           + ', '.join(_quote(c) for c in columns)
           + f' FROM snapshot ORDER BY {_quote(contract["row_key"])}')
    return sql, where_params + when_params + when_params


def _cell(value, kind):
    if value is None:
        return None
    if kind == 'boolean':
        return 'true' if value else 'false'
    if kind in {'date', 'timestamp'}:
        return value.isoformat()
    if kind == 'decimal':
        return format(value, 'f')
    return value


def _state(value):
    return 'unknown' if value is None else 'true' if value is True else 'false'


def case_endpoint_rows(rows, contract):
    columns = [contract['measure'], *contract['categories']]
    kinds = {contract['measure']: 'decimal', **contract['dimension_types']}
    result, terms = {}, []
    for key, outer, when, term, *values in rows:
        inputs = {c: _cell(v, kinds[c]) for c, v in zip(columns, values)}
        included = outer is True
        term_string = None if term is None else format(term, 'f')
        contribution = (ScaledInteger.parse(term_string) if included and term_string is not None
                        else ScaledInteger.zero(contract['scale']))
        result[key] = {'included': included, 'contribution': contribution,
                       'null_term': included and term is None, 'inputs': inputs,
                       'where_state': _state(outer), 'when_state': _state(when),
                       'branch': 'then' if when is True else 'else',
                       'term': term_string, 'measure_value': inputs[contract['measure']]}
        if included and term is not None:
            terms.append(contribution)
    return result, terms


def case_evidence_row(key, baseline, target):
    if baseline['inputs'] != target['inputs'] or baseline['measure_value'] != target['measure_value']:
        _fail('CASE definitions did not read the same immutable row inputs', 'snapshot_integrity')
    ai, bi = baseline['included'], target['included']
    result = {'order_id': key, 'predicate_inputs': baseline['inputs'],
              'measure_value': baseline['measure_value'],
              'membership': 'included_in_both' if ai and bi else 'baseline_only' if ai else 'target_only' if bi else 'included_in_neither',
              'delta': (target['contribution'] - baseline['contribution']).to_fixed_string()}
    for side, value in (('baseline', baseline), ('target', target)):
        for field in ('included', 'null_term', 'where_state', 'when_state', 'branch'):
            result[side + '_' + field] = value[field]
        result[side + '_case_term'] = value['term']
        result[side + '_contribution'] = value['contribution'].to_fixed_string()
    return result
