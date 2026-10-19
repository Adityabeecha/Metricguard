import assert from 'node:assert/strict';
import { test } from 'node:test';
import { comparisonRequestKey, calculationPage, isComparisonCalculation, splitCalculations } from './guidedWorkflow.ts';

test('retries reuse the same comparison identity, scoped to workspace and exact request', async () => {
  const request = { snapshot_id: 'snapshot', baseline_version_id: 'a', target_version_id: 'b' };
  const key = await comparisonRequestKey('workspace', request);
  assert.equal(key, await comparisonRequestKey('workspace', { ...request }));
  assert.notEqual(key, await comparisonRequestKey('other', request));
  assert.notEqual(key, await comparisonRequestKey('workspace', { ...request, target_version_id: 'c' }));
});

test('saved calculation search bounds the list without discarding historical entries', () => {
  const entries = Array.from({ length: 44 }, (_, i) => ({ id: String(i), name: `Calculation ${i}`, source_label: i === 43 ? 'dbt' : 'SQL' }));
  const first = calculationPage(entries, '', 0);
  assert.equal(first.items.length, 8);
  assert.equal(first.total, 44);
  assert.equal(calculationPage(entries, '', 99).items.length, 4);
  assert.deepEqual(calculationPage(entries, 'DBT', 5).items, [entries[43]]);
  assert.equal(calculationPage(entries, 'missing', 0).total, 0);
  assert.equal(entries.length, 44);
});

test('calculations saved by guided comparisons are grouped apart from imported queries', () => {
  const entries = [
    { id: 'a', name: 'Imported', source_label: 'dbt' },
    { id: 'b', name: 'Guided flat', source_label: 'Flat snapshot SQL' },
    { id: 'c', name: 'Guided two-table', source_label: 'Relational snapshot SQL' },
    { id: 'd', name: 'Typed', source_label: 'SQL query' },
  ];
  const { imported, fromComparisons } = splitCalculations(entries);
  assert.deepEqual(imported.map(d => d.id), ['a', 'd']);
  assert.deepEqual(fromComparisons.map(d => d.id), ['b', 'c']);
  assert.equal(isComparisonCalculation({ source_label: 'dbt' }), false);
});
