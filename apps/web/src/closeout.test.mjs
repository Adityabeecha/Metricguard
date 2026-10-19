import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chooseWorkspaceAfterConnect, classifyLoadError, readReloadEnded } from './authState.ts';
import { NOT_DECLARED, summarizeDataset } from './datasetSummary.ts';
import { buildFlatSql, buildRelationalSql, describeCalculation, flatFilterColumns, formProblems } from './comparisonBuilder.ts';
import { summarizeResult } from './resultSummary.ts';
import { buildLibraryItems, filterLibraryItems } from './investigationLibrary.ts';
import './resultUnits.test.mjs';

// ---------- fixtures (the 8-row V8 flat case and the 5-row relational case) ----------
const flatContract = {
  id: 'fc1', version: 4, version_number: 1, name: 'flat-orders', row_key: 'row_id', measure: 'sales_amount', scale: 2,
  categories: ['status', 'region', 'channel', 'is_active', 'order_day'],
  dimension_types: { status: 'string', region: 'string', channel: 'string', is_active: 'boolean', order_day: 'date' },
  measure_meaning: 'Row sales amount', unit_label: 'INR', values_comparable: true, scope: 'entire_supplied_snapshot',
  null_policy: 'blank_nullable', contract_hash: 'hash-flat',
};
const flatSnapshot = { id: 's-flat', name: 'flat-orders', row_count: 8, contract_hash: 'hash-flat', execution_policy: 'flat_snapshot_v1', currency: null,
  schema: { row_id: 'VARCHAR', sales_amount: 'DECIMAL(18,2)', status: 'VARCHAR', region: 'VARCHAR', channel: 'VARCHAR', is_active: 'BOOLEAN', order_day: 'DATE' } };
const relContract = {
  id: 'rc1', version: 1, version_number: 1, mode: 'relational_snapshot', policy: 'relational-snapshot-v1', name: 'Governed Points Contract',
  contract_hash: 'hash-rel', fact: { table: 'fact_items', key: 'fact_id', grain: 'fact_id', categories: ['kind'] },
  dimension: { table: 'dim_groups', key: 'group_id', grain: 'group_id', categories: ['segment'] },
  relationship: { fact_foreign_key: 'group_id', dimension_key: 'group_id', cardinality: 'many_to_one', missing_dimension_policy: 'preserve_and_report' },
  measure: { column: 'amount', precision: 10, scale: 2 }, measure_meaning: 'Governed point items', unit_label: 'points',
  values_comparable: true, scope: 'entire_supplied_snapshot', null_policy: 'blank_nullable',
};
const relSnapshot = { id: 's-rel', name: 'points', row_count: 5, contract_hash: 'hash-rel', execution_policy: 'relational_snapshot_v1', currency: null, unit_label: null,
  schema: { 'fact_items.fact_id': 'VARCHAR', 'fact_items.group_id': 'VARCHAR', 'fact_items.amount': 'DECIMAL(10,2)', 'fact_items.kind': 'VARCHAR', 'dim_groups.group_id': 'VARCHAR', 'dim_groups.segment': 'VARCHAR' } };
const contracts = { flat_contracts: [flatContract], relational_contracts: [relContract] };

// ---------- 1. sign-in / workspace states ----------
test('auth: 401 without a key asks to connect; after a reload explains why', () => {
  assert.deepEqual(classifyLoadError({ code: 'UNAUTHORIZED', message: 'x' }, { connected: false, reloadEnded: false }), { kind: 'signin', reason: 'required' });
  assert.deepEqual(classifyLoadError({ code: 'HTTP_401', message: 'x' }, { connected: false, reloadEnded: true }), { kind: 'signin', reason: 'reload_ended' });
  assert.deepEqual(classifyLoadError({ code: 'UNAUTHORIZED', message: 'x' }, { connected: true, reloadEnded: false }), { kind: 'signin', reason: 'invalid_key' });
});

test('auth: 403 is access denied (key valid) or needs a key (local fallback)', () => {
  assert.equal(classifyLoadError({ code: 'FORBIDDEN', message: 'Access denied' }, { connected: true, reloadEnded: false }).reason, 'not_member');
  assert.equal(classifyLoadError({ code: 'FORBIDDEN', message: 'Auth required' }, { connected: false, reloadEnded: false }).reason, 'needs_key');
  assert.equal(classifyLoadError({ code: 'FORBIDDEN', message: 'Auth required' }, { connected: false, reloadEnded: true }).kind, 'signin');
});

