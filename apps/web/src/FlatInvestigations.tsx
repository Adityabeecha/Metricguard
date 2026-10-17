import {useEffect, useState, type FormEvent} from 'react';
import {ArrowRight, Check, Download, GitCompareArrows, RefreshCw, UploadCloud} from 'lucide-react';
import {api, versionName, type Bootstrap, type DatasetPreviewRecord, type FlatContract, type FlatEvidence, type InvestigationDetail, type Run, type SnapshotImport} from './api';

type Props = {data: Bootstrap; refresh: () => Promise<Bootstrap | null>};
const pending = (state: string) => ['queued', 'running', 'pending'].includes(state);
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const amount = (value: string | null | undefined, unit: string) => value == null ? 'Undefined (SUM has no non-NULL inputs)' : `${value}${unit ? ` ${unit}` : ''}`;
const membershipLabel = (value: string | undefined, baseline: boolean, target: boolean) =>
  value === 'included_in_both' ? 'Both definitions' : value === 'baseline_only' ? 'Baseline only' :
  value === 'target_only' ? 'Target only' : value === 'excluded_from_both' ? 'Neither definition' :
  baseline && target ? 'Both definitions' : baseline ? 'Baseline only' : target ? 'Target only' : 'Neither definition';

export function FlatInvestigations({data, refresh}: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preparationFile, setPreparationFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<DatasetPreviewRecord | null>(null);
  const [contractId, setContractId] = useState('');
  const [name, setName] = useState('');
  const [key, setKey] = useState('');
  const [measure, setMeasure] = useState('');
  const [categories, setCategories] = useState<string[]>([]);
  const [precision, setPrecision] = useState(18);
  const [scale, setScale] = useState(2);
  const [meaning, setMeaning] = useState('');
  const [unit, setUnit] = useState('');
  const [comparable, setComparable] = useState(false);
  const [scopeConfirmed, setScopeConfirmed] = useState(false);
  const [nullPolicy, setNullPolicy] = useState<FlatContract['null_policy']>('blank_nullable');
  const [snapshotName, setSnapshotName] = useState('');
  const [importId, setImportId] = useState('');
  const [snapshotId, setSnapshotId] = useState('');
  const [baselineName, setBaselineName] = useState('Baseline sum');
  const [targetName, setTargetName] = useState('Target sum');
  const [baselineSql, setBaselineSql] = useState('');
  const [targetSql, setTargetSql] = useState('');
  const [baselineId, setBaselineId] = useState('');
  const [targetId, setTargetId] = useState('');
  const [relationship, setRelationship] = useState('candidate');
  const [run, setRun] = useState<Run | null>(null);
  const [rows, setRows] = useState<FlatEvidence[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [caseTitle, setCaseTitle] = useState('');
  const [decision, setDecision] = useState('');
  const [actor, setActor] = useState('');
  const [openCase, setOpenCase] = useState<InvestigationDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const contracts = data.flat_contracts || [];
  const contract = contracts.find(item => item.id === contractId);
  const snapshots = data.snapshots.filter(item => item.contract_hash === contract?.contract_hash && (item as {execution_policy?: string}).execution_policy === 'flat_snapshot_v1');
  const snapshot = snapshots.find(item => item.id === snapshotId);
  const versions = data.definitions.flatMap(definition => definition.versions.filter(version => version.contract_hash === contract?.contract_hash && (version as {execution_policy?: string}).execution_policy === 'flat_snapshot_v1' && version.support_state === 'executable').map(version => ({definition, version})));
  const cases = data.investigations.filter(item => (item.context as {mode?: string}).mode === 'flat_snapshot');
  const headers = preview?.state === 'previewed' ? preview.preview?.headers || [] : [];

  useEffect(() => {
    if (!preview || !pending(preview.state)) return;
    const timer = setInterval(() => void api.datasetPreview(preview.id).then(setPreview).catch(error => setError(errorText(error))), 1500);
    return () => clearInterval(timer);
  }, [preview?.id, preview?.state]);
  useEffect(() => {
    if (!importId) return;
    const timer = setInterval(() => void refresh().then(next => {
      const entry = next?.imports.find(item => item.id === importId);
      if (!entry || pending(entry.state)) return;
      setImportId('');
      if (entry.state === 'validated' && entry.snapshot_id) {setSnapshotId(entry.snapshot_id); setNotice('Snapshot validated by the reconciliation engine.');}
      else setError(entry.error?.message || `Import ended in ${entry.state}.`);
    }).catch(error => setError(errorText(error))), 1500);
    return () => clearInterval(timer);
  }, [importId, refresh]);
  useEffect(() => {
    if (!run || !pending(run.state)) return;
    const timer = setInterval(() => void api.run(run.id).then(next => {setRun(next); if (!pending(next.state)) void refresh();}).catch(error => setError(errorText(error))), 1500);
    return () => clearInterval(timer);
  }, [run?.id, run?.state, refresh]);
  useEffect(() => {
    if (!run?.result) return;
    let cancelled = false;
    void api.flatEvidence(run.id, offset, 100).then(page => {if (!cancelled) {setRows(page.items); setTotal(page.total);}}).catch(error => setError(errorText(error)));
    return () => {cancelled = true;};
  }, [run?.id, run?.result, offset]);

  async function perform(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try {await work();} catch (error) {setError(errorText(error));} finally {setBusy(false);}
  }
  function chooseFile(next: File | null) {
    setFile(next); setPreview(null); setSnapshotId(''); setRun(null); setOpenCase(null);
    if (next && !snapshotName) setSnapshotName(next.name.replace(/\.(csv|parquet)$/i, ''));
    setNullPolicy(next?.name.toLowerCase().endsWith('.parquet') ? 'native_null' : 'blank_nullable');
  }
  function requestPreview() {
    if (!file) {setError('Choose a CSV or Parquet extract first.'); return;}
    void perform(async () => {const form = new FormData(); form.append('file', file); setPreview(await api.previewDataset(form));});
  }
  function register(event: FormEvent) {
    event.preventDefault();
    if (!scopeConfirmed) {setError('Confirm that the supplied file defines the entire comparison scope.'); return;}
    if (!comparable) {setError('Confirm that every non-NULL measure uses the declared unit.'); return;}
    if (key === measure || categories.includes(key) || categories.includes(measure)) {setError('Key, measure, and categorical fields must be distinct.'); return;}
    if (categories.length > 8) {setError('Register at most eight categorical fields.'); return;}
    if (headers.length && [key, measure, ...categories].some(column => !headers.includes(column))) {setError('A selected field is missing from the preview.'); return;}
    void perform(async () => {
      const created = await api.post<FlatContract>('/flat/contracts', {version: 3, mode: 'flat_snapshot', name, row_key: key, measure, precision, scale, categories, measure_meaning: meaning, unit_label: unit, values_comparable: comparable, scope: 'entire_supplied_snapshot', null_policy: nullPolicy});
      setContractId(created.id); await refresh(); setNotice(`Contract v${created.version_number} registered. Its identity is ${created.contract_hash.slice(0, 12)}…`);
      const starter = `SELECT SUM(${measure}) FROM snapshot`;
      setBaselineSql(starter); setTargetSql(starter);
    });
  }
  function upload(event: FormEvent) {
    event.preventDefault();
    if (!file || !contract) {setError('Choose a file and register its contract first.'); return;}
    if ((file.name.toLowerCase().endsWith('.parquet')) !== (contract.null_policy === 'native_null')) {setError('The file format does not match the contract NULL policy.'); return;}
    void perform(async () => {
      const form = new FormData(); form.append('file', file); form.append('name', snapshotName || file.name); form.append('contract_id', contract.id);
      if (preparationFile) form.append('preparation_manifest', preparationFile);
      const result = await api.uploadFlat(form); await refresh();
      if ('state' in result) {const entry = result as SnapshotImport; if (entry.state === 'validated' && entry.snapshot_id) setSnapshotId(entry.snapshot_id); else setImportId(entry.id);}
      else setSnapshotId(result.id);
      setNotice('Extract submitted for isolated validation.');
    });
  }
  function define(event: FormEvent, side: 'baseline' | 'target') {
    event.preventDefault();
    if (!contract) {setError('Register a contract first.'); return;}
    void perform(async () => {
      const definition = await api.post<{versions: Array<{id: string; support_state: string; support_reason?: string}>}>('/flat/definitions', {name: side === 'baseline' ? baselineName : targetName, raw_sql: side === 'baseline' ? baselineSql : targetSql, contract_id: contract.id});
      const version = definition.versions.at(-1);
      await refresh();
      if (!version || version.support_state !== 'executable') {setError(version?.support_reason || 'SQL was refused. Review the query and try again.'); return;}
      if (side === 'baseline') setBaselineId(version.id); else setTargetId(version.id);
      setNotice(`${side === 'baseline' ? 'Baseline' : 'Target'} definition v${definition.versions.length} registered.`);
    });
  }
  function compare(event: FormEvent) {
    event.preventDefault();
    if (!snapshot || !contract || !baselineId || !targetId) {setError('Choose a validated snapshot and two executable definitions.'); return;}
    if (baselineId === targetId) {setError('Choose two distinct definition versions.'); return;}
    void perform(async () => {
      const started = await api.post<Run>('/flat/comparisons', {baseline_version_id: baselineId, target_version_id: targetId, snapshot_id: snapshot.id, context: {mode: 'flat_snapshot', scope: 'entire_supplied_snapshot', contract_hash: contract.contract_hash, unit_label: contract.unit_label, values_comparable: true}, relationship}, {'Idempotency-Key': crypto.randomUUID()});
      setRun(started); setOffset(0); await refresh(); setNotice('Comparison queued for reconciliation.');
    });
  }
  function save(event: FormEvent) {
    event.preventDefault();
    if (!run || !['succeeded', 'partial'].includes(run.state)) {setError('Complete a comparison before saving the case.'); return;}
    void perform(async () => {
      const saved = await api.post<{id: string}>('/investigations', {title: caseTitle, run_id: run.id, snapshot_id: run.snapshot_id, decision, actor_label: actor});
      await refresh(); setOpenCase(await api.investigation(saved.id)); setNotice('Investigation saved with pinned inputs.');
    });
  }
  function open(id: string) {void perform(async () => {const detail = await api.investigation(id); setOpenCase(detail); setRun(detail.run); setOffset(0); setContractId((data.flat_contracts || []).find(item => item.contract_hash === detail.snapshot.contract_hash)?.id || ''); setSnapshotId(detail.snapshot_id); setBaselineId(detail.baseline_version_id); setTargetId(detail.target_version_id); setBaselineSql(detail.baseline_version.raw_sql); setTargetSql(detail.target_version.raw_sql); setNotice('Opened saved investigation and its pinned result.');});}
  function replay() {if (!openCase) return; void perform(async () => {setRun(await api.post<Run>(`/investigations/${openCase.id}/replays`, {}, {'Idempotency-Key': crypto.randomUUID()})); setOffset(0); await refresh(); setNotice('Pinned replay queued.');});}
  const unitForRun = (run?.context as {unit_label?: string} | undefined)?.unit_label || contract?.unit_label || '';
  return <div className="v2-page">
    <div className="page-heading"><div><div className="eyebrow">FLAT INVESTIGATION · WHOLE-SNAPSHOT SUM COMPARISON <span className="eyebrow-line"/></div><h1>Flat investigation workflow</h1><p>Compare two SUM filters over an externally prepared snapshot. Upload the complete extract after preparation is done.</p></div><button className="button secondary" onClick={() => void refresh()}><RefreshCw size={16}/> Refresh</button></div>
    {data.mode !== 'isolated' && <div className="v2-status warn">This workflow requires isolated mode.</div>}
    {!data.runner_available && data.mode === 'isolated' && <div className="v2-status warn">The reconciliation engine is unavailable. Queued work will resume when it starts.</div>}
    {error && <div className="v2-status warn" role="alert">{error}</div>}{notice && <div className="v2-status good" role="status">{notice}</div>}
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">01</span><div><h2>Inspect the prepared extract</h2><p>Preview identifies columns and sample values. The reconciliation engine validates every row after upload.</p></div></div><div className="v2-step-body"><label className="upload-zone"><UploadCloud size={27}/><strong>{file?.name || 'Choose a CSV or Parquet extract'}</strong><span>{file ? `${Math.round(file.size / 1024)} KB selected` : 'Local prepared file'}</span><input type="file" accept=".csv,.parquet,text/csv,application/vnd.apache.parquet" onChange={event => chooseFile(event.target.files?.[0] || null)}/></label><button className="button secondary" type="button" disabled={!file || busy || data.mode !== 'isolated'} onClick={requestPreview}>Preview columns</button>{preview?.state === 'failed' && <div className="v2-status warn" role="alert">{preview.error?.message || 'Preview failed.'}</div>}{preview?.state === 'previewed' && preview.preview && <div className="v2-preview"><strong>{headers.length} columns · SHA-256 {preview.preview.source_hash}</strong><div className="v2-preview-columns">{headers.map(column => <code key={column}>{column} · {preview.preview?.declared_types[column] || preview.preview?.observed_types[column] || 'type unknown'}</code>)}</div><div className="table-scroll"><table><thead><tr>{headers.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{preview.preview.sample_rows.slice(0, 5).map((row, index) => <tr key={index}>{headers.map(column => <td key={column}>{row[column] ?? 'NULL'}</td>)}</tr>)}</tbody></table></div></div>}</div></section>
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">02</span><div><h2>Register the exact dataset contract</h2><p>One unique non-NULL key, one exact decimal measure, and explicitly listed categories.</p></div></div><div className="v2-step-body v2-grid two"><form className="form-stack" onSubmit={register}><label>Contract name<input required value={name} onChange={event => setName(event.target.value)}/></label><div className="form-grid two"><label>Row key<select required value={key} onChange={event => setKey(event.target.value)}><option value="">Choose column</option>{headers.map(column => <option key={column}>{column}</option>)}</select></label><label>Decimal measure<select required value={measure} onChange={event => setMeasure(event.target.value)}><option value="">Choose column</option>{headers.map(column => <option key={column}>{column}</option>)}</select></label></div><div className="form-grid two"><label>Precision<input type="number" min={1} max={38} required value={precision} onChange={event => setPrecision(Number(event.target.value))}/></label><label>Scale<input type="number" min={0} max={Math.min(precision, 18)} required value={scale} onChange={event => setScale(Number(event.target.value))}/></label></div><fieldset><legend>Registered categorical fields</legend><div className="v2-preview-columns">{headers.filter(column => column !== key && column !== measure).map(column => <label key={column}><input type="checkbox" checked={categories.includes(column)} onChange={event => setCategories(event.target.checked ? [...categories, column] : categories.filter(item => item !== column))}/> {column}</label>)}</div></fieldset><label>Measure meaning<input required value={meaning} onChange={event => setMeaning(event.target.value)} placeholder="What one row's amount represents"/></label><label>Unit label<input required value={unit} onChange={event => setUnit(event.target.value)} placeholder="Source-declared unit; unknown currency stays unknown"/></label><label>NULL policy<select value={nullPolicy} onChange={event => setNullPolicy(event.target.value as FlatContract['null_policy'])}><option value="blank_nullable">CSV blank is NULL</option><option value="literal_NULL">CSV literal NULL is NULL</option><option value="native_null">Parquet native NULL</option></select></label><label><input type="checkbox" checked={scopeConfirmed} onChange={event => setScopeConfirmed(event.target.checked)}/> The entire supplied snapshot is the comparison scope.</label><label><input type="checkbox" checked={comparable} onChange={event => setComparable(event.target.checked)}/> All non-NULL measure values use this same declared unit.</label><p className="v2-help">An absent source record is NULL when preparation records it as NULL. It does not establish a zero amount. No currency symbol or financial loss is inferred.</p><button className="button primary" disabled={busy || !headers.length}>Register contract <ArrowRight size={15}/></button></form><div className="v2-side"><label>Registered contract<select value={contractId} onChange={event => {setContractId(event.target.value); setSnapshotId(''); setBaselineId(''); setTargetId(''); setRun(null); setOpenCase(null);}}><option value="">Choose contract</option>{contracts.map(item => <option key={item.id} value={item.id}>{item.name} · v{item.version_number}</option>)}</select></label>{contract && <><p><strong>Contract hash</strong> {contract.contract_hash}</p><p><strong>Meaning</strong> {contract.measure_meaning}</p><p><strong>Unit</strong> {contract.unit_label}</p><p><strong>Scope</strong> Whole supplied snapshot</p><p><strong>Categories</strong> {contract.categories.join(', ') || 'None'}</p></>}</div></div></section>
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">03</span><div><h2>Validate and select snapshot</h2><p>The reconciliation engine checks the registered types, key, and source bytes.</p></div></div><div className="v2-step-body v2-grid two"><form className="form-stack" onSubmit={upload}><label>Snapshot name<input required value={snapshotName} onChange={event => setSnapshotName(event.target.value)}/></label><label>External preparation manifest (optional)<input type="file" accept=".json,application/json" onChange={event => setPreparationFile(event.target.files?.[0] || null)}/></label><p className="v2-help">{preparationFile ? `${preparationFile.name} selected` : 'No preparation manifest selected'}</p><p className="v2-help">Attach a bounded JSON manifest to link the prepared file to external lineage and source hashes. MetricGuard checks the uploaded file hash; original files and lineage stay outside the application.</p><button className="button primary" disabled={!file || !contract || busy || data.mode !== 'isolated'}>Upload prepared extract <ArrowRight size={15}/></button>{importId && <div className="v2-status info">Import {importId} is being validated.</div>}</form><div className="v2-side"><label>Validated snapshot<select value={snapshotId} onChange={event => {setSnapshotId(event.target.value); setRun(null);}}><option value="">Choose snapshot</option>{snapshots.map(item => <option key={item.id} value={item.id}>{item.name} · {item.row_count} rows</option>)}</select></label>{snapshot && <><p><strong>Prepared CSV hash</strong> {snapshot.source_hash}</p><p><strong>Snapshot manifest hash</strong> {snapshot.manifest_hash}</p><p><strong>Rows</strong> {snapshot.row_count}</p><details><summary>External preparation provenance</summary>{snapshot.preparation ? <><p>Manifest SHA-256: {snapshot.preparation_manifest_hash}</p><pre className="json-view">{JSON.stringify(snapshot.preparation, null, 2)}</pre><p>Lineage and original files are referenced by hash but are not stored or independently checked by MetricGuard. Matching hashes establish artifact identity, not transformation correctness.</p></> : <p>Unavailable for this snapshot.</p>}</details></>}</div></div></section>
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">04</span><div><h2>Register two SUM definitions</h2><p>Use the registered measure and category fields. Supported filters are AND combinations of equality and IN.</p></div></div><div className="v2-step-body v2-grid two">{(['baseline', 'target'] as const).map(side => <form className="form-stack" key={side} onSubmit={event => define(event, side)}><label>{side === 'baseline' ? 'Baseline' : 'Target'} name<input required value={side === 'baseline' ? baselineName : targetName} onChange={event => side === 'baseline' ? setBaselineName(event.target.value) : setTargetName(event.target.value)}/></label><label>SQL<textarea required rows={6} spellCheck={false} value={side === 'baseline' ? baselineSql : targetSql} onChange={event => {if (side === 'baseline') {setBaselineSql(event.target.value); setBaselineId('');} else {setTargetSql(event.target.value); setTargetId('');} setRun(null);}} placeholder={contract ? `SELECT SUM(${contract.measure}) FROM snapshot` : 'Register a contract first'}/></label><button className="button primary" disabled={!contract || busy}>Register {side} SQL <ArrowRight size={15}/></button><label>Executable version<select value={side === 'baseline' ? baselineId : targetId} onChange={event => {if (side === 'baseline') setBaselineId(event.target.value); else setTargetId(event.target.value); setRun(null);}}><option value="">Choose version</option>{versions.map(({definition, version}) => <option key={version.id} value={version.id}>{definition.name} · v{version.version_number}</option>)}</select></label></form>)}</div></section>
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">05</span><div><h2>Run and inspect the comparison</h2><p>Both definitions run on this one immutable whole snapshot.</p></div></div><div className="v2-step-body"><form className="form-stack" onSubmit={compare}><label>Relationship<select value={relationship} onChange={event => {setRelationship(event.target.value); setRun(null);}}><option value="candidate">Candidate</option><option value="expected_agreement">Expected agreement</option><option value="intentional_variant">Intentional variant</option><option value="unrelated">Unrelated</option></select></label><button className="button primary" disabled={!snapshot || !baselineId || !targetId || busy || data.mode !== 'isolated'}>Run comparison <GitCompareArrows size={15}/></button></form>{run && <div className="v2-result"><div className="v2-result-heading"><strong>Run {run.id} · {run.state}</strong>{run.result && <div><button type="button" className="button secondary small" onClick={() => void api.download(`/comparisons/${encodeURIComponent(run.id)}/export?format=json`, `comparison-${run.id}.json`).catch(e => window.alert(e instanceof Error ? e.message : String(e)))}><Download size={14}/> JSON · first 100 rows</button> <button type="button" className="button secondary small" onClick={() => void api.download(`/comparisons/${encodeURIComponent(run.id)}/export?format=html`, `comparison-${run.id}.html`).catch(e => window.alert(e instanceof Error ? e.message : String(e)))}><Download size={14}/> HTML · first 100 rows</button></div>}</div>{run.error && <div className="v2-status warn" role="alert">{run.error.message || run.error.code}</div>}{run.result && <><div className="v2-totals"><div><span>Baseline</span><strong>{amount(run.result.baseline_value, unitForRun)}</strong></div><div><span>Target</span><strong>{amount(run.result.target_value, unitForRun)}</strong></div><div><span>Target − baseline</span><strong>{amount(run.result.delta_target_minus_baseline, unitForRun)}</strong></div></div><p className="v2-help"><strong>What these totals mean:</strong> The baseline and target show what each SQL filter includes from the prepared snapshot. The signed gap (target − baseline) explains the difference between them. A negative gap means the target filter captured less value than baseline. Row deltas below show which records contribute to this overall gap.</p><p className="v2-help"><strong>Unknown currency:</strong> If the preparation manifest did not declare a currency, it remains unknown here. MetricGuard reproduces the numeric comparison without inferring currency semantics.</p><p className="v2-help"><strong>Evidence and exports:</strong> All {total || 0} row deltas are shown below; page through them with Previous/Next. JSON and HTML exports include the first 100 rows and explicitly state how many were omitted.</p>{run.result.limitations?.length > 0 && <div className="v2-status warn">{run.result.limitations.join(' · ')}</div>}<div className="table-scroll"><table><thead><tr><th>ROW KEY</th><th>MEMBERSHIP</th><th>BASELINE</th><th>TARGET</th><th>DELTA</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.order_id}-${index}`}><td className="mono-cell">{row.order_id}</td><td>{membershipLabel(row.membership, row.baseline_included, row.target_included)}</td><td className="mono-cell">{row.baseline_null_term ? 'NULL' : row.baseline_contribution ?? '—'}</td><td className="mono-cell">{row.target_null_term ? 'NULL' : row.target_contribution ?? '—'}</td><td className="mono-cell">{row.delta ?? '—'}</td></tr>)}</tbody></table></div><div className="pagination"><span>{total ? `${offset + 1}–${Math.min(offset + 100, total)} of ${total}` : 'No evidence rows'}</span><div><button className="button secondary small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous</button><button className="button secondary small" disabled={offset + 100 >= total} onClick={() => setOffset(offset + 100)}>Next</button></div></div><details><summary>Run provenance (external lineage stays with prepared extract)</summary><pre className="json-view">{JSON.stringify(run.result.provenance, null, 2)}</pre></details></>}</div>}</div></section>
    <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">06</span><div><h2>Save and replay</h2><p>A saved case pins the policy, contract, snapshot, definitions, and context.</p></div></div><div className="v2-step-body v2-grid two"><form className="form-stack" onSubmit={save}><label>Case title<input required value={caseTitle} onChange={event => setCaseTitle(event.target.value)}/></label><label>Decision and rationale<textarea required rows={4} value={decision} onChange={event => setDecision(event.target.value)}/></label><label>Recorded by<input required value={actor} onChange={event => setActor(event.target.value)}/></label><button className="button primary" disabled={!run || !['succeeded', 'partial'].includes(run.state) || busy}>Save case <Check size={15}/></button></form><div className="v2-side"><strong>Saved flat investigations</strong><div className="v2-case-list">{cases.map(item => <button key={item.id} onClick={() => open(item.id)}><strong>{item.title}</strong><small>{versionName(data.definitions, item.baseline_version_id)} → {versionName(data.definitions, item.target_version_id)}</small></button>)}</div>{openCase && <div className="v2-open-case"><h3>{openCase.title}</h3><p>{openCase.decision}</p><small>Snapshot {openCase.snapshot.manifest_hash}</small><button className="button secondary" disabled={!openCase.replay_available || busy || data.mode !== 'isolated'} onClick={replay}><RefreshCw size={15}/> Replay pinned case</button></div>}</div></div></section>
  </div>;
}
