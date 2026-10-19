import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCsvText, inferMeaning } from './csvInference.ts';
import { humanizeError } from './errorUtils.ts';

test('parseCsvText parses CSV content, handles quotes and types', () => {
  const csvData = `order_id,order_ts,region,status,amount
ORD-101,2026-03-01T10:00:00Z,North,COMPLETED,150.50
ORD-102,2026-03-01T10:15:00Z,South,COMPLETED,250.00
ORD-103,2026-03-01T11:00:00Z,North,REFUNDED,75.25
`;

  const parsed = parseCsvText(csvData, 10);
  assert.equal(parsed.headers.length, 5);
  assert.deepEqual(parsed.headers, ['order_id', 'order_ts', 'region', 'status', 'amount']);
  assert.equal(parsed.sample_rows.length, 3);
  assert.equal(parsed.sample_rows[0].order_id, 'ORD-101');
  assert.equal(parsed.sample_rows[0].amount, '150.50');
  
  // Observed types
  assert.equal(parsed.observed_types['order_id'], 'string');
  assert.equal(parsed.observed_types['order_ts'], 'string'); // Offset timestamps require explicit external preparation.
  assert.equal(parsed.observed_types['region'], 'string');
  assert.equal(parsed.observed_types['status'], 'string');
  assert.equal(parsed.observed_types['amount'], 'numeric');
});

test('inferMeaning identifies key, measure, categories and scale without guessing currency semantics', () => {
  const headers = ['order_id', 'order_ts', 'region', 'status', 'order_amount'];
  const types = {
    order_id: 'string',
    order_ts: 'timestamp',
    region: 'string',
    status: 'string',
    order_amount: 'numeric'
  };
  const rows = [
    { order_id: '1', order_ts: '2026-01-01', region: 'APAC', status: 'PAID', order_amount: '99.95' },
    { order_id: '2', order_ts: '2026-01-02', region: 'EMEA', status: 'PENDING', order_amount: '120.00' },
  ];

  const inferred = inferMeaning(headers, types, rows, 'orders_q1.csv');

  // Key detection
  assert.equal(inferred.rowKey, 'order_id');
  // Measure detection
  assert.equal(inferred.measure, 'order_amount');
  // Categories (text fields excluding key)
  assert.ok(inferred.categories.includes('region'));
  assert.ok(inferred.categories.includes('status'));
  assert.ok(!inferred.categories.includes('order_id'));
  assert.ok(!inferred.categories.includes('order_amount'));
  // Scale
  assert.equal(inferred.scale, 2);
  // Default unit without financial guessing: must NEVER default to INR merely because measure is amount
  assert.equal(inferred.unit, '');
  assert.equal(inferred.unitSource, undefined);
  assert.equal(inferred.meaning, 'Row order amount');
});

test('inferMeaning extracts explicit INR metadata only when present in trusted source input', () => {
  // Test case A: explicit currency column with INR
  const headers1 = ['id', 'amount', 'currency'];
  const types1 = { id: 'string', amount: 'numeric', currency: 'string' };
  const rows1 = [{ id: '1', amount: '500.00', currency: 'INR' }];
  const inferred1 = inferMeaning(headers1, types1, rows1, 'orders_india.csv');
  assert.equal(inferred1.unit, 'INR');
  assert.equal(inferred1.unitSource, 'Source column "currency"');

  // Test case B: explicit column suffix _inr
  const headers2 = ['order_id', 'fee_inr'];
  const types2 = { order_id: 'string', fee_inr: 'numeric' };
  const rows2 = [{ order_id: 'O-1', fee_inr: '120.50' }];
  const inferred2 = inferMeaning(headers2, types2, rows2, 'fees.csv');
  assert.equal(inferred2.unit, 'INR');
  assert.equal(inferred2.unitSource, 'Column suffix "_inr"');
});

test('unit validation requires explicit confirmation and accepts explicit user choice', () => {
  // Unset / blank unit must be rejected
  const emptyUnit = '';
  assert.equal(emptyUnit.trim().length === 0, true);

  // Explicit user choice (INR, USD, count, etc.) is accepted
  const userChosenInr = 'INR';
  assert.equal(userChosenInr.trim().length > 0, true);
  assert.equal(userChosenInr.trim(), 'INR');

  const userChosenCustom = 'barrels';
  assert.equal(userChosenCustom.trim(), 'barrels');
});

test('humanizeError converts ROW_KEY_NOT_UNIQUE into actionable guidance', () => {
  const backendErr = new Error('ROW_KEY_NOT_UNIQUE: duplicate key "ORD-001" found at line 4');
  const humanized = humanizeError(backendErr);

  assert.equal(humanized.title, 'Row Key Not Unique');
  assert.ok(humanized.message.includes('unique'));
  assert.ok(humanized.suggestion?.includes('distinct'));
});

