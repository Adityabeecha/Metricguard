// Guided calculation form → SQL text. The form never produces its own IR: the SQL it
// writes is registered through the same /flat/definitions or /relational/definitions
// validation as SQL typed in the editor, so both paths yield identical validated
// structures (or the same refusal). Pure module; type-only imports.
import type { FlatContract, RelationalContract } from './api';

export type ValueType = 'string' | 'boolean' | 'date' | 'timestamp';
export type FilterOp = 'eq' | 'neq' | 'in' | 'is_null' | 'is_not_null' | 'gt' | 'gte' | 'lt' | 'lte';

export type FilterColumn = {
  /** Value used in the form, e.g. `status` or `dimension.segment`. */
  id: string;
  /** Display label, e.g. `status` or `dim_groups.segment`. */
  label: string;
  type: ValueType;
  side?: 'fact' | 'dimension';
  column: string;
};

export type Filter = { column: string; op: FilterOp; value: string };

export type CalculationForm = {
  filters: Filter[];
  /** How conditions combine. 'any' (OR) needs a dataset with compound filters enabled. */
  match?: 'all' | 'any';
  /** What is added up: the amount column (SUM) or the number of records (COUNT(*)). */
  aggregate?: 'sum' | 'count';
  /** Relational only: how facts without a matching dimension row are treated. */
  join?: 'left' | 'inner';
};

export const OP_LABEL: Record<FilterOp, string> = {
  eq: 'is', neq: 'is not', in: 'is one of', is_null: 'is blank (NULL)', is_not_null: 'is not blank',
  gt: 'is after / greater than', gte: 'is on or after / at least', lt: 'is before / less than', lte: 'is on or before / at most',
};

const NEEDS_VALUE = (op: FilterOp) => op !== 'is_null' && op !== 'is_not_null';

export function operatorsFor(type: ValueType, relational: boolean): FilterOp[] {
  if (relational) return ['eq', 'in', 'is_null', 'is_not_null'];
  if (type === 'boolean') return ['eq', 'neq', 'is_null', 'is_not_null'];
  if (type === 'date' || type === 'timestamp') return ['eq', 'neq', 'in', 'gt', 'gte', 'lt', 'lte', 'is_null', 'is_not_null'];
  return ['eq', 'neq', 'in', 'is_null', 'is_not_null'];
}

export function quoteIdent(name: string): string {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : `"${name.replace(/"/g, '""')}"`;
}

function literal(raw: string, type: ValueType): string {
  const v = raw.trim();
  if (type === 'boolean') {
    if (/^(true|yes|1)$/i.test(v)) return 'TRUE';
    if (/^(false|no|0)$/i.test(v)) return 'FALSE';
    throw new Error(`“${raw}” is not true or false.`);
  }
  if (type === 'date') {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) throw new Error(`“${raw}” is not a date like 2026-09-01.`);
    return `DATE '${v}'`;
  }
  if (type === 'timestamp') {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?$/.test(v)) throw new Error(`“${raw}” is not a timestamp like 2026-09-01T00:00:00.`);
    return `TIMESTAMP '${v}'`;
  }
  return `'${raw.replace(/'/g, "''")}'`;
}

function splitList(raw: string): string[] {
  return raw.split(',').map(s => s.trim()).filter(Boolean);
}

function predicate(ref: string, f: Filter, type: ValueType): string {
  switch (f.op) {
    case 'is_null': return `${ref} IS NULL`;
    case 'is_not_null': return `${ref} IS NOT NULL`;
    case 'in': {
      const values = splitList(f.value);
      if (values.length === 0) throw new Error('List at least one value, separated by commas.');
      return `${ref} IN (${values.map(v => literal(v, type)).join(', ')})`;
    }
    default: {
      const sym = { eq: '=', neq: '!=', gt: '>', gte: '>=', lt: '<', lte: '<=' }[f.op];
      return `${ref} ${sym} ${literal(f.value, type)}`;
    }
  }
}

/** Problems to show beside the form before anything is sent. */
export function formProblems(form: CalculationForm, columns: FilterColumn[]): string[] {
  const problems: string[] = [];
  form.filters.forEach((f, i) => {
    const col = columns.find(c => c.id === f.column);
    if (!col) { problems.push(`Condition ${i + 1}: choose a column.`); return; }
    if (NEEDS_VALUE(f.op) && !f.value.trim()) problems.push(`Condition ${i + 1}: enter a value for ${col.label}.`);
    else if (NEEDS_VALUE(f.op)) {
      try { predicate('x', f, col.type); } catch (e) { problems.push(`Condition ${i + 1}: ${(e as Error).message}`); }
    }
  });
  return problems;
}

export function flatFilterColumns(contract: FlatContract): FilterColumn[] {
  return (contract.categories || []).map(name => ({
    id: name, label: name, column: name,
    type: (contract.dimension_types?.[name] as ValueType | undefined) || 'string',
  }));
}

