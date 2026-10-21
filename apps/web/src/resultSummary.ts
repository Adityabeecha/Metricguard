// Plain-language reading of a comparison result. Works on exact decimal strings and
// never converts them to floating point. Keeps the mathematical distinctions:
// an undefined total is not zero, an explained difference is not automatically
// correct, and a negative change is not automatically a financial loss.

type UnitResult = {
  aggregate?: string;
  currency?: string | null;
  unit_label?: string | null;
  distinct?: { entity_column: string } | null;
  ratio?: { unit: string; numerator?: { unit_label?: string }; denominator?: { unit_label?: string } } | null;
};

/** Unit of a result/change, never the dataset amount unit for counts or shares. */
export function resultUnit(result: UnitResult | null | undefined, declaredUnit = ''): string {
  const amount = result?.unit_label || declaredUnit || result?.currency || '';
  if (result?.aggregate === 'count') return 'records';
  if (result?.aggregate === 'count_distinct') return `distinct ${result.distinct?.entity_column || 'values'}`;
  if (result?.aggregate === 'ratio') {
    const ratio = result.ratio;
    if (ratio?.unit === 'share') return 'pts';
    if (ratio?.unit === 'amount_per_record') return `${ratio.numerator?.unit_label || amount} per record`.trim();
    if (ratio?.unit === 'records_per_amount') return `records per ${ratio.denominator?.unit_label || amount}`.trim();
    if (ratio?.unit === 'amount_per_amount') return `${ratio.numerator?.unit_label || amount} per ${ratio.denominator?.unit_label || amount}`.trim();
    return '';
  }
  return amount;
}

/** Match percentage-point units without losing precision through Number. */
export function resultDisplayValue(result: UnitResult | null | undefined, value: string | null | undefined): string | null {
  return value == null ? null : result?.aggregate === 'ratio' && result.ratio?.unit === 'share' ? percentOf(value) : value;
}

export type SummaryInput = {
  baseline_value: string | null;
  target_value: string | null;
  delta_target_minus_baseline: string | null;
  residual: string | null;
  limitations?: string[];
  explanation_status?: string;
  aggregate?: 'count' | 'count_distinct' | 'ratio';
  ratio?: { baseline: { undefined_reason: string | null }; target: { undefined_reason: string | null }; unit: string; display_places: number; gap_exact?: string | null } | null;
  distinct?: { entity_column: string; gained: number; lost: number; kept: number } | null;
};

/**
 * Move the decimal point two places right without floating point:
 * "0.400000" → "40.0000", "-0.0125" → "-1.25", "3" → "300".
 */
