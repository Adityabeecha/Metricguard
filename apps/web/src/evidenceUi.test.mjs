import assert from 'node:assert/strict';
import { test } from 'node:test';
import { decimalEquals, decimalSum, runningTotals } from './exactDecimal.ts';
import { hashCells } from './views/evidence/hashCells.ts';
import { categoryLabel } from './views/evidence/categories.ts';
import { caseSummaryMarkdown } from './views/evidence/copySummary.ts';
import { loadSummaryBreakdown, preferredBreakdownColumn, savedFilterColumns } from './views/evidence/breakdownSelection.ts';

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

const split = (column, change, total, extra = {}) => ({ available: true, columns: ['region', 'status'], column,
  abs_change_total: total, groups: [{ value: 'top', change, records: 1, changed_records: 1, current: null, proposed: null }], ...extra });

test('summary prefers the same available filtered column as Breakdown, including qualified columns', async () => {
  assert.equal(preferredBreakdownColumn(['fact.status', 'dimension.status'], ['dimension.status']), 'dimension.status');
  assert.equal(preferredBreakdownColumn(['fact.status', 'dimension.status'], ['status']), undefined);
  assert.equal(preferredBreakdownColumn(['fact.region', 'dimension.status'], ['status']), 'dimension.status');
  const choices = { region: split('region', '-100', '100'), status: split('status', '-10', '100') };
  const selected = await loadSummaryBreakdown(async column => choices[column || 'region'], ['status']);
  assert.equal(selected.column, 'status'); // the preferred filter wins even when another column has a larger share
});

test('saved filter preferences cover flat, relational, boolean, ratio and legacy predicates without amount columns', () => {
  const target = { ir: { predicate_tree: { op: 'and', children: [
    { op: 'predicate', predicate: { field: 'status', op: 'eq', values: ['done'], type: 'string' } },
    { op: 'not', child: { op: 'predicate', predicate: { field: 'status', op: 'is_null', values: [], type: 'string' } } },
  ] }, numerator: { measure: 'amount', filter: { op: 'predicate', predicate: { field: 'region', op: 'eq', values: ['north'], type: 'string' } } },
    measure: 'amount' } };
  const baseline = { ir: { predicates: [{ table: 'dimension', column: 'segment', op: 'eq', values: ['a'] }],
    predicate: { op: 'eq', left: { op: 'column', name: 'status' }, right: { op: 'literal', value: 'old' } },
    expression: { op: 'column', name: 'amount' } } };
  assert.deepEqual(savedFilterColumns(target, baseline), ['status', 'region', 'dimension.segment']);
  assert.deepEqual(savedFilterColumns({ ir: { predicates: [{ field: 'payment_status', op: 'eq', values: ['paid'] }] } }), ['payment_status']);
});

test('summary fallback selects the largest top-group share across columns, not first column or raw change', async () => {
  const choices = { region: split('region', '-600', '1000'), status: split('status', '-70', '100') };
  const selected = await loadSummaryBreakdown(async column => choices[column || 'region'], ['unavailable_column']);
  assert.equal(selected.column, 'status');
});

test('equally strong breakdown shares keep the available column order', async () => {
  const choices = { region: split('region', '-60', '100'), status: split('status', '-3', '5') };
  const selected = await loadSummaryBreakdown(async column => choices[column || 'region'], []);
  assert.equal(selected.column, 'region');
});

test('summary fallback compares very large shares and rational effects exactly', async () => {
  const columns = ['region', 'status', 'unsupported'];
  const choices = {
    region: split('region', '9007199254740992', '18014398509481986', { columns }),
    status: split('status', '9007199254740993', '18014398509481986', { columns }),
    unsupported: split('unsupported', null, '1', { columns, ratio: true }),
  };
  const selected = await loadSummaryBreakdown(async column => choices[column || 'region'], []);
  assert.equal(selected.column, 'status');
  const rational = {
    region: split('region', '0.33', '1', { ratio: true, groups: [{ value: 'top', change: '0.33', change_exact: '1/3' }] }),
    status: split('status', '0.33', '1', { ratio: true, groups: [{ value: 'top', change: '0.33', change_exact: '334/1000' }] }),
  };
  assert.equal((await loadSummaryBreakdown(async column => rational[column || 'region'], [])).column, 'status');
});

