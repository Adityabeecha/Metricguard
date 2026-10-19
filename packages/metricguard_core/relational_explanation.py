"""Versioned read-only explanations of already verified relational evidence.

This is a presentation policy, not a new SQL or execution policy. Callers must
validate the complete artifact before publishing a summary or page explanation.
No source queries, hybrid definitions or per-predicate attribution run here.
"""

from .common import digest
from .numeric import ScaledInteger
from .relational import context_for

POLICY = 'relational-join-evidence-v1'
SIDES = ('baseline', 'target')
OUTCOMES = ('included_value', 'included_null_measure', 'excluded_join',
            'excluded_filter_false', 'excluded_filter_unknown')
MEMBERSHIPS = ('included_in_both', 'baseline_only', 'target_only', 'included_in_neither')


def predicate_states(ir, row, contract):
    """Evaluate the existing bounded categorical leaves in SQL three-valued logic."""
    items = []
    for predicate in ir['predicates']:
        role, column, op = predicate['table'], predicate['column'], predicate['op']
        value = row[role + '_values'][column]
        if op == 'is_null':
            state = 'true' if value is None else 'false'
        elif op == 'is_not_null':
            state = 'false' if value is None else 'true'
        elif value is None:
            state = 'unknown'
        else:
            state = 'true' if value in predicate['values'] else 'false'
        items.append({'table': role, 'field': contract[role]['table'] + '.' + column,
                      'column': column, 'op': op, 'values': predicate['values'],
                      'observed': value, 'state': state})
    states = {item['state'] for item in items}
    # FALSE AND UNKNOWN is FALSE; only TRUE survives a WHERE clause.
    state = 'false' if 'false' in states else 'unknown' if 'unknown' in states else 'true'
    return state, items


def explain_row(context, row):
    """Describe a validated fact row without inferring missing source values."""
    if context['contract'].get('version') == 2:
        from .relational_star import explain_row as explain_star_row
        return explain_star_row(context, row)
    explanation = {'fact_key': row['fact_key'], 'join_membership': row['join_membership']}
    for side in SIDES:
        ir = context[side]
        joined = row[side + '_join_included']
        if joined:
            where_state, predicates = predicate_states(ir, row, context['contract'])
            outcome = ('excluded_filter_false' if where_state == 'false' else
                       'excluded_filter_unknown' if where_state == 'unknown' else
                       'included_null_measure' if row['measure_value'] is None else 'included_value')
        else:
            where_state, predicates, outcome = 'not_evaluated', [], 'excluded_join'
        expected_inclusion = outcome in ('included_value', 'included_null_measure')
        if (expected_inclusion != row[side + '_included'] or
                (outcome == 'included_null_measure') != row[side + '_null_term']):
            raise ValueError('EVIDENCE_INVALID')
        explanation[side] = {'join_kind': ir['join'], 'joined': joined, 'where_state': where_state,
                             'outcome': outcome, 'predicates': predicates}
    return explanation


class JoinEvidenceSummary:
    """Constant-size rollup over validated rows; never retain the full row set."""

    def __init__(self, context):
        self.context = context
        scale = context['contract']['measure']['scale']
        self.sides = {side: {outcome: 0 for outcome in OUTCOMES} for side in SIDES}
        self.memberships = {membership: 0 for membership in MEMBERSHIPS}
        self.relationships = {membership: 0 for membership in ('matched', 'null_foreign_key', 'missing_dimension')}
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
        self.relationships[row['join_membership']] += 1

    def report(self, result, artifact):
        c, manifest = self.context['contract'], self.context['manifest']
        defined = result['baseline_value'] is not None and result['target_value'] is not None
        if self.sums['target'] - self.sums['baseline'] != self.delta:
            raise ValueError('EVIDENCE_INVALID')
        proof = manifest['join_proof']
        if (self.rows != proof['fact_rows'] or self.relationships['matched'] != proof['matched_fact_rows'] or
                self.relationships['null_foreign_key'] != proof['null_foreign_key_rows'] or
                self.relationships['missing_dimension'] != proof['missing_nonnull_foreign_key_rows']):
            raise ValueError('EVIDENCE_INVALID')
        if defined and self.delta != ScaledInteger.parse(result['delta_target_minus_baseline']):
            raise ValueError('EVIDENCE_INVALID')
        report = {
            'schema_version': 1, 'policy': POLICY,
            'pins': {'execution_policy': manifest['execution_policy'], 'contract_version': c['version'],
                     'contract_hash': c['contract_hash'], 'snapshot_manifest_hash': manifest['manifest_hash'],
                     'database_hash': manifest['database_hash'], 'source_hashes': manifest['source_hashes'],
                     'context_hash': digest(context_for(c)),
                     'baseline_ir_hash': self.context['baseline']['ir_hash'], 'target_ir_hash': self.context['target']['ir_hash'],
                     'evidence_sha256': artifact['sha256'], 'relational_binding': artifact['relational_binding']},
            'contract': {key: c[key] for key in ('fact', 'dimension', 'relationship', 'measure', 'measure_meaning',
                                                'unit_label', 'scope', 'null_policy')},
            'context': context_for(c),
            'definitions': {side: {'join': self.context[side]['join'], 'predicates': self.context[side]['predicates']}
                            for side in SIDES},
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
                            'Filter states explain inclusion, not a causal share of the gap. An INNER-excluded row has no evaluated WHERE state.',
                            'All-row counts and bookkeeping sums use the complete verified artifact; exported supporting records remain bounded.']}
        if not defined:
            report['limitations'].append('Row bookkeeping sums are not endpoint SUMs when no non-NULL terms exist; their difference is not a defined metric gap.')
        return {**report, 'summary_hash': digest(report)}
