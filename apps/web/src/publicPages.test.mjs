import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { DEMO_ROWS, demoBreakdown, demoChanges, demoTotals } from './demoCase.ts';
import { scrollToHash } from './publicScroll.ts';
import { sitePage } from './router.ts';

test('the sample case balances exactly from its own rows', () => {
  const t = demoTotals();
  assert.equal(t.current, '750.75');
  assert.equal(t.proposed, '410.75');
  assert.equal(t.delta, '-340.00');
  assert.equal(t.listed, '-340.00');
  assert.ok(!/[1-9]/.test(t.residual), `residual ${t.residual}`);
  assert.equal(t.records, 8);
  assert.equal(t.blank, 1);
  assert.deepEqual(demoChanges().map(c => [c.row.row_id, c.delta]), [['R003', '-200.00'], ['R007', '-90.00'], ['R004', '-50.00']]);
});

test('the sample breakdown puts pending first and leaves completed orders unchanged', () => {
  const byStatus = demoBreakdown('status');
  assert.equal(byStatus[0].value, 'pending');
  assert.equal(byStatus[0].change, '-200.00');
  assert.equal(byStatus[0].changed, 1);
  const completed = byStatus.find(g => g.value === 'completed');
  assert.equal(completed.records, 5);
  assert.equal(completed.changed, 0);
  assert.ok(!/[1-9]/.test(completed.change));
  const total = byStatus.reduce((n, g) => n + g.share, 0);
  assert.ok(Math.abs(total - 1) < 1e-9);
  // A blank region is its own group, not dropped.
  assert.ok(demoBreakdown('region').some(g => g.value === null));
  assert.equal(DEMO_ROWS.length, 8);
});

test('the sample case has a public route', () => {
  for (const path of ['/demo', '/demo/MG-F3A3', '/demo/mg-f3a3/']) assert.equal(sitePage(path), 'demo', path);
  assert.equal(sitePage('/demo/other'), 'not-found');
});

test('hash links scroll to an existing section only', () => {
  const calls = [];
  const doc = { getElementById: id => id === 'security' ? { scrollIntoView: o => calls.push([id, o.behavior]) } : null };
  assert.equal(scrollToHash('#security', doc, false), true);
  assert.equal(scrollToHash('#security', doc, true), true);
  assert.deepEqual(calls, [['security', 'smooth'], ['security', 'auto']]);
  assert.equal(scrollToHash('', doc, false), false);
  assert.equal(scrollToHash('#missing', doc, false), false);
  assert.equal(scrollToHash('#a"]b', doc, false), false);
});

test('public pages carry no unfilled placeholders or apologetic notes', () => {
  const source = readFileSync(new URL('./PublicPages.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\[(region|contact email)\]/i);
  assert.doesNotMatch(source, /stand-in servers|live-service checks pending|not customer data/);
  assert.match(source, /href="\/demo\/MG-F3A3"/);
});
