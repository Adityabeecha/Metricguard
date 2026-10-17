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

export function isZeroDecimal(value: string | null | undefined): boolean {
  return value != null && !/[1-9]/.test(value);
}

export function absDecimal(value: string): string {
  return value.replace(/^[-+]/, '');
}

function withUnit(value: string, unit: string): string {
  return unit ? `${value} ${unit}` : value;
}

export function summarizeResult(
  result: SummaryInput,
  unit: string,
  records?: { rows: RecordDelta[]; total: number; complete: boolean },
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
    const n = changedRecords.length;
    const noun = n === 1 ? 'record accounts' : 'records account';
    if (isZeroDecimal(delta)) {
      recordsSentence = n === 0 ? 'No record changes its contribution.'
        : `${n} ${n === 1 ? 'record changes' : 'records change'} contribution, but the changes cancel out.`;
    } else if (records.complete) {
      recordsSentence = `${n} ${noun} for the difference.`;
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
  for (const limitation of result.limitations || []) {
    if (!uncertainties.includes(limitation)) uncertainties.push(limitation);
  }

  return { defined, direction, magnitude: defined && delta ? absDecimal(delta) : null, headline, recordsSentence, changedRecords, uncertainties };
}
