import {useEffect, useState} from 'react';
import {api, ApiError, type JoinEvidencePage, type JoinEvidenceSummary, type RelationalPredicate,
  type RelationalRowOutcome, type RelationalSideExplanation, type RelationalRun, type Version} from '../api';
import './relationalEvidence.css';

const sides = ['baseline', 'target'] as const;
const outcomeLabels:Record<RelationalRowOutcome,string> = {
  included_value: 'Included non-NULL measure',
  included_null_measure: 'Included NULL measure · ignored by SUM',
  excluded_join: 'Excluded by INNER JOIN',
  excluded_filter_false: 'Excluded · WHERE is FALSE',
  excluded_filter_unknown: 'Excluded · WHERE is UNKNOWN',
};
const membershipLabels = {matched:'Dimension matched', null_foreign_key:'NULL foreign key', missing_dimension:'No dimension record'};
const value = (v:string|null) => v === null ? 'NULL' : JSON.stringify(v);
const title = (side:string) => side === 'baseline' ? 'Baseline' : 'Target';

function predicateLabel(p:RelationalPredicate, contract:JoinEvidenceSummary['contract']) {
  const field = `${contract[p.table].table}.${p.column}`;
  if (p.op === 'is_null') return `${field} IS NULL`;
  if (p.op === 'is_not_null') return `${field} IS NOT NULL`;
  return `${field} ${p.op === 'eq' ? '=' : 'IN'} ${p.values.map(v=>value(v)).join(', ')}`;
}

function RowReason({side, detail}:{side:'baseline'|'target';detail:RelationalSideExplanation}) {
  return <section className="join-row-reason">
    <h4>{title(side)} · {detail.join_kind.toUpperCase()} JOIN</h4>
    <p>{outcomeLabels[detail.outcome]}</p>
    {detail.outcome === 'excluded_join'
      ? <p className="v2-help">The fact has no dimension match. INNER JOIN removes it before WHERE; filters were not evaluated.</p>
      : <>
        <p className="v2-help">WHERE: {detail.where_state.toUpperCase()}{detail.predicates.length === 0 && ' · no categorical filters'}.
          {detail.where_state === 'unknown' && ' A NULL comparison is UNKNOWN and does not pass WHERE.'}
          {detail.outcome === 'included_null_measure' && ' The fact passes the join and filters, but its NULL measure is ignored by SUM. Its zero bookkeeping contribution does not establish a zero source value.'}</p>
        {detail.predicates.length > 0 && <div className="table-scroll"><table><caption className="sr-only">{title(side)} categorical predicate observations</caption>
          <thead><tr><th>Field</th><th>Predicate</th><th>Observed value</th><th>State</th></tr></thead>
          <tbody>{detail.predicates.map((p,i)=><tr key={i}><td className="mono-cell">{p.field}</td>
            <td>{p.op === 'is_null' ? 'IS NULL' : p.op === 'is_not_null' ? 'IS NOT NULL' : `${p.op === 'eq' ? '=' : 'IN'} ${p.values.map(value).join(', ')}`}</td>
            <td className="mono-cell">{value(p.observed)}</td><td>{p.state.toUpperCase()}</td></tr>)}</tbody>
        </table></div>}
      </>}
  </section>;
}

/** Verified join evidence for a fact table with several lookup tables (explanation policy v2). */
type StarLink = {foreign_key:string|null;dimension_key:string|null;matched:boolean;membership:'matched'|'null_foreign_key'|'missing_dimension'};
type StarSummary = {schema_version:2;policy:'relational-join-evidence-v2';summary_hash:string;fact_rows:number;
  changed_inclusion_rows:number;nonzero_contribution_rows:number;null_semantics:string;limitations:string[];
  contract:{fact:{table:string;key:string;grain:string};measure:{column:string};unit_label:string;
    dimensions:Array<{role:string;table:string;key:string;grain:string;fact_foreign_key:string}>};
  join_proof:{fact_rows:number;left_join_rows:number;dimensions:Record<string,{dimension_rows:number;matched_fact_rows:number;null_foreign_key_rows:number;missing_nonnull_foreign_key_rows:number}>};
  definitions:Record<'baseline'|'target',{joins:Record<string,'inner'|'left'>;predicates:Array<{table:string;column:string;op:string;values:string[]}>}>;
  side_counts:Record<'baseline'|'target',Record<RelationalRowOutcome,number>>;
  reconciliation:{status:string;row_delta_sum:string;residual:string|null};
  pins:{execution_policy:string;contract_hash:string;evidence_sha256:string;source_hashes:Record<string,string>}};

