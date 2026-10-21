import test from 'node:test';
import assert from 'node:assert/strict';
import { metricSupport, matchingMetricLoads, memberName, metricDifference, retainedCalculations, sqlDefinitionRequest } from './metricWorkflow.ts';

const snapshots = [
  { id: 'wrong', contract_hash: 'other', status: 'validated', created_at: '2026-10-20', execution_policy: 'flat_snapshot_v1' },
  { id: 'older', contract_hash: 'retail', status: 'validated', created_at: '2026-10-18', execution_policy: 'flat_snapshot_v1' },
  { id: 'newer', contract_hash: 'retail', status: 'validated', created_at: '2026-10-19', execution_policy: 'flat_snapshot_v1' },
];
const approved = { id: 'v1', contract_hash: 'retail', support_state: 'executable' };
const definition = { id: 'd1', versions: [approved, { id: 'v2', contract_hash: 'other', support_state: 'executable' }] };
const metric = { active_binding: { definition_id: 'd1', approved_version_id: 'v1' } };

test('metric readiness requires the approved version and an available matching dataset', () => {
  const state = metricSupport(metric, [definition], snapshots);
  assert.equal(state.label, 'Ready to compare');
  assert.equal(state.version.id, 'v1');
  assert.deepEqual(state.loads.map(s => s.id), ['newer', 'older']);
  assert.equal(metricSupport(metric, [definition], [snapshots[0]]).isAnalyzable, false);
  assert.equal(metricSupport({ active_binding: { definition_id: 'd1' } }, [definition], snapshots).label, 'Waiting for approval');
});

test('legacy SQL with no dataset pin cannot borrow a dataset or appear ready', () => {
  const legacy = { ...approved, contract_hash: null };
  assert.deepEqual(matchingMetricLoads(legacy, snapshots), []);
  const state = metricSupport(metric, [{ ...definition, versions: [legacy] }], snapshots);
  assert.equal(state.label, 'Add SQL for a dataset');
  assert.match(state.reason, /Not linked to a dataset.*add the SQL again for a dataset/);
});

test('a proposed calculation waits for approval before becoming runnable', () => {
  const proposed = { id: 'm1', active_binding: null };
  const state = metricSupport(proposed, [definition], snapshots, [{ metric_id: 'm1', definition_id: 'd1', status: 'candidate' }]);
  assert.equal(state.label, 'Waiting for approval');
  assert.equal(state.isAnalyzable, false);
  assert.equal(state.version, null);
  assert.equal(metricSupport({ ...proposed, candidate_count: 1 }, [definition], snapshots).label, 'Waiting for approval');
});

test('two versions compare only on their common pinned dataset', () => {
  assert.deepEqual(matchingMetricLoads(approved, snapshots, definition.versions[1]), []);
  assert.deepEqual(matchingMetricLoads(approved, snapshots, { ...approved, id: 'candidate' }).map(s => s.id), ['newer', 'older']);
});

test('new SQL must name an available dataset and uses its contract', () => {
  assert.deepEqual(sqlDefinitionRequest('Revenue', ' SELECT SUM(amount) FROM snapshot ', 'retail-id', [{ id: 'retail-id', contract_hash: 'retail' }], snapshots),
    { endpoint: '/flat/definitions', body: { name: 'Revenue', raw_sql: 'SELECT SUM(amount) FROM snapshot', contract_id: 'retail-id' } });
  assert.throws(() => sqlDefinitionRequest('Revenue', 'SELECT 1', '', [], snapshots), /Choose a dataset/);
  assert.throws(() => sqlDefinitionRequest('Revenue', 'SELECT 1', 'retail-id', [{ id: 'retail-id', contract_hash: 'gone' }], snapshots), /available data load/);
});

test('history resolves names and uses Unknown member when no name is available', () => {
  const members = [{ user_id: 'u1', name: 'Ada', email: 'ada@example.com' }, { user_id: 'u2', name: '', email: 'lee@example.com' }];
  assert.equal(memberName('u1', members), 'Ada');
  assert.equal(memberName('u2', members), 'Unknown member');
  assert.equal(memberName('opaque-uuid', members), 'Unknown member');
  assert.equal(memberName(null, members), 'Unknown member');
});

test('metric differences preserve exact decimals, grouping and aggregate units', () => {
  assert.equal(metricDifference({ delta_target_minus_baseline: '-3606367.61', currency: 'INR' }), '−3,606,367.61 INR');
  assert.equal(metricDifference({ delta_target_minus_baseline: '12', aggregate: 'count', currency: 'INR' }), '12 records');
  assert.equal(metricDifference({ delta_target_minus_baseline: '-0.125', aggregate: 'ratio', ratio: { unit: 'share' } }), '−12.5 pts');
});

test('saved calculations hide throwaway runs by default, while Show all includes them', () => {
  const definitions = [{ id: 'case' }, { id: 'metric' }, { id: 'run' }, { id: 'unused' }];
  const usage = [
    { definition_id: 'case', retained: true }, { definition_id: 'metric', retained: true },
    { definition_id: 'run', retained: false }, { definition_id: 'unused', retained: false },
  ];
  assert.deepEqual(retainedCalculations(definitions, usage, false).map(d => d.id), ['case', 'metric']);
  assert.equal(retainedCalculations(definitions, usage, true).length, 4);
  assert.equal(retainedCalculations(definitions, [], false).length, 0);
});
