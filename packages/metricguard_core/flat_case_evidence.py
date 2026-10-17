"""V7 CASE evidence consistency checks using the execution predicate compiler.

Typed artifact inputs are NOT independent proof of source authenticity. This
module verifies consistency with trusted frozen definitions. The supervisor's
immutable snapshot/source checks and published artifact integrity remain the
source-authenticity boundary. Never accept executable context from an artifact.
"""

import duckdb

from .flat import (MAX_FLAT_ROWS, POLICY, _amount, _canonical_type, _quote,
                   flat_schema, validate_contract)
from .flat_boolean import PREDICATE_POLICY
from .flat_case import (MEASURE_POLICY, case_endpoint_rows, case_evidence_row,
                        case_query, check_case_ir)
from .numeric import ScaledInteger

BATCH_ROWS = 256
CASE_FIELDS = {'predicate_inputs', 'measure_value', 'baseline_where_state',
               'target_where_state', 'baseline_when_state', 'target_when_state',
               'baseline_branch', 'target_branch', 'baseline_case_term', 'target_case_term'}


def trusted_case_context(context):
    """Check both definition identities and all structures before compiling SQL."""
    try:
        if not isinstance(context, dict) or set(context) != {'contract', 'baseline', 'target'}:
            raise ValueError('EVIDENCE_INVALID')
        contract = validate_contract(context['contract'])
        if contract['version'] != 7:
            raise ValueError('EVIDENCE_INVALID')
        return {'contract': contract,
                'baseline': check_case_ir(context['baseline'], contract),
                'target': check_case_ir(context['target'], contract)}
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('EVIDENCE_INVALID') from error


def case_binding(context):
    context = trusted_case_context(context)
    return {'contract_hash': context['contract']['contract_hash'],
            'baseline_ir_hash': context['baseline']['ir_hash'],
            'target_ir_hash': context['target']['ir_hash'],
            'measure_policy': MEASURE_POLICY}


def _endpoint(value, scale):
    if value is None:
        return None
    number = ScaledInteger.parse(value)
    if (number.scale > scale or
            abs(number.coeff) * 10 ** scale > (10 ** 38 - 1) * 10 ** number.scale):
        raise ValueError('EVIDENCE_INVALID')
    return number


def validate_case_result(result, context):
    """Version-gated summary/bridge checks, including one-undefined-endpoint runs."""
    context = trusted_case_context(context)
    contract, baseline, target = (context[k] for k in ('contract', 'baseline', 'target'))
    try:
        av = _endpoint(result['baseline_value'], contract['scale'])
        bv = _endpoint(result['target_value'], contract['scale'])
        defined = av is not None and bv is not None
        gap = bv - av if defined else None
        provenance = result['provenance']
        expected_provenance = {**case_binding(context), 'contract_version': 7,
                               'predicate_policy': PREDICATE_POLICY,
                               'execution_policy': POLICY, 'evidence_unit': 'row_key'}
        if (not isinstance(provenance, dict) or
                any(provenance.get(k) != v for k, v in expected_provenance.items()) or
                type(provenance.get('contract_version')) is not int):
            raise ValueError('EVIDENCE_INVALID')
        expected_labels = {
            'method': 'flat_case_change', 'currency': None,
            'unit_label': contract['unit_label'], 'measure_meaning': contract['measure_meaning'],
            'numeric_tolerance': '0', 'shapley': None,
            'definition_status': 'same_supported_representation' if baseline == target else 'different',
            'value_status': 'defined' if defined else 'undefined',
            'explanation_status': 'complete_case_definition_change' if defined else 'unavailable',
        }
        if any(k not in result or result[k] != v for k, v in expected_labels.items()):
            raise ValueError('EVIDENCE_INVALID')
        if defined:
            if (ScaledInteger.parse(result['delta_target_minus_baseline']) != gap or
                    ScaledInteger.parse(result['absolute_gap']) != abs(gap) or
                    ScaledInteger.parse(result['residual']) != 0):
                raise ValueError('EVIDENCE_INVALID')
        elif any(result[k] is not None for k in ('delta_target_minus_baseline', 'absolute_gap', 'residual')):
            raise ValueError('EVIDENCE_INVALID')
        step = {
            'rule': 'case_definition_change',
            'baseline': {'where': baseline['predicate_tree'], 'case': baseline['measure_expression']},
            'target': {'where': target['predicate_tree'], 'case': target['measure_expression']},
            'changed': baseline != target, 'position': 1,
            'previous_value': result['baseline_value'], 'next_value': result['target_value'],
            'value_before': result['baseline_value'], 'value_after': result['target_value'],
            'contribution': result['delta_target_minus_baseline'],
        }
        if (result['steps'] != ([step] if defined else []) or
                result['definition_differences'] != [step]):
            raise ValueError('EVIDENCE_INVALID')
        for actual in result['steps'] + result['definition_differences']:
            if type(actual['changed']) is not bool or type(actual['position']) is not int:
                raise ValueError('EVIDENCE_INVALID')
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('EVIDENCE_INVALID') from error


