import { resultUnit, resultDisplayValue } from './resultSummary.ts';
// One list of investigations regardless of the execution mode that produced them.
// Pure module with no browser dependencies, so it can be tested under node.
import type { Bootstrap, InvestigationDraft } from './api';

export type LibraryKind = 'single' | 'relational' | 'orders';
export type LibraryStatus = 'saved' | 'draft' | 'running' | 'refused';

export type LibraryItem = {
  id: string;
  /** Case number shown and searched: the number of the run the case pins (the same one its seal shows). */
  number: string;
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
  /** Why the draft's comparison did not finish, when it failed or was refused. */
  problem?: string;
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
  refused: 'Failed or refused',
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
      number: caseNumber(inv.run_id || inv.id),
      source: 'saved',
      kind: kindOf(ctx?.mode, snapshot?.execution_policy),
      status: 'saved',
      title: inv.title || 'Saved case',
      metricNames: metricsFor([inv.baseline_version_id, inv.target_version_id]),
      datasetName: snapshotName(inv.snapshot_id),
      currentLabel: run?.target_snapshot_id ? `${label(inv.baseline_version_id)} on ${snapshotName(inv.snapshot_id)}` : label(inv.baseline_version_id),
      proposedLabel: run?.target_snapshot_id ? `a later load (${snapshotName(run.target_snapshot_id)})` : label(inv.target_version_id),
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
      number: caseNumber(d.comparison_run_id || d.id),
      source: 'draft',
      kind: kindOf(ctx?.mode, snapshot?.execution_policy),
      status: run?.state === 'failed' || d.status === 'refused' ? 'refused' : d.status === 'running' ? 'running' : 'draft',
      problem: run?.state === 'failed' ? run.error?.message || 'The comparison failed without a recorded reason.' : undefined,
      title: d.title || 'Untitled draft',
      metricNames: metricsFor([d.baseline_version_id, d.target_version_id], d.metric_id),
      datasetName: d.snapshot_id ? snapshotName(d.snapshot_id) : 'No dataset chosen',
      currentLabel: run?.target_snapshot_id ? `${label(d.baseline_version_id)} on ${snapshotName(d.snapshot_id)}` : label(d.baseline_version_id),
      proposedLabel: run?.target_snapshot_id ? `a later load (${snapshotName(run.target_snapshot_id)})` : label(d.target_version_id),
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
    return [item.number, item.title, item.datasetName, item.currentLabel, item.proposedLabel, ...item.metricNames]
      .some(text => text.toLowerCase().includes(q));
  });
}

/** A short, stable case number, made from the id of the run a case pins: MG-3F2A. */
export function caseNumber(id: string): string {
  return `MG-${id.replace(/[^0-9a-zA-Z]/g, '').slice(0, 4).toUpperCase()}`;
}

/** The stamp a case carries in lists. */
export const STAMP: Record<LibraryStatus, { label: string; tone: 'sealed' | 'draft' | 'running' | 'failed' }> = {
  saved: { label: 'Sealed', tone: 'sealed' },
  draft: { label: 'Draft', tone: 'draft' },
  running: { label: 'Running', tone: 'running' },
  refused: { label: 'Failed', tone: 'failed' },
};

type RunLike = { id: string; created_at: string; purpose?: string; snapshot_id: string; target_snapshot_id?: string; baseline_version_id: string; target_version_id: string;
  result?: { baseline_value?: string | null; target_value?: string | null; delta_target_minus_baseline?: string | null } | null };

/** Whether a finished run produced a value on both sides (an empty filter on a ratio leaves it Undefined). */
export function hasDefinedResult(run: Pick<RunLike, 'result'>): boolean {
  const res = run.result;
  return Boolean(res && res.baseline_value != null && res.target_value != null && res.delta_target_minus_baseline != null);
}

/**
 * Comparisons with a difference nobody has explained yet: newest first, one per
 * (current, proposed, dataset) pair, excluding runs already saved in a case and replays.
 * Failed runs never appear (they have no result). Runs whose result is Undefined and runs
 * the analyst dismissed are left out unless asked for.
 */
export function unexplainedRuns<R extends RunLike>(
  runs: R[],
  investigations: Array<{ run_id: string; baseline_version_id?: string; target_version_id?: string; snapshot_id?: string }>,
  options: { dismissed?: ReadonlySet<string>; includeUndefined?: boolean; includeDismissed?: boolean } = {},
): R[] {
  const savedRunIds = new Set(investigations.map(i => i.run_id));
  const savedPairs = new Set(investigations.map(i => `${i.baseline_version_id}|${i.target_version_id}|${i.snapshot_id}`));
  const seen = new Set<string>();
  return [...runs]
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
    .filter(r => {
      const res = r.result;
      if (!res || savedRunIds.has(r.id) || /replay/.test(r.purpose || '')) return false;
      const pair = `${r.baseline_version_id}|${r.target_version_id}|${r.snapshot_id}`;
      if (savedPairs.has(pair) || seen.has(pair)) return false;
      seen.add(pair);
      if (!options.includeDismissed && options.dismissed?.has(r.id)) return false;
      if (!hasDefinedResult(r)) return Boolean(options.includeUndefined);
      return /[1-9]/.test(res.delta_target_minus_baseline || '');
    });
}

/** Runs dismissed from "Needs a conclusion", remembered per workspace in this browser only. */
const dismissedKey = (workspaceId: string) => `mg.dismissed-runs.${workspaceId}`;

export function readDismissedRuns(workspaceId: string): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(dismissedKey(workspaceId)) || '[]');
    return new Set(Array.isArray(raw) ? raw.filter((x: unknown): x is string => typeof x === 'string') : []);
  } catch { return new Set(); }
}

export function writeDismissedRuns(workspaceId: string, ids: ReadonlySet<string>): void {
  // Keep the newest few hundred; older dismissed runs have long scrolled out of the list.
  try { localStorage.setItem(dismissedKey(workspaceId), JSON.stringify([...ids].slice(-500))); } catch { /* per-browser convenience only */ }
}
