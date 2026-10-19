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