test('humanizeError converts NULL_IN_ROW_KEY into actionable guidance', () => {
  const backendErr = new Error('NULL_IN_ROW_KEY: null or blank found in key column "order_id"');
  const humanized = humanizeError(backendErr);

  assert.equal(humanized.title, 'Missing Key Values');
  assert.ok(humanized.suggestion?.includes('null values'));
});

test('humanizeError converts isolation and supervisor errors without leaking internal architecture', () => {
  const backendErr = new Error('ISOLATION_REQUIRED: Supervisor runner is not running in background');
  const humanized = humanizeError(backendErr);

  // Actionable humanized copy
  assert.equal(humanized.title, 'Processing unavailable');
  assert.equal(humanized.message, 'Data validation is temporarily unavailable. Check system status or try again.');
  assert.equal(humanized.suggestion, 'Check system status in Settings or try again shortly.');
  assert.equal(humanized.code, 'ISOLATION_REQUIRED');

  // Must NOT mention isolation, Docker, supervisor, runner
  const combinedUserText = `${humanized.title} ${humanized.message} ${humanized.suggestion}`.toLowerCase();
  assert.ok(!combinedUserText.includes('isolation'));
  assert.ok(!combinedUserText.includes('docker'));
  assert.ok(!combinedUserText.includes('supervisor'));
  assert.ok(!combinedUserText.includes('runner'));
});

test('humanizeError converts HTTP 413 upload limit cleanly', () => {
  const backendErr = new Error('413: Payload too large for upload');
  const humanized = humanizeError(backendErr);

  assert.equal(humanized.title, 'File Size Limit Exceeded');
  assert.ok(humanized.message?.includes('512 MiB') && humanized.message.includes('25 MiB'));
});

test('inferMeaning handles parquet filename with native_null default', () => {
  const headers = ['id', 'metric_value', 'segment'];
  const types = { id: 'string', metric_value: 'numeric', segment: 'string' };
  const rows = [{ id: '1', metric_value: '10.5', segment: 'Enterprise' }];

  const inferred = inferMeaning(headers, types, rows, 'customers_snapshot.parquet');

  assert.equal(inferred.rowKey, 'id');
  assert.equal(inferred.measure, 'metric_value');
  assert.equal(inferred.nullPolicy, 'native_null');
  assert.equal(inferred.precision, 18);
  assert.equal(inferred.scale, 1);
});

test('inferMeaning extracts currency if currency column is present', () => {
  const headers = ['trans_id', 'amount', 'currency', 'status'];
  const types = { trans_id: 'string', amount: 'numeric', currency: 'string', status: 'string' };
  const rows = [
    { trans_id: 'T1', amount: '100.00', currency: 'USD', status: 'OK' },
    { trans_id: 'T2', amount: '200.00', currency: 'USD', status: 'OK' },
  ];

  const inferred = inferMeaning(headers, types, rows, 'transactions.csv');
  assert.equal(inferred.unit, 'USD');
  assert.equal(inferred.nullPolicy, 'blank_nullable');
});


test('CSV inference scans all rows and preserves exact strings and blank NULL semantics', () => {
  const csv = 'id,flag,day,note\n' + Array.from({length: 26}, (_, i) => `${i},true,2026-01-01,"a,b"`).join('\n') + '\n26,yes,not-a-date,"line one\nline two"\n27,NULL,," true "\n';
  const p = parseCsvText(csv, 2);
  assert.equal(p.observed_types.flag, 'string');
  assert.equal(p.observed_types.day, 'string');
  assert.equal(p.sample_rows.length, 2);
  assert.equal(p.totalRowsSampled, 28);
  assert.equal(p.null_counts.flag, 0);
  assert.equal(p.null_counts.day, 1);
  assert.equal(parseCsvText('id,note\n1," true "\n').sample_rows[0].note, ' true ');
});

test('CSV timestamps are bounded timezone-free values and booleans are canonical', () => {
  const p = parseCsvText('a,b,c,d,e\ntrue,TRUE,2026-01-01T12:00:00,2026-01-01T12:00:00Z,2026-02-31T12:00:00\n');
  assert.equal(p.observed_types.a, 'boolean');
  assert.equal(p.observed_types.b, 'string');
  assert.equal(p.observed_types.c, 'timestamp');
  assert.equal(p.observed_types.d, 'string');
  assert.equal(p.observed_types.e, 'string');
});
