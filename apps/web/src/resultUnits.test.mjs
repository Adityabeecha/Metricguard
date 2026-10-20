import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resultUnit, resultDisplayValue } from './resultSummary.ts';
import { buildLibraryItems } from './investigationLibrary.ts';
import { parseThemeChoice, resolveTheme } from './themeChoice.ts';
import { aggregateRowLimitReason, buildFlatSql, operatorsFor } from './comparisonBuilder.ts';

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
    assert.equal(aggregateRowLimitReason(100001, version), 'Subtraction and CASE datasets over 100,000 rows cannot be compared yet; use a smaller file');
  }
});

test('"is not" and "is none of" are offered except on the original one-lookup two-table format', () => {
  assert.deepEqual(operatorsFor('string', true, 1), ['eq', 'in', 'is_null', 'is_not_null']);
  for (const version of [2, 3]) assert.ok(operatorsFor('string', true, version).includes('not_in') && operatorsFor('string', true, version).includes('neq'));
  for (const op of ['gt', 'gte', 'lt', 'lte']) assert.ok(operatorsFor('string', true, 2).includes(op));
  assert.ok(operatorsFor('string', false).includes('not_in'));
  const contract = { version: 5, measure: 'amount', categories: ['status'], dimension_types: { status: 'string' } };
  assert.equal(buildFlatSql(contract, { filters: [{ column: 'status', op: 'not_in', value: 'void, refunded' }] }),
    "SELECT SUM(amount) FROM snapshot WHERE status NOT IN ('void', 'refunded')");
});

test('a draft whose comparison failed shows as failed, with the reason', () => {
  const data = { snapshots: [], definitions: [], metrics: [], investigations: [],
    runs: [{ id: 'r1', state: 'failed', result: null, error: { code: 'EVIDENCE_LIMIT', message: 'Temporary evidence storage is full' } }] };
  const [item] = buildLibraryItems(data, [{ id: 'd1', comparison_run_id: 'r1', status: 'running' }]);
  assert.equal(item.status, 'refused');
  assert.equal(item.problem, 'Temporary evidence storage is full');
});

test('prepared-format (v3) two-table datasets are read like v2, not as the one-lookup format', async () => {
  const { summarizeDataset } = await import('./datasetSummary.ts');
  const { relationalFilterColumns } = await import('./comparisonBuilder.ts');
  const contract = { id: 'c3', version: 3, mode: 'relational_snapshot', policy: 'relational-star-prepared-v1', name: 'Star large',
    contract_hash: 'h3', fact: { table: 'orders', key: 'order_id', grain: 'order', categories: ['status'] },
    dimensions: [{ role: 'customer', table: 'customers', key: 'customer_id', grain: 'customer', categories: ['segment'], fact_foreign_key: 'customer_id' }],
    measure: { column: 'amount', precision: 14, scale: 2 }, measure_meaning: 'Order amount', unit_label: 'INR',
    values_comparable: true, scope: 'entire_supplied_snapshot', null_policy: 'blank_nullable' };
  const snapshot = { id: 's3', name: 'Star large', row_count: 500000, contract_hash: 'h3', execution_policy: 'relational_snapshot_v1' };
  const summary = summarizeDataset(snapshot, { flat_contracts: [], relational_contracts: [contract] });
  assert.ok(summary.filterColumns.some(c => c.includes('segment')));
  assert.ok(relationalFilterColumns(contract).some(c => c.column === 'segment'));
});

test('theme choice: unknown stored values fall back to system, and system follows the operating system', () => {
  assert.equal(parseThemeChoice('dark'), 'dark');
  assert.equal(parseThemeChoice(null), 'system');
  assert.equal(parseThemeChoice('sepia'), 'system');
  assert.equal(resolveTheme('system', true), 'dark');
  assert.equal(resolveTheme('system', false), 'light');
  assert.equal(resolveTheme('light', true), 'light');
});
