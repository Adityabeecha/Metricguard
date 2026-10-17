// Pure dataset description derived from the snapshot's own registered contract.
// Never substitutes example column names: anything the contract does not declare
// is reported as missing. Type-only imports keep this testable under node.
import type { FlatContract, RelationalContract, Snapshot } from './api';

export const NOT_DECLARED = 'Not declared';

export type ColumnRole = 'key' | 'measure' | 'minuend' | 'subtrahend' | 'foreign_key' | 'dimension_key' | 'category' | 'attribute';

export type DatasetSummary = {
  kind: 'flat' | 'relational' | 'retail' | 'unknown';
  kindLabel: string;
  /** Which registered contract describes this dataset; null when none matches. */
  contractName: string | null;
  keyLabel: string;
  measureLabel: string;
  unitLabel: string;
  unitDeclared: boolean;
  measureMeaning: string | null;
  filterColumns: string[];
  joinLabel: string | null;
  tables: string[];
  missing: string[];
  roleOf: (column: string) => ColumnRole;
};

type Contracts = { flat_contracts?: FlatContract[]; relational_contracts?: RelationalContract[] };

function declaredUnit(...values: Array<string | null | undefined>): string | null {
  for (const v of values) {
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return null;
}

export function summarizeDataset(snapshot: Snapshot, contracts: Contracts): DatasetSummary {
  const relational = snapshot.execution_policy === 'relational_snapshot_v1'
    ? (contracts.relational_contracts || []).find(c => c.contract_hash === snapshot.contract_hash) || null
    : null;
  const flat = !relational && snapshot.contract_hash
    ? (contracts.flat_contracts || []).find(c => c.contract_hash === snapshot.contract_hash) || null
    : null;

  if (snapshot.execution_policy === 'relational_snapshot_v1') {
    const missing: string[] = [];
    if (!relational) missing.push('registered relational contract');
    const unit = declaredUnit(relational?.unit_label, snapshot.unit_label);
    if (!unit) missing.push('unit');
    const fact = relational?.fact, dim = relational?.dimension, rel = relational?.relationship;
    const q = (table: string | undefined, col: string | undefined) => (table && col ? `${table}.${col}` : NOT_DECLARED);
    const roles = new Map<string, ColumnRole>();
    if (relational && fact && dim && rel) {
      roles.set(`${fact.table}.${fact.key}`, 'key');
      roles.set(`${dim.table}.${dim.key}`, 'dimension_key');
      roles.set(`${fact.table}.${rel.fact_foreign_key}`, 'foreign_key');
      roles.set(`${fact.table}.${relational.measure.column}`, 'measure');
      fact.categories.forEach(c => roles.set(`${fact.table}.${c}`, 'category'));
      dim.categories.forEach(c => roles.set(`${dim.table}.${c}`, 'category'));
    }
    return {
      kind: 'relational',
      kindLabel: 'Two tables (fact + dimension)',
      contractName: relational?.name || null,
      keyLabel: relational ? `${q(fact?.table, fact?.key)} · ${q(dim?.table, dim?.key)}` : NOT_DECLARED,
      measureLabel: relational ? q(fact?.table, relational.measure.column) : NOT_DECLARED,
      unitLabel: unit || NOT_DECLARED,
      unitDeclared: Boolean(unit),
      measureMeaning: relational?.measure_meaning || null,
      filterColumns: relational ? [...fact!.categories.map(c => `${fact!.table}.${c}`), ...dim!.categories.map(c => `${dim!.table}.${c}`)] : [],
      joinLabel: relational ? `${fact!.table}.${rel!.fact_foreign_key} → ${dim!.table}.${dim!.key} (many-to-one)` : null,
      tables: relational ? [fact!.table, dim!.table] : [],
      missing,
      roleOf: column => roles.get(column) || 'attribute',
    };
  }

  if (flat) {
    const unit = declaredUnit(flat.unit_label, snapshot.unit_label);
    const missing = unit ? [] : ['unit'];
    const isSub = flat.version === 6 && flat.measure_expression;
    const roles = new Map<string, ColumnRole>();
    roles.set(flat.row_key, 'key');
    if (isSub) {
      roles.set(flat.measure_expression!.minuend, 'minuend');
      roles.set(flat.measure_expression!.subtrahend, 'subtrahend');
    } else if (flat.measure) {
      roles.set(flat.measure, 'measure');
    }
    (flat.categories || []).forEach(c => { if (!roles.has(c)) roles.set(c, 'category'); });
    return {
      kind: 'flat',
      kindLabel: 'Single table',
      contractName: flat.name,
      keyLabel: flat.row_key || NOT_DECLARED,
      measureLabel: isSub ? `${flat.measure_expression!.minuend} − ${flat.measure_expression!.subtrahend}` : flat.measure || NOT_DECLARED,
      unitLabel: unit || NOT_DECLARED,
      unitDeclared: Boolean(unit),
      measureMeaning: flat.measure_meaning || null,
      filterColumns: [...(flat.categories || [])],
      joinLabel: null,
      tables: ['snapshot'],
      missing,
      roleOf: column => roles.get(column) || 'attribute',
    };
  }

  // Original fixed-schema orders snapshot (v1) or a v2 mapped extract: its roles are fixed by the product.
  if (!snapshot.execution_policy || snapshot.mapping_hash || snapshot.schema?.order_id) {
    const unit = declaredUnit(snapshot.currency, snapshot.unit_label);
    const fixedRoles: Record<string, ColumnRole> = { order_id: 'key', order_amount: 'measure', refund_amount: 'measure' };
    return {
      kind: 'retail',
      kindLabel: 'Orders extract (fixed schema)',
      contractName: null,
      keyLabel: 'order_id',
      measureLabel: 'order_amount',
      unitLabel: unit || NOT_DECLARED,
      unitDeclared: Boolean(unit),
      measureMeaning: null,
      filterColumns: ['status', 'payment_status', 'region'].filter(c => !snapshot.schema || c in snapshot.schema),
      joinLabel: null,
      tables: ['orders'],
      missing: unit ? [] : ['unit'],
      roleOf: column => fixedRoles[column] || (['status', 'payment_status', 'region'].includes(column) ? 'category' : 'attribute'),
    };
  }

  const unit = declaredUnit(snapshot.unit_label, snapshot.currency);
  return {
    kind: 'unknown',
    kindLabel: 'Dataset',
    contractName: null,
    keyLabel: NOT_DECLARED,
    measureLabel: NOT_DECLARED,
    unitLabel: unit || NOT_DECLARED,
    unitDeclared: Boolean(unit),
    measureMeaning: null,
    filterColumns: [],
    joinLabel: null,
    tables: [],
    missing: ['registered contract', ...(unit ? [] : ['unit'])],
    roleOf: () => 'attribute',
  };
}

export const ROLE_LABEL: Record<ColumnRole, string> = {
  key: 'Row key',
  measure: 'Amount to total',
  minuend: 'Amount (minus…)',
  subtrahend: '…subtracted amount',
  foreign_key: 'Connects to dimension',
  dimension_key: 'Dimension key',
  category: 'Filter column',
  attribute: 'Not used',
};
