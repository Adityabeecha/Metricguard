"""Bounded boolean trees shared by opt-in FlatContracts v5–v7."""

import json

from sqlglot import exp

from .common import CoreError

PREDICATE_POLICY = 'flat-boolean-v1'
MAX_DEPTH = 8
MAX_NODES = 63
MAX_LEAVES = 16


def _key(node):
    return json.dumps(node, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _canonical(node):
    op = node['op']
    if op == 'predicate':
        return node
    if op == 'not':
        return {'op': op, 'child': _canonical(node['child'])}
    children = []
    for child in node['children']:
        child = _canonical(child)
        children.extend(child['children'] if child['op'] == op else [child])
    return {'op': op, 'children': sorted(children, key=_key)}


def lower_boolean(root, lower_leaf):
    """Keep AND/OR/NOT in typed IR; never apply two-valued simplifications."""
    count = [0, 0]

    def lower(node, depth=1):
        if isinstance(node, exp.Paren):
            return lower(node.this, depth)
        count[0] += 1
        if depth > MAX_DEPTH or count[0] > MAX_NODES:
            raise CoreError('unsupported_sql', 'Boolean expression exceeds depth 8 or 63 nodes')
        if isinstance(node, (exp.And, exp.Or)):
            # SQLGlot represents a flat chain as a left-deep binary tree. Flatten
            # this associative operator before applying the logical depth bound.
            pending, children = [node], []
            while pending:
                child = pending.pop()
                while isinstance(child, exp.Paren):
                    child = child.this
                if type(child) is type(node):
                    pending.extend([child.expression, child.this])
                else:
                    children.append(lower(child, depth + 1))
            return {'op': 'and' if isinstance(node, exp.And) else 'or',
                    'children': children}
        if isinstance(node, exp.Not):
            return {'op': 'not', 'child': lower(node.this, depth + 1)}
        count[1] += 1
        if count[1] > MAX_LEAVES:
            raise CoreError('unsupported_sql', 'Boolean expression exceeds 16 predicate leaves')
        return {'op': 'predicate', 'predicate': lower_leaf(node)}

    return None if root is None else _canonical(lower(root))


def validate_boolean(root, validate_leaf):
    """Validate before compilation and return (nodes, leaves) for combined bounds.

    An absent WHERE is a constant TRUE, not a predicate leaf. Callers can use the
    validated counts without recursively traversing untrusted trees a second time.
    """
    if root is None:
        return 0, 0
    count = [0, 0]

    def visit(node, depth=1):
        count[0] += 1
        if depth > MAX_DEPTH or count[0] > MAX_NODES or not isinstance(node, dict):
            raise CoreError('invalid_ir', 'Boolean tree shape or complexity is invalid')
        op = node.get('op')
        if not isinstance(op, str):
            raise CoreError('invalid_ir', 'Boolean tree operator must be a string')
        if op == 'predicate' and set(node) == {'op', 'predicate'}:
            count[1] += 1
            if count[1] > MAX_LEAVES:
                raise CoreError('invalid_ir', 'Boolean tree exceeds 16 predicate leaves')
            validate_leaf(node['predicate'])
        elif op == 'not' and set(node) == {'op', 'child'}:
            visit(node['child'], depth + 1)
        elif op in ('and', 'or') and set(node) == {'op', 'children'}:
            children = node['children']
            if not isinstance(children, list) or not 2 <= len(children) <= MAX_LEAVES:
                raise CoreError('invalid_ir', 'AND/OR requires 2 to 16 children')
            for child in children:
                visit(child, depth + 1)
        else:
            raise CoreError('invalid_ir', 'Unsupported boolean tree node')

    visit(root)
    if root != _canonical(root):
        raise CoreError('invalid_ir', 'Boolean tree is not canonical')
    return count[0], count[1]


def compile_boolean(root, compile_leaf):
    """Called only after validate_boolean; identifiers/values come from typed leaves."""
    if root is None:
        return 'TRUE', []
    op = root['op']
    if op == 'predicate':
        sql, params = compile_leaf(root['predicate'])
        return '(' + sql + ')', params
    if op == 'not':
        sql, params = compile_boolean(root['child'], compile_leaf)
        return '(NOT ' + sql + ')', params
    parts, params = [], []
    for child in root['children']:
        sql, values = compile_boolean(child, compile_leaf)
        parts.append(sql)
        params.extend(values)
    return '(' + (' AND ' if op == 'and' else ' OR ').join(parts) + ')', params
