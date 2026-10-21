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
  const lo100=startsAtZero ? 0n : min*100n-span*25n,hi100=max*100n+span*8n;
  const ratio=(v:bigint) => Number(((v*100n-lo100)*1_000_000n)/(hi100-lo100 || 1n))/1_000_000;
  return {levels,steps:amounts.slice(1,-1),start:amounts[0],end:amounts.at(-1)!,startsAtZero,ratio};
}
