// Guided calculation form → SQL text. The form never produces its own IR: the SQL it
// writes is registered through the same /flat/definitions or /relational/definitions
// validation as SQL typed in the editor, so both paths yield identical validated
// structures (or the same refusal). Pure module; type-only imports.
import type { FlatContract, RelationalContract, RelationalLookup } from './api';

/** Matches the engine's FULL_EVIDENCE_ROWS boundary: every aggregate works on large plain-amount
 *  datasets; only subtraction (v6) and CASE (v7) datasets over 100,000 rows are refused (server-checked too). */
export function aggregateRowLimitReason(rowCount: number, contractVersion?: number): string {
  return rowCount > 100_000 && (contractVersion === 6 || contractVersion === 7)
    ? 'Subtraction and CASE datasets over 100,000 rows cannot be compared yet; use a smaller extract' : '';
}

/** Lookup tables of a relational contract, the same shape for contract v1 (one) and v2 (several). */
function lookupsOf(contract: RelationalContract): RelationalLookup[] {
  if (contract.version !== 1) return contract.dimensions || [];
  return [{ role: 'dimension', table: contract.dimension.table, key: contract.dimension.key, grain: contract.dimension.grain,
            categories: contract.dimension.categories, fact_foreign_key: contract.relationship.fact_foreign_key }];
}

/** Relational datasets with several lookup tables (contract v2): each lookup is joined INNER, LEFT, or not at all. */
export function starLookups(contract: FlatContract | RelationalContract): RelationalLookup[] {
  const relational = contract as RelationalContract;
  return relational.mode === 'relational_snapshot' && relational.version !== 1 ? lookupsOf(relational) : [];
}

export type LookupJoin = 'left' | 'inner' | 'none';

export type ValueType = 'string' | 'boolean' | 'date' | 'timestamp';
export type FilterOp = 'eq' | 'neq' | 'in' | 'not_in' | 'is_null' | 'is_not_null' | 'gt' | 'gte' | 'lt' | 'lte';

export type FilterColumn = {
  /** Value used in the form, e.g. `status` or `dimension.segment`. */
  id: string;
  /** Display label, e.g. `status` or `dim_groups.segment`. */
  label: string;
  type: ValueType;
  side?: 'fact' | 'dimension';
  /** Relational: the lookup role this column belongs to (contract v2), or 'dimension' (v1). */
  role?: string;
  column: string;
};

export type Filter = { column: string; op: FilterOp; value: string };

/**
 * sum: SUM(amount) · count: COUNT(*) · count_distinct: COUNT(DISTINCT column) ·
 * average: AVG(amount) · per_record: SUM(amount) / COUNT(*) ·
 * rate: COUNT(*) FILTER (WHERE condition) / COUNT(*) · share: SUM(amount) FILTER (WHERE condition) / SUM(amount)
 */
export type Aggregate = 'sum' | 'count' | 'count_distinct' | 'average' | 'per_record' | 'rate' | 'share' | 'amount_ratio' | 'margin';

export const RATIO_AGGREGATES: Aggregate[] = ['average', 'per_record', 'rate', 'share', 'amount_ratio', 'margin'];
/** Need a dataset with several amount columns: SUM(a) / SUM(b) and SUM(a - b) / SUM(a). */
export const MULTI_AMOUNT_AGGREGATES: Aggregate[] = ['amount_ratio', 'margin'];

export type CalculationForm = {
  filters: Filter[];
  /** How conditions combine. 'any' (OR) needs a dataset with compound filters enabled. */
  match?: 'all' | 'any';
  /** What is calculated: a total, a count, a distinct count, an average or a ratio. */
  aggregate?: Aggregate;
  /** count_distinct: the column whose distinct values are counted. */
  distinctColumn?: string;
  /** rate and share: the condition that selects the numerator's records. */
  partFilter?: Filter;
  /** Datasets with several amount columns: the amount used (the top of a ratio). */
  amountColumn?: string;
  /** amount_ratio: the amount at the bottom; margin: the amount subtracted (e.g. cost). */
  secondAmountColumn?: string;
  /** Relational only: how facts without a matching dimension row are treated. */
  join?: 'left' | 'inner';
  /** Relational, several lookup tables: how each lookup is joined (default: LEFT when a filter uses it, else not joined). */
  joins?: Record<string, LookupJoin>;
};

