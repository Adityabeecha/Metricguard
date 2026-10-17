import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mappingIssue, matchingSnapshots, boundVersions, comparisonIssue, sourcePolicyIssue} from './v2Workflow.ts';

const columns = {
  order_id: 'source_order', customer_id: 'source_customer', order_date: 'booked_on',
  payment_date: 'paid_on', status: 'order_state', payment_status: 'payment_state',
  order_amount: 'gross_amount', refund_amount: 'refund_total', currency: 'currency_code',
};
const mapping = {id: 'map-1', contract_id: 'contract-1', contract_hash: 'contract-hash', mapping_hash: 'mapping-hash', columns};
const snapshot = {id: 'snapshot-1', contract_hash: 'contract-hash', mapping_hash: 'mapping-hash'};
const executable = (id, aggregate='sum') => ({id, mapping_id: 'map-1', contract_hash: 'contract-hash', mapping_hash: 'mapping-hash', support_state: 'executable', ir: {aggregate}});

test('mapping refuses missing, duplicate, and absent source columns', () => {
  assert.match(mappingIssue({...columns, currency: ''}, Object.values(columns)), /currency/i);
  assert.match(mappingIssue({...columns, currency: 'gross_amount'}, Object.values(columns)), /distinct/i);
  assert.match(mappingIssue(columns, Object.values(columns).filter(value => value !== 'gross_amount')), /gross_amount/);
  assert.equal(mappingIssue(columns, Object.values(columns)), null);
});

test('snapshot list keeps only the selected mapping and contract identity', () => {
  assert.deepEqual(matchingSnapshots([snapshot, {...snapshot, id: 'other', mapping_hash: 'changed'}, {...snapshot, id: 'old', contract_hash: null}], mapping).map(item => item.id), ['snapshot-1']);
});

test('definition choices keep only executable versions bound to this mapping', () => {
  const definitions = [{id: 'd1', versions: [executable('good'), {...executable('unsupported'), support_state: 'unsupported'}, {...executable('old'), mapping_hash: 'changed'}]}];
  assert.deepEqual(boundVersions(definitions, mapping).map(item => item.version.id), ['good']);
});

test('comparison refuses mismatched aggregate family and identities', () => {
  assert.match(comparisonIssue(executable('left'), executable('right', 'count'), snapshot), /aggregate/i);
  assert.match(comparisonIssue(executable('left'), executable('right'), {...snapshot, mapping_hash: 'changed'}), /mapping/i);
  assert.match(comparisonIssue(executable('left'), executable('left'), snapshot), /two/i);
  assert.match(comparisonIssue(executable('left'), executable('right'), snapshot, {contract_hash: 'changed', mapping_hash: 'mapping-hash'}), /context/i);
  assert.match(comparisonIssue(executable('left', 'count'), executable('right', 'count'), snapshot, {contract_hash: 'contract-hash', mapping_hash: 'mapping-hash', unit: 'currency'}), /unit count/i);
  assert.equal(comparisonIssue(executable('left'), executable('right'), snapshot), null);
});

test('source format must match the approved null policy', () => {
  assert.equal(sourcePolicyIssue('extract.csv', 'blank_nullable'), null);
  assert.equal(sourcePolicyIssue('extract.csv', 'literal_NULL'), null);
  assert.equal(sourcePolicyIssue('extract.parquet', 'native_null'), null);
  assert.match(sourcePolicyIssue('extract.parquet', 'blank_nullable'), /Parquet.*native/i);
  assert.match(sourcePolicyIssue('extract.csv', 'native_null'), /CSV.*null/i);
  assert.match(sourcePolicyIssue('extract.txt', 'native_null'), /CSV or Parquet/i);
});
