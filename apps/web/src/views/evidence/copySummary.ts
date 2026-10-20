import type { EvidenceBreakdown, EvidenceSummary, InvestigationDetail } from '../../api';
import { caseNumber } from '../../investigationLibrary.ts';
import { groupDigits } from '../../resultSummary.ts';

const minus = (text: string) => text.replace(/^-/, '−');
const day = (iso?: string | null) => iso ? new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '';
const keyOf = (row: Record<string, unknown>) => String(row.fact_key ?? row.row_key ?? row.order_id ?? '—');

/**
 * Markdown for Slack, Jira or a pull request that also reads well as plain text
 * (docs/design/evidence-ui-spec.md §8). Every figure is the exact string the server returned.
 */
export function caseSummaryMarkdown(opts: {
  detail: InvestigationDetail; unit: string; summary: EvidenceSummary | null; breakdown: EvidenceBreakdown | null;
  link: string; replayVerifiedAt?: string | null;
}): string {
  const { detail, unit, summary, breakdown, link } = opts;
  const result = detail.run.result;
  const withUnit = (v?: string | null) => v == null ? '—' : minus(`${groupDigits(v)}${unit ? ` ${unit}` : ''}`);
  const change = result?.delta_target_minus_baseline ?? null;
  const direction = change == null ? 'cannot be calculated' : change.startsWith('-') ? 'is lower than the current one' : /[1-9]/.test(change) ? 'is higher than the current one' : 'equals the current one';
  const lines = [`**${detail.title}** (${caseNumber(detail.run.id)})`, `${withUnit(change)}: the proposed calculation ${direction}.`];
  if (summary) {
    const residual = result?.residual ?? '0';
    lines.push(`• ${groupDigits(String(summary.changed))} record${summary.changed === 1 ? '' : 's'} explain it, unexplained ${withUnit(residual)}${/[1-9]/.test(residual) ? '' : ' (balanced)'}.`);
    const top = summary.top_changes.slice(0, 3).map(r => `${keyOf(r as unknown as Record<string, unknown>)} ${minus(groupDigits(String(r.delta ?? '0')))}`);
    if (top.length) lines.push(`• Largest: ${top.join(', ')}.`);
  }
  const lead = breakdown?.available ? breakdown.groups?.[0] : null;
  if (lead && breakdown?.column && lead.change && /[1-9]/.test(lead.change)) {
    const total = Math.abs(Number(breakdown.abs_change_total ?? breakdown.total_change ?? 0));
    const share = total ? Math.round(Math.abs(Number(lead.change)) / total * 100) : null;
    const column = breakdown.column.split('.').pop();
    lines.push(`• Where it sits: ${column} = ${lead.value ?? 'blank'} ${withUnit(lead.change)}${share != null ? ` (${share}% of the difference)` : ''}.`);
  }
  lines.push(`Evidence sealed ${day(detail.created_at)}${opts.replayVerifiedAt ? ` · replay verified ${day(opts.replayVerifiedAt)}` : ''}`);
  lines.push(link);
  return lines.join('\n');
}
