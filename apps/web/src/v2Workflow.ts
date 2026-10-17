import type {ColumnMapping, Context, DatasetContract, Definition, Snapshot, Version} from './api';

export const roles = [
  ['order_id', 'Unique row key', 'VARCHAR'],
  ['customer_id', 'Eligible entity key', 'VARCHAR'],
  ['order_date', 'Order business date', 'DATE'],
  ['payment_date', 'Payment business date', 'DATE'],
  ['status', 'Order category', 'VARCHAR'],
  ['payment_status', 'Payment category', 'VARCHAR'],
  ['order_amount', 'Order amount', 'DECIMAL(18,2)'],
  ['refund_amount', 'Refund amount', 'DECIMAL(18,2)'],
  ['currency', 'Currency code', 'VARCHAR'],
] as const;

export const schema = Object.fromEntries(roles.map(([role, , kind]) => [role, kind]));

export function mappingIssue(columns: Record<string, string>, sourceColumns?: string[]): string | null {
  for (const [role] of roles) {
    if (!columns[role]?.trim()) return `Map ${role} to a source column.`;
    if (sourceColumns && !sourceColumns.includes(columns[role])) return `Source column ${columns[role]} for ${role} was not found in the preview.`;
  }
  if (new Set(roles.map(([role]) => columns[role])).size !== roles.length) return 'Every analytical role needs a distinct source column.';
  return null;
}

export function matchingSnapshots(snapshots: Snapshot[], mapping: Pick<ColumnMapping, 'contract_hash' | 'mapping_hash'> | undefined): Snapshot[] {
  if (!mapping) return [];
  return snapshots.filter(snapshot => snapshot.contract_hash === mapping.contract_hash && snapshot.mapping_hash === mapping.mapping_hash);
}

export function boundVersions(definitions: Definition[], mapping: Pick<ColumnMapping, 'id' | 'contract_hash' | 'mapping_hash'> | undefined) {
  if (!mapping) return [];
  return definitions.flatMap(definition => definition.versions
    .filter(version => version.mapping_id === mapping.id && version.contract_hash === mapping.contract_hash && version.mapping_hash === mapping.mapping_hash && version.support_state === 'executable')
    .map(version => ({definition, version})));
}

export function sourcePolicyIssue(name: string, policy: DatasetContract['null_policy']): string | null {
  const lower = name.toLowerCase();
  if (lower.endsWith('.csv')) return policy === 'native_null' ? 'CSV requires a blank or literal NULL policy in the approved contract.' : null;
  if (lower.endsWith('.parquet')) return policy === 'native_null' ? null : 'Parquet requires native NULL policy in the approved contract.';
  return 'Choose a CSV or Parquet extract.';
}

export function comparisonIssue(left: Version | undefined, right: Version | undefined, snapshot: Snapshot | undefined, context?: Pick<Context, 'contract_hash' | 'mapping_hash'> & Partial<Pick<Context, 'unit'>>): string | null {
  if (!snapshot) return 'Choose a validated mapped snapshot.';
  if (!left || !right) return 'Choose two bound definition versions.';
  if (left.id === right.id) return 'Choose two distinct definition versions.';
  if (context && (context.contract_hash !== snapshot.contract_hash || context.mapping_hash !== snapshot.mapping_hash)) return 'The reporting context contract or mapping differs from the selected snapshot.';
  for (const version of [left, right]) {
    if (version.contract_hash !== snapshot.contract_hash || version.mapping_hash !== snapshot.mapping_hash) return 'The definition contract or mapping differs from the selected snapshot.';
    if (version.support_state !== 'executable') return version.support_reason || 'A selected definition is not executable.';
  }
  if ((left.ir as {aggregate?: string} | null)?.aggregate !== (right.ir as {aggregate?: string} | null)?.aggregate) return 'Choose definitions from the same aggregate family.';
  const requiredUnit = (left.ir as {aggregate?: string} | null)?.aggregate === 'sum' ? 'currency' : 'count';
  if (context?.unit && context.unit !== requiredUnit) return `This comparison requires unit ${requiredUnit}.`;
  return null;
}