test('auth: network failure and 5xx are service unavailable', () => {
  const net = new TypeError('Failed to fetch');
  assert.equal(classifyLoadError(net, { connected: false, reloadEnded: false }).kind, 'unavailable');
  assert.equal(classifyLoadError({ code: 'HTTP_502', message: 'Bad gateway' }, { connected: true, reloadEnded: false }).kind, 'unavailable');
});

test('auth: workspace choice after connecting', () => {
  assert.deepEqual(chooseWorkspaceAfterConnect('a', [{ id: 'a' }, { id: 'b' }]), { action: 'stay' });
  assert.deepEqual(chooseWorkspaceAfterConnect('x', [{ id: 'b' }]), { action: 'go', workspaceId: 'b' });
  assert.deepEqual(chooseWorkspaceAfterConnect('x', [{ id: 'a' }, { id: 'b' }]), { action: 'choose' });
  assert.deepEqual(chooseWorkspaceAfterConnect('x', []), { action: 'none' });
});

test('auth: reload marker is ignored once connected and never stores the key', () => {
  const store = new Map([['metricguard.connectedThisTab', '1']]);
  const storage = { getItem: k => store.get(k) ?? null };
  assert.equal(readReloadEnded(storage, false), true);
  assert.equal(readReloadEnded(storage, true), false);
  assert.ok(![...store.values()].some(v => v.startsWith('mg_')));
});

// ---------- 2. dataset summaries ----------
test('summary: flat dataset uses its own contract, no order_id fallback', () => {
  const s = summarizeDataset(flatSnapshot, contracts);
  assert.equal(s.kind, 'flat');
  assert.equal(s.keyLabel, 'row_id');
  assert.equal(s.measureLabel, 'sales_amount');
  assert.equal(s.unitLabel, 'INR');
  assert.equal(s.roleOf('row_id'), 'key');
  assert.equal(s.roleOf('status'), 'category');
  assert.deepEqual(s.missing, []);
});

test('summary: relational dataset reads the relational contract (unit not "Unspecified")', () => {
  const s = summarizeDataset(relSnapshot, contracts);
  assert.equal(s.kind, 'relational');
  assert.equal(s.keyLabel, 'fact_items.fact_id · dim_groups.group_id');
  assert.equal(s.measureLabel, 'fact_items.amount');
  assert.equal(s.unitLabel, 'points');
  assert.equal(s.joinLabel, 'fact_items.group_id → dim_groups.group_id (many-to-one)');
  assert.equal(s.roleOf('fact_items.group_id'), 'foreign_key');
  assert.equal(s.roleOf('dim_groups.segment'), 'category');
  assert.ok(!JSON.stringify(s).includes('order_'));
});

test('summary: missing contract is reported as missing, never invented', () => {
  const s = summarizeDataset({ ...relSnapshot, contract_hash: 'other' }, contracts);
  assert.equal(s.keyLabel, NOT_DECLARED);
  assert.equal(s.unitLabel, NOT_DECLARED);
  assert.ok(s.missing.includes('registered relational contract'));
  const u = summarizeDataset({ ...flatSnapshot, contract_hash: 'nope', schema: { a: 'VARCHAR' } }, contracts);
  assert.equal(u.kind, 'unknown');
  assert.equal(u.measureLabel, NOT_DECLARED);
});

// ---------- 3. guided calculations ----------
test('builder: the 750.75 → 410.75 calculations', () => {
  assert.equal(buildFlatSql(flatContract, { filters: [] }), 'SELECT SUM(sales_amount) FROM snapshot');
  assert.equal(buildFlatSql(flatContract, { filters: [{ column: 'status', op: 'eq', value: 'completed' }] }),
    "SELECT SUM(sales_amount) FROM snapshot WHERE status = 'completed'");
});

test('builder: typed literals, lists, NULL checks and quoting', () => {
  const sql = buildFlatSql(flatContract, { filters: [
    { column: 'is_active', op: 'eq', value: 'true' },
    { column: 'order_day', op: 'gte', value: '2026-09-03' },
    { column: 'region', op: 'is_null', value: '' },
    { column: 'status', op: 'in', value: "completed, o'neil" },
  ] });
  assert.equal(sql, "SELECT SUM(sales_amount) FROM snapshot WHERE is_active = TRUE AND order_day >= DATE '2026-09-03' AND region IS NULL AND status IN ('completed', 'o''neil')");
});