export function relationalFilterColumns(contract: RelationalContract): FilterColumn[] {
  return [
    ...contract.fact.categories.map(c => ({ id: `fact.${c}`, label: `${contract.fact.table}.${c}`, column: c, side: 'fact' as const, type: 'string' as const })),
    ...contract.dimension.categories.map(c => ({ id: `dimension.${c}`, label: `${contract.dimension.table}.${c}`, column: c, side: 'dimension' as const, type: 'string' as const })),
  ];
}

/** Guided form supports the plain-SUM and subtraction contracts; CASE contracts use the SQL editor. */
export function guidedFormSupported(contract: FlatContract | RelationalContract): boolean {
  if ((contract as RelationalContract).mode === 'relational_snapshot') return true;
  const flat = contract as FlatContract;
  return flat.version !== 7;
}

/** Whether this dataset accepts OR between conditions (flat contracts with the boolean filter policy). */
export function supportsAnyMatch(contract: FlatContract | RelationalContract): boolean {
  if ((contract as RelationalContract).mode === 'relational_snapshot') return false;
  const flat = contract as FlatContract;
  return flat.version >= 5 || flat.predicate_policy === 'flat-boolean-v1';
}

function joinConditions(parts: string[], match: CalculationForm['match']): string {
  if (parts.length < 2 || match !== 'any') return parts.join(' AND ');
  return `(${parts.join(' OR ')})`;
}

/** COUNT(*) is available on single-amount datasets (not subtraction or CASE datasets, not two tables). */
export function supportsCount(contract: FlatContract | RelationalContract): boolean {
  if ((contract as RelationalContract).mode === 'relational_snapshot') return false;
  const version = (contract as FlatContract).version;
  return version !== 6 && version !== 7;
}

export function flatMeasureSql(contract: FlatContract): string {
  if (contract.version === 6 && contract.measure_expression) {
    return `${quoteIdent(contract.measure_expression.minuend)} - ${quoteIdent(contract.measure_expression.subtrahend)}`;
  }
  return quoteIdent(contract.measure);
}

export function buildFlatSql(contract: FlatContract, form: CalculationForm): string {
  const columns = flatFilterColumns(contract);
  const where = form.filters.map(f => {
    const col = columns.find(c => c.id === f.column);
    if (!col) throw new Error('Choose a column for every condition.');
    return predicate(quoteIdent(col.column), f, col.type);
  });
  if (form.match === 'any' && !supportsAnyMatch(contract)) throw new Error('This dataset only supports conditions that must all be true.');
  if (form.aggregate === 'count' && !supportsCount(contract)) throw new Error('This dataset can only total its amount column.');
  const aggregate = form.aggregate === 'count' ? 'COUNT(*)' : `SUM(${flatMeasureSql(contract)})`;
  return `SELECT ${aggregate} FROM snapshot${where.length ? ` WHERE ${joinConditions(where, form.match)}` : ''}`;
}

export function buildRelationalSql(contract: RelationalContract, form: CalculationForm): string {
  const columns = relationalFilterColumns(contract);
  const join = form.join === 'inner' ? 'INNER JOIN' : 'LEFT JOIN';
  const where = form.filters.map(f => {
    const col = columns.find(c => c.id === f.column);
    if (!col) throw new Error('Choose a column for every condition.');
    return predicate(`${col.side === 'dimension' ? 'd' : 'f'}.${quoteIdent(col.column)}`, f, 'string');
  });
  const r = contract.relationship;
  return `SELECT SUM(f.${quoteIdent(contract.measure.column)}) FROM ${contract.fact.table} f ${join} ${contract.dimension.table} d ` +
    `ON f.${quoteIdent(r.fact_foreign_key)} = d.${quoteIdent(contract.dimension.key)}` +
    (where.length ? ` WHERE ${where.join(' AND ')}` : '');
}

/** One plain sentence describing what a guided calculation adds up. */
export function describeCalculation(form: CalculationForm, columns: FilterColumn[], amountLabel: string, relational: boolean): string {
  const parts = form.filters.map(f => {
    const col = columns.find(c => c.id === f.column);
    const label = col?.label || f.column || '…';
    if (!NEEDS_VALUE(f.op)) return `${label} ${OP_LABEL[f.op]}`;
    const value = f.op === 'in' ? splitList(f.value).join(', ') : f.value.trim();
    return `${label} ${OP_LABEL[f.op]} ${value || '…'}`;
  });
  const scope = relational
    ? form.join === 'inner' ? 'records that have a matching dimension row' : 'all records, including those without a matching dimension row'
    : 'all rows';
  const what = form.aggregate === 'count' ? 'Number of records' : `Total of ${amountLabel}`;
  return `${what} over ${scope}${parts.length ? ` where ${parts.join(form.match === 'any' ? ' or ' : ' and ')}` : ''}.`;
}
