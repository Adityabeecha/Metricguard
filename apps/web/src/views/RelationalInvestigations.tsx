import {useEffect, useRef, useState, type FormEvent} from 'react';
import {ArrowRight, Download, Play, RefreshCw, Save} from 'lucide-react';
import {api, ApiError, type DatasetPreviewRecord, type RelationalBootstrap, type RelationalCase,
  type RelationalContract, type RelationalRun, type RelationalSnapshot, type SnapshotImport} from '../api';
import {RelationalEvidencePanel} from './RelationalEvidencePanel';

const pending = (state?:string) => state === 'queued' || state === 'running';
const errorText = (e:unknown) => e instanceof ApiError ? `${e.code}: ${e.message}` : e instanceof Error ? e.message : String(e);
const amount = (v:string|null|undefined, unit:string, missing='NULL — no non-NULL terms') => v == null ? missing : `${v} ${unit}`;
type Role = 'fact' | 'dimension';
type TableForm = {table:string; key:string; grain:string; categories:string[]};
const emptyTable = (table:string):TableForm => ({table,key:'',grain:'',categories:[]});

// Completion-based polling avoids overlapping requests on a busy local machine.
function usePoll<T>(id:string|undefined, active:boolean, read:()=>Promise<T>, accept:(value:T)=>void, fail:(e:unknown)=>void) {
  const callbacks = useRef({read,accept,fail}); callbacks.current = {read,accept,fail};
  useEffect(() => {
    if (!id || !active) return;
    let stopped = false; let timer:ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {const value = await callbacks.current.read(); if (!stopped) callbacks.current.accept(value);}
      catch (e) {if (!stopped) callbacks.current.fail(e);}
      if (!stopped) timer = setTimeout(poll, 1500);
    };
    timer = setTimeout(poll, 1000);
    return () => {stopped = true; clearTimeout(timer);};
  }, [id,active]);
}

