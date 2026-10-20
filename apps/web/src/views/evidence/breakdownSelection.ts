import type { BreakdownGroup, EvidenceBreakdown, Version } from '../../api';

/** Exact qualified matches win; a bare name only resolves when it is unambiguous. */
export function preferredBreakdownColumn(columns: string[], preferred: string[]): string | undefined {
  for (const wanted of preferred) {
    if (columns.includes(wanted)) return wanted;
    // A missing qualified role must never resolve to a different table.
    if (wanted.includes('.')) continue;
    const matches = columns.filter(column => column.split('.').pop() === wanted.split('.').pop());
    if (matches.length === 1) return matches[0];
  }
  return undefined;
}

/** Saved cases have immutable IR instead of the editor's form state. Visit filter locations only. */
export function savedFilterColumns(...versions: Array<Pick<Version, 'ir'> | null | undefined>): string[] {
  const columns = new Set<string>();
  const visit = (value: unknown) => {
    if (Array.isArray(value)) { value.forEach(visit); return; }
    if (!value || typeof value !== 'object') return;
    const node = value as Record<string, unknown>;
    if (node.op === 'column' && typeof node.name === 'string') columns.add(node.name);
    if (typeof node.op === 'string' && typeof node.field === 'string') columns.add(node.field);
    if (typeof node.op === 'string' && typeof node.column === 'string') {
      const role = typeof node.table === 'string' && node.table !== 'snapshot' ? `${node.table}.` : '';
      columns.add(role + node.column);
    }
    Object.values(node).forEach(visit);
  };
  for (const version of versions) {
    const ir = version?.ir;
    if (!ir) continue;
    [ir.predicates, ir.predicate_tree, ir.predicate, ir.where].forEach(visit);
    for (const part of ['numerator', 'denominator']) {
      const node = ir[part];
      if (node && typeof node === 'object') visit((node as Record<string, unknown>).filter);
    }
    const expression = ir.measure_expression;
    if (expression && typeof expression === 'object') visit((expression as Record<string, unknown>).when);
  }
  return [...columns];
}

type Fraction = { numerator: bigint; denominator: bigint };

/** Server effects/totals may be decimal strings or exact rational strings. */
function absoluteFraction(value: string | null | undefined): Fraction | null {
  if (value == null) return null;
  const rational = /^[+-]?(\d+)\/(\d+)$/.exec(value);
  if (rational) {
    const denominator = BigInt(rational[2]);
    return denominator ? { numerator: BigInt(rational[1]), denominator } : null;
  }
  const decimal = /^[+-]?(\d+)(?:\.(\d+))?$/.exec(value);
  return decimal ? { numerator: BigInt(decimal[1] + (decimal[2] || '')), denominator: 10n ** BigInt(decimal[2]?.length || 0) } : null;
}

const greater = (left: Fraction, right: Fraction) =>
  left.numerator * right.denominator > right.numerator * left.denominator;

export type BreakdownLead = { group: BreakdownGroup; share: Fraction };

/** Never infer an unsupported effect from per-group side totals or the net change. */
export function breakdownLead(data: EvidenceBreakdown | null): BreakdownLead | null {
  if (!data?.available || !data.column) return null;
  const total = absoluteFraction(data.abs_change_total);
  if (!total?.numerator) return null;
  let lead: BreakdownLead | null = null;
  for (const group of data.groups || []) {
    if (group.change == null) continue;
    const change = absoluteFraction(group.change_exact ?? group.change);
    if (!change?.numerator) continue;
    const share = { numerator: change.numerator * total.denominator, denominator: change.denominator * total.numerator };
    if (share.numerator > share.denominator) continue;
    if (!lead || greater(share, lead.share)) lead = { group, share };
  }
  return lead;
}

/** Rounded half-up without coercing evidence values through Number. */
export function breakdownSharePercent(lead: BreakdownLead): string {
  const { numerator, denominator } = lead.share;
  return ((numerator * 200n + denominator) / (denominator * 2n)).toString();
}

/** Preferred filter first; otherwise compare each column's largest supported group share. */
export async function loadSummaryBreakdown(
  load: (column?: string, top?: number) => Promise<EvidenceBreakdown>, preferred: string[],
): Promise<EvidenceBreakdown | null> {
  const first = await load(undefined, 1);
  if (!first.available) return null;
  const wanted = preferredBreakdownColumn(first.columns || [], preferred);
  if (wanted) return wanted === first.column ? first : load(wanted, 1);
  const others = await Promise.all([...new Set(first.columns || [])].filter(column => column !== first.column)
    .map(column => load(column, 1)));
  let selected: EvidenceBreakdown | null = null;
  let best: BreakdownLead | null = null;
  for (const candidate of [first, ...others]) {
    const lead = breakdownLead(candidate);
    if (lead && (!best || greater(lead.share, best.share))) { selected = candidate; best = lead; }
  }
  return selected;
}

/** A group's share of the absolute difference (0–1), computed exactly; ratio totals arrive as fractions like "1/3". */
export function groupShare(change: string | null | undefined, changeExact: string | null | undefined, absTotal: string | null | undefined): number {
  const total = absoluteFraction(absTotal);
  const part = absoluteFraction(changeExact ?? change);
  if (!total?.numerator || !part) return 0;
  const num = part.numerator * total.denominator, den = part.denominator * total.numerator;
  return Number((num * 1_000_000n) / den) / 1_000_000;
}
