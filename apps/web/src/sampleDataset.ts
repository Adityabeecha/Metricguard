import type { Bootstrap, Snapshot } from './api.ts';

export const SAMPLE_NAME = 'Sample orders';
export const SAMPLE_FILE = '/samples/sample_orders.csv';

/** The sample's definition: the same contract the upload dialog would build for this file. */
export const SAMPLE_CONTRACT = {
  version: 5,
  predicate_policy: 'flat-boolean-v1',
  mode: 'flat_snapshot',
  name: SAMPLE_NAME,
  row_key: 'order_id',
  measure: 'amount',
  precision: 18,
  scale: 2,
  categories: ['status', 'region', 'channel', 'order_date', 'customer_id'],
  dimension_types: { status: 'string', region: 'string', channel: 'string', order_date: 'date', customer_id: 'string' },
  nullable_categories: [],
  measure_meaning: 'Order amount (synthetic sample)',
  unit_label: 'INR',
  values_comparable: true,
  scope: 'entire_supplied_snapshot',
  null_policy: 'blank_nullable',
} as const;

/** True for the dataset created from the sample, so lists can mark it. */
export function isSampleDataset(snapshot: Pick<Snapshot, 'name'> | null | undefined): boolean {
  return snapshot?.name === SAMPLE_NAME;
}

/** An existing sample dataset in this workspace, if one was already added. */
export function existingSample(data: Pick<Bootstrap, 'snapshots'>): Snapshot | null {
  return (data.snapshots || []).find(s => isSampleDataset(s)) || null;
}

export type FirstRunStep = { key: 'data' | 'compare' | 'save'; label: string; done: boolean };

/** The three first-run steps, ticked off from what the workspace already has. Empty once a case is saved. */
export function firstRunSteps(data: Pick<Bootstrap, 'snapshots' | 'runs' | 'investigations'>): FirstRunStep[] {
  const saved = (data.investigations || []).length > 0;
  if (saved) return [];
  return [
    { key: 'data', label: 'Add data', done: (data.snapshots || []).length > 0 },
    { key: 'compare', label: 'Compare two calculations', done: (data.runs || []).length > 0 },
    { key: 'save', label: 'Save the case', done: false },
  ];
}
