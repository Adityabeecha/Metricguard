/** Dataset identity follows its execution contract; filenames and load labels can change. */
type DatasetChoice = {
  snapshot: { id: string; name: string; created_at: string; execution_policy?: string; contract_hash?: string | null };
  flat?: { name: string };
  relational?: { name: string };
};

export function comparisonDatasetGroups<T extends DatasetChoice>(choices: T[]) {
  const grouped = new Map<string, T[]>();
  for (const choice of choices) {
    const key = `${choice.snapshot.execution_policy || 'flat_snapshot_v1'}|${choice.snapshot.contract_hash || choice.snapshot.id}`;
    grouped.set(key, [...(grouped.get(key) || []), choice]);
  }
  return [...grouped].map(([key, entries]) => {
    const loads = [...entries].sort((a, b) => b.snapshot.created_at.localeCompare(a.snapshot.created_at)
      || b.snapshot.id.localeCompare(a.snapshot.id));
    const oldest = loads[loads.length - 1];
    return { key, name: (oldest.flat || oldest.relational)?.name || oldest.snapshot.name, loads, latest: loads[0] };
  }).sort((a, b) => b.latest.snapshot.created_at.localeCompare(a.latest.snapshot.created_at));
}

export function initialComparisonLoad<T extends DatasetChoice>(choices: T[], currentId: string, preferredId?: string): string {
  // Preserve an upload handoff even while its refreshed dataset list is still arriving.
  if (preferredId) return preferredId;
  if (choices.some(c => c.snapshot.id === currentId)) return currentId;
  const groups = comparisonDatasetGroups(choices);
  return groups.length === 1 ? groups[0].latest.snapshot.id : '';
}

export const COMPARISON_KINDS = [
  { value: 'calculations', label: 'Two calculations on the same data' },
  { value: 'loads', label: 'Same calculation on two data loads' },
] as const;

export function matchCountKey(snapshotId: string, sql: string): string { return `${snapshotId}|${sql.trim()}`; }

type MatchResult = { matched: number; total: number } | null | 'error' | undefined;
/** Only counts for the currently selected load and exact query can block submission. */
export function emptyCalculationSides(snapshotId: string, sql: { baseline: string; target: string }, counts: Record<string, MatchResult>) {
  return (['baseline', 'target'] as const).filter(side => {
    const result = sql[side].trim() ? counts[matchCountKey(snapshotId, sql[side])] : undefined;
    return result && result !== 'error' && result.matched === 0;
  });
}

/** An asynchronously loaded account name must never replace typing or an existing case's author. */
export function recordedByDefault(current: string, identity: { user: { name: string | null; email: string | null } | null } | null, edited: boolean): string {
  return edited ? current : identity?.user?.name || identity?.user?.email || current;
}
