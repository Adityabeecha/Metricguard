// Plain-language reading of a comparison result. Works on exact decimal strings and
// never converts them to floating point. Keeps the mathematical distinctions:
// an undefined total is not zero, an explained difference is not automatically
// correct, and a negative change is not automatically a financial loss.

export type SummaryInput = {
  baseline_value: string | null;
  target_value: string | null;
  delta_target_minus_baseline: string | null;
  residual: string | null;
  limitations?: string[];
  explanation_status?: string;
};

export type RecordDelta = { key: string; delta: string | null };

export type ResultSummary = {
  defined: boolean;
  direction: 'up' | 'down' | 'same' | 'undefined';
  /** Absolute change as an exact string, e.g. "340.00". */
  magnitude: string | null;
  headline: string;
  recordsSentence: string | null;
  changedRecords: RecordDelta[];
  uncertainties: string[];
};

/**
 * Thousands separators for an exact decimal string, without parsing it as a float:
 * "-1000269" → "-1,000,269", "16008872.12" → "16,008,872.12". Non-numeric text is returned unchanged.
 */
export function groupDigits(value: string): string {
  const match = /^([-+]?)(\d+)(\.\d+)?$/.exec(value);
  if (!match) return value;
  const [, sign, whole, fraction = ''] = match;
  return `${sign}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${fraction}`;
}

export function isZeroDecimal(value: string | null | undefined): boolean {
  return value != null && !/[1-9]/.test(value);
}

export function absDecimal(value: string): string {
  return value.replace(/^[-+]/, '');
}

function withUnit(value: string, unit: string): string {
  const shown = groupDigits(value);
  return unit ? `${shown} ${unit}` : shown;
}

/**
 * Engine limitations are fixed strings stored with each result. Reword the generic
 * ones so they never contradict the dataset's declared unit; others pass through.
 */
export function plainLimitation(text: string, unit: string): string | null {
  if (/^Unknown currency remains unknown/i.test(text)) {
    return unit
      ? `Amounts are compared in the declared unit (${unit}). MetricGuard does not convert units, and a difference is not verified as a financial gain or loss.`
      : 'No unit was declared, so amounts are compared as plain numbers; a difference is not verified as a financial gain or loss.';
  }
  if (/^Observed equality applies only to this frozen whole snapshot/i.test(text)) {
    return 'These totals apply only to this frozen dataset; other data could give different results.';
  }
  if (/^An endpoint SUM is NULL/i.test(text)) return null; // already stated in the headline
  return text;
}

export function summarizeResult(
  result: SummaryInput,
  unit: string,
  records?: { rows: RecordDelta[]; total: number; complete: boolean; changedCount?: number },
): ResultSummary {
  const uncertainties: string[] = [];
  const delta = result.delta_target_minus_baseline;
  const defined = result.baseline_value != null && result.target_value != null && delta != null;

  let direction: ResultSummary['direction'] = 'undefined';
  let headline: string;
  if (!defined) {
    headline = result.baseline_value == null && result.target_value == null
      ? 'The change cannot be calculated: neither calculation has any values to add up, so both totals are undefined (not zero).'
      : `The change cannot be calculated: the ${result.baseline_value == null ? 'current' : 'proposed'} calculation has no values to add up, so its total is undefined (not zero).`;
    uncertainties.push('An undefined total means there were no non-blank amounts to add; it is not the same as a total of 0.');
  } else if (isZeroDecimal(delta)) {
    direction = 'same';
    headline = 'Both calculations give the same total on this dataset.';
    uncertainties.push('Equal totals on this frozen dataset do not prove the two calculations are equivalent on other data.');
  } else {
    direction = delta!.startsWith('-') ? 'down' : 'up';
    headline = `The proposed calculation is ${withUnit(absDecimal(delta!), unit)} ${direction === 'down' ? 'lower' : 'higher'} than the current calculation.`;
  }

  const changedRecords = (records?.rows || []).filter(r => r.delta != null && !isZeroDecimal(r.delta));
  let recordsSentence: string | null = null;
  if (defined && records) {
    const n = records.changedCount ?? changedRecords.length;
    const noun = n === 1 ? 'record accounts' : 'records account';
    if (isZeroDecimal(delta)) {
      recordsSentence = n === 0 ? 'No record changes its contribution.'
        : `${n} ${n === 1 ? 'record changes' : 'records change'} contribution, but the changes cancel out.`;
    } else if (records.complete || records.changedCount != null) {
      recordsSentence = `${groupDigits(String(n))} ${noun} for the difference.`;
    } else {
      recordsSentence = `At least ${n} of the first ${records.rows.length} of ${records.total} records contribute to the difference.`;
    }
  }

  if (defined && result.residual != null && !isZeroDecimal(result.residual)) {
    uncertainties.push(`${withUnit(result.residual, unit)} of the change is not explained by individual records (unexplained difference).`);
  }
  if (defined && !isZeroDecimal(delta)) {
    uncertainties.push('The records show where the totals differ; they do not show which calculation is correct.');
    if (direction === 'down') uncertainties.push('A lower total is a measurement difference, not automatically a financial loss.');
  }
  for (const raw of result.limitations || []) {
    const limitation = plainLimitation(raw, unit);
    if (limitation && !uncertainties.includes(limitation)) uncertainties.push(limitation);
  }

  return { defined, direction, magnitude: defined && delta ? absDecimal(delta) : null, headline, recordsSentence, changedRecords, uncertainties };
}
