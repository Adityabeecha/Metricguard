import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { SAMPLE_CONTRACT, SAMPLE_NAME, existingSample, firstRunSteps, isSampleDataset } from './sampleDataset.ts';

test('first-run steps tick off from the workspace and disappear after the first saved case', () => {
  const empty = { snapshots: [], runs: [], investigations: [] };
  assert.deepEqual(firstRunSteps(empty).map(s => s.done), [false, false, false]);
  assert.deepEqual(firstRunSteps({ ...empty, snapshots: [{}] }).map(s => s.done), [true, false, false]);
  assert.deepEqual(firstRunSteps({ snapshots: [{}], runs: [{}], investigations: [] }).map(s => s.done), [true, true, false]);
  assert.deepEqual(firstRunSteps({ snapshots: [{}], runs: [{}], investigations: [{}] }), []);
});

test('the bundled sample matches its contract and the free-beta limits', () => {
  const csv = readFileSync(new URL('../public/samples/sample_orders.csv', import.meta.url), 'utf8');
  const lines = csv.trim().split(/\r?\n/);
  const header = lines[0].split(',');
  assert.equal(lines.length - 1, 5000);
  assert.ok(csv.length < 25 * 1024 * 1024);
  for (const column of [SAMPLE_CONTRACT.row_key, SAMPLE_CONTRACT.measure, ...SAMPLE_CONTRACT.categories]) assert.ok(header.includes(column), column);
  const keys = new Set(lines.slice(1).map(line => line.split(',')[0]));
  assert.equal(keys.size, 5000);  // the row key is unique
  const amount = header.indexOf('amount');
  assert.ok(lines.slice(1).every(line => /^(\d+\.\d{2})?$/.test(line.split(',')[amount])));
});

test('the sample dataset is recognised in a list', () => {
  assert.ok(isSampleDataset({ name: SAMPLE_NAME }));
  assert.ok(!isSampleDataset({ name: 'orders' }));
  assert.equal(existingSample({ snapshots: [{ id: 'a', name: 'orders' }, { id: 'b', name: SAMPLE_NAME }] }).id, 'b');
  assert.equal(existingSample({ snapshots: [] }), null);
});
