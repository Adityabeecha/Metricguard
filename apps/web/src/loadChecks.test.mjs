import assert from 'node:assert/strict';
import { test } from 'node:test';
import { executeLoadChecks, loadCheckPairOptions } from './loadChecks.ts';

test('load pair options group by contract and preserve chronological direction', () => {
  const snapshots = [
    { id: 'new', name: 'New', manifest_hash: 'new', contract_hash: 'same', execution_policy: 'flat_snapshot_v1', created_at: '2026-02-01' },
    { id: 'old', name: 'Old', manifest_hash: 'old', contract_hash: 'same', execution_policy: 'flat_snapshot_v1', created_at: '2026-01-01' },
    { id: 'other', contract_hash: 'other', execution_policy: 'flat_snapshot_v1', created_at: '2026-01-01' },
    { id: 'legacy', created_at: '2026-01-01' },
  ];
  assert.deepEqual(loadCheckPairOptions(snapshots, 'new').map(s => s.id), ['old']);
  assert.deepEqual(loadCheckPairOptions(snapshots, 'old'), []);
});

test('checks wait for a terminal run before starting the next metric', async () => {
  const events = [];
  const result = await executeLoadChecks([{ metric_id: 'a' }, { metric_id: 'b' }], {
    start: async item => { events.push(`start ${item.metric_id}`); return { id: item.metric_id, state: 'queued' }; },
    poll: async id => { events.push(`poll ${id}`); return { id, state: 'succeeded' }; },
    pause: async () => {}, onRun: () => {}, stopped: () => false,
  });
  assert.deepEqual(events, ['start a', 'poll a', 'start b', 'poll b']);
  assert.equal(result.completed, 2);
});

test('a daily limit stops the sequence preserving previously completed work', async () => {
  const starts = [];
  const result = await executeLoadChecks([{ metric_id: 'a' }, { metric_id: 'b' }, { metric_id: 'c' }], {
    start: async item => { starts.push(item.metric_id); if (item.metric_id === 'b') throw new Error('Daily allowance exhausted.'); return { id: item.metric_id, state: 'succeeded' }; },
    poll: async () => assert.fail('already terminal'), pause: async () => {}, onRun: () => {}, stopped: () => false,
  });
  assert.deepEqual(starts, ['a', 'b']);
  assert.equal(result.completed, 1);
  assert.equal(result.error, 'Daily allowance exhausted.');
});

test('stopping after the current run prevents another admission', async () => {
  let stopped = false;
  const starts = [];
  const result = await executeLoadChecks([{ metric_id: 'a' }, { metric_id: 'b' }], {
    start: async item => { starts.push(item.metric_id); return { id: item.metric_id, state: 'queued' }; },
    poll: async id => { stopped = true; return { id, state: 'succeeded' }; },
    pause: async () => {}, onRun: () => {}, stopped: () => stopped,
  });
  assert.deepEqual(starts, ['a']);
  assert.equal(result.completed, 1);
});