function StarRowReasons({detail}:{detail:unknown}) {
  const row = detail as Record<'baseline'|'target',{outcome:RelationalRowOutcome;where_state:string;unmatched_inner_joins:string[];
    predicates:Array<{field:string;observed:string|null;state:string}>}>;
  return <div className="join-row-detail">{sides.map(side=><section key={side} className="join-row-reason">
    <h4>{title(side)}</h4><p>{outcomeLabels[row[side].outcome]}</p>
    {row[side].outcome === 'excluded_join'
      ? <p className="v2-help">No match in {row[side].unmatched_inner_joins.join(', ')}. The required join removes this record before filters are evaluated.</p>
      : <><p className="v2-help">WHERE: {row[side].where_state.toUpperCase()}. Blank comparisons are UNKNOWN and do not pass WHERE.</p>
        {row[side].predicates.length > 0 && <table><thead><tr><th>Field</th><th>Observed value</th><th>Filter state</th></tr></thead>
          <tbody>{row[side].predicates.map((p,i)=><tr key={i}><td>{p.field}</td><td>{value(p.observed)}</td><td>{p.state.toUpperCase()}</td></tr>)}</tbody></table>}</>}
  </section>)}</div>;
}

function StarEvidence({summary,items,tab,explanations}:{summary:StarSummary;items:Array<Record<string,unknown>>;tab:'change'|'proof'|'records'|'pins';explanations:Map<string,unknown>}) {
  const c = summary.contract;
  const joinText = (joins:Record<string,'inner'|'left'>) => Object.keys(joins).length === 0 ? 'No lookup joined'
    : Object.entries(joins).map(([role,kind])=>`${kind.toUpperCase()} JOIN ${role}`).join(' · ');
  return <>
    <p>{summary.fact_rows} facts checked. {summary.changed_inclusion_rows} changed inclusion; {summary.nonzero_contribution_rows} have a nonzero row difference.</p>
    {tab === 'change' && <div className="v2-grid two join-definitions">{sides.map(side=><section key={side}>
      <h4>{title(side)} · {joinText(summary.definitions[side].joins)}</h4>
      <p className="mono-cell">SUM({c.fact.table}.{c.measure.column})</p>
      {summary.definitions[side].predicates.length > 0
        ? <ul>{summary.definitions[side].predicates.map((p,i)=><li key={i} className="mono-cell">{p.table}.{p.column} {p.op === 'is_null' ? 'IS NULL' : p.op === 'is_not_null' ? 'IS NOT NULL' : `${p.op === 'eq' ? '=' : 'IN'} ${p.values.map(v=>value(v)).join(', ')}`}</li>)}</ul>
        : <p>No categorical filters.</p>}
    </section>)}</div>}
    {tab === 'proof' && <><div className="table-scroll"><table><caption>Lookup tables · proven on the frozen snapshot</caption>
      <thead><tr><th>Lookup</th><th>Join path</th><th className="join-number">Rows</th><th className="join-number">Matched facts</th><th className="join-number">NULL link</th><th className="join-number">Link not found</th></tr></thead>
      <tbody>{c.dimensions.map(d=>{const p = summary.join_proof.dimensions[d.role]; return <tr key={d.role}>
        <td>{d.role}</td><td className="mono-cell">{c.fact.table}.{d.fact_foreign_key} → {d.table}.{d.key}</td>
        <td className="join-number mono-cell">{p.dimension_rows}</td><td className="join-number mono-cell">{p.matched_fact_rows}</td>
        <td className="join-number mono-cell">{p.null_foreign_key_rows}</td><td className="join-number mono-cell">{p.missing_nonnull_foreign_key_rows}</td></tr>;})}</tbody>
    </table></div>
    <div className="table-scroll"><table><caption>All facts by outcome · counts are for the complete artifact</caption>
      <thead><tr><th>Outcome</th><th className="join-number">Baseline</th><th className="join-number">Target</th></tr></thead>
      <tbody>{(Object.keys(outcomeLabels) as RelationalRowOutcome[]).map(outcome=><tr key={outcome}><td>{outcomeLabels[outcome]}</td>
        <td className="join-number mono-cell">{summary.side_counts.baseline[outcome]}</td><td className="join-number mono-cell">{summary.side_counts.target[outcome]}</td></tr>)}</tbody>
    </table></div></>}
    {tab === 'change' && <div className={`v2-status ${summary.reconciliation.status === 'reconciled' ? 'good' : 'warn'}`}>
      {summary.reconciliation.status === 'reconciled'
        ? <>Row contributions across all {summary.fact_rows} facts sum to {summary.reconciliation.row_delta_sum} {c.unit_label}, matching target minus baseline. Residual: {summary.reconciliation.residual}.</>
        : <>At least one endpoint SUM is NULL. Gap and residual are not applicable.</>}
    </div>}
    {tab === 'records' && <div className="table-scroll"><table className="join-records"><caption>Supporting facts on this page</caption>
      <thead><tr><th>Fact key</th>{c.dimensions.map(d=><th key={d.role}>{d.role} link</th>)}<th>Measure</th><th>Target − baseline</th></tr></thead>
      <tbody>{items.map(row=>{const links = row.links as Record<string,StarLink>; return <tr key={String(row.fact_key)}>
        <td className="mono-cell">{String(row.fact_key)}
          {explanations.has(String(row.fact_key)) && <details><summary>Explain this fact</summary><StarRowReasons detail={explanations.get(String(row.fact_key))}/></details>}
        </td>
        {c.dimensions.map(d=><td key={d.role}>{membershipLabels[links[d.role].membership]}<br/><span className="mono-cell">{value(links[d.role].foreign_key)}</span></td>)}
        <td className="mono-cell">{row.measure_value == null ? 'NULL' : String(row.measure_value)}</td>
        <td className="mono-cell">{String(row.delta)}</td></tr>;})}</tbody>
    </table></div>}
    <p className="v2-help">{summary.null_semantics}</p>
    {tab === 'pins' && <><ul className="v2-help">{summary.limitations.map((limitation,i)=><li key={i}>{limitation}</li>)}</ul>
    <details><summary>All frozen hashes, context and reconciliation</summary><pre className="json-view">{JSON.stringify(summary,null,2)}</pre></details></>}
  </>;
}

