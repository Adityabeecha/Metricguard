from fractions import Fraction
from itertools import permutations
from pathlib import Path

from packages.metricguard_core import compare, ingest_csv, parse_definition
from packages.metricguard_core.shapley import exact_three_group_shapley


RULES = ('amount_expression', 'eligibility_predicate', 'time_attribution')


def test_exact_shapley_matches_independent_permutation_oracle():
    values = {0: '0', 1: '1', 2: '2', 4: '3', 3: '7', 5: '9', 6: '11', 7: '20'}
    result = exact_three_group_shapley(values, RULES)
    assert result['status'] == 'complete'
    expected = {rule: Fraction(0) for rule in RULES}
    for order in permutations(range(3)):
        mask = 0
        for index in order:
            next_mask = mask | (1 << index)
            expected[RULES[index]] += (Fraction(values[next_mask]) - Fraction(values[mask])) / 6
            mask = next_mask
    actual = {item['rule']: Fraction(int(item['numerator']), int(item['denominator']))
              for item in result['contributions']}
    assert actual == expected
    assert sum(actual.values(), Fraction(0)) == Fraction(20)
    assert result['gap'] == '20'
    assert result['evaluated_hybrids'] == 8


def test_shapley_abstains_when_any_hybrid_undefined():
    values = {mask: '10' for mask in range(8)}
    values[5] = None
    result = exact_three_group_shapley(values, RULES)
    assert result['status'] == 'unavailable'
    assert result['contributions'] == []
    assert result['undefined_hybrids'] == [5]


def test_comparison_keeps_ordered_bridge_and_adds_exact_shapley(tmp_path):
    root = Path(__file__).resolve().parents[1]
    examples = root / 'MetricGuard_Planning_Pack/examples'
    snapshot = tmp_path / 'golden.duckdb'
    ingest_csv(examples / 'orders_golden.csv', snapshot)
    baseline = parse_definition((examples / 'dashboard_a.sql').read_text())
    target = parse_definition((examples / 'finance.sql').read_text())
    context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
               'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
               'scope': 'synthetic_retail_all_orders'}
    result = compare(snapshot, baseline, target, context)
    assert result['method'] == 'ordered_rule_bridge'
    assert [step['contribution'] for step in result['steps']] == ['-4320000.00', '-420000.00', '-1260000.00']
    allocation = result['shapley']
    assert allocation['status'] == 'complete'
    assert sum((Fraction(int(x['numerator']), int(x['denominator'])) for x in allocation['contributions']), Fraction(0)) == Fraction(-6000000)
