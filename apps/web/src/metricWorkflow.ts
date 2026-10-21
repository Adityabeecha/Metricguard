import type { Definition, MetricBinding, MetricItem, Result, Snapshot, Version } from './api';
import { resultDisplayValue, resultUnit, valueWithUnit } from './resultSummary.ts';

export type MetricMember = { user_id: string; name?: string | null; email?: string | null };
type PinnedVersion = Pick<Version, 'contract_hash'>;
type Load = Pick<Snapshot, 'id' | 'contract_hash' | 'status' | 'created_at' | 'execution_policy'>;

/** A supported SQL shape alone does not make a calculation runnable on arbitrary data. */
export function matchingMetricLoads<T extends Load>(version: PinnedVersion | null | undefined, snapshots: T[], other?: PinnedVersion | null): T[] {
  if (!version?.contract_hash || (other && other.contract_hash !== version.contract_hash)) return [];
  return snapshots.filter(s => s.contract_hash === version.contract_hash && s.status === 'validated')
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
}

export function metricSupport(metric: MetricItem, definitions: Definition[], snapshots: Snapshot[], bindings: Pick<MetricBinding, 'metric_id' | 'definition_id' | 'status'>[] = []) {
  const candidate = bindings.find(b => b.metric_id === metric.id && b.status === 'candidate');
  const definition = definitions.find(d => d.id === (metric.active_binding?.definition_id || candidate?.definition_id)) || null;
  const version = definition?.versions.find(v => v.id === metric.active_binding?.approved_version_id) || null;
  const loads = matchingMetricLoads(version, snapshots);
  if (!definition && (metric.candidate_count || 0) > 0) return { state: 'unbound', label: 'Waiting for approval', isAnalyzable: false,
    reason: 'Review a proposed calculation and make it active to approve a version.', definition, version, loads };
  if (!definition) return { state: 'unbound', label: 'Add SQL for a dataset', isAnalyzable: false,
    reason: 'Add a calculation and choose the dataset it uses.', definition, version, loads };
  if (!version) return { state: 'unbound', label: 'Waiting for approval', isAnalyzable: false,
    reason: 'Review a proposed calculation and make it active to approve a version.', definition, version, loads };
  if (!version.contract_hash) return { state: 'limited', label: 'Add SQL for a dataset', isAnalyzable: false,
    reason: 'Not linked to a dataset — add the SQL again for a dataset', definition, version, loads };
  const supported = ['executable', 'supported'].includes(version.support_state);
  if (!supported) return { state: 'limited', label: 'Add SQL for a dataset', isAnalyzable: false,
    reason: 'This SQL cannot be compared yet. Add supported SQL for the selected dataset.', definition, version, loads };
  if (!loads.length) return { state: 'limited', label: 'Add SQL for a dataset', isAnalyzable: false,
    reason: 'No data load is available for this calculation. Add a load of its dataset or add SQL for an available dataset.', definition, version, loads };
  return { state: 'analyzable', label: 'Ready to compare', isAnalyzable: true,
    reason: 'Compare this approved calculation using its dataset.', definition, version, loads };
}

export function memberName(actorId: string | null | undefined, members: MetricMember[]): string {
  const member = members.find(m => m.user_id === actorId);
  return member?.name?.trim() || 'Unknown member';
}

export function metricDifference(result: Result): string {
  return valueWithUnit(resultDisplayValue(result, result.delta_target_minus_baseline), resultUnit(result)).replace(/^-/, '−');
}

export function retainedCalculations<T extends { id: string }>(definitions: T[], usage: Array<{ definition_id: string; retained: boolean }>, showAll: boolean): T[] {
  if (showAll) return definitions;
  const retained = new Set(usage.filter(u => u.retained).map(u => u.definition_id));
  return definitions.filter(d => retained.has(d.id));
}

export function sqlDefinitionRequest(name: string, sql: string, contractId: string, contracts: Array<{ id: string; contract_hash: string }>, snapshots: Load[]) {
  const contract = contracts.find(c => c.id === contractId);
  if (!contract) throw new Error('Choose a dataset before adding SQL.');
  if (!snapshots.some(s => s.contract_hash === contract.contract_hash && s.status === 'validated')) throw new Error('Choose a dataset with an available data load.');
  if (!sql.trim()) throw new Error('SQL query cannot be empty.');
  return { endpoint: '/flat/definitions', body: { name: name.trim(), raw_sql: sql.trim(), contract_id: contract.id } };
}
