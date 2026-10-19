"""Verifier for ratio evidence artifacts (flat-ratio-v1).

Every row is recomputed from its own amount, its filter outcomes and the frozen part
definitions; its share is recomputed from the run's totals. At the end the totals the
rows add up to must reproduce the stored ratio block exactly, so the artifact proves
the ratios, their change, the numerator/denominator split and every record share.
"""
from __future__ import annotations

from fractions import Fraction

from . import flat_ratio as fr
from .common import digest
from .numeric import ScaledInteger, parse_fixed_point

ROW_FIELDS = {'order_id', 'baseline_included', 'target_included', 'membership',
              'baseline_numerator_input', 'target_numerator_input', 'baseline_denominator_input', 'target_denominator_input',
              'baseline_in_numerator', 'target_in_numerator', 'baseline_in_denominator', 'target_in_denominator',
              'baseline_numerator', 'target_numerator', 'baseline_denominator', 'target_denominator',
              'baseline_null_term', 'target_null_term', 'delta'}
FLAGS = ('baseline_included', 'target_included', 'baseline_in_numerator', 'target_in_numerator',
         'baseline_in_denominator', 'target_in_denominator', 'baseline_null_term', 'target_null_term')


def _invalid():
    raise ValueError('EVIDENCE_INVALID')


def binding(context):
    baseline, target = context['baseline'], context['target']
    return {'policy': fr.RATIO_POLICY, 'contract_hash': context['contract']['contract_hash'],
            'baseline_ir_hash': baseline['ir_hash'], 'target_ir_hash': target['ir_hash']}


def _coefficient(text, scale):
    """Canonical fixed-point text at exactly `scale` decimals, as an integer coefficient."""
    if not isinstance(text, str):
        _invalid()
    try:
        coeff, found = parse_fixed_point(text)
    except ValueError as error:
        raise ValueError('EVIDENCE_INVALID') from error
    if found != scale:
        _invalid()
    return coeff


def _number(text):
    if not isinstance(text, str):
        _invalid()
    try:
        return fr.to_fraction(text)
    except (ValueError, TypeError) as error:
        raise ValueError('EVIDENCE_INVALID') from error


