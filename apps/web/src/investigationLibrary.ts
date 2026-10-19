import { resultUnit, resultDisplayValue } from './resultSummary.ts';
// One list of investigations regardless of the execution mode that produced them.
// Pure module with no browser dependencies, so it can be tested under node.
import type { Bootstrap, InvestigationDraft } from './api';

export type LibraryKind = 'single' | 'relational' | 'orders';
export type LibraryStatus = 'saved' | 'draft' | 'running' | 'refused';

export type LibraryItem = {
  id: string;
  source: 'saved' | 'draft';
  kind: LibraryKind;
  status: LibraryStatus;
  title: string;
  metricNames: string[];
  datasetName: string;
  currentLabel: string;
  proposedLabel: string;
  change: string | null;
  unit: string;
  hasConclusion: boolean;
  updatedAt: string;
};

export const KIND_LABEL: Record<LibraryKind, string> = {
  single: 'Single table',
  relational: 'Two tables',
  orders: 'Orders extract',
};

export const STATUS_LABEL: Record<LibraryStatus, string> = {
  saved: 'Saved',
  draft: 'Draft',
  running: 'Running',
  refused: 'Refused',
};

function kindOf(mode: string | undefined, policy: string | undefined): LibraryKind {
  if (mode === 'relational_snapshot' || policy === 'relational_snapshot_v1') return 'relational';
  if (mode === 'flat_snapshot' || policy === 'flat_snapshot_v1') return 'single';
  return 'orders';
}

export function buildLibraryItems(data: Pick<Bootstrap, 'investigations' | 'runs' | 'snapshots' | 'definitions' | 'metrics'>, drafts: InvestigationDraft[]): LibraryItem[] {
  const versionOwner = new Map<string, { definitionId: string; label: string }>();
  for (const d of data.definitions || []) {
    for (const v of d.versions) versionOwner.set(v.id, { definitionId: d.id, label: `${d.name} · v${v.version_number}` });
  }
  const metricsFor = (versionIds: Array<string | null | undefined>, metricId?: string | null) => {
    const defs = new Set(versionIds.map(id => (id ? versionOwner.get(id)?.definitionId : undefined)).filter(Boolean));
    return (data.metrics || [])
      .filter(m => m.id === metricId || (m.active_binding && defs.has(m.active_binding.definition_id)))
      .map(m => m.display_name);
  };
  const label = (id?: string | null) => (id ? versionOwner.get(id)?.label || 'Calculation' : '—');
  const snapshotName = (id?: string | null) => data.snapshots.find(s => s.id === id)?.name || 'Dataset unavailable';

  const items: LibraryItem[] = [];
  for (const inv of data.investigations || []) {
    const run = (data.runs || []).find(r => r.id === inv.run_id);
    const ctx = inv.context as unknown as { mode?: string; unit_label?: string; currency?: string };
    const snapshot = data.snapshots.find(s => s.id === inv.snapshot_id);
    items.push({
      id: inv.id,
      source: 'saved',
      kind: kindOf(ctx?.mode, snapshot?.execution_policy),
      status: 'saved',
      title: inv.title || 'Saved investigation',
      metricNames: metricsFor([inv.baseline_version_id, inv.target_version_id]),
      datasetName: snapshotName(inv.snapshot_id),
      currentLabel: label(inv.baseline_version_id),
      proposedLabel: label(inv.target_version_id),
      change: resultDisplayValue(run?.result, run?.result?.delta_target_minus_baseline),
      unit: resultUnit(run?.result, ctx?.unit_label || ctx?.currency || ''),
      hasConclusion: Boolean(inv.decision),
      updatedAt: inv.created_at,
    });
  }
  const savedRuns = new Set((data.investigations || []).map(i => i.run_id));
  for (const d of drafts) {
    if (d.comparison_run_id && savedRuns.has(d.comparison_run_id) && d.status === 'completed') continue;
    const run = d.comparison_run_id ? (data.runs || []).find(r => r.id === d.comparison_run_id) : undefined;
    const snapshot = data.snapshots.find(s => s.id === d.snapshot_id);
    const ctx = run?.context as unknown as { mode?: string; unit_label?: string } | undefined;
    items.push({
      id: d.id,
      source: 'draft',
      kind: kindOf(ctx?.mode, snapshot?.execution_policy),
      status: d.status === 'running' ? 'running' : d.status === 'refused' ? 'refused' : 'draft',
      title: d.title || 'Untitled draft',
      metricNames: metricsFor([d.baseline_version_id, d.target_version_id], d.metric_id),
      datasetName: d.snapshot_id ? snapshotName(d.snapshot_id) : 'No dataset chosen',
      currentLabel: label(d.baseline_version_id),
      proposedLabel: label(d.target_version_id),
      change: resultDisplayValue(run?.result, run?.result?.delta_target_minus_baseline),
      unit: resultUnit(run?.result, ctx?.unit_label || ''),
      hasConclusion: false,
      updatedAt: (d as { updated_at?: string; created_at?: string }).updated_at || (d as { created_at?: string }).created_at || '',
    });
  }
  return items.sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''));
}

export function filterLibraryItems(items: LibraryItem[], f: { query: string; kind: LibraryKind | 'all'; status: LibraryStatus | 'all' }): LibraryItem[] {
  const q = f.query.trim().toLowerCase();
  return items.filter(item => {
    if (f.kind !== 'all' && item.kind !== f.kind) return false;
    if (f.status !== 'all' && item.status !== f.status) return false;
    if (!q) return true;
    return [item.title, item.datasetName, item.currentLabel, item.proposedLabel, ...item.metricNames]
      .some(text => text.toLowerCase().includes(q));
  });
}
