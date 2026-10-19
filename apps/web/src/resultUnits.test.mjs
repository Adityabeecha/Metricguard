import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resultUnit, resultDisplayValue } from './resultSummary.ts';
import { buildLibraryItems } from './investigationLibrary.ts';
import { aggregateRowLimitReason } from './comparisonBuilder.ts';

test('summary units follow the aggregate, including secondary amounts and historical currency', () => {
  assert.equal(resultUnit({ aggregate: 'count' }, 'USD'), 'records');
  assert.equal(resultUnit({ aggregate: 'count_distinct', distinct: { entity_column: 'customer' } }, 'USD'), 'distinct customer');
  assert.equal(resultUnit({ unit_label: 'items' }, 'USD'), 'items');
  assert.equal(resultUnit({ currency: 'INR' }), 'INR');
  assert.equal(resultUnit(null, 'USD'), 'USD');
  assert.equal(resultUnit({ ratio: { unit: 'amount_per_record', numerator: { unit_label: 'EUR' } }, aggregate: 'ratio' }, 'USD'), 'EUR per record');
  assert.equal(resultUnit({ ratio: { unit: 'records_per_amount', denominator: { unit_label: 'kg' } }, aggregate: 'ratio' }), 'records per kg');
  assert.equal(resultUnit({ ratio: { unit: 'amount_per_amount', numerator: { unit_label: 'kg' }, denominator: { unit_label: 'litres' } }, aggregate: 'ratio' }), 'kg per litres');
});

test('share changes are scaled to percentage points without rounding through floats', () => {
  const share = { aggregate: 'ratio', ratio: { unit: 'share' } };
  assert.equal(resultUnit(share, 'USD'), 'pts');
  assert.equal(resultDisplayValue(share, '-0.012345'), '-1.2345');
  assert.equal(resultDisplayValue(share, null), null);
  assert.equal(resultDisplayValue({ aggregate: 'count' }, '12'), '12');
});

test('saved cases and drafts both use run units rather than context currency', () => {
  const result = { aggregate: 'ratio', ratio: { unit: 'share' }, delta_target_minus_baseline: '-0.125' };
  const data = { snapshots: [], definitions: [], metrics: [],
    runs: [{ id: 'saved', result }, { id: 'draft', result: { aggregate: 'count', delta_target_minus_baseline: '3' }, context: { unit_label: 'USD' } }],
    investigations: [{ id: 'case', run_id: 'saved', context: { unit_label: 'USD' } }] };
  const items = buildLibraryItems(data, [{ id: 'draft-case', comparison_run_id: 'draft' }]);
  assert.deepEqual(items.map(i => [i.change, i.unit]), [['-12.5', 'pts'], ['3', 'records']]);
});

test('large snapshots allow every aggregate except on subtraction and CASE datasets', () => {
  for (const version of [3, 4, 5, 8, undefined]) assert.equal(aggregateRowLimitReason(5000000, version), '');
  for (const version of [6, 7]) {
    assert.equal(aggregateRowLimitReason(100000, version), '');
    assert.equal(aggregateRowLimitReason(100001, version), 'Subtraction and CASE datasets over 100,000 rows cannot be compared yet; use a smaller extract');
  }
});
