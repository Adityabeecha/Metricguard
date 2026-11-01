/** Plot geometry from exact coefficients. Only a bounded pixel ratio becomes a Number. */
export function bridgeGeometry(start:string,steps:string[],end:string) {
  const texts=[start,...steps,end];
  const scale=Math.max(...texts.map(v=>v.split('.')[1]?.length||0));
  const coefficient=(text:string) => {
    if(!/^[+-]?\d+(?:\.\d+)?$/.test(text)) throw new Error('Invalid bridge value');
    const negative=text.startsWith('-'),[whole,fraction='']=text.replace(/^[+-]/,'').split('.');
    return BigInt(whole+fraction.padEnd(scale,'0'))*(negative ? -1n : 1n);
  };
  const amounts=texts.map(coefficient),levels=[amounts[0]];
  amounts.slice(1,-1).forEach(n=>levels.push(levels.at(-1)!+n));
  levels.push(amounts.at(-1)!);
  const min=levels.reduce((a,b)=>a<b?a:b),max=levels.reduce((a,b)=>a>b?a:b);
  const span=max-min || (max<0n ? -max : max) || 1n;
  const startsAtZero=min>=0n && min*4n<=span;
  // A shortened axis starts at a round number below the lowest level and says so. "Round" is relative to the
  // span (a power of ten no larger than it), so the steps stay readable however large the totals are.
  let step=1n;
  while(step*10n<=span) step*=10n;
  const below=min-span/4n;
  const rounded=(below>=0n ? below/step : -((-below+step-1n)/step))*step;
  const floor=startsAtZero ? 0n : rounded<min ? rounded : rounded-step;
  const decimal=(v:bigint) => {
    const digits=(v<0n ? -v : v).toString().padStart(scale+1,'0');
    const text=scale ? `${digits.slice(0,-scale)}.${digits.slice(-scale)}` : digits;
    return `${v<0n ? '-' : ''}${text.replace(/\.0+$/,'')}`;
  };
  const lo100=floor*100n,hi100=max*100n+span*8n;
  const ratio=(v:bigint) => Number(((v*100n-lo100)*1_000_000n)/(hi100-lo100 || 1n))/1_000_000;
  return {levels,steps:amounts.slice(1,-1),start:amounts[0],end:amounts.at(-1)!,startsAtZero,ratio,axisStart:decimal(floor)};
}
