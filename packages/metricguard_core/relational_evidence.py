"""Validate joined row semantics against trusted frozen definitions and manifest.

Source authenticity still relies on the isolated fixed runner and snapshot
hash. These checks enforce membership, arithmetic and grain across the artifact.
"""

from .common import digest, CoreError
from .numeric import ScaledInteger
from .relational import context_for, includes, validate_ir
from .relational_snapshot import amount, validate_manifest
from .relational_explanation import JoinEvidenceSummary

FIELDS = {'fact_key', 'foreign_key', 'dimension_key', 'dimension_matched', 'join_membership',
          'baseline_join_included', 'target_join_included', 'fact_values', 'dimension_values', 'measure_value'}
BASE_FIELDS = {'order_id', 'baseline_included', 'target_included', 'baseline_contribution',
               'target_contribution', 'delta', 'baseline_null_term', 'target_null_term', 'membership'}


def binding(context):
    c = validate_manifest(context['manifest'], context['contract'])
    a, b = (validate_ir(context[k], c) for k in ('baseline', 'target'))
    return digest({'policy': c['policy'], 'contract_hash': c['contract_hash'],
                   'snapshot_manifest_hash': context['manifest']['manifest_hash'],
                   'baseline_ir_hash': a['ir_hash'], 'target_ir_hash': b['ir_hash']})


def validate_result(result, context):
    c, manifest = context['contract'], context['manifest']
    binding(context)
    a, b = context['baseline'], context['target']
    expected = {'execution_policy': 'relational_snapshot_v1', 'engine_version': c['policy'], 'contract_version': 1,
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
    if result.get('explanation_status') != ('complete_join_filter_change' if defined else 'unavailable'):
        raise ValueError('RELATIONAL_RESULT_INVALID')
    if not defined and (any(result.get(k) is not None for k in ('delta_target_minus_baseline', 'absolute_gap', 'residual')) or result.get('steps') != []):
        raise ValueError('RELATIONAL_RESULT_INVALID')
    if defined:
        steps = result['steps']
        if len(steps) != 1 or steps[0].get('rule') != 'join_and_filter_change':
            raise ValueError('RELATIONAL_RESULT_INVALID')
        for side, ir in (('baseline', a), ('target', b)):
            if steps[0].get(side) != {'join': ir['join'], 'predicates': ir['predicates']}:
                raise ValueError('RELATIONAL_RESULT_INVALID')
        av, bv, delta = (ScaledInteger.parse(result[k]) for k in ('baseline_value', 'target_value', 'delta_target_minus_baseline'))
        if (bv - av != delta or ScaledInteger.parse(result['residual']) != 0 or
                ScaledInteger.parse(steps[0]['contribution']) != delta or
                steps[0]['previous_value'] != result['baseline_value'] or steps[0]['next_value'] != result['target_value']):
            raise ValueError('RELATIONAL_RESULT_INVALID')


class RelationalEvidenceValidator:
    def __init__(self, context, *, include_join_evidence=False):
        binding(context)
        self.context = context
        self.contract = context['contract']
        self.keys, self.dimensions, self.missing_keys = set(), {}, set()
        self.matched = self.null_fk = self.rows = 0
        self.sums = [ScaledInteger.zero(self.contract['measure']['scale']) for _ in range(2)]
        self.terms = [0, 0]
        self.explanation = JoinEvidenceSummary(context) if include_join_evidence else None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.keys.clear(); self.dimensions.clear(); self.missing_keys.clear()

    def add(self, row):
        try:
            self._add(row)
        except (CoreError, KeyError, TypeError, ValueError) as error:
            raise ValueError('EVIDENCE_INVALID') from error

    def _add(self, row):
        if not isinstance(row, dict) or set(row) != BASE_FIELDS | FIELDS:
            raise ValueError('EVIDENCE_INVALID')
        c = self.contract
        key, fk, dk = row['fact_key'], row['foreign_key'], row['dimension_key']
        if not isinstance(key, str) or not key or len(key) > 256 or key != row['order_id'] or key in self.keys:
            raise ValueError('EVIDENCE_INVALID')
        for value in (fk, dk):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
                raise ValueError('EVIDENCE_INVALID')
        matched = dk is not None
        if type(row['dimension_matched']) is not bool or row['dimension_matched'] != matched or (matched and fk != dk):
            raise ValueError('EVIDENCE_INVALID')
        if row['join_membership'] != ('matched' if matched else 'null_foreign_key' if fk is None else 'missing_dimension'):
            raise ValueError('EVIDENCE_INVALID')
        for role, field in (('fact', 'fact_values'), ('dimension', 'dimension_values')):
            values = row[field]
            if (not isinstance(values, dict) or set(values) != set(c[role]['categories']) or
                    any(v is not None and (not isinstance(v, str) or len(v) > 256 or '\x00' in v) for v in values.values())):
                raise ValueError('EVIDENCE_INVALID')
        if not matched and any(v is not None for v in row['dimension_values'].values()):
            raise ValueError('EVIDENCE_INVALID')
        if matched:
            if dk in self.missing_keys or (dk in self.dimensions and self.dimensions[dk] != row['dimension_values']):
                raise ValueError('EVIDENCE_INVALID')
            self.dimensions[dk] = row['dimension_values']
        elif fk is not None:
            if fk in self.dimensions:
                raise ValueError('EVIDENCE_INVALID')
            self.missing_keys.add(fk)
        raw = row['measure_value']
        canonical = amount(raw, c)
        if canonical != raw:
            raise ValueError('EVIDENCE_INVALID')
        n = ScaledInteger.parse(raw) if raw is not None else ScaledInteger.zero(c['measure']['scale'])
        for i, side in enumerate(('baseline', 'target')):
            ir = self.context[side]
            joined = ir['join'] == 'left' or matched
            inc = includes(ir, matched, row['fact_values'], row['dimension_values'])
            flags = {side + '_join_included': joined, side + '_included': inc,
                     side + '_null_term': inc and raw is None}
            if any(type(row[k]) is not bool or row[k] != v for k, v in flags.items()):
                raise ValueError('EVIDENCE_INVALID')
            expected = n if inc and raw is not None else ScaledInteger.zero(c['measure']['scale'])
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
        self.keys.add(key); self.rows += 1; self.matched += int(matched); self.null_fk += int(fk is None)
        if self.rows > self.context['manifest']['row_count']:
            raise ValueError('EVIDENCE_INVALID')
        if self.explanation is not None:
            self.explanation.add(row)

    def finish(self, result):
        validate_result(result, self.context)
        p = self.context['manifest']['join_proof']
        if (self.rows != p['fact_rows'] or self.matched != p['matched_fact_rows'] or self.null_fk != p['null_foreign_key_rows'] or
                len(self.dimensions) > p['dimension_rows']):
            raise ValueError('EVIDENCE_INVALID')
        for i, side in enumerate(('baseline', 'target')):
            total = result[side + '_value']
            if (total is None) != (self.terms[i] == 0):
                raise ValueError('EVIDENCE_INVALID')
            if total is not None and (ScaledInteger.parse(total) != self.sums[i] or abs(self.sums[i].coeff) >= 10 ** 38):
                raise ValueError('EVIDENCE_INVALID')
