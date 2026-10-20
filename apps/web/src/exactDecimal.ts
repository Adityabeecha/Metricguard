/** Exact arithmetic on decimal strings ("-200.00", "75.5"), so the browser never rounds evidence totals. */

function parts(value: string): { digits: bigint; scale: number } {
  const text = value.trim();
  if (!/^[+-]?\d+(\.\d+)?$/.test(text)) throw new Error(`Not a decimal: ${value}`);
  const negative = text.startsWith('-');
  const [whole, fraction = ''] = text.replace(/^[+-]/, '').split('.');
  const digits = BigInt(whole + fraction) * (negative ? -1n : 1n);
  return { digits, scale: fraction.length };
}

function format(digits: bigint, scale: number): string {
  const negative = digits < 0n;
  let text = (negative ? -digits : digits).toString();
  if (scale > 0) {
    text = text.padStart(scale + 1, '0');
    text = `${text.slice(0, -scale)}.${text.slice(-scale)}`;
  }
  return negative && /[1-9]/.test(text) ? `-${text}` : text;
}

/** Exact sum, keeping the largest number of decimal places among the inputs. */
export function decimalSum(values: Array<string | null | undefined>): string {
  const items = values.filter((v): v is string => v != null && v !== '').map(parts);
  const scale = items.reduce((max, item) => Math.max(max, item.scale), 0);
  const total = items.reduce((sum, item) => sum + item.digits * 10n ** BigInt(scale - item.scale), 0n);
  return format(total, scale);
}

/** Running totals down a list (exact). */
export function runningTotals(values: string[]): string[] {
  const out: string[] = [];
  values.forEach((_, i) => out.push(decimalSum(values.slice(0, i + 1))));
  return out;
}

/** True when two decimal strings are the same number ("340.00" equals "340.0"). */
export function decimalEquals(a: string, b: string): boolean {
  return decimalSum([a, b.startsWith('-') ? b.slice(1) : `-${b}`]).replace(/[0.]/g, '').replace('-', '') === '';
}