export function percentOf(value: string): string {
  const match = /^([-+]?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;
  const [, sign, whole, fraction = ''] = match;
  const digits = (whole + fraction.padEnd(2, '0'));
  const point = whole.length + 2;
  const intPart = digits.slice(0, point).replace(/^0+(?=\d)/, '');
  const fracPart = digits.slice(point);
  return `${sign}${intPart}${fracPart ? `.${fracPart}` : ''}`;
}

const UNDEFINED_REASON: Record<string, string> = {
  null_numerator: 'its top part has no values to add up',
  null_denominator: 'its bottom part has no values to add up',
  zero_denominator: 'its bottom part is 0, and dividing by 0 is undefined',
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

/** A value followed by its unit, as on the result page: "-1 distinct customer_id", "40%", "Unavailable". */
export function valueWithUnit(value: string | null | undefined, unit: string): string {
  if (value == null) return 'Unavailable';
  return unit === '%' ? `${groupDigits(value)}%` : withUnit(value, unit);
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
  records?: { rows: RecordDelta[]; total: number; complete: boolean; changedCount?: number;
    /** Records that differ between the two sides (changed-only listings and two-load comparisons). */
    differing?: number },
  /** 'loads': the same calculation on an earlier and a later load of one dataset. */
  compare: 'calculations' | 'loads' | 'sources' = 'calculations',
): ResultSummary {
  const loads = compare === 'loads' || compare === 'sources';
  const current = compare === 'sources' ? 'source A' : loads ? 'earlier load' : 'current calculation';
  const proposed = compare === 'sources' ? 'source B' : loads ? 'later load' : 'proposed calculation';
  // "Source A" is a name and takes no article; the others read "the later load", "the current calculation".
  const the = (noun: string, start = false) => compare === 'sources' ? (start ? noun[0].toUpperCase() + noun.slice(1) : noun) : `${start ? 'The' : 'the'} ${noun}`;
  const uncertainties: string[] = [];
  const delta = result.delta_target_minus_baseline;
  const defined = result.baseline_value != null && result.target_value != null && delta != null;

  let direction: ResultSummary['direction'] = 'undefined';
  let headline: string;
  const isRatio = result.aggregate === 'ratio';
  const exactGap = isRatio ? result.ratio?.gap_exact : null;
  const sameValue = exactGap != null ? /^[-+]?0(?:\/[1-9]\d*)?$/.test(exactGap) : isZeroDecimal(delta);
  const isDistinct = result.aggregate === 'count_distinct';
  const noun = isRatio ? 'value' : isDistinct ? 'count' : 'total';
  if (!defined && isRatio && result.ratio) {
    const sides = (['baseline', 'target'] as const).filter(s => result.ratio![s].undefined_reason);
    headline = 'The change cannot be calculated: ' + sides.map(s =>
      `the ${s === 'baseline' ? (loads ? 'earlier load’s' : 'current') : (loads ? 'later load’s' : 'proposed')} ratio is undefined because ${UNDEFINED_REASON[result.ratio![s].undefined_reason!] || 'it has no value'}`).join(', and ') + '.';
    uncertainties.push('An undefined ratio is not 0. The changes in its top and bottom parts are still shown exactly below.');
  } else if (!defined) {
    headline = result.baseline_value == null && result.target_value == null
      ? `The change cannot be calculated: neither ${loads ? 'load' : 'calculation'} has any values to add up, so both totals are undefined (not zero).`
      : `The change cannot be calculated: the ${result.baseline_value == null ? current : proposed} has no values to add up, so its total is undefined (not zero).`;
    uncertainties.push('An undefined total means there were no non-blank amounts to add; it is not the same as a total of 0.');
  } else if (sameValue) {
    direction = 'same';
    headline = loads ? `Both loads give the same ${noun}${isRatio ? ' (exactly)' : ''}.`
      : `Both calculations give the same ${noun} on this dataset${isRatio ? ' (exactly)' : ''}.`;
    uncertainties.push(`Equal ${noun}s on this frozen dataset do not prove the two calculations are equivalent on other data.`);
  } else {
    direction = (exactGap ?? delta!).startsWith('-') ? 'down' : 'up';
    const amount = isRatio && result.ratio?.unit === 'share' ? `${percentOf(absDecimal(delta!))} percentage points` : withUnit(absDecimal(delta!), unit);
    headline = isRatio && isZeroDecimal(delta)
      ? `${the(proposed, true)} is ${direction === 'down' ? 'lower' : 'higher'} by less than the displayed precision. Exact change: ${exactGap}${result.ratio?.unit === 'share' ? ' (multiply by 100 for percentage points)' : ''}.`
      : `${the(proposed, true)} is ${amount} ${direction === 'down' ? 'lower' : 'higher'} than ${the(current)}.`;
  }

  const changedRecords = (records?.rows || []).filter(r => r.delta != null && !isZeroDecimal(r.delta));
  let recordsSentence: string | null = null;
  const item = isDistinct ? `${result.distinct?.entity_column || 'distinct'} value` : 'record';
  if (defined && records) {
    const n = records.changedCount ?? changedRecords.length;
    const noun = n === 1 ? `${item} accounts` : `${item}s account`;
    if (isDistinct && result.distinct && (result.distinct.gained || result.distinct.lost)) {
      recordsSentence = `${groupDigits(String(result.distinct.gained))} ${item}${result.distinct.gained === 1 ? '' : 's'} counted only in the proposed calculation, ` +
        `${groupDigits(String(result.distinct.lost))} only in the current one.`;
    } else if (sameValue) {
      recordsSentence = n === 0 ? `No ${item} changes its contribution.`
        : `${n} ${n === 1 ? `${item} changes` : `${item}s change`} contribution, but the changes cancel out.`;
    } else if (records.complete || records.changedCount != null) {
      recordsSentence = `${groupDigits(String(n))} ${noun} for the difference.`;
      const quiet = (records.differing ?? 0) - n;
      if (quiet > 0) {
        recordsSentence += loads
          ? ` Another ${groupDigits(String(quiet))} ${quiet === 1 ? 'record differs' : 'records differ'} between the loads without changing the total (outside the calculation, or a blank or zero amount).`
          : ` Another ${groupDigits(String(quiet))} ${quiet === 1 ? 'record changes' : 'records change'} inclusion without changing the total (a blank or zero amount).`;
      }
    } else {
      recordsSentence = `At least ${n} of the first ${records.rows.length} of ${records.total} records contribute to the difference.`;
    }
  }

  if (defined && result.residual != null && !isZeroDecimal(result.residual)) {
    uncertainties.push(`${withUnit(result.residual, unit)} of the change is not explained by individual records (unexplained difference).`);
  }
  if (defined && !sameValue) {
    uncertainties.push('The records show where the totals differ; they do not show which calculation is correct.');
    if (direction === 'down') uncertainties.push('A lower total is a measurement difference, not automatically a financial loss.');
  }
  for (const raw of result.limitations || []) {
    const limitation = plainLimitation(raw, unit);
    if (limitation && !uncertainties.includes(limitation)) uncertainties.push(limitation);
  }

  return { defined, direction, magnitude: defined && delta ? absDecimal(delta) : null, headline, recordsSentence, changedRecords, uncertainties };
}

/**
 * An exact fraction "p/q" (or integer "p") as a decimal rounded half-up at `places`,
 * computed with BigInt so no floating point is involved.
 */
export function fractionDisplay(text: string, places: number): string {
  const match = /^(-?)(\d+)(?:\/(\d+))?$/.exec(text);
  if (!match) return text;
  const negative = match[1] === '-';
  const numerator = BigInt(match[2]);
  const denominator = BigInt(match[3] || '1');
  const scale = 10n ** BigInt(places);
  const scaled = (numerator * scale * 2n + denominator) / (denominator * 2n);
  const whole = (scaled / scale).toString();
  const fraction = places ? '.' + (scaled % scale).toString().padStart(places, '0') : '';
  return `${negative && scaled !== 0n ? '-' : ''}${whole}${fraction}`;
}