test('builder: problems are reported before sending', () => {
  const cols = flatFilterColumns(flatContract);
  assert.deepEqual(formProblems({ filters: [{ column: 'status', op: 'eq', value: '' }] }, cols), ['Condition 1: enter a value for status.']);
  assert.match(formProblems({ filters: [{ column: 'order_day', op: 'eq', value: '09/03/2026' }] }, cols)[0], /not a date/);
  assert.deepEqual(formProblems({ filters: [{ column: 'region', op: 'is_not_null', value: '' }] }, cols), []);
});

test('builder: relational join and dimension filter', () => {
  assert.equal(buildRelationalSql(relContract, { join: 'left', filters: [] }),
    'SELECT SUM(f.amount) FROM fact_items f LEFT JOIN dim_groups d ON f.group_id = d.group_id');
  assert.equal(buildRelationalSql(relContract, { join: 'inner', filters: [{ column: 'dimension.segment', op: 'eq', value: 'north' }] }),
    "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE d.segment = 'north'");
});

test('builder: plain description', () => {
  const cols = flatFilterColumns(flatContract);
  assert.equal(describeCalculation({ filters: [{ column: 'status', op: 'eq', value: 'completed' }] }, cols, 'sales_amount', false),
    'Total of sales_amount over all rows where status is completed.');
});

// ---------- 5. result summary ----------
const rows8 = [
  ['R001', '0.00'], ['R002', '0.00'], ['R003', '-200.00'], ['R004', '-50.00'],
  ['R005', '0.00'], ['R006', '0.00'], ['R007', '-90.00'], ['R008', '0.00'],
].map(([key, delta]) => ({ key, delta }));

test('result: 750.75 → 410.75 reads as 340.00 INR lower, three records', () => {
  const s = summarizeResult({ baseline_value: '750.75', target_value: '410.75', delta_target_minus_baseline: '-340.00', residual: '0' }, 'INR', { rows: rows8, total: 8, complete: true });
  assert.equal(s.headline, 'The proposed calculation is 340.00 INR lower than the current calculation.');
  assert.equal(s.recordsSentence, '3 records account for the difference.');
  assert.deepEqual(s.changedRecords.map(r => r.key), ['R003', 'R004', 'R007']);
  assert.ok(s.uncertainties.some(u => /not automatically a financial loss/.test(u)));
  assert.ok(s.uncertainties.some(u => /do not show which calculation is correct/.test(u)));
  assert.ok(!s.uncertainties.some(u => /unexplained/.test(u)));
});

test('result: undefined totals are not zero', () => {
  const s = summarizeResult({ baseline_value: null, target_value: '10.00', delta_target_minus_baseline: null, residual: null }, 'points');
  assert.equal(s.defined, false);
  assert.equal(s.direction, 'undefined');
  assert.match(s.headline, /undefined \(not zero\)/);
  assert.equal(s.recordsSentence, null);
});

test('result: unexplained residual is surfaced', () => {
  const s = summarizeResult({ baseline_value: '65.00', target_value: '10.00', delta_target_minus_baseline: '-55.00', residual: '1.00' }, 'points');
  assert.ok(s.uncertainties.some(u => u.startsWith('1.00 points of the change is not explained')));
});

// ---------- 4. unified investigation list ----------
test('library: single-table and two-table cases appear together and are searchable', () => {
  const data = {
    definitions: [{ id: 'd1', name: 'Gross', versions: [{ id: 'v1', version_number: 1 }] }, { id: 'd2', name: 'Completed only', versions: [{ id: 'v2', version_number: 1 }] }],
    metrics: [{ id: 'm1', display_name: 'Net sales', active_binding: { definition_id: 'd2' } }],
    snapshots: [flatSnapshot, relSnapshot],
    runs: [{ id: 'r1', result: { delta_target_minus_baseline: '-340.00' } }, { id: 'r2', result: { delta_target_minus_baseline: '-55.00' } }],
    investigations: [
      { id: 'i1', title: 'Completed filter', decision: 'ok', run_id: 'r1', snapshot_id: 's-flat', baseline_version_id: 'v1', target_version_id: 'v2', context: { mode: 'flat_snapshot', unit_label: 'INR' }, created_at: '2026-10-06' },
      { id: 'i2', title: 'North join', decision: 'ok', run_id: 'r2', snapshot_id: 's-rel', baseline_version_id: 'v1', target_version_id: 'v2', context: { mode: 'relational_snapshot', unit_label: 'points' }, created_at: '2026-10-05' },
    ],
  };
  const items = buildLibraryItems(data, []);
  assert.deepEqual(items.map(i => i.kind), ['single', 'relational']);
  assert.equal(filterLibraryItems(items, { query: '', kind: 'relational', status: 'all' }).length, 1);
  assert.equal(filterLibraryItems(items, { query: 'net sales', kind: 'all', status: 'all' }).length, 2);
  assert.equal(filterLibraryItems(items, { query: 'points', kind: 'all', status: 'all' })[0].id, 'i2');
});

