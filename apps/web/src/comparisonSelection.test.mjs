import assert from 'node:assert/strict';
import { test } from 'node:test';
import { COMPARISON_KINDS, comparisonDatasetGroups, initialComparisonLoad, emptyCalculationSides, matchCountKey, recordedByDefault } from './comparisonSelection.ts';
import { buildFlatSql } from './comparisonBuilder.ts';

const choice = (id, name, hash, at, policy = 'flat_snapshot_v1') => ({
  snapshot: { id, name, contract_hash: hash, created_at: at, execution_policy: policy }, flat: { name: 'Orders September' },
});
const older = choice('old', 'orders_september', 'orders', '2026-09-01');
const newer = choice('new', 'orders_september_newer', 'orders', '2026-10-01');

test('dataset picker groups loads by contract identity, with the latest load first', () => {
  const groups = comparisonDatasetGroups([older, newer, choice('other', 'orders_september', 'different', '2026-08-01')]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].name, 'Orders September');
  assert.deepEqual(groups[0].loads.map(c => c.snapshot.id), ['new', 'old']);
  assert.equal(groups[0].latest.snapshot.id, 'new');
  assert.equal(comparisonDatasetGroups([older, choice('rel', 'orders', 'orders', '2026-10-01', 'relational_snapshot_v1')]).length, 2);
});

test('one dataset is selected automatically; an explicit uploaded or historical load stays selected', () => {
  assert.equal(initialComparisonLoad([older, newer], ''), 'new');
  assert.equal(initialComparisonLoad([older, newer], 'old'), 'old');
  assert.equal(initialComparisonLoad([older, newer], 'new'), 'new');
  assert.equal(initialComparisonLoad([older], 'old', 'new'), 'new');
  assert.equal(initialComparisonLoad([older, newer], 'old', 'new'), 'new');
  assert.equal(initialComparisonLoad([older, choice('other', 'Other', 'other', '2026-10-02')], ''), '');
  assert.equal(initialComparisonLoad([], ''), '');
});

test('either current zero match count blocks; an edited SQL or different load invalidates it', () => {
  const sql = { baseline: 'SELECT SUM(amount) FROM snapshot', target: "SELECT SUM(amount) FROM snapshot WHERE status = 'paid'" };
  const counts = { [matchCountKey('new', sql.target)]: { matched: 0, total: 42 } };
  assert.deepEqual(emptyCalculationSides('new', sql, counts), ['target']);
  assert.deepEqual(emptyCalculationSides('new', { ...sql, baseline: sql.target, target: sql.baseline }, counts), ['baseline']);
  assert.deepEqual(emptyCalculationSides('old', sql, counts), []);
  assert.deepEqual(emptyCalculationSides('new', { ...sql, target: `${sql.target} AND region = 'west'` }, counts), []);
  assert.deepEqual(emptyCalculationSides('new', sql, { [matchCountKey('new', sql.target)]: 'error' }), []);
});

test('editing a guided form condition clears a zero count for its previous query', () => {
  const contract = { version: 4, measure: 'amount', categories: ['status'], dimension_types: { status: 'string' } };
  const oldSql = buildFlatSql(contract, { filters: [{ column: 'status', op: 'eq', value: 'missing' }] });
  const newSql = buildFlatSql(contract, { filters: [{ column: 'status', op: 'eq', value: 'paid' }] });
  const baseline = buildFlatSql(contract, { filters: [] });
  const counts = { [matchCountKey('new', oldSql)]: { matched: 0, total: 42 } };
  assert.deepEqual(emptyCalculationSides('new', { baseline, target: oldSql }, counts), ['target']);
  assert.deepEqual(emptyCalculationSides('new', { baseline, target: newSql }, counts), []);
});

test('recorded by starts from the account name but respects typing and existing case authors', () => {
  const identity = { user: { name: 'Ada Analyst', email: 'ada@example.test' } };
  assert.equal(recordedByDefault('', identity, false), 'Ada Analyst');
  assert.equal(recordedByDefault('Old browser name', identity, false), 'Ada Analyst');
  assert.equal(recordedByDefault('My typed name', identity, true), 'My typed name');
  assert.equal(recordedByDefault('Original case author', identity, true), 'Original case author');
  assert.equal(recordedByDefault('', { user: { name: null, email: 'ada@example.test' } }, false), 'ada@example.test');
  assert.equal(recordedByDefault('Local analyst', null, false), 'Local analyst');
});

test('comparison type offers the two separate analyst questions', () => {
  assert.deepEqual(COMPARISON_KINDS.map(k => k.label), ['Two calculations on the same data', 'Same calculation on two data loads']);
});