class RatioEvidenceValidator:
    """add(row) per evidence line, then finish(expected_result)."""

    def __init__(self, context, expected_result):
        if not isinstance(context, dict) or not isinstance(expected_result, dict):
            _invalid()
        self.contract, self.baseline, self.target = context['contract'], context['baseline'], context['target']
        if (self.baseline.get('aggregate_policy') != fr.RATIO_POLICY or
                self.target.get('aggregate_policy') != fr.RATIO_POLICY):
            _invalid()
        ratio = expected_result.get('ratio')
        if not isinstance(ratio, dict):
            _invalid()
        self.scales = fr.scales(self.baseline, self.target, self.contract)
        s_n, s_d, _ = self.scales
        self.defined = ratio.get('status') == 'defined'
        # The claimed totals fix every record's weight; finish() proves they are the rows' totals.
        try:
            claimed = [None if ratio[side][part] is None else _number(ratio[side][part])
                       for side in ('baseline', 'target') for part in ('numerator', 'denominator')]
        except (KeyError, TypeError) as error:
            raise ValueError('EVIDENCE_INVALID') from error
        self.claimed = claimed
        n0, d0, n1, d1 = claimed
        if self.defined and (None in claimed or d0 == 0 or d1 == 0):
            _invalid()
        try:
            self.weight_n, self.weight_d = fr.weights(ratio, n0, d0, n1, d1, s_n, s_d)
        except (fr.CoreError, TypeError, ValueError) as error:
            raise ValueError('EVIDENCE_INVALID') from error
        # Integer coefficients: numerator values at s_n, denominator values at s_d, inputs at each side's input scale.
        self.inputs = {'baseline': fr.input_scales(self.baseline, self.contract),
                       'target': fr.input_scales(self.target, self.contract)}
        # Each part's term is aligned from its own scale to the shared one; computed once, not per row.
        self.align = {(side, part_name): 10 ** (scale - fr.part_scale(ir[part_name], self.contract))
                      for side, ir in (('baseline', self.baseline), ('target', self.target))
                      for part_name, scale in (('numerator', s_n), ('denominator', s_d))}
        # Large snapshots list only records whose inclusion or contribution changed; the
        # rest add identical terms to both sides. Their rows then prove the parts' changes.
        coverage = expected_result.get('evidence_coverage')
        self.changed_only = isinstance(coverage, dict) and coverage.get('mode') == 'changed_records'
        self.totals = {(side, part): 0 for side in ('baseline', 'target') for part in ('numerator', 'denominator')}
        self.seen = {key: False for key in self.totals}
        self.previous = None
        self.share_total = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def add(self, row):
        if not isinstance(row, dict) or set(row) != ROW_FIELDS or not isinstance(row['order_id'], str):
            _invalid()
        if any(type(row[flag]) is not bool for flag in FLAGS):
            _invalid()
        key = row['order_id']
        if self.previous is not None and key <= self.previous:
            _invalid()
        self.previous = key
        b, t = row['baseline_included'], row['target_included']
        membership = ('included_in_both' if b and t else 'baseline_only' if b else 'target_only' if t else 'included_in_neither')
        if row['membership'] != membership:
            _invalid()
        s_n, s_d, s_g = self.scales
        values = {}
        for side, ir in (('baseline', self.baseline), ('target', self.target)):
            included = row[f'{side}_included']
            reads = False
            for part_name, scale in (('numerator', s_n), ('denominator', s_d)):
                part = ir[part_name]
                inside = row[f'{side}_in_{part_name}']
                if (inside and not included) or (part['filter'] is None and inside != included):
                    _invalid()
                raw = row[f'{side}_{part_name}_input']
                if part['measure'] == '*' and raw is not None:
                    _invalid()
                value = None if raw is None else _coefficient(raw, self.inputs[side][part_name])
                term = fr.part_coeff(part, inside, value) * self.align[(side, part_name)]
                if row[f'{side}_{part_name}'] != ScaledInteger(term, scale).to_fixed_string():
                    _invalid()
                values[(side, part_name)] = term
                self.totals[(side, part_name)] += term
                if inside and (part['aggregate'] != 'sum' or value is not None):
                    self.seen[(side, part_name)] = True
                reads = reads or (inside and fr._reads_amount(part) and value is None)
            if row[f'{side}_null_term'] != reads:
                _invalid()
        share = ((values[('target', 'numerator')] - values[('baseline', 'numerator')]) * self.weight_n -
                 (values[('target', 'denominator')] - values[('baseline', 'denominator')]) * self.weight_d)
        if row['delta'] != ScaledInteger(share, s_g).to_fixed_string():
            _invalid()
        self.share_total += share

    def finish(self, expected_result):
        totals = []
        s_n, s_d, s_g = self.scales
        for side, ir in (('baseline', self.baseline), ('target', self.target)):
            for part_name, scale in (('numerator', s_n), ('denominator', s_d)):
                part = ir[part_name]
                total = Fraction(self.totals[(side, part_name)], 10 ** scale)
                totals.append(total if (part['aggregate'] != 'sum' or self.seen[(side, part_name)]) else None)
        if self.changed_only:
            # Rows hold only the changed records: listed (target - baseline) per part must be
            # exactly the claimed change in that part; the claimed totals then fix the ratio block.
            n0, d0, n1, d1 = self.claimed
            for index, (before, after) in enumerate(((n0, n1), (d0, d1))):
                if before is not None and after is not None:
                    scale = s_n if index == 0 else s_d
                    part_name = 'numerator' if index == 0 else 'denominator'
                    listed = self.totals[('target', part_name)] - self.totals[('baseline', part_name)]
                    if Fraction(listed, 10 ** scale) != after - before:
                        _invalid()
        else:
            if totals != self.claimed:
                _invalid()
            n0, d0, n1, d1 = totals
        ratio, r0, r1, _, _ = fr.summarize(n0, d0, n1, d1, self.baseline, self.target, self.contract)
        if digest(ratio) != digest(expected_result.get('ratio')):
            _invalid()
        shown = fr.result_fields(ratio, r0, r1)
        if shown != (expected_result.get('baseline_value'), expected_result.get('target_value'),
                     expected_result.get('delta_target_minus_baseline')):
            _invalid()
        expected_total = fr.to_fraction(ratio['scaled_gap']) if ratio['status'] == 'defined' else Fraction(0)
        if Fraction(self.share_total, 10 ** s_g) != expected_total:
            _invalid()
        if expected_result.get('aggregate') != 'ratio':
            _invalid()


def expected_delta(result):
    """The exact sum the artifact's record shares must reach (G, or 0 when undefined)."""
    ratio = result['ratio']
    return ratio['scaled_gap'] if ratio['status'] == 'defined' else fr.fixed(Fraction(0), ratio['share_scale'])