// ---------- improvement pass ----------
import { groupDigits, plainLimitation } from './resultSummary.ts';
import { supportsAnyMatch } from './comparisonBuilder.ts';

test('numbers: thousands separators without losing digits', () => {
  assert.equal(groupDigits('-1000269'), '-1,000,269');
  assert.equal(groupDigits('16008872.12'), '16,008,872.12');
  assert.equal(groupDigits('340.00'), '340.00');
  assert.equal(groupDigits('0.000001'), '0.000001');
  assert.equal(groupDigits('n/a'), 'n/a');
});

test('limitations: declared unit is never contradicted', () => {
  assert.match(plainLimitation('Unknown currency remains unknown; no verified financial loss is claimed.', 'INR'), /declared unit \(INR\)/);
  assert.match(plainLimitation('Unknown currency remains unknown; no verified financial loss is claimed.', ''), /No unit was declared/);
  assert.equal(plainLimitation('An endpoint SUM is NULL because no non-NULL terms matched; the gap is undefined.', 'INR'), null);
  const s = summarizeResult({ baseline_value: '750.75', target_value: '410.75', delta_target_minus_baseline: '-340.00', residual: '0',
    limitations: ['Unknown currency remains unknown; no verified financial loss is claimed.'] }, 'INR');
  assert.ok(!s.uncertainties.some(u => /Unknown currency/.test(u)));
});

test('builder: OR conditions only where the dataset allows them', () => {
  const v5 = { ...flatContract, version: 5, predicate_policy: 'flat-boolean-v1' };
  assert.equal(supportsAnyMatch(v5), true);
  assert.equal(supportsAnyMatch(flatContract), false);
  assert.equal(supportsAnyMatch(relContract), false);
  assert.equal(buildFlatSql(v5, { match: 'any', filters: [{ column: 'status', op: 'eq', value: 'pending' }, { column: 'status', op: 'eq', value: 'returned' }] }),
    "SELECT SUM(sales_amount) FROM snapshot WHERE (status = 'pending' OR status = 'returned')");
  assert.throws(() => buildFlatSql(flatContract, { match: 'any', filters: [{ column: 'status', op: 'eq', value: 'a' }, { column: 'region', op: 'eq', value: 'b' }] }));
});

test('result: exact changed count from the server summary on large data', () => {
  const s = summarizeResult({ baseline_value: '3292679', target_value: '2292410', delta_target_minus_baseline: '-1000269', residual: '0' }, 'rentals',
    { rows: [{ key: '1', delta: '-16' }], total: 17379, complete: true, changedCount: 5514 });
  assert.equal(s.headline, 'The proposed calculation is 1,000,269 rentals lower than the current calculation.');
  assert.equal(s.recordsSentence, '5,514 records account for the difference.');
});

// ---------- end-to-end fixes ----------
import { inferMeaning, parseCsvText } from './csvInference.ts';
import { humanizeError } from './errorUtils.ts';
import { classifyInvestigationError } from './investigationUtils.ts';

test('upload: a repeating key is never replaced by the amount column, and is flagged', () => {
  const parsed = parseCsvText('order_ref,net_value,state\nA1,1.00,paid\nA1,2.00,paid\n', 25);
  assert.equal(parsed.key_issues.order_ref, 'repeats "A1"');
  const inf = inferMeaning(parsed.headers, parsed.observed_types, parsed.sample_rows, 'x.csv', parsed.key_issues);
  assert.notEqual(inf.rowKey, 'net_value');
  assert.equal(inf.measure, 'net_value');
  assert.match(inf.keyWarning, /No column uniquely identifies every row/);
});