export function RelationalEvidencePanel({run,versions}:{run:RelationalRun;versions:Version[]}) {
  const [tab,setTab] = useState<'change'|'proof'|'records'|'pins'>('change');
  const [offset,setOffset] = useState(0);
  const [page,setPage] = useState<JoinEvidencePage|null>(null);
  const [loading,setLoading] = useState(true);
  const [error,setError] = useState('');
  const [retry,setRetry] = useState(0);
  useEffect(()=>{
    let stopped = false;
    setLoading(true);setError('');setPage(null);
    void api.relationalJoinEvidence(run.id,offset).then(result=>{
      if (stopped) return;
      // Never display a page bound to another run or another input revision.
      if (result.run_id !== run.id || result.snapshot_id !== run.snapshot_id ||
          result.baseline_version_id !== run.baseline_version_id || result.target_version_id !== run.target_version_id ||
          result.offset !== offset || result.join_evidence.summary.pins.contract_hash !== run.context.contract_hash ||
          !((result.join_evidence.summary.policy === 'relational-join-evidence-v1' && result.join_evidence.summary.schema_version === 1) ||
            ((result.join_evidence.summary as unknown as StarSummary).policy === 'relational-join-evidence-v2' &&
             (result.join_evidence.summary as unknown as StarSummary).schema_version === 2))) {
        throw new Error('Join evidence does not match this comparison. Reopen the saved run before continuing.');
      }
      setPage(result);
    }).catch(e=>{
      if (!stopped) setError(e instanceof ApiError ? `${e.code}: ${e.message}` : e instanceof Error ? e.message : String(e));
    }).finally(()=>{if (!stopped) setLoading(false);});
    return ()=>{stopped = true;};
  },[run.id,run.snapshot_id,run.baseline_version_id,run.target_version_id,run.context.contract_hash,offset,retry]);
  const summary = page?.join_evidence.summary;
  const proof = summary?.join_proof;
  const reconciliation = summary?.reconciliation;
  const definitions = summary?.definitions;
  const c = summary?.contract;
  const explanations = new Map(page?.join_evidence.rows.map(row=>[row.fact_key,row]));

  return <div className="join-evidence" aria-busy={loading}>
    <div className="join-evidence-heading"><h3>Join evidence</h3><span className="v2-help">{summary ? 'Complete artifact checked · supporting records paged' : 'Supporting records appear after the integrity check'}</span></div>
    <nav className="join-evidence-tabs" aria-label="Join evidence views">
      {([{id:'change',label:'What changed'},{id:'proof',label:'Join proof'},{id:'records',label:'Supporting records'},{id:'pins',label:'Provenance'}] as const).map(item=><button
        key={item.id} type="button" className={`section-tab-button ${tab === item.id ? 'active' : ''}`} aria-pressed={tab === item.id} onClick={()=>setTab(item.id)}>{item.label}</button>)}
    </nav>
    {loading && <p role="status">Checking the saved artifact and every fact’s contribution…</p>}
    {error && <div className="v2-status warn" role="alert">{error}<div className="join-evidence-actions">
      <button type="button" className="button secondary small" onClick={()=>setRetry(v=>v+1)}>Retry evidence check</button>
      {offset > 0 && <button type="button" className="button secondary small" onClick={()=>setOffset(0)}>Return to first page</button>}
    </div></div>}
    {page && summary && (summary as unknown as StarSummary).schema_version === 2 && <>
      <StarEvidence summary={summary as unknown as StarSummary} items={page.items as unknown as Array<Record<string,unknown>>} tab={tab} explanations={explanations}/>
      {tab === 'records' && <div className="pagination"><span>{page.items.length ? offset+1 : 0}–{offset+page.items.length} of {page.total} facts</span><div>
        <button type="button" className="button secondary small" disabled={loading || offset === 0} onClick={()=>setOffset(v=>Math.max(0,v-100))}>Previous</button>
        <button type="button" className="button secondary small" disabled={loading || offset+100 >= page.total} onClick={()=>setOffset(v=>v+100)}>Next</button>
      </div></div>}
    </>}
    {page && summary && summary.schema_version === 1 && c && proof && reconciliation && definitions && <>
      {tab === 'change' && <>
        <p>{summary.fact_rows} facts checked. {summary.changed_inclusion_rows} changed inclusion; {summary.nonzero_contribution_rows} have a nonzero row difference.</p>
        <div className="v2-grid two join-definitions">{sides.map(side=><section key={side}>
          <h4>{title(side)} · {definitions[side].join.toUpperCase()} JOIN</h4>
          <p className="mono-cell">SUM({c.fact.table}.{c.measure.column})</p>
          <p className="v2-help">{definitions[side].join === 'left' ? 'Unmatched facts survive the join. WHERE can still exclude them.' : 'Only facts with a dimension match survive the join.'}</p>
          {definitions[side].predicates.length > 0 ? <ul>{definitions[side].predicates.map((p,i)=><li key={i} className="mono-cell">{predicateLabel(p,c)}</li>)}</ul> : <p>No categorical filters.</p>}
          {versions.find(v=>v.id === (side === 'baseline' ? run.baseline_version_id : run.target_version_id)) && <details><summary>Original SQL and version</summary>
            <p className="mono-cell">{side === 'baseline' ? run.baseline_version_id : run.target_version_id}</p>
            <pre className="json-view">{versions.find(v=>v.id === (side === 'baseline' ? run.baseline_version_id : run.target_version_id))!.raw_sql}</pre>
          </details>}
        </section>)}</div>
        <div className="table-scroll"><table><caption>All facts by outcome · counts are for the complete artifact</caption>
          <thead><tr><th>Outcome</th><th className="join-number">Baseline</th><th className="join-number">Target</th></tr></thead>
          <tbody>{(Object.keys(outcomeLabels) as RelationalRowOutcome[]).map(outcome=><tr key={outcome}><td>{outcomeLabels[outcome]}</td>
            <td className="join-number mono-cell">{summary.side_counts.baseline[outcome]}</td><td className="join-number mono-cell">{summary.side_counts.target[outcome]}</td></tr>)}</tbody>
        </table></div>
        <div className={`v2-status ${reconciliation.status === 'reconciled' ? 'good' : 'warn'}`}>
          {reconciliation.status === 'reconciled' ? <>
            Row contributions across all {summary.fact_rows} facts sum to {reconciliation.row_delta_sum} {c.unit_label}, matching target minus baseline. Residual: {reconciliation.residual}.
          </> : <>At least one endpoint SUM is NULL. Gap and residual are not applicable. Row bookkeeping totals do not establish a zero endpoint.</>}
        </div>
        <p className="v2-help">One combined join and filter change explains the difference. Individual predicates are not assigned causal shares. Measure meaning: {c.measure_meaning}. Unit: {c.unit_label} (declared).</p>
      </>}
      {tab === 'proof' && <>
        <p className="mono-cell">{c.fact.table}.{c.relationship.fact_foreign_key} → {c.dimension.table}.{c.dimension.key}</p>
        <div className="table-scroll"><table><caption>Structural evidence for the frozen snapshot</caption><thead><tr><th>Check</th><th>Observed</th></tr></thead><tbody>
          <tr><td>Fact key · {c.fact.key}</td><td>{proof.fact_key_unique ? 'Unique and non-NULL' : 'Not proven'}</td></tr>
          <tr><td>Dimension key · {c.dimension.key}</td><td>{proof.dimension_key_unique ? 'Unique and non-NULL' : 'Not proven'}</td></tr>
          <tr><td>Fact rows / rows after LEFT JOIN</td><td className="mono-cell">{proof.fact_rows} / {proof.left_join_rows}</td></tr>
          <tr><td>Dimension rows</td><td className="mono-cell">{proof.dimension_rows}</td></tr>
          <tr><td>Maximum dimension matches per fact / multiplied facts</td><td className="mono-cell">{proof.max_dimension_matches_per_fact} / {proof.fanout_rows}</td></tr>
          <tr><td>Facts with a dimension match</td><td className="mono-cell">{proof.matched_fact_rows}</td></tr>
          <tr><td>Facts with a NULL foreign key</td><td className="mono-cell">{proof.null_foreign_key_rows}</td></tr>
          <tr><td>Facts with a non-NULL key missing from the dimension</td><td className="mono-cell">{proof.missing_nonnull_foreign_key_rows}</td></tr>
        </tbody></table></div>
        <p>Declared fact grain: {c.fact.grain}. Declared dimension grain: {c.dimension.grain}.</p>
        <p className="v2-help">Uniqueness and join multiplicity are proven within this snapshot. The business meaning of a row and the authenticity of the source remain declarations. Missing matches establish neither a zero measure nor a valid relationship.</p>
        <p className="v2-help">{summary.null_semantics}</p>
      </>}
      {tab === 'records' && <>
        <p className="v2-help">Every fact appears once, including facts excluded by both definitions. Open a row to inspect the evaluated filter inputs.</p>
        <div className="table-scroll"><table className="join-records"><caption className="sr-only">Paged supporting facts and exact contributions</caption>
          <thead><tr><th>Fact key</th><th>Dimension match</th><th>Measure</th><th>Baseline contribution</th><th>Target contribution</th><th>Target − baseline</th></tr></thead>
          <tbody>{page.items.map(row=>{
            const explanation = explanations.get(row.fact_key);
            return <tr key={row.fact_key}><td><span className="mono-cell">{row.fact_key}</span>
              {explanation && <details><summary>Explain this fact</summary><div className="join-row-detail">{sides.map(side=><RowReason key={side} side={side} detail={explanation[side]}/>)}
                <section className="join-row-reason"><h4>Registered supporting fields</h4>
                  {row.dimension_matched ? <p className="v2-help">Dimension values come from the matching dimension row.</p>
                    : <p className="v2-help">There is no matching dimension row. Its NULL fields below are the LEFT JOIN representation of that missing match.</p>}
                  <div className="table-scroll"><table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>
                    {(['fact','dimension'] as const).flatMap(role=>Object.entries(row[`${role}_values`]).map(([field,observed])=><tr key={`${role}.${field}`}>
                      <td className="mono-cell">{c[role].table}.{field}</td><td className="mono-cell">{value(observed)}</td></tr>))}
                  </tbody></table></div>
                </section>
              </div></details>}
            </td><td>{membershipLabels[row.join_membership]}<br/><span className="mono-cell">FK: {value(row.foreign_key)}<br/>Dimension key: {value(row.dimension_key)}</span></td>
              <td className="mono-cell">{row.measure_value ?? 'NULL'}</td>
              {sides.map(side=><td key={side}><span className="mono-cell">{row[`${side}_contribution`]}</span><br/>
                <span className="v2-help">{explanation ? outcomeLabels[explanation[side].outcome] : 'Explanation unavailable'}</span></td>)}
              <td className="mono-cell">{row.delta}</td></tr>;
          })}</tbody>
        </table></div>
        <div className="pagination"><span>{page.items.length ? offset+1 : 0}–{offset+page.items.length} of {page.total} facts</span><div>
          <button type="button" className="button secondary small" disabled={loading || offset === 0} onClick={()=>setOffset(v=>Math.max(0,v-100))}>Previous</button>
          <button type="button" className="button secondary small" disabled={loading || offset+100 >= page.total} onClick={()=>setOffset(v=>v+100)}>Next</button>
        </div></div>
      </>}
      {tab === 'pins' && <>
        <p className="v2-help">This view is derived from frozen definitions and the verified artifact. Opening it does not execute the SQL again.</p>
        <dl className="join-pins"><dt>Run / snapshot</dt><dd className="mono-cell">{run.id}<br/>{run.snapshot_id}</dd>
          <dt>Baseline / target versions</dt><dd className="mono-cell">{run.baseline_version_id}<br/>{run.target_version_id}</dd>
          <dt>Execution policy / explanation policy</dt><dd className="mono-cell">{summary.pins.execution_policy}<br/>{summary.policy}</dd>
          <dt>Contract SHA-256</dt><dd className="mono-cell">{summary.pins.contract_hash}</dd>
          <dt>Fact source SHA-256</dt><dd className="mono-cell">{summary.pins.source_hashes.fact}</dd>
          <dt>Dimension source SHA-256</dt><dd className="mono-cell">{summary.pins.source_hashes.dimension}</dd>
          <dt>Evidence SHA-256</dt><dd className="mono-cell">{summary.pins.evidence_sha256}</dd>
          <dt>Summary SHA-256</dt><dd className="mono-cell">{summary.summary_hash}</dd>
        </dl>
        <details><summary>All frozen hashes, context and reconciliation</summary><pre className="json-view">{JSON.stringify(summary,null,2)}</pre></details>
        <ul className="v2-help">{summary.limitations.map((limitation,i)=><li key={i}>{limitation}</li>)}</ul>
      </>}
    </>}
  </div>;
}
