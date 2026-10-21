import assert from 'node:assert/strict';
import { test } from 'node:test';
import { insertAt, sqlTokens } from './views/evidence/sqlHighlight.ts';
import { datasetTableName, flatSqlColumns } from './comparisonBuilder.ts';
import { categoryLabel } from './views/evidence/categories.ts';

test('SQL highlighting marks keywords, functions, strings, numbers, columns and the table', () => {
  const tokens = sqlTokens("SELECT SUM(amount) FROM orders_october WHERE status = 'paid' AND qty > 2", ['amount', 'status'], ['orders_october', 'snapshot']);
  const kind = text => tokens.find(t => t.text === text)?.kind;
  assert.equal(kind('SELECT'), 'keyword');
  assert.equal(kind('SUM'), 'function');
  assert.equal(kind('amount'), 'column');
  assert.equal(kind('orders_october'), 'table');
  assert.equal(kind("'paid'"), 'string');
  assert.equal(kind('2'), 'number');
  assert.equal(kind('qty'), 'plain');
  assert.equal(tokens.map(t => t.text).join(''), "SELECT SUM(amount) FROM orders_october WHERE status = 'paid' AND qty > 2");
});

test('inserting a column adds spaces only where needed', () => {
  assert.deepEqual(insertAt('SELECT SUM() FROM t', 11, 11, 'amount'), { value: 'SELECT SUM(amount) FROM t', cursor: 17 });
  assert.deepEqual(insertAt('WHERE', 5, 5, 'status'), { value: 'WHERE status', cursor: 12 });
  assert.equal(insertAt('', 0, 0, 'snapshot').value, 'snapshot');
});

test('the dataset table name follows the server rule', () => {
  assert.equal(datasetTableName({ name: 'Orders October' }), 'orders_october');
  assert.equal(datasetTableName({ name: 'orders_september' }), 'orders_september');
  assert.equal(datasetTableName({ name: '2026 loads!' }), '');
  assert.equal(datasetTableName(null), '');
});

test('the SQL editor lists the row ID, every amount and the filter columns with types', () => {
  const contract = { version: 8, row_key: 'order_id', measure: 'revenue', categories: ['status', 'order_date'],
    dimension_types: { status: 'string', order_date: 'date' },
    decimal_columns: [{ name: 'revenue', precision: 18, scale: 2, unit_label: 'INR' }, { name: 'cost', precision: 18, scale: 2, unit_label: 'INR' }] };
  assert.deepEqual(flatSqlColumns(contract).map(c => `${c.name}:${c.role || c.type}`),
    ['order_id:row ID', 'revenue:amount', 'cost:amount', 'status:string', 'order_date:date']);
});

test('two sources read as A and B, not earlier and later', () => {
  assert.equal(categoryLabel('added', { loads: true, sources: true }), 'Only in source B');
  assert.equal(categoryLabel('removed', { loads: true, sources: true }), 'Only in source A');
  assert.equal(categoryLabel('value_changed', { loads: true, sources: true }), 'Value differs');
  assert.equal(categoryLabel('added', { loads: true }), 'New in the later load');
});
