type Fraction = {n:bigint;d:bigint};
const gcd = (a:bigint,b:bigint):bigint => b ? gcd(b,a%b) : a;
function fraction(value: unknown): Fraction | null {
  if (typeof value !== 'string') return null;
  const ratio = /^[+-]?(\d+)\/(\d+)$/.exec(value);
  if (ratio) return BigInt(ratio[2]) ? {n:BigInt(ratio[1]),d:BigInt(ratio[2])} : null;
  const decimal = /^[+-]?(\d+)(?:\.(\d+))?$/.exec(value);
  return decimal ? {n:BigInt(decimal[1]+(decimal[2]||'')),d:10n**BigInt(decimal[2]?.length||0)} : null;
}
function sum(values:Fraction[]):Fraction {
  return values.reduce((a,b) => {
    const n=a.n*b.d+b.n*a.d,d=a.d*b.d,g=gcd(n,d);
    return {n:n/g,d:d/g};
  },{n:0n,d:1n});
}
const greater=(a:Fraction,b:Fraction) => a.n*b.d>b.n*a.d;
function percent(part:Fraction,total:Fraction):string {
  const n=part.n*total.d*1000n,d=part.d*total.n;
  const tenth=(2n*n+d)/(2n*d), whole=tenth/10n, tail=tenth%10n;
  return `${whole}${tail ? `.${tail}` : ''}`;
}

/** Shares describe absolute record effects; offsetting changes never claim >100% of a net gap. */
export function recordConcentrationLine(summary: {changed:number;top_changes:Array<{delta?:string|null;effect?:string|null;effect_exact?:string|null}>;abs_change_total?:string|null}|null,
  result:{aggregate?:string;delta_target_minus_baseline?:string|null;ratio?:{gap_exact?:string|null}}):string {
  if (!summary?.changed || !summary.top_changes.length) return '';
  const isRatio=result.aggregate==='ratio';
  const rows=summary.top_changes.flatMap(row => {
    const value=fraction(isRatio ? row.effect_exact : row.delta);
    return value?.n ? [value] : [];
  });
  if (!rows.length) return '';
  const net=fraction(isRatio ? result.ratio?.gap_exact : result.delta_target_minus_baseline);
  if (!net) return '';
  const ten=rows.slice(0,10), sumTen=sum(ten);
  const offsets=!net.n || greater(sumTen,net);
  const absolute=!isRatio ? fraction(summary.abs_change_total) : null;
  const denominator=offsets ? absolute : net;
  if (!denominator?.n || greater(sumTen,denominator)) return `The largest ${ten.length} records include offsetting changes; their absolute effects exceed the net difference.`;
  const five=rows.slice(0,5), sumFive=sum(five);
  const few=sumFive.n*denominator.d*100n>=80n*denominator.n*sumFive.d;
  const selected=few ? five : ten, amount=few ? sumFive : sumTen;
  const share=percent(amount,denominator);
  if (offsets) return `Top ${selected.length} records explain ${share}% of absolute record changes — some changes offset each other.`;
  const dispersed=amount.n*denominator.d*100n<=20n*denominator.n*amount.d;
  return `Top ${selected.length} records explain ${share}% of the difference — ${few ? 'a few large records' : dispersed ? 'spread across many records' : 'shared across records'}.`;
}