test('Where it sits prints the largest supported group and rounds its exact share without floating point', () => {
  const detail = { id: 'case', title: 'Exact share', created_at: '2026-10-20', run: { id: 'abcd', result: { delta_target_minus_baseline: '1' } } };
  const breakdown = split('status', '0', '2000000000000000000000000000000000000', { groups: [
    { value: 'zero', change: '0' }, { value: 'leading', change: '289999999999999999999999999999999999' },
  ] });
  const text = caseSummaryMarkdown({ detail, unit: 'INR', summary: null, breakdown, link: 'case-link' });
  assert.match(text, /Where it sits: status = leading .*\(14% of the difference\)/);
  assert.doesNotMatch(text, /status = zero/);
});

test('unsupported or zero group effects do not manufacture a Where it sits statement', async () => {
  const detail = { id: 'case', title: 'Undefined', created_at: '2026-10-20', run: { id: 'abcd', result: {} } };
  for (const breakdown of [split('status', null, null, { ratio: true }), split('status', '0.00', '0'),
    split('status', null, '1', { ratio: true, groups: [{ value: 'undefined', change: null, change_exact: '1/2', current: '100', proposed: '0' }] }),
    { ...split('status', '50', '100'), available: false }]) {
    const text = caseSummaryMarkdown({ detail, unit: '', summary: null, breakdown, link: 'case-link' });
    assert.doesNotMatch(text, /Where it sits/);
  }
  assert.equal(await loadSummaryBreakdown(async () => ({ available: false, columns: [] }), []), null);
});

test('share summaries use exact percentage points and record effects instead of scaled deltas', () => {
  const detail = { id: 'case', title: 'Share', created_at: '2026-10-20', run: { id: 'abcd', result: {
    aggregate: 'ratio', ratio: { unit: 'share' }, delta_target_minus_baseline: '-0.200000', residual: '0.000000',
  } } };
  const summary = { changed: 2, top_changes: [
    { row_key: 'R1', delta: '-800000000', effect: '-0.200000' },
    { row_key: 'R2', delta: '999999999', effect: null },
  ] };
  const breakdown = split('status', '-0.200000', '1/5', { ratio: true, groups: [
    { value: 'pending', change: '-0.200000', change_exact: '-1/5' },
  ] });
  const text = caseSummaryMarkdown({ detail, unit: 'pts', summary, breakdown, link: 'case-link' });
  assert.match(text, /−20\.0000 pts: the proposed calculation is lower/);
  assert.match(text, /Largest: R1 −20\.0000 pts\./);
  assert.doesNotMatch(text, /R2|800,000,000|999,999,999/);
  assert.match(text, /Where it sits: status = pending −20\.0000 pts \(100% of the difference\)/);
});

test('a missing qualified filter never selects the same name from a different lookup', () => {
  assert.equal(preferredBreakdownColumn(['dimension.status'], ['fact.status']), undefined);
  assert.equal(preferredBreakdownColumn(['dimension.status'], ['status']), 'dimension.status');
});


import { evidenceCountLabel, twoLoadCountNotes, evidenceSealLabel } from './views/evidence/evidencePresentation.ts';
test('two-load counts separate every changed record from records affecting the total', () => {
  const summary = { added:{records:300,delta:'203'},removed:{records:20,delta:'-9'},value_changed:{records:5,delta:'1'} };
  const categories = [{key:'added',records:203},{key:'removed',records:9},{key:'value_changed',records:3}];
  const notes = twoLoadCountNotes(summary,categories);
  assert.match(notes[0], /^300 new records in the later load; 203 change the total/);
  assert.match(notes[1], /^20 records removed from the later load; 9 change the total/);
  assert.match(notes[2], /^5 records changed value; 3 change the total/);
  assert.deepEqual(twoLoadCountNotes(summary,null),[]);
  assert.match(twoLoadCountNotes({added:{records:2,delta:'0'}},[])[0], /0 change the total/);
  assert.equal(evidenceCountLabel(203,true),'203 records that change the total');
  assert.equal(evidenceCountLabel(1,true),'1 record that changes the total');
});
test('only a saved case may claim its evidence is sealed', () => {
  assert.equal(evidenceSealLabel(false,null),'EVIDENCE READY');
  assert.equal(evidenceSealLabel(false,'intact'),'EVIDENCE READY');
  assert.equal(evidenceSealLabel(true,null),'SEALED');
  assert.equal(evidenceSealLabel(true,'intact'),'Seal intact');
});