test('upload: a unique reference column is suggested as the key over the whole file', () => {
  const rows = Array.from({ length: 40 }, (_, i) => `R${i},${i}.50,paid`).join('\n');
  const parsed = parseCsvText(`order_ref,net_value,state\n${rows}\n`, 25);
  const inf = inferMeaning(parsed.headers, parsed.observed_types, parsed.sample_rows, 'x.csv', parsed.key_issues);
  assert.equal(inf.rowKey, 'order_ref');
  assert.equal(inf.measure, 'net_value');
  assert.ok(!inf.categories.includes('order_ref') && !inf.categories.includes('net_value'));
  // a duplicate only after the 25-row display sample is still caught
  const late = parseCsvText(`order_ref,net_value\n${Array.from({ length: 30 }, (_, i) => `R${i},1.00`).join('\n')}\nR3,2.00\n`, 25);
  assert.equal(late.key_issues.order_ref, 'repeats "R3"');
});

test('errors: suggestions match the actual problem', () => {
  assert.match(humanizeError({ code: 'INVALID_CONTRACT', message: 'Column names must be distinct plain identifiers' }).suggestion, /only one role/);
  assert.match(humanizeError({ code: 'INVALID_KEYS', message: 'Row key values must be unique; duplicate A1' }).title, /Not Unique/);
  assert.match(classifyInvestigationError({ code: 'UNSUPPORTED_SQL', message: 'Only one SUM over the whole snapshot is supported' }).guidance, /COUNT\(DISTINCT column\).*AVG\(amount\)/);
  assert.match(classifyInvestigationError({ code: 'UNSUPPORTED_SQL', message: 'Exactly one SELECT is required' }).guidance, /single SELECT/);
});

import { closestValue, defaultConditionColumn, unknownFilterValues, unknownValueWarnings } from './comparisonBuilder.ts';

const STATUS = { complete: true, distinct_count: 4, values: ['paid', 'cancelled', 'refunded', 'pending'].map(value => ({ value, count: 1 })) };

test('typed condition values are checked against the values a column really holds', () => {
  assert.deepEqual(unknownFilterValues({ column: 'status', op: 'eq', value: 'complete' }, 'string', STATUS), ['complete']);
  assert.deepEqual(unknownFilterValues({ column: 'status', op: 'in', value: 'paid, Refunded' }, 'string', STATUS), ['Refunded']);
  assert.deepEqual(unknownFilterValues({ column: 'status', op: 'eq', value: 'paid' }, 'string', STATUS), []);
  // An incomplete list cannot prove a value is missing, and blank tests take no value.
  assert.deepEqual(unknownFilterValues({ column: 'status', op: 'eq', value: 'x' }, 'string', { ...STATUS, complete: false }), []);
  assert.deepEqual(unknownFilterValues({ column: 'status', op: 'is_null', value: 'x' }, 'string', STATUS), []);
  assert.equal(closestValue('Refunded', STATUS), 'refunded');
  assert.equal(closestValue('canceled', STATUS), 'cancelled');
  assert.equal(closestValue('zzzzzz', STATUS), null);
  assert.deepEqual(unknownValueWarnings({ column: 'status', op: 'eq', value: 'pend' }, { id: 'status', label: 'status', column: 'status', type: 'string' }, STATUS),
    ['“pend” does not occur in status; did you mean “pending”?']);
});

test('a new condition starts on a column with a short list of values, not on an ID column', () => {
  const cols = ['customer_id', 'status'].map(id => ({ id, label: id, column: id, type: 'string' }));
  const known = { customer_id: { complete: false, distinct_count: 50001, values: [] }, status: STATUS };
  assert.equal(defaultConditionColumn(cols, known), 'status');
  // Before look-ups load: the first text column that is not a timestamp, an ID, or known to have many values.
  const withTime = [{ id: 'started', label: 'started', column: 'started', type: 'timestamp' }, ...cols];
  assert.equal(defaultConditionColumn(withTime, {}), 'status');
  assert.equal(defaultConditionColumn(withTime, { customer_id: known.customer_id }), 'status');
  assert.equal(defaultConditionColumn(withTime.slice(0, 1), {}), '');
});