export const OP_LABEL: Record<FilterOp, string> = {
  eq: 'is', neq: 'is not', in: 'is one of', not_in: 'is none of', is_null: 'is blank (NULL)', is_not_null: 'is not blank',
  gt: 'is after / greater than', gte: 'is on or after / at least', lt: 'is before / less than', lte: 'is on or before / at most',
};

const NEEDS_VALUE = (op: FilterOp) => op !== 'is_null' && op !== 'is_not_null';

/** Two-table datasets: the original one-lookup format (v1) accepts only =, IN and blank tests. */
export function operatorsFor(type: ValueType, relational: boolean, relationalVersion = 2): FilterOp[] {
  // Two-table columns are text; ranges compare by character order (right for ISO dates and codes).
  if (relational) return relationalVersion === 1 ? ['eq', 'in', 'is_null', 'is_not_null'] : ['eq', 'neq', 'in', 'not_in', 'gt', 'gte', 'lt', 'lte', 'is_null', 'is_not_null'];
  if (type === 'boolean') return ['eq', 'neq', 'is_null', 'is_not_null'];
  if (type === 'date' || type === 'timestamp') return ['eq', 'neq', 'in', 'not_in', 'gt', 'gte', 'lt', 'lte', 'is_null', 'is_not_null'];
  return ['eq', 'neq', 'in', 'not_in', 'is_null', 'is_not_null'];
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
    case 'in':
    case 'not_in': {
      const values = splitList(f.value);
      if (values.length === 0) throw new Error('List at least one value, separated by commas.');
      return `${ref} ${f.op === 'in' ? 'IN' : 'NOT IN'} (${values.map(v => literal(v, type)).join(', ')})`;
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
  if (form.aggregate && MULTI_AMOUNT_AGGREGATES.includes(form.aggregate) && !form.secondAmountColumn) {
    problems.push('Choose the second amount column.');
  }
  if (form.aggregate === 'count_distinct' && !columns.some(c => c.id === form.distinctColumn)) {
    problems.push('Choose the column whose distinct values are counted.');
  }
  if (form.aggregate === 'rate' || form.aggregate === 'share') {
    const f = form.partFilter;
    const col = columns.find(c => c.id === f?.column);
    if (!f || !col) problems.push('Choose the condition for the top of the ratio.');
    else if (NEEDS_VALUE(f.op) && !f.value.trim()) problems.push(`Enter a value for ${col.label} in the ratio condition.`);
    else if (NEEDS_VALUE(f.op)) {
      try { predicate('x', f, col.type); } catch (e) { problems.push(`Ratio condition: ${(e as Error).message}`); }
    }
  }
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
  const star = contract.version !== 1;
  return [
    ...contract.fact.categories.map(c => ({ id: `fact.${c}`, label: `${contract.fact.table}.${c}`, column: c, side: 'fact' as const, type: 'string' as const })),
    ...lookupsOf(contract).flatMap(d => d.categories.map(c => ({
      id: star ? `${d.role}.${c}` : `dimension.${c}`, label: `${d.table}.${c}`, column: c, side: 'dimension' as const,
      role: d.role, type: 'string' as const }))),
  ];
}

/** How each lookup is joined for this form: the analyst's choice, else LEFT if a filter uses it, else not joined. */
export function lookupJoins(contract: RelationalContract, form: CalculationForm): Record<string, LookupJoin> {
  const columns = relationalFilterColumns(contract);
  const used = new Set(form.filters.map(f => columns.find(c => c.id === f.column)?.role).filter(Boolean) as string[]);
  return Object.fromEntries(lookupsOf(contract).map(d => [d.role, form.joins?.[d.role] ?? (used.has(d.role) ? 'left' : 'none')]));
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

/** COUNT(DISTINCT column) needs a single-amount dataset (contract v3–v5). */
export function supportsDistinct(contract: FlatContract | RelationalContract): boolean {
  if ((contract as RelationalContract).mode === 'relational_snapshot') return false;
  const version = (contract as FlatContract).version;
  return (version >= 3 && version <= 5) || version === 8;
}

/** Averages and ratios need compound filters: a single-amount dataset (contract v5) or several amounts (v8). */
export function supportsRatios(contract: FlatContract | RelationalContract): boolean {
  if ((contract as RelationalContract).mode === 'relational_snapshot') return false;
  const version = (contract as FlatContract).version;
  return version === 5 || version === 8;
}

/** The amount columns of a dataset with several amounts (contract v8), else an empty list. */
export function amountColumns(contract: FlatContract | RelationalContract): string[] {
  if ((contract as RelationalContract).mode === 'relational_snapshot') return [];
  const flat = contract as FlatContract;
  return flat.version === 8 ? (flat.decimal_columns || []).map(c => c.name) : [];
}

export function flatMeasureSql(contract: FlatContract, form?: CalculationForm): string {
  if (contract.version === 8) {
    const names = (contract.decimal_columns || []).map(c => c.name);
    const chosen = form?.amountColumn && names.includes(form.amountColumn) ? form.amountColumn : names[0];
    return quoteIdent(chosen);
  }
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
  if (form.aggregate === 'count_distinct' && !supportsDistinct(contract)) throw new Error('This dataset cannot count distinct values.');
  if (form.aggregate && RATIO_AGGREGATES.includes(form.aggregate) && !supportsRatios(contract)) {
    throw new Error('Averages and ratios need a dataset registered with compound filters (re-register it to enable them).');
  }
  const m = flatMeasureSql(contract, form);
  const second = () => {
    const names = amountColumns(contract);
    if (!form.secondAmountColumn || !names.includes(form.secondAmountColumn)) throw new Error('Choose the second amount column.');
    if (quoteIdent(form.secondAmountColumn) === m) throw new Error('Choose two different amount columns.');
    return quoteIdent(form.secondAmountColumn);
  };
  if (form.aggregate && MULTI_AMOUNT_AGGREGATES.includes(form.aggregate) && amountColumns(contract).length < 2) {
    throw new Error('This needs a dataset registered with several amount columns.');
  }
  const partCondition = () => {
    const f = form.partFilter;
    const col = columns.find(c => c.id === f?.column);
    if (!f || !col) throw new Error('Choose the condition for the top of the ratio.');
    return predicate(quoteIdent(col.column), f, col.type);
  };
  const distinct = () => {
    const col = columns.find(c => c.id === form.distinctColumn);
    if (!col) throw new Error('Choose the column whose distinct values are counted.');
    return `COUNT(DISTINCT ${quoteIdent(col.column)})`;
  };
  const aggregate = {
    sum: () => `SUM(${m})`,
    count: () => 'COUNT(*)',
    count_distinct: distinct,
    average: () => `AVG(${m})`,
    per_record: () => `SUM(${m}) / COUNT(*)`,
    rate: () => `COUNT(*) FILTER (WHERE ${partCondition()}) / COUNT(*)`,
    share: () => `SUM(${m}) FILTER (WHERE ${partCondition()}) / SUM(${m})`,
    amount_ratio: () => `SUM(${m}) / SUM(${second()})`,
    margin: () => `SUM(${m} - ${second()}) / SUM(${m})`,
  }[form.aggregate || 'sum']();
  return `SELECT ${aggregate} FROM snapshot${where.length ? ` WHERE ${joinConditions(where, form.match)}` : ''}`;
}

function describeFilter(f: Filter, columns: FilterColumn[]): string {
  const col = columns.find(c => c.id === f.column);
  const label = col?.label || f.column || '…';
  if (!NEEDS_VALUE(f.op)) return `${label} ${OP_LABEL[f.op]}`;
  const value = f.op === 'in' || f.op === 'not_in' ? splitList(f.value).join(', ') : f.value.trim();
  return `${label} ${OP_LABEL[f.op]} ${value || '…'}`;
}

export function buildRelationalSql(contract: RelationalContract, form: CalculationForm): string {
  const columns = relationalFilterColumns(contract);
  if (contract.version !== 1) {
    const joins = lookupJoins(contract, form);
    const lookups = lookupsOf(contract);
    const alias = (role: string) => `l${lookups.findIndex(d => d.role === role) + 1}`;
    const where = form.filters.map(f => {
      const col = columns.find(c => c.id === f.column);
      if (!col) throw new Error('Choose a column for every condition.');
      if (col.side === 'dimension' && joins[col.role!] === 'none') throw new Error(`Join ${col.role} to filter on ${col.label}.`);
      return predicate(`${col.side === 'dimension' ? alias(col.role!) : 'f'}.${quoteIdent(col.column)}`, f, 'string');
    });
    const joinSql = lookups.filter(d => joins[d.role] !== 'none').map(d =>
      ` ${joins[d.role] === 'inner' ? 'INNER JOIN' : 'LEFT JOIN'} ${d.table} ${alias(d.role)} ON f.${quoteIdent(d.fact_foreign_key)} = ${alias(d.role)}.${quoteIdent(d.key)}`).join('');
    return `SELECT SUM(f.${quoteIdent(contract.measure.column)}) FROM ${contract.fact.table} f${joinSql}` +
      (where.length ? ` WHERE ${where.join(' AND ')}` : '');
  }
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
  const amount = form.amountColumn || amountLabel;
  const parts = form.filters.map(f => describeFilter(f, columns));
  const joined = Object.entries(form.joins || {}).filter(([, kind]) => kind !== 'none');
  const scope = relational && form.joins
    ? joined.length === 0 ? 'all records'
      : `records ${joined.map(([role, kind]) => `${kind === 'inner' ? 'with a matching' : 'with or without a matching'} ${role}`).join(' and ')}`
    : relational
    ? form.join === 'inner' ? 'records that have a matching dimension row' : 'all records, including those without a matching dimension row'
    : 'all rows';
  const condition = form.partFilter ? describeFilter(form.partFilter, columns) : '…';
  const distinctLabel = columns.find(c => c.id === form.distinctColumn)?.label || '…';
  const what = {
    sum: `Total of ${amount}`,
    count: 'Number of records',
    count_distinct: `Number of distinct ${distinctLabel} values`,
    average: `Average ${amount} (blank amounts skipped)`,
    per_record: `Total ${amount} per record`,
    rate: `Share of records where ${condition}`,
    share: `Share of ${amount} from records where ${condition}`,
    amount_ratio: `Total ${amount} ÷ total ${form.secondAmountColumn || '…'}`,
    margin: `Margin: total (${amount} − ${form.secondAmountColumn || '…'}, skipping records where either is blank) ÷ total ${amount}`,
  }[form.aggregate || 'sum'];
  return `${what} over ${scope}${parts.length ? ` where ${parts.join(form.match === 'any' ? ' or ' : ' and ')}` : ''}.`;
}

/** The values a filter column holds in the chosen dataset (from /snapshots/{id}/column-values). */
export type KnownValues = { complete: boolean; distinct_count: number; values: Array<{ value: string; count: number }> };

/** Columns with this many distinct values or fewer are offered as clickable values and as the default condition. */
export const FEW_VALUES = 20;

/** The column a new condition starts on: the first one with a short, complete list of values, else none. */
export function defaultConditionColumn(columns: FilterColumn[], known: Record<string, KnownValues | null | undefined>): string {
  return columns.find(c => {
    const k = known[c.id];
    return k && k.complete && k.distinct_count > 0 && k.distinct_count <= FEW_VALUES;
  })?.id || '';
}

/**
 * Typed values that do not occur in the column. Only text equality and list conditions are
 * checked, and only when every value of the column is known, so an empty result is never a guess.
 */
export function unknownFilterValues(f: Filter, type: ValueType, known: KnownValues | null | undefined): string[] {
  if (!known?.complete || type !== 'string' || !['eq', 'neq', 'in', 'not_in'].includes(f.op)) return [];
  const typed = f.op === 'in' || f.op === 'not_in' ? splitList(f.value) : [f.value].filter(v => v.trim() !== '');
  const present = new Set(known.values.map(v => v.value));
  return typed.filter(v => !present.has(v));
}

function editDistance(a: string, b: string): number {
  const row = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    let prev = row[0]; row[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const next = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = next;
    }
  }
  return row[b.length];
}

/** The known value an unknown one was most likely meant to be (case, a typo, or a shared start), if any. */
export function closestValue(typed: string, known: KnownValues | null | undefined): string | null {
  if (!known) return null;
  const t = typed.trim().toLowerCase();
  if (!t) return null;
  let best: string | null = null, bestScore = Infinity;
  for (const { value } of known.values) {
    const v = value.toLowerCase();
    const score = v === t ? 0 : v.startsWith(t) || t.startsWith(v) ? 1 : editDistance(t, v);
    if (score < bestScore) { best = value; bestScore = score; }
  }
  return best !== null && bestScore <= Math.max(2, Math.floor(t.length / 3)) ? best : null;
}

/** Plain warning for each typed value that matches nothing in the column. */
export function unknownValueWarnings(f: Filter, column: FilterColumn | undefined, known: KnownValues | null | undefined): string[] {
  if (!column) return [];
  return unknownFilterValues(f, column.type, known).map(v => {
    const near = closestValue(v, known);
    return `“${v}” does not occur in ${column.label}${near ? `; did you mean “${near}”?` : '.'}`;
  });
}
