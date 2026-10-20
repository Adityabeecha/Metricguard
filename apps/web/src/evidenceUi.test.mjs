import assert from 'node:assert/strict';
import { test } from 'node:test';
import { decimalEquals, decimalSum, runningTotals } from './exactDecimal.ts';
import { hashCells } from './views/evidence/hashCells.ts';
import { categoryLabel } from './views/evidence/categories.ts';
import { caseSummaryMarkdown } from './views/evidence/copySummary.ts';

test('decimal sums are exact, keep the widest scale and never drift', () => {
  assert.equal(decimalSum(['-200.00', '-90.00', '-50.00']), '-340.00');
  assert.equal(decimalSum(['0.1', '0.2']), '0.3');
  assert.equal(decimalSum(['75.5', '-0.25', '-290.00']), '-214.75');
  assert.equal(decimalSum(['-0.00', '0']), '0.00');
  assert.equal(decimalSum(['99999999999999999999999999.99', '0.01']), '100000000000000000000000000.00');
  assert.equal(decimalSum([]), '0');
  assert.deepEqual(runningTotals(['-200.00', '-90.00', '-50.00']), ['-200.00', '-290.00', '-340.00']);
  assert.ok(decimalEquals('340.00', '340.0'));
  assert.ok(!decimalEquals('340.00', '340.01'));
  assert.throws(() => decimalSum(['12,5']));
});

test('hash prints are deterministic and mirror-symmetric', () => {
  const hash = '761d2b86d3f47de44f00929f5ab7053a31fd41143cf6214f4b5fdaf20ae790a7';
  const cells = hashCells(hash);
  assert.equal(cells.length, 36);
  assert.deepEqual(hashCells(hash), cells);
  for (let row = 0; row < 6; row++) {
    for (let col = 0; col < 3; col++) assert.equal(cells[row * 6 + col], cells[row * 6 + 5 - col]);
  }
  assert.notDeepEqual(hashCells('442bd3d056832e6ea3a24bc62635fcffc72c957f4deef1d7e50d28c5990af648'), cells);
});

test('category labels follow the kind of comparison', () => {
  assert.equal(categoryLabel('excluded', {}), 'Excluded by proposed');
  assert.equal(categoryLabel('added', { distinct: true }), 'Newly counted');
  assert.equal(categoryLabel('removed', { loads: true }), 'Gone from the later load');
});

test('copied summary carries the exact figures, balance and case link', () => {
  const detail = {
    id: 'case-1', title: 'Demo · Completed orders only', created_at: '2026-10-17T15:45:00Z',
    run: { id: '73c8604a-xyz', result: { delta_target_minus_baseline: '-340.00', residual: '0.00' } },
  };
  const summary = { total: 8, changed: 3, blank_amount_rows: 1,
    top_changes: [{ order_id: 'R003', delta: '-200.00' }, { order_id: 'R007', delta: '-90.00' }, { order_id: 'R004', delta: '-50.00' }] };
  const breakdown = { available: true, columns: ['status'], column: 'status', abs_change_total: '340.00',
    groups: [{ value: 'pending', change: '-200.00', records: 1, changed_records: 1, current: null, proposed: null }] };
  const text = caseSummaryMarkdown({ detail, unit: 'INR', summary, breakdown, link: 'https://app/w/x/investigations/case-1', replayVerifiedAt: '2026-10-20T10:00:00Z' });
  assert.match(text, /^\*\*Demo · Completed orders only\*\* \(MG-73C8\)/);
  assert.match(text, /−340\.00 INR: the proposed calculation is lower than the current one\./);
  assert.match(text, /3 records explain it, unexplained 0\.00 INR \(balanced\)/);
  assert.match(text, /Largest: R003 −200\.00, R007 −90\.00, R004 −50\.00/);
  assert.match(text, /Where it sits: status = pending −200\.00 INR \(59% of the difference\)/);
  assert.match(text, /replay verified 20 Oct 2026/);
  assert.ok(text.endsWith('https://app/w/x/investigations/case-1'));
});