export function RelationalInvestigations({data,refresh,initialId}:{data:RelationalBootstrap;refresh:()=>Promise<RelationalBootstrap|null>;initialId?:string}) {
  const [files,setFiles] = useState<Record<Role,File|null>>({fact:null,dimension:null});
  const [previews,setPreviews] = useState<Record<Role,DatasetPreviewRecord|null>>({fact:null,dimension:null});
  const [tables,setTables] = useState<Record<Role,TableForm>>({fact:emptyTable('fact_records'),dimension:emptyTable('dim_records')});
  const [foreignKey,setForeignKey] = useState(''); const [measure,setMeasure] = useState('');
  const [precision,setPrecision] = useState(18); const [scale,setScale] = useState(2);
  const [name,setName] = useState(''); const [meaning,setMeaning] = useState(''); const [unit,setUnit] = useState('');
  const [nullPolicy,setNullPolicy] = useState<'blank_nullable'|'literal_NULL'>('blank_nullable');
  const [scope,setScope] = useState(false); const [comparable,setComparable] = useState(false);
  const [contractId,setContractId] = useState(''); const [snapshotName,setSnapshotName] = useState('');
  const [importJob,setImportJob] = useState<SnapshotImport|null>(null); const [snapshotId,setSnapshotId] = useState('');
  const [sql,setSql] = useState({baseline:'',target:''}); const [definitionIds,setDefinitionIds] = useState({baseline:'',target:''});
  const [run,setRun] = useState<RelationalRun|null>(null);
  const [title,setTitle] = useState(''); const [decision,setDecision] = useState(''); const [actor,setActor] = useState('');
  const [savedCase,setSavedCase] = useState<RelationalCase|null>(null);
  const [busy,setBusy] = useState(false); const [error,setError] = useState(''); const [notice,setNotice] = useState('');
  const contracts = data.relational_contracts || [];
  const contract = contracts.find(c => c.id === contractId);
  const snapshots = data.snapshots.filter(s => s.execution_policy === 'relational_snapshot_v1' && s.contract_hash === contract?.contract_hash) as RelationalSnapshot[];
  const snapshot = snapshots.find(s => s.id === snapshotId);
  const versions = data.definitions.flatMap(d => d.versions.filter(v => v.ir?.mode === 'relational_snapshot' && v.contract_hash === contract?.contract_hash).map(v => ({definition:d,version:v})));
  const cases = data.investigations.filter(c => (c.context as unknown as {mode?:string}).mode === 'relational_snapshot');
  const locked = busy || pending(importJob?.state);
  const headers = (role:Role) => previews[role]?.preview?.headers || [];
  const setFailure = (e:unknown) => setError(errorText(e));
  const resetResult = () => {setRun(null);setSavedCase(null);};

  function loadCase(detail:RelationalCase) {
    if (detail.context.mode !== 'relational_snapshot') throw new Error('This case uses another execution policy.');
    setContractId(contracts.find(c=>c.contract_hash===detail.snapshot.contract_hash)?.id || '');
    setSnapshotId(detail.snapshot_id);setDefinitionIds({baseline:detail.baseline_version_id,target:detail.target_version_id});
    setSql({baseline:detail.baseline_version.raw_sql,target:detail.target_version.raw_sql});
    resetResult();setSavedCase(detail);setRun(detail.run);setTitle(detail.title);setDecision(detail.decision);setActor(detail.actor_label);
  }
  useEffect(()=>{
    if (!initialId || initialId === 'new') return;
    let stopped = false;setBusy(true);
    void (async()=>{
      try {
        try {const detail=await api.relationalCase(initialId);if (!stopped) loadCase(detail);}
        catch(e) {
          if (!(e instanceof ApiError) || !['NOT_FOUND','HTTP_404'].includes(e.code)) throw e;
          const value=await api.relationalRun(initialId);
          if (value.context.mode!=='relational_snapshot') throw new Error('This run uses another execution policy.');
          if (!stopped) {
            setContractId(contracts.find(c=>c.contract_hash===value.context.contract_hash)?.id || '');
            setSnapshotId(value.snapshot_id);setDefinitionIds({baseline:value.baseline_version_id,target:value.target_version_id});
            const versions=data.definitions.flatMap(d=>d.versions);
            setSql({baseline:versions.find(v=>v.id===value.baseline_version_id)?.raw_sql || '',target:versions.find(v=>v.id===value.target_version_id)?.raw_sql || ''});
            resetResult();setRun(value);
          }
        }
      } catch(e) {if (!stopped) setFailure(e);} finally {if (!stopped) setBusy(false);}
    })();
    return ()=>{stopped=true;};
  },[initialId]);

  usePoll(previews.fact?.id,pending(previews.fact?.state),()=>api.datasetPreview(previews.fact!.id),v=>setPreviews(p=>({...p,fact:v})),setFailure);
  usePoll(previews.dimension?.id,pending(previews.dimension?.state),()=>api.datasetPreview(previews.dimension!.id),v=>setPreviews(p=>({...p,dimension:v})),setFailure);
  usePoll(importJob?.id,pending(importJob?.state),()=>api.snapshotImport(importJob!.id),v=>{
    setImportJob(v);
    if (!pending(v.state)) {
      if (v.state === 'validated' && v.snapshot_id) {setSnapshotId(v.snapshot_id); setNotice('Both tables validated; snapshot is ready.');}
      else setError(`${v.error?.code || v.state}: ${v.error?.message || 'Import did not complete.'}`);
      void refresh();
    }
  },setFailure);
  usePoll(run?.id,pending(run?.state),()=>api.relationalRun(run!.id),v=>{setRun(v);if (!pending(v.state)) void refresh();},setFailure);

  async function perform(work:()=>Promise<void>) {
    setBusy(true);setError('');setNotice('');
    try {await work();} catch(e) {setFailure(e);} finally {setBusy(false);}
  }
  function chooseFile(role:Role, file:File|null) {
    setFiles(p=>({...p,[role]:file}));setPreviews(p=>({...p,[role]:null}));
    setTables(p=>({...p,[role]:{...p[role],key:'',categories:[]}}));
    if (role === 'fact') {setForeignKey('');setMeasure('');}
  }
  function preview(role:Role) {void perform(async()=>{
    const file=files[role]; if (!file) return;
    const form=new FormData();form.append('file',file);
    const value=await api.previewDataset(form);setPreviews(p=>({...p,[role]:value}));
  });}
  function register(e:FormEvent) {e.preventDefault();void perform(async()=>{
    if (!scope || !comparable) throw new Error('Confirm whole-snapshot scope and comparable declared units.');
    const created=await api.post<RelationalContract>('/relational/contracts',{
      version:1,mode:'relational_snapshot',policy:'relational-snapshot-v1',name,fact:tables.fact,dimension:tables.dimension,
      relationship:{fact_foreign_key:foreignKey,dimension_key:tables.dimension.key,cardinality:'many_to_one',missing_dimension_policy:'preserve_and_report'},
      measure:{column:measure,precision,scale},measure_meaning:meaning,unit_label:unit,values_comparable:true,
      scope:'entire_supplied_snapshot',null_policy:nullPolicy});
    setContractId(created.id);setSnapshotId('');setDefinitionIds({baseline:'',target:''});resetResult();await refresh();
    setNotice('Relational contract registered. Existing SQL text is retained; register it against this contract before running.');
  });}
  function upload(e:FormEvent) {e.preventDefault();void perform(async()=>{
    if (!files.fact || !files.dimension || !contract) throw new Error('Choose both CSV files and a registered contract.');
    const form=new FormData();form.append('fact_file',files.fact);form.append('dimension_file',files.dimension);
    form.append('contract_id',contract.id);form.append('name',snapshotName);
    resetResult();setSnapshotId('');const value=await api.uploadRelational(form);
    if ('state' in value) setImportJob(value); else {setSnapshotId(value.id);setNotice('Reused the same validated source files and contract.');}
    await refresh();
  });}
  function define(e:FormEvent,side:'baseline'|'target') {e.preventDefault();void perform(async()=>{
    if (!contract) throw new Error('Choose the registered relational contract.');
    const d=await api.post<{versions:Array<{id:string}>}>('/relational/definitions',{name:`${side === 'baseline' ? 'Baseline' : 'Target'} · ${contract.name}`,raw_sql:sql[side],contract_id:contract.id});
    setDefinitionIds(p=>({...p,[side]:d.versions.at(-1)!.id}));resetResult();await refresh();setNotice(`${side} definition registered.`);
  });}
  function start(e:FormEvent) {e.preventDefault();void perform(async()=>{
    if (!contract || !snapshot || !definitionIds.baseline || !definitionIds.target) throw new Error('Choose the snapshot and register both definitions.');
    const started=await api.post<RelationalRun>('/relational/comparisons',{
      baseline_version_id:definitionIds.baseline,target_version_id:definitionIds.target,snapshot_id:snapshot.id,
      context:{mode:'relational_snapshot',scope:contract.scope,contract_hash:contract.contract_hash,unit_label:contract.unit_label,values_comparable:true},relationship:'candidate'
    },{'Idempotency-Key':crypto.randomUUID()});resetResult();setRun(started);await refresh();
  });}
  function open(id:string) {void perform(async()=>{
    const detail=await api.relationalCase(id);
    loadCase(detail);
  });}
  const runUnit=run?.context.unit_label || contract?.unit_label || '';
  const columnSelect=(role:Role,value:string,change:(v:string)=>void,label:string)=><label>{label}<select required value={value} onChange={e=>change(e.target.value)}><option value="">Choose column</option>{headers(role).map(n=><option key={n}>{n}</option>)}</select></label>;
  const starter=contract ? `SELECT SUM(f.${contract.measure.column}) FROM ${contract.fact.table} f LEFT JOIN ${contract.dimension.table} d ON f.${contract.relationship.fact_foreign_key} = d.${contract.dimension.key}` : '';

  return <div className="v2-page">
    <div className="page-heading"><div><div className="eyebrow">RELATIONAL INVESTIGATION</div><h1>Compare at the fact grain</h1><p>One fact table, one unique dimension, exact SUM. Inspect join membership alongside each contribution.</p></div></div>
    {data.mode !== 'isolated' && <div className="v2-status warn">Start the local application in isolated mode to import and compare these files.</div>}
    {data.mode === 'isolated' && !data.runner_available && <div className="v2-status warn">The reconciliation engine is unavailable. Queued work will resume when it starts.</div>}
    {error && <div className="v2-status warn" role="alert">{error}</div>}{notice && <div className="v2-status info" role="status">{notice}</div>}
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">01</span><div><h2>Inspect the two source files</h2><p>UTF-8 CSV only · 25 MiB combined · 100,000 rows per table. Extra fields are left out explicitly by the contract.</p></div></div>
      <div className="v2-step-body v2-grid two">{(['fact','dimension'] as Role[]).map(role=><fieldset key={role} disabled={locked} className="form-stack"><legend>{role === 'fact' ? 'Fact table' : 'Dimension table'}</legend>
        <label>Source CSV<input type="file" accept=".csv,text/csv" onChange={e=>chooseFile(role,e.target.files?.[0]||null)}/></label>
        <button type="button" className="button secondary" disabled={!files[role] || pending(previews[role]?.state) || data.mode !== 'isolated'} onClick={()=>preview(role)}>Preview {role} columns</button>
        {pending(previews[role]?.state) && <p role="status">Reading a bounded preview…</p>}
        {previews[role]?.error && <div className="v2-status warn">{previews[role]?.error?.message}</div>}
        {previews[role]?.preview && <><small className="mono-cell">Source SHA-256: {previews[role]?.preview?.source_hash}</small><div className="table-scroll"><table><thead><tr>{headers(role).map(n=><th key={n}>{n}</th>)}</tr></thead><tbody>{previews[role]?.preview?.sample_rows.map((r,i)=><tr key={i}>{headers(role).map(n=><td key={n}>{r[n] ?? 'NULL'}</td>)}</tr>)}</tbody></table></div></>}
      </fieldset>)}</div>
    </section>
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">02</span><div><h2>Declare the keys, relationship and measure</h2><p>Keys are exact strings. Unmatched foreign keys are retained and reported. No automatic deduplication or key conversion.</p></div></div>
      <div className="v2-step-body"><form onSubmit={register}><fieldset disabled={locked} className="form-stack"><label>Contract name<input required maxLength={160} value={name} onChange={e=>setName(e.target.value)}/></label>
        <div className="v2-grid two">{(['fact','dimension'] as Role[]).map(role=><div className="form-stack" key={role}>
          <h3>{role === 'fact' ? 'Fact' : 'Dimension'} roles</h3><label>SQL table name<input required pattern="[a-z_][a-z0-9_]{0,63}" value={tables[role].table} onChange={e=>setTables(p=>({...p,[role]:{...p[role],table:e.target.value}}))}/></label>
          {columnSelect(role,tables[role].key,v=>setTables(p=>({...p,[role]:{...p[role],key:v,categories:p[role].categories.filter(n=>n!==v)}})),'Unique non-NULL key')}
          <label>Declared row grain<input required maxLength={300} placeholder="What exactly one row represents" value={tables[role].grain} onChange={e=>setTables(p=>({...p,[role]:{...p[role],grain:e.target.value}}))}/></label>
          <fieldset><legend>Categorical filter fields (strings, at most eight)</legend><div className="v2-preview-columns">{headers(role).filter(n=>n!==tables[role].key && (role!=='fact' || (n!==foreignKey && n!==measure))).map(n=><label key={n}><input type="checkbox" checked={tables[role].categories.includes(n)} onChange={e=>setTables(p=>({...p,[role]:{...p[role],categories:e.target.checked?[...p[role].categories,n]:p[role].categories.filter(x=>x!==n)}}))}/> {n}</label>)}</div></fieldset>
        </div>)}</div>
        <div className="form-grid two">{columnSelect('fact',foreignKey,setForeignKey,'Fact foreign key → dimension key')}{columnSelect('fact',measure,setMeasure,'Exact decimal fact measure')}</div>
        <div className="form-grid two"><label>Precision<input required type="number" min={1} max={38} value={precision} onChange={e=>setPrecision(Number(e.target.value))}/></label><label>Scale<input required type="number" min={0} max={Math.min(18,precision)} value={scale} onChange={e=>setScale(Number(e.target.value))}/></label></div>
        <label>Measure meaning<input required maxLength={300} value={meaning} onChange={e=>setMeaning(e.target.value)}/></label><label>Declared unit<input required maxLength={80} placeholder="Use unknown currency if the source does not establish one" value={unit} onChange={e=>setUnit(e.target.value)}/></label>
        <label>CSV NULL policy<select value={nullPolicy} onChange={e=>setNullPolicy(e.target.value as typeof nullPolicy)}><option value="blank_nullable">Blank field is NULL</option><option value="literal_NULL">Exact token NULL is NULL</option></select></label>
        <label><input type="checkbox" checked={scope} onChange={e=>setScope(e.target.checked)}/> Both complete files define one whole-snapshot scope.</label>
        <label><input type="checkbox" checked={comparable} onChange={e=>setComparable(e.target.checked)}/> All non-NULL measure values share the declared unit; no currency conversion is implied.</label>
        <button className="button primary" disabled={!headers('fact').length || !headers('dimension').length}>Register relational contract <ArrowRight size={15}/></button>
      </fieldset></form></div>
    </section>
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">03</span><div><h2>Validate a frozen snapshot</h2><p>Full-table key uniqueness and join multiplicity are checked before publication.</p></div></div>
      <div className="v2-step-body v2-grid two"><form onSubmit={upload}><fieldset disabled={locked} className="form-stack">
        <label>Registered contract<select required value={contractId} onChange={e=>{setContractId(e.target.value);setSnapshotId('');setDefinitionIds({baseline:'',target:''});resetResult();}}><option value="">Choose contract</option>{contracts.map(c=><option key={c.id} value={c.id}>{c.name} · revision {c.version_number}</option>)}</select></label>
        <label>Snapshot name<input required maxLength={160} value={snapshotName} onChange={e=>setSnapshotName(e.target.value)}/></label>
        <button className="button primary" disabled={!contract || !files.fact || !files.dimension || data.mode !== 'isolated'}>Import both CSV files</button>
      </fieldset>{importJob && <p role="status">Import: {importJob.state}</p>}</form>
        <div className="form-stack"><label>Validated snapshot<select disabled={locked} value={snapshotId} onChange={e=>{setSnapshotId(e.target.value);resetResult();}}><option value="">Choose snapshot</option>{snapshots.map(s=><option key={s.id} value={s.id}>{s.name} · {s.row_count} fact rows</option>)}</select></label>
          {snapshot && <><p>{snapshot.join_proof.matched_fact_rows} matched · {snapshot.join_proof.unmatched_fact_rows} unmatched ({snapshot.join_proof.null_foreign_key_rows} NULL foreign keys) · {snapshot.join_proof.fanout_rows} multiplied rows</p><p className="v2-help">{snapshot.null_semantics}</p><details><summary>Frozen contract, source hashes and grain proof</summary><pre className="json-view">{JSON.stringify({contract,snapshot},null,2)}</pre></details></>}
        </div></div>
    </section>
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">04</span><div><h2>Define and run the comparison</h2><p>Qualified SUM of the fact measure; one declared INNER or LEFT equality join. Optional AND over string =, IN, IS NULL or IS NOT NULL. No CASE or arithmetic.</p></div></div>
      <div className="v2-step-body"><div className="v2-grid two">{(['baseline','target'] as const).map(side=><form key={side} onSubmit={e=>define(e,side)}><fieldset disabled={locked || pending(run?.state)} className="form-stack"><label>{side === 'baseline' ? 'Baseline SQL' : 'Target SQL'}<textarea required rows={6} spellCheck={false} value={sql[side]} onChange={e=>{setSql(p=>({...p,[side]:e.target.value}));setDefinitionIds(p=>({...p,[side]:''}));resetResult();}}/></label>
        <button type="button" className="button secondary" disabled={!contract} onClick={()=>{setSql(p=>({...p,[side]:starter}));setDefinitionIds(p=>({...p,[side]:''}));resetResult();}}>Use declared LEFT JOIN example</button>
        <button className="button primary" disabled={!contract}>Register {side} SQL</button>
        <label>Registered version<select value={definitionIds[side]} onChange={e=>{const id=e.target.value;setDefinitionIds(p=>({...p,[side]:id}));const v=versions.find(x=>x.version.id===id);if(v) setSql(p=>({...p,[side]:v.version.raw_sql}));resetResult();}}><option value="">Choose version</option>{versions.map(x=><option key={x.version.id} value={x.version.id}>{x.definition.name}</option>)}</select></label>
      </fieldset></form>)}</div><form onSubmit={start}><button className="button primary" disabled={locked || pending(run?.state) || !snapshot || !definitionIds.baseline || !definitionIds.target || data.mode !== 'isolated'}><Play size={15}/> Run comparison</button></form>
      </div>
    </section>
    {run && <section className="panel v2-step"><div className="v2-step-header"><h2>Comparison · {run.state}</h2></div><div className="v2-step-body">
      {run.error && <div className="v2-status warn" role="alert">{run.error.code}: {run.error.message}</div>}
      {run.result && <><div className="v2-totals"><div><span>Baseline</span><strong>{amount(run.result.baseline_value,runUnit)}</strong></div><div><span>Target</span><strong>{amount(run.result.target_value,runUnit)}</strong></div><div><span>Target − baseline</span><strong>{amount(run.result.delta_target_minus_baseline,runUnit,'Not defined — an endpoint SUM is NULL')}</strong></div></div>
        <p>Residual: {run.result.residual ?? 'not applicable'}. Every fact is retained in evidence, including facts excluded by both definitions.</p>
        <RelationalEvidencePanel key={run.id} run={run} versions={data.definitions.flatMap(d=>d.versions)}/>
        <div className="v2-status warn">{run.result.limitations.join(' ')}</div><details><summary>Run provenance</summary><pre className="json-view">{JSON.stringify(run.result.provenance,null,2)}</pre></details>
        <button className="button secondary" disabled={busy} onClick={()=>void perform(async()=>{await api.download(`/comparisons/${run.id}/export?format=json`,`metricguard-${run.id}.json`);})}><Download size={15}/> JSON report · first 100 rows</button>
        <button className="button secondary" disabled={busy} onClick={()=>void perform(async()=>{await api.download(`/comparisons/${run.id}/export?format=html`,`metricguard-${run.id}.html`);})}>HTML report · first 100 rows</button>
      </>}
    </div></section>}
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">05</span><div><h2>Save and replay the investigation</h2><p>Replay uses the frozen contract, snapshot, definitions and original unit context.</p></div></div><div className="v2-step-body v2-grid two">
      <form onSubmit={e=>{e.preventDefault();void perform(async()=>{if (!run?.result) return;const saved=await api.post<{id:string}>('/investigations',{title,decision,actor_label:actor,run_id:run.id,snapshot_id:run.snapshot_id});await refresh();setSavedCase(await api.relationalCase(saved.id));setNotice('Investigation saved with pinned inputs.');});}}><fieldset disabled={busy} className="form-stack"><label>Case title<input required maxLength={160} value={title} onChange={e=>setTitle(e.target.value)}/></label><label>Decision and rationale<textarea required maxLength={2000} value={decision} onChange={e=>setDecision(e.target.value)}/></label><label>Recorded by<input required maxLength={160} value={actor} onChange={e=>setActor(e.target.value)}/></label><button className="button primary" disabled={!run?.result}><Save size={15}/> Save investigation</button></fieldset></form>
      <div className="form-stack"><strong>Saved relational cases</strong>{cases.map(c=><button className="button secondary" disabled={locked} key={c.id} onClick={()=>open(c.id)}>{c.title}</button>)}
        {savedCase && <><h3>{savedCase.title}</h3><p>{savedCase.decision}</p><button className="button secondary" disabled={locked || pending(run?.state) || !savedCase.replay_available || data.mode!=='isolated'} onClick={()=>void perform(async()=>{const value=await api.post<RelationalRun>(`/investigations/${savedCase.id}/replays`,{}, {'Idempotency-Key':crypto.randomUUID()});setRun(value);await refresh();})}><RefreshCw size={15}/> Replay saved inputs</button>{!savedCase.replay_available && <p>Snapshot unavailable; stored result remains readable.</p>}</>}
      </div></div></section>
  </div>;
}
