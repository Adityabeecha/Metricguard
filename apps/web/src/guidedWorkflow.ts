/** Immutable input identity makes a retry reuse the server's comparison. */
export async function comparisonRequestKey(projectId: string, request: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify([projectId, request]));
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return `guided-${Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('')}`;
}

export function calculationPage<T extends { id: string; name: string; source_label: string }>(entries: T[], query: string, page: number) {
  const needle = query.trim().toLowerCase();
  const filtered = entries.filter(d => `${d.name} ${d.id} ${d.source_label}`.toLowerCase().includes(needle));
  const pages = Math.max(1, Math.ceil(filtered.length / 8));
  const current = Math.min(Math.max(0, page), pages - 1);
  return { items: filtered.slice(current * 8, (current + 1) * 8), total: filtered.length, pages, current };
}

/** Source labels the server gives calculations registered by the guided comparison form. */
const COMPARISON_SOURCES = new Set(['Flat snapshot SQL', 'Relational snapshot SQL']);

export function isComparisonCalculation(d: { source_label: string }): boolean {
  return COMPARISON_SOURCES.has(d.source_label);
}

/** Imported queries stay in view; calculations saved by guided comparisons are grouped apart. */
export function splitCalculations<T extends { source_label: string }>(entries: T[]) {
  return { imported: entries.filter(d => !isComparisonCalculation(d)), fromComparisons: entries.filter(isComparisonCalculation) };
}

/**
 * Saved calculations for the "start from" picker: those used in saved cases first, then the
 * rest newest first, narrowed by a search over name and SQL. The chosen one always stays listed.
 */
export function groupSavedCalculations<T extends { definition: { name: string }; version: { id: string; raw_sql: string; observed_at: string } }>(
  entries: T[], usedInCases: ReadonlySet<string>, query: string, keep?: string,
) {
  const needle = query.trim().toLowerCase();
  const newest = [...entries].sort((a, b) => (b.version.observed_at || '').localeCompare(a.version.observed_at || ''));
  const matches = newest.filter(x => x.version.id === keep || !needle
    || `${x.definition.name} ${x.version.raw_sql}`.toLowerCase().includes(needle));
  return {
    used: matches.filter(x => usedInCases.has(x.version.id)),
    other: matches.filter(x => !usedInCases.has(x.version.id)),
    total: entries.length,
    shown: matches.length,
  };
}
