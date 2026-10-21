export type SqlDiffPart = {kind:'same'|'added'|'removed';text:string};
const tokens=(sql:string) => sql.match(/--[^\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|\s+|[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?|<>|!=|<=|>=|\|\||[\s\S]/g)||[];

/** Bounded token LCS, with exact prefix/suffix fallback for long SQL. Both inputs reconstruct verbatim. */
export function sqlTokenDiff(before:string, after:string):SqlDiffPart[] {
  const a=tokens(before),b=tokens(after),out:SqlDiffPart[]=[];
  const push=(kind:SqlDiffPart['kind'],text:string) => {
    if (!text) return;
    const last=out.at(-1);
    if (last?.kind===kind) last.text+=text; else out.push({kind,text});
  };
  let start=0, endA=a.length,endB=b.length;
  while(start<endA && start<endB && a[start]===b[start]) {push('same',a[start]);start++;}
  while(endA>start && endB>start && a[endA-1]===b[endB-1]) {endA--;endB--;}
  const n=endA-start,m=endB-start;
  if (n*m>40_000) {
    push('removed',a.slice(start,endA).join(''));push('added',b.slice(start,endB).join(''));
  } else {
    const dp=Array.from({length:n+1},()=>new Uint16Array(m+1));
    for(let i=n-1;i>=0;i--) for(let j=m-1;j>=0;j--)
      dp[i][j]=a[start+i]===b[start+j] ? 1+dp[i+1][j+1] : Math.max(dp[i+1][j],dp[i][j+1]);
    let i=0,j=0;
    while(i<n || j<m) {
      if(i<n && j<m && a[start+i]===b[start+j]) {push('same',a[start+i]);i++;j++;}
      else if(i<n && (j===m || dp[i+1][j]>=dp[i][j+1])) {push('removed',a[start+i]);i++;}
      else {push('added',b[start+j]);j++;}
    }
  }
  push('same',a.slice(endA).join(''));
  return out;
}