class CaseEvidenceValidator:
    """Re-evaluate at most 256 typed rows at once, retaining only endpoint totals.

    This is deliberately native DuckDB evaluation through case_query, rather than
    a second Python interpretation of SQL's three-valued boolean language.
    """

    def __init__(self, context):
        self.context = trusted_case_context(context)
        self.contract = self.context['contract']
        self.schema = flat_schema(self.contract)
        self.queries = [case_query(self.context[side], self.contract)
                        for side in ('baseline', 'target')]
        self.rows = []
        self.connection = None
        self.previous_key = None
        self.row_count = 0
        self.sums = {side: ScaledInteger.zero(self.contract['scale']) for side in ('baseline', 'target')}
        self.terms = {side: 0 for side in self.sums}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.finish()
        finally:
            if self.connection is not None:
                self.connection.close()

    def add(self, row):
        try:
            key, inputs = row['order_id'], row['predicate_inputs']
            if (not isinstance(key, str) or not 1 <= len(key) <= 256 or
                    (self.previous_key is not None and key <= self.previous_key) or
                    self.row_count >= MAX_FLAT_ROWS or not isinstance(inputs, dict) or
                    set(inputs) != {self.contract['measure'], *self.contract['categories']}):
                raise ValueError('EVIDENCE_INVALID')
            if any(type(row.get(side + '_' + flag)) is not bool
                   for side in self.sums for flag in ('included', 'null_term')):
                raise ValueError('EVIDENCE_INVALID')
            _amount(inputs[self.contract['measure']], self.contract, self.row_count + 1)
            for name in self.contract['categories']:
                value = inputs[name]
                if value is None:
                    # Absent optional nullable_categories retains historical permissiveness.
                    if ('nullable_categories' in self.contract and
                            name not in self.contract['nullable_categories']):
                        raise ValueError('EVIDENCE_INVALID')
                elif not _canonical_type(value, self.contract['dimension_types'][name]):
                    raise ValueError('EVIDENCE_INVALID')
            self.previous_key = key
            self.row_count += 1
            self.rows.append(row)
            if len(self.rows) == BATCH_ROWS:
                self.flush()
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('EVIDENCE_INVALID') from error

    def flush(self):
        if not self.rows:
            return
        try:
            if self.connection is None:
                self.connection = duckdb.connect(':memory:', config={
                    'enable_external_access': 'false', 'autoinstall_known_extensions': 'false',
                    'autoload_known_extensions': 'false', 'memory_limit': '64MB', 'threads': '1'})
                self.connection.execute('SET lock_configuration = true')
                self.connection.execute('CREATE TABLE snapshot (' + ', '.join(
                    _quote(name) + ' ' + kind for name, kind in self.schema.items()) + ')')
            con = self.connection
            con.execute('DELETE FROM snapshot')
            placeholders = '(' + ','.join('?' for _ in self.schema) + ')'
            parameters = []
            for row in self.rows:
                parameters.extend([row['order_id'], *[row['predicate_inputs'][name]
                                  for name in self.schema if name != self.contract['row_key']]])
            con.execute('INSERT INTO snapshot VALUES ' + ','.join(placeholders for _ in self.rows), parameters)
            endpoints = [case_endpoint_rows(con.execute(sql, params).fetchall(), self.contract)[0]
                         for sql, params in self.queries]
            for row in self.rows:
                key = row['order_id']
                expected = case_evidence_row(key, endpoints[0][key], endpoints[1][key])
                # Exact canonical inputs/terms and states are required for v7. All
                # numeric values were produced by the same frozen physical types.
                if set(row) != set(expected) or row != expected:
                    raise ValueError('EVIDENCE_INVALID')
                for side in self.sums:
                    self.sums[side] += ScaledInteger.parse(expected[side + '_contribution'])
                    self.terms[side] += int(expected[side + '_included'] and not expected[side + '_null_term'])
            self.rows.clear()
        except (duckdb.Error, KeyError, TypeError, ValueError) as error:
            raise ValueError('EVIDENCE_INVALID') from error

    def finish(self, expected_result=None):
        self.flush()
        if expected_result is not None:
            validate_case_result(expected_result, self.context)
        for side in self.sums:
            value = self.sums[side] if self.terms[side] else None
            # Check only the final accumulated endpoint; do not prohibit cancellation.
            if value is not None:
                _endpoint(value.to_fixed_string(), self.contract['scale'])
            if expected_result is not None:
                reported = _endpoint(expected_result[side + '_value'], self.contract['scale'])
                if (value is None) != (reported is None) or (value is not None and value != reported):
                    raise ValueError('EVIDENCE_INVALID')


def validate_case_evidence_row(row, context):
    """Single-row consistency check; artifact paths reuse one batched validator."""
    with CaseEvidenceValidator(context) as validator:
        validator.add(row)
