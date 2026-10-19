"""Boundaries for exact ratio decisions and cached evidence publication."""
import hashlib
import json
from decimal import Decimal, localcontext

import pytest

from services.api import evidence_cache
from services.api.monitoring import evaluate_materiality


def test_ratio_decimal_coefficients_ignore_ambient_decimal_precision():
    from packages.metricguard_core.flat_ratio import _coeff
    with localcontext() as ctx:
        ctx.prec = 6
        assert _coeff(Decimal('123456789012345678901234567890123456.78'), 2) == 12345678901234567890123456789012345678


def test_governance_unit_follows_frozen_metric_expression():
    from services.api.metric_contracts import _flat_metric_unit
    dataset = {'version': 8, 'unit_label': 'INR', 'decimal_columns': [
        {'name': 'revenue', 'unit_label': 'INR'}, {'name': 'units', 'unit_label': 'units'}]}
    assert _flat_metric_unit(dataset, {'aggregate': 'sum', 'measure': 'units'}) == 'units'
    assert _flat_metric_unit(dataset, {'aggregate': 'count_distinct', 'measure': 'customer'}) == 'count'
    ratio = {'aggregate': 'ratio', 'numerator': {'aggregate': 'sum', 'measure': 'revenue'},
             'denominator': {'aggregate': 'sum', 'measure': 'revenue'}}
    assert _flat_metric_unit(dataset, ratio) == 'share'
    ratio['denominator'] = {'aggregate': 'count', 'measure': '*'}
    assert _flat_metric_unit(dataset, ratio) == 'INR per record'


def test_ratio_materiality_uses_exact_gap_when_display_rounds_to_zero():
    result = evaluate_materiality('0.00', '1.00', '1.00',
        exact_ratio={'status': 'defined', 'baseline': {'exact': '1'},
                     'target': {'exact': '1000000000001/1000000000000'},
                     'gap_exact': '1/1000000000000'})
    assert result['is_material'] is True
    assert result['reason'] == 'non_zero_delta'
    assert result['details']['gap_exact'] == '1/1000000000000'


def test_ratio_threshold_comparison_is_rational_at_boundary():
    ratio = {'status': 'defined', 'baseline': {'exact': '1/3'},
             'target': {'exact': '2/3'}, 'gap_exact': '1/3'}
    assert evaluate_materiality('0.33', '0.33', '0.67', absolute_threshold='0.33333333333333333334', exact_ratio=ratio)['is_material'] is False
    assert evaluate_materiality('0.33', '0.33', '0.67', relative_threshold=1, exact_ratio=ratio)['is_material'] is True


def test_invalid_exact_ratio_fails_closed():
    result = evaluate_materiality('0', '1', '1', exact_ratio={
        'status': 'defined', 'baseline': {'exact': '1'}, 'target': {'exact': '2'}, 'gap_exact': '0'})
    assert result['is_material'] and result['reason'] == 'unparseable_value'


def test_cache_does_not_publish_bytes_changed_after_verification(tmp_path):
    evidence_cache.clear()
    path = tmp_path / 'evidence.jsonl'
    original = b'{"order_id":"A","delta":"1"}\n'
    path.write_bytes(original)
    key = (str(path), 'run', 1, hashlib.sha256(original).hexdigest())
    def racing_change():
        path.write_bytes(original.replace(b'"1"', b'"9"'))
    with pytest.raises(ValueError, match='EVIDENCE_INVALID'):
        evidence_cache.page(path, key, racing_change, 0, 1)


def test_entity_members_pagination_has_bounded_results_and_exact_total(tmp_path):
    path = tmp_path / 'members.jsonl'
    data = ''.join(json.dumps({'k': f'R{i:03}', 'e': 'customer', 'b': True, 't': False}) + '\n' for i in range(12)).encode()
    path.write_bytes(data)
    info = {'sha256': hashlib.sha256(data).hexdigest(), 'row_count': 12, 'byte_count': len(data)}
    page = evidence_cache.entity_records_page(path, info, 'customer', offset=5, limit=3)
    assert page['total'] == 12 and [r['record'] for r in page['items']] == ['R005', 'R006', 'R007']
    assert page['next_offset'] == 8


def test_subtraction_ratio_refuses_precision_that_duckdb_cannot_preserve():
    from packages.metricguard_core.common import CoreError
    from packages.metricguard_core.flat import parse_flat, validate_contract
    contract = validate_contract({'version': 8, 'mode': 'flat_snapshot', 'name': 'Exact wide values',
        'row_key': 'record_id', 'decimal_columns': [
            {'name': n, 'precision': 38, 'scale': 2, 'unit_label': 'units'} for n in ('revenue', 'cost')],
        'scale': 2, 'categories': [], 'dimension_types': {}, 'measure_meaning': 'Source values',
        'unit_label': 'units', 'values_comparable': True, 'scope': 'entire_supplied_snapshot',
        'null_policy': 'blank_nullable', 'measure_policy': 'flat-multi-measure-v1', 'predicate_policy': 'flat-boolean-v1'})
    with pytest.raises(CoreError, match='38 decimal digits'):
        parse_flat('SELECT SUM(revenue - cost) / SUM(revenue) FROM snapshot', contract)
