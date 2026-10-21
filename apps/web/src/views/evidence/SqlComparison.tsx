import { sqlTokenDiff } from './sqlDiff';

export function SqlComparison({before,after,currentLabel='Current',proposedLabel='Proposed'}:{before:string;after:string;currentLabel?:string;proposedLabel?:string}) {
  const parts=sqlTokenDiff(before,after),changed=parts.some(p=>p.kind!=='same');
  return <section className="sql-comparison" aria-labelledby="sql-comparison-title">
    <h3 id="sql-comparison-title"><span className="exhibit">C</span> Calculations</h3>
    <div className="calc-pair sql-pair">
      <div><h4>{currentLabel} SQL</h4><pre aria-label={`${currentLabel} SQL`}><code>{before || 'No SQL was saved.'}</code></pre></div>
      <div><h4>{proposedLabel} SQL</h4><pre aria-label={`${proposedLabel} SQL`}><code>{after || 'No SQL was saved.'}</code></pre></div>
    </div>
    <h4>SQL changes</h4>
    {!changed ? <p>The calculation is the same on both sides.</p> : <>
      <p className="helper-muted">+ Added · − Removed</p>
      <pre className="sql-diff" aria-label="SQL changes"><code>{parts.map((part,i)=>part.kind==='same' ? <span key={i}>{part.text}</span>
        : part.kind==='added' ? <ins key={i} aria-label={`Added: ${part.text}`}><span aria-hidden="true">+ </span>{part.text}</ins>
        : <del key={i} aria-label={`Removed: ${part.text}`}><span aria-hidden="true">− </span>{part.text}</del>)}</code></pre>
    </>}
  </section>;
}
