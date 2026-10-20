import type { EvidenceBreakdown, EvidenceSummary, InvestigationDetail } from '../../api';
import { caseNumber } from '../../investigationLibrary.ts';
import { groupDigits, resultDisplayValue } from '../../resultSummary.ts';
import { breakdownLead, breakdownSharePercent } from './breakdownSelection.ts';

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
  const display = (v?: string | null) => resultDisplayValue(result, v);
  const withUnit = (v?: string | null) => {
    const value = display(v);
    return value == null ? '—' : minus(`${groupDigits(value)}${unit ? ` ${unit}` : ''}`);
  };
  const change = result?.delta_target_minus_baseline ?? null;
  const direction = change == null ? 'cannot be calculated' : change.startsWith('-') ? 'is lower than the current one' : /[1-9]/.test(change) ? 'is higher than the current one' : 'equals the current one';
  const lines = [`**${detail.title}** (${caseNumber(detail.run.id)})`, `${withUnit(change)}: the proposed calculation ${direction}.`];
  if (summary) {
    const residual = result?.residual ?? '0';
    lines.push(`• ${groupDigits(String(summary.changed))} record${summary.changed === 1 ? '' : 's'} explain it, unexplained ${withUnit(residual)}${/[1-9]/.test(residual) ? '' : ' (balanced)'}.`);
    const ratio = result?.aggregate === 'ratio';
    const top = summary.top_changes.slice(0, 3).flatMap(r => {
      const value = display(ratio ? r.effect : r.delta);
      if (value == null) return [];
      return [`${keyOf(r as unknown as Record<string, unknown>)} ${minus(groupDigits(value))}${ratio && unit ? ` ${unit}` : ''}`];
    });
    if (top.length) lines.push(`• Largest: ${top.join(', ')}.`);
  }
  const lead = breakdownLead(breakdown);
  if (lead && breakdown?.column) {
    const column = breakdown.column.split('.').pop();
    const value = lead.group.no_match ? 'no matching lookup row' : lead.group.value ?? 'blank';
    lines.push(`• Where it sits: ${column} = ${value} ${withUnit(lead.group.change)} (${breakdownSharePercent(lead)}% of the difference).`);
  }
  lines.push(`Evidence sealed ${day(detail.created_at)}${opts.replayVerifiedAt ? ` · replay verified ${day(opts.replayVerifiedAt)}` : ''}`);
  lines.push(link);
  return lines.join('\n');
}
