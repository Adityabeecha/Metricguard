"""Exact bounded allocation across the three declared rule groups."""
from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import permutations


def exact_three_group_shapley(values: dict[int, str | None], rules: tuple[str, str, str]) -> dict:
    if len(rules) != 3 or len(set(rules)) != 3:
        raise ValueError('Shapley allocation requires three distinct rule groups')
    undefined = [mask for mask in range(8) if values.get(mask) is None]
    if undefined:
        return {'status': 'unavailable', 'method': 'exact_three_group_shapley',
                'contributions': [], 'undefined_hybrids': undefined,
                'evaluated_hybrids': 8 - len(undefined),
                'limitation': 'All eight hybrid endpoints must be defined for exact Shapley allocation.'}
    exact = {mask: Fraction(Decimal(values[mask])) for mask in range(8)}
    contributions = {rule: Fraction(0) for rule in rules}
    for order in permutations(range(3)):
        mask = 0
        for index in order:
            next_mask = mask | (1 << index)
            contributions[rules[index]] += (exact[next_mask] - exact[mask]) / 6
            mask = next_mask
    gap = exact[7] - exact[0]
    if sum(contributions.values(), Fraction(0)) != gap:
        raise ArithmeticError('Exact Shapley contributions do not sum to the gap')
    with localcontext() as context:
        context.prec = 100
        items = [{'rule': rule, 'numerator': str(value.numerator),
                  'denominator': str(value.denominator),
                  'display_decimal': f'{Decimal(value.numerator) / Decimal(value.denominator):.9f}'}
                 for rule, value in contributions.items()]
    return {'status': 'complete', 'method': 'exact_three_group_shapley',
            'contributions': items, 'gap': str(gap.numerator) if gap.denominator == 1
            else f'{gap.numerator}/{gap.denominator}',
            'evaluated_hybrids': 8, 'undefined_hybrids': [],
            'limitation': 'Allocation averages six rule orders; it is not causal. Display decimals are rounded; rational values are exact.'}
