import {useEffect, useMemo, useState, type FormEvent, type ReactNode} from 'react';
import {ArrowRight, Check, GitCompareArrows, RefreshCw, UploadCloud} from 'lucide-react';
import {api, compact, versionName, type Bootstrap, type ColumnMapping, type Context, type DatasetContract, type DatasetPreviewRecord, type Definition, type InvestigationDetail, type Run, type Snapshot, type SnapshotImport, type Version} from './api';
import {boundVersions, comparisonIssue, mappingIssue, matchingSnapshots, roles, schema, sourcePolicyIssue} from './v2Workflow';

type Props = {data: Bootstrap; refresh: () => Promise<Bootstrap | null>; onOpenRun: (run: Run) => void};
type ContractDraft = {name: string; currency: string; timezone: string; scope: string; grain: string; cutoff_at: string; null_policy: DatasetContract['null_policy']};
const defaultDraft: ContractDraft = {name: '', currency: 'INR', timezone: 'Asia/Kolkata', scope: '', grain: 'one_row_per_order_id', cutoff_at: new Date().toISOString().slice(0, 10), null_policy: 'blank_nullable'};
const pending = (state: string) => ['queued', 'running', 'pending'].includes(state);
const showError = (error: unknown) => error instanceof Error ? error.message : String(error);
const shortHash = (hash: string) => `${hash.slice(0, 12)}…`;
const quoteColumn = (column: string) => `"${column.replaceAll('"', '""')}"`;

function Step({number, title, detail, children}: {number: string; title: string; detail: string; children: ReactNode}) {
  return <section className="v2-step panel"><div className="v2-step-header"><span className="step-index">{number}</span><div><h2>{title}</h2><p>{detail}</p></div></div><div className="v2-step-body">{children}</div></section>;
}

function Status({children, tone = 'info'}: {children: ReactNode; tone?: 'info' | 'warn' | 'good'}) {
  return <div className={`v2-status ${tone}`} role={tone === 'warn' ? 'alert' : 'status'}>{children}</div>;
}

function defaultContext(snapshot: Snapshot | undefined): Context {
  return {start: '', end_exclusive: '', timezone: snapshot?.timezone || 'Asia/Kolkata', currency: snapshot?.currency || snapshot?.unit_label || '', unit: 'currency', grain: snapshot?.grain || 'one_row_per_order_id', scope: snapshot?.scope || '', contract_hash: snapshot?.contract_hash, mapping_hash: snapshot?.mapping_hash};
}

export function V2Investigations({data, refresh, onOpenRun}: Props) {
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<ContractDraft>(defaultDraft);
  const [contractId, setContractId] = useState('');
  const [mappingId, setMappingId] = useState('');
  const [sourceColumns, setSourceColumns] = useState<Record<string, string>>({});
  const [file, setFile] = useState<File | null>(null);
  const [previewRecord, setPreviewRecord] = useState<DatasetPreviewRecord | null>(null);
  const [previewMessage, setPreviewMessage] = useState('');
  const [snapshotName, setSnapshotName] = useState('');
  const [importId, setImportId] = useState<string | null>(null);
  const [snapshotId, setSnapshotId] = useState('');
  const [definitionId, setDefinitionId] = useState('');
  const [definitionName, setDefinitionName] = useState('');
  const [sourceLabel, setSourceLabel] = useState('Extract SQL');
  const [ownerLabel, setOwnerLabel] = useState('Local analyst');
  const [timeColumn, setTimeColumn] = useState('');
  const [sql, setSql] = useState('');
  const [leftId, setLeftId] = useState('');
  const [rightId, setRightId] = useState('');
  const [context, setContext] = useState<Context | null>(null);
  const [relationship, setRelationship] = useState('candidate');
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [caseTitle, setCaseTitle] = useState('');
  const [decision, setDecision] = useState('');
  const [actor, setActor] = useState('');
  const [openCase, setOpenCase] = useState<InvestigationDetail | null>(null);

  const contracts = data.dataset_contracts || [];
  const mappings = data.column_mappings || [];
  const cases = data.investigations.filter(item => (item.context as {mode?: string}).mode !== 'flat_snapshot');
  const selectedContractId = contractId || contracts[0]?.id || '';
  const contract = contracts.find(item => item.id === selectedContractId);
  const contractMappings = mappings.filter(item => item.contract_id === selectedContractId);
  const selectedMappingId = mappingId || contractMappings[0]?.id || '';
  const mapping = contractMappings.find(item => item.id === selectedMappingId);
  const snapshots = matchingSnapshots(data.snapshots, mapping);
  const snapshot = snapshots.find(item => item.id === snapshotId) || snapshots[0];
  const versions = boundVersions(data.definitions, mapping);
  const left = versions.find(item => item.version.id === leftId)?.version;
  const right = versions.find(item => item.version.id === rightId)?.version;
  const selectedContext = context || defaultContext(snapshot);
  const preview = previewRecord?.state === 'previewed' ? previewRecord.preview : null;
  const compareProblem = comparisonIssue(left, right, snapshot, selectedContext) || (!selectedContext.start || !selectedContext.end_exclusive || selectedContext.start >= selectedContext.end_exclusive ? 'Enter a valid reporting period; the end date is exclusive.' : null);
  const mappedDefinitions = useMemo(() => mapping ? data.definitions.filter(definition => definition.versions.some(version => version.mapping_id === mapping.id)) : [], [data.definitions, mapping]);
  const mappedVersions = useMemo(() => mapping ? data.definitions.flatMap(definition => definition.versions.filter(version => version.mapping_id === mapping.id).map(version => ({definition, version}))) : [], [data.definitions, mapping]);

  useEffect(() => {
    if (!previewRecord || !pending(previewRecord.state)) return;
    const timer = setInterval(() => {
      void api.datasetPreview(previewRecord.id).then(record => {
        setPreviewRecord(record);
        if (record.state === 'previewed') setPreviewMessage('Preview ready. Assign each analytical role below.');
        if (record.state === 'failed') setPreviewMessage('Preview failed. You can enter column names manually.');
      }).catch(error => {
        setPreviewMessage(`Preview unavailable: ${showError(error)}. Enter source column names manually.`);
        setPreviewRecord(null);
      });
    }, 1500);
    return () => clearInterval(timer);
  }, [previewRecord?.id, previewRecord?.state]);

  useEffect(() => {
    if (!importId) return;
    const timer = setInterval(() => {
      void refresh().then(next => {
        const entry = next?.imports.find(item => item.id === importId);
        if (!entry || pending(entry.state)) return;
        setImportId(null);
        if (entry.state === 'validated' && entry.snapshot_id) {
          setSnapshotId(entry.snapshot_id);
          const accepted = next?.snapshots.find(item => item.id === entry.snapshot_id);
          setContext(defaultContext(accepted));
          setNotice('Mapped snapshot validated. Select two bound definitions to compare.');
        } else setError(entry.error?.message || `Mapped upload ended in ${entry.state}.`);
      }).catch(error => setError(showError(error)));
    }, 1500);
    return () => clearInterval(timer);
  }, [importId, refresh]);

  useEffect(() => {
    if (!activeRun || !pending(activeRun.state)) return;
    const timer = setInterval(() => {
      void api.run(activeRun.id).then(run => {
        setActiveRun(run);
        if (!pending(run.state)) void refresh();
      }).catch(error => setError(showError(error)));
    }, 1500);
    return () => clearInterval(timer);
  }, [activeRun?.id, activeRun?.state, refresh]);

  async function perform(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await work(); } catch (error) { setError(showError(error)); } finally { setBusy(false); }
  }

  function selectContract(id: string) {
    setContractId(id); setMappingId(''); setSnapshotId(''); setLeftId(''); setRightId(''); setContext(null); setOpenCase(null); setActiveRun(null);
  }

  function selectMapping(id: string) {
    setMappingId(id); setSnapshotId(''); setLeftId(''); setRightId(''); setContext(null); setTimeColumn(''); setOpenCase(null); setActiveRun(null);
  }

  function chooseFile(next: File | null) {
    setFile(next); setPreviewRecord(null); setPreviewMessage('');
    setSourceColumns({});
    if (next && !snapshotName) setSnapshotName(next.name.replace(/\.(csv|parquet)$/i, ''));
  }

  function approveContract(event: FormEvent) {
    event.preventDefault();
    void perform(async () => {
      const approved = await api.post<DatasetContract>('/dataset-contracts', {version: 2, ...draft, row_key: 'order_id', entity_key: 'customer_id', decimal_scale: 2, schema});
      await refresh(); selectContract(approved.id);
      setNotice(`Contract v${approved.version_number} approved · ${shortHash(approved.contract_hash)}. Map each source column next.`);
    });
  }

  function startPreview() {
    if (!file) { setError('Choose a CSV or Parquet extract before requesting a preview.'); return; }
    if (data.mode !== 'isolated') { setPreviewMessage('Preview requires local isolated mode. Enter column names manually for now.'); return; }
    void perform(async () => {
      const form = new FormData(); form.append('file', file);
      const record = await api.previewDataset(form);
      setPreviewRecord(record);
      setPreviewMessage(pending(record.state) ? 'Preview queued in the local runner.' : '');
    });
  }

  function approveMapping(event: FormEvent) {
    event.preventDefault();
    const problem = mappingIssue(sourceColumns, preview?.headers);
    if (problem) { setError(problem); return; }
    if (!contract) { setError('Approve or select a dataset contract first.'); return; }
    void perform(async () => {
      const approved = await api.post<ColumnMapping>('/column-mappings', {contract_id: contract.id, columns: sourceColumns});
      await refresh(); selectMapping(approved.id);
      setNotice(`Mapping v${approved.version_number} approved · ${shortHash(approved.mapping_hash)}. The source names are now frozen.`);
    });
  }

  function uploadMapped(event: FormEvent) {
    event.preventDefault();
    if (!file || !mapping || !contract) { setError('Choose an extract and approved mapping first.'); return; }
    const policyProblem = sourcePolicyIssue(file.name, contract.null_policy);
    if (policyProblem) { setError(policyProblem); return; }
    void perform(async () => {
      const form = new FormData(); form.append('file', file); form.append('mapping_id', mapping.id); form.append('name', snapshotName.trim() || file.name);
      const result = await api.uploadMapped(form);
      await refresh();
      if ('state' in result) {
        const queued = result as SnapshotImport;
        if (queued.state === 'validated' && queued.snapshot_id) { setSnapshotId(queued.snapshot_id); setNotice('Existing validated mapped snapshot reused.'); }
        else { setImportId(queued.id); setNotice('Mapped extract queued for isolated validation.'); }
      } else {
        setSnapshotId(result.id); setContext(defaultContext(result)); setNotice('Existing validated mapped snapshot reused.');
      }
    });
  }

  function importDefinition(event: FormEvent) {
    event.preventDefault();
    if (!mapping) { setError('Approve a mapping before binding source SQL.'); return; }
    void perform(async () => {
      const body = {name: definitionName.trim() || mappedDefinitions.find(item => item.id === definitionId)?.name || 'Mapped metric', source_label: sourceLabel.trim() || 'Extract SQL', owner_label: ownerLabel.trim() || 'Local analyst', raw_sql: sql, time_column: timeColumn || mapping.columns.order_date, mapping_id: mapping.id};
      const path = definitionId ? `/definitions/${definitionId}/versions` : '/definitions';
      const imported = await api.post<Definition | Version>(path, body);
      await refresh();
      const version = 'versions' in imported ? imported.versions.at(-1) : imported;
      if (version?.support_state === 'executable') {
        if (!leftId) setLeftId(version.id); else setRightId(version.id);
        setNotice(`Source SQL bound to mapping ${shortHash(mapping.mapping_hash)}. Definition v${version.version_number} is executable.`);
        setSql('');
      } else setNotice(`Definition saved as ${version?.support_state || 'unknown'}: ${version?.support_reason || 'Provide the missing context before comparison.'}`);
    });
  }

  function useStarterQuery(kind: 'gross' | 'after_refunds') {
    if (!mapping) return;
    if (kind === 'gross') {
      setDefinitionName('Gross order amount');
      setTimeColumn(mapping.columns.order_date);
      setSql(`SELECT SUM(${quoteColumn(mapping.columns.order_amount)}) FROM orders`);
    } else {
      setDefinitionName('Amount after refunds');
      setTimeColumn(mapping.columns.order_date);
      setSql(`SELECT SUM(${quoteColumn(mapping.columns.order_amount)} - COALESCE(${quoteColumn(mapping.columns.refund_amount)}, 0)) FROM orders`);
    }
    setDefinitionId('');
  }

  function startComparison(event: FormEvent) {
    event.preventDefault();
    if (compareProblem) { setError(compareProblem); return; }
    void perform(async () => {
      const body = {baseline_version_id: leftId, target_version_id: rightId, snapshot_id: snapshot!.id, context: selectedContext, relationship};
      await api.post('/comparisons/validate', body);
      const run = await api.post<Run>('/comparisons', body, {'Idempotency-Key': crypto.randomUUID()});
      setActiveRun(run); setOpenCase(null); await refresh();
      setNotice('Comparison accepted. The run will update here as the local worker completes it.');
    });
  }

  function saveCase(event: FormEvent) {
    event.preventDefault();
    if (!activeRun || !['succeeded', 'partial'].includes(activeRun.state)) { setError('Wait for a completed comparison before saving a decision.'); return; }
    void perform(async () => {
      const saved = await api.post<{id: string}>('/investigations', {title: caseTitle, run_id: activeRun.id, snapshot_id: activeRun.snapshot_id, decision, actor_label: actor});
      await refresh(); setCaseTitle(''); setDecision('');
      const detail = await api.investigation(saved.id); setOpenCase(detail);
      setNotice('Local investigation and decision saved with pinned versions, snapshot, and context.');
    });
  }

  function reopenCase(id: string) {
    void perform(async () => {
      const detail = await api.investigation(id);
      setOpenCase(detail); setActiveRun(detail.run);
      const matchingMapping = mappings.find(item => item.id === detail.baseline_version.mapping_id);
      if (matchingMapping) { setContractId(matchingMapping.contract_id); setMappingId(matchingMapping.id); }
      setSnapshotId(detail.snapshot_id); setLeftId(detail.baseline_version_id); setRightId(detail.target_version_id);
      setContext(detail.context); setRelationship(detail.relationship);
      setNotice('Opened the pinned investigation. Replay uses these saved inputs.');
    });
  }

  function replayCase() {
    if (!openCase) return;
    void perform(async () => {
      const run = await api.post<Run>(`/investigations/${openCase.id}/replays`, {}, {'Idempotency-Key': crypto.randomUUID()});
      setActiveRun(run); await refresh();
      setNotice('Pinned investigation replay queued. The original saved decision remains available.');
    });
  }

  return <div className="v2-page">
    <div className="page-heading"><div><div className="eyebrow">LOCAL REAL DATA <span className="eyebrow-line"/></div><h1>Guided investigation</h1><p>Approve one extract contract and mapping, compare bound SQL on a fixed snapshot, then save a replayable case.</p></div><button className="button secondary" onClick={() => void refresh()}><RefreshCw size={16}/> Refresh</button></div>
    {data.mode !== 'isolated' && <Status tone="warn">Mapped extract validation and replay require local isolated mode. You can prepare contract and mapping details here.</Status>}
    {!data.runner_available && data.mode === 'isolated' && <Status tone="warn">Local worker unavailable. Queued previews, imports, and comparisons will continue when it starts.</Status>}
    {error && <Status tone="warn">{error}</Status>}{notice && <Status tone="good">{notice}</Status>}

    <Step number="01" title="Declare the dataset contract" detail="Fix the business scope and types before accepting source data. A change creates a new version and hash.">
      <div className="v2-grid two"><form className="form-stack" onSubmit={approveContract}><label>Contract name<input required maxLength={160} value={draft.name} onChange={event => setDraft({...draft, name: event.target.value})} placeholder="August sales extract"/></label><div className="form-grid two"><label>Reporting currency<input required maxLength={3} pattern="[A-Z]{3}" value={draft.currency} onChange={event => setDraft({...draft, currency: event.target.value.toUpperCase()})}/></label><label>Timezone<input required value={draft.timezone} onChange={event => setDraft({...draft, timezone: event.target.value})}/></label></div><div className="form-grid two"><label>Scope<input required value={draft.scope} onChange={event => setDraft({...draft, scope: event.target.value})}/></label><label>Grain<input required value={draft.grain} onChange={event => setDraft({...draft, grain: event.target.value})}/></label></div><div className="form-grid two"><label>Extract cutoff<input required type="date" value={draft.cutoff_at} onChange={event => setDraft({...draft, cutoff_at: event.target.value})}/></label><label>Source NULL policy<select value={draft.null_policy} onChange={event => setDraft({...draft, null_policy: event.target.value as DatasetContract['null_policy']})}><option value="blank_nullable">CSV · blank for nullable fields</option><option value="literal_NULL">CSV · literal NULL</option><option value="native_null">Parquet · native NULL</option></select></label></div><button className="button primary" disabled={busy}>Approve contract <ArrowRight size={15}/></button></form><div className="v2-side"><label>Approved contract<select value={selectedContractId} onChange={event => selectContract(event.target.value)}><option value="">Select a contract</option>{contracts.map(item => <option key={item.id} value={item.id}>{item.name} · v{item.version_number}</option>)}</select></label>{contract ? <><p><strong>Identity</strong> {shortHash(contract.contract_hash)}</p><p><strong>Scope</strong> {contract.scope} · {contract.grain}</p><p><strong>Policy</strong> {contract.currency} · {contract.timezone} · {contract.null_policy}</p><div className="v2-role-list">{roles.map(([role, label, kind]) => <div key={role}><span>{label}</span><code>{role} · {kind}</code></div>)}</div></> : <Status tone="warn">Approve or select a contract before mapping columns.</Status>}</div></div>
    </Step>

    <Step number="02" title="Preview and map source columns" detail="Inspect source names, then explicitly assign each analytical role to one distinct column.">
      <label className="upload-zone"><UploadCloud size={27}/><strong>{file?.name || 'Choose a flat CSV or Parquet extract'}</strong><span>{file ? `${Math.round(file.size / 1024)} KB selected` : 'Local CSV or Parquet, up to 25 MiB'}</span><input type="file" accept=".csv,.parquet,text/csv,application/vnd.apache.parquet" onChange={event => chooseFile(event.target.files?.[0] || null)}/></label>
      <div className="v2-actions"><button className="button secondary" type="button" disabled={!file || busy || data.mode !== 'isolated'} onClick={startPreview}>Preview columns</button><span>Preview runs locally. You can type source column names when preview is unavailable.</span></div>
      {previewMessage && <Status>{previewMessage}</Status>}{previewRecord?.state === 'failed' && <Status tone="warn">{previewRecord.error?.message || 'Preview failed. Check the extract or enter source column names manually.'}</Status>}
      {preview && <div className="v2-preview"><strong>{preview.headers.length} source columns · SHA-256 {shortHash(preview.source_hash)}</strong><div className="v2-preview-columns">{preview.headers.map(column => <code key={column}>{column}{(preview.declared_types[column] || preview.observed_types[column]) && ` · ${preview.declared_types[column] || preview.observed_types[column]}`}</code>)}</div>{preview.sample_rows?.length > 0 && <div className="table-scroll"><table><thead><tr>{preview.headers.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{preview.sample_rows.slice(0, 5).map((row, index) => <tr key={index}>{preview.headers.map(column => <td key={column}>{row[column] ?? 'NULL'}</td>)}</tr>)}</tbody></table></div>}</div>}
      <div className="v2-grid two v2-map-grid"><form className="form-stack" onSubmit={approveMapping}><div className="v2-map-fields">{roles.map(([role, label]) => <label key={role}>{label} <code>{role}</code><input required list={preview ? `v2-columns-${role}` : undefined} value={sourceColumns[role] || ''} onChange={event => setSourceColumns({...sourceColumns, [role]: event.target.value})} placeholder={`Source column for ${role}`}/>{preview && <datalist id={`v2-columns-${role}`}>{preview.headers.map(column => <option key={column} value={column}/>)}</datalist>}</label>)}</div><button className="button primary" disabled={!contract || busy}>Approve mapping <ArrowRight size={15}/></button></form><div className="v2-side"><label>Approved mapping<select value={selectedMappingId} onChange={event => selectMapping(event.target.value)} disabled={!contract}><option value="">Select a mapping</option>{contractMappings.map(item => <option key={item.id} value={item.id}>Mapping v{item.version_number} · {shortHash(item.mapping_hash)}</option>)}</select></label>{mapping ? <><p><strong>Contract</strong> {shortHash(mapping.contract_hash)}</p><p><strong>Mapping</strong> {shortHash(mapping.mapping_hash)}</p><div className="v2-role-list">{roles.map(([role]) => <div key={role}><code>{role}</code><span>← {mapping.columns[role]}</span></div>)}</div></> : <Status tone="warn">Map all nine analytical roles before accepting a snapshot.</Status>}</div></div>
    </Step>

    <Step number="03" title="Validate a mapped snapshot" detail="The reconciliation engine checks file layout, types, keys, currency, and the approved mapping.">
      <div className="v2-grid two"><form className="form-stack" onSubmit={uploadMapped}><label>Snapshot name<input required value={snapshotName} onChange={event => setSnapshotName(event.target.value)} placeholder="August orders snapshot"/></label><p className="v2-help">{file?.name || 'Choose an extract above.'} · {mapping ? `mapping ${shortHash(mapping.mapping_hash)}` : 'approved mapping required'}</p>{file && contract && sourcePolicyIssue(file.name, contract.null_policy) && <Status tone="warn">{sourcePolicyIssue(file.name, contract.null_policy)}</Status>}<button className="button primary" disabled={!file || !mapping || busy || data.mode !== 'isolated' || !!(file && contract && sourcePolicyIssue(file.name, contract.null_policy))}>Validate mapped extract <ArrowRight size={15}/></button>{importId && <Status>Validation queued. Import ID {importId}</Status>}</form><div className="v2-side"><label>Validated snapshot<select value={snapshot?.id || ''} onChange={event => {const selected = snapshots.find(item => item.id === event.target.value); setSnapshotId(event.target.value); setContext(defaultContext(selected)); setActiveRun(null);}} disabled={!snapshots.length}><option value="">Select a snapshot</option>{snapshots.map(item => <option key={item.id} value={item.id}>{item.name} · {item.row_count} rows</option>)}</select></label>{snapshot ? <><p><strong>Rows</strong> {snapshot.row_count}</p><p><strong>Manifest</strong> {shortHash(snapshot.manifest_hash)}</p><p><strong>Source</strong> {shortHash(snapshot.source_hash)}</p></> : <Status tone="warn">No validated snapshot matches this exact contract and mapping.</Status>}</div></div>
    </Step>

    <Step number="04" title="Bind source SQL" detail="Use source column names from the approved mapping. The binder stores both original SQL and canonical SQL.">
      <div className="v2-grid two"><form className="form-stack" onSubmit={importDefinition}><label>Definition action<select value={definitionId} onChange={event => setDefinitionId(event.target.value)}><option value="">Create a new definition</option>{mappedDefinitions.map(item => <option key={item.id} value={item.id}>Add version to {item.name}</option>)}</select></label><label>Definition name<input required value={definitionName} onChange={event => setDefinitionName(event.target.value)} placeholder="Net sales"/></label><div className="form-grid two"><label>Source label<input value={sourceLabel} onChange={event => setSourceLabel(event.target.value)}/></label><label>Owner label<input value={ownerLabel} onChange={event => setOwnerLabel(event.target.value)}/></label></div><label>Business date column<select value={timeColumn || mapping?.columns.order_date || ''} onChange={event => setTimeColumn(event.target.value)} disabled={!mapping}><option value={mapping?.columns.order_date || ''}>{mapping?.columns.order_date || 'Order date'}</option>{mapping?.columns.payment_date !== mapping?.columns.order_date && <option value={mapping?.columns.payment_date || ''}>{mapping?.columns.payment_date || 'Payment date'}</option>}</select></label><div className="v2-actions"><button className="button secondary small" type="button" disabled={!mapping} onClick={() => useStarterQuery('gross')}>Use gross amount query</button><button className="button secondary small" type="button" disabled={!mapping} onClick={() => useStarterQuery('after_refunds')}>Use after refunds query</button></div><label>Source SQL<textarea required rows={8} value={sql} onChange={event => setSql(event.target.value)} spellCheck={false} placeholder={mapping ? `SELECT SUM(${mapping.columns.order_amount}) FROM orders` : 'SELECT SUM(source_amount) FROM orders'}/></label><button className="button primary" disabled={!mapping || busy}>Bind SQL <ArrowRight size={15}/></button></form><div className="v2-side"><strong>Bound versions for this mapping</strong>{mappedVersions.length ? <div className="v2-version-list">{mappedVersions.map(({definition, version}) => <div key={version.id}><strong>{definition.name} · v{version.version_number}</strong><span className={`tag ${version.support_state === 'executable' ? 'same' : 'changed'}`}>{version.support_state.replaceAll('_', ' ')}</span>{version.support_reason && <small>{version.support_reason}</small>}{version.bound_sql && <code>{version.bound_sql}</code>}</div>)}</div> : <Status tone="warn">No source SQL is bound to this mapping yet.</Status>}</div></div>
    </Step>

    <Step number="05" title="Compare on the frozen snapshot" detail="Both versions must use this snapshot's exact contract and mapping, with the same aggregate family.">
      <form className="form-stack" onSubmit={startComparison}><div className="form-grid two"><label>Baseline version<select value={leftId} onChange={event => setLeftId(event.target.value)}><option value="">Choose version</option>{versions.map(({definition, version}) => <option key={version.id} value={version.id}>{definition.name} · v{version.version_number}</option>)}</select></label><label>Target version<select value={rightId} onChange={event => setRightId(event.target.value)}><option value="">Choose version</option>{versions.map(({definition, version}) => <option key={version.id} value={version.id}>{definition.name} · v{version.version_number}</option>)}</select></label></div><div className="form-grid two"><label>Start date<input type="date" required value={selectedContext.start} onChange={event => setContext({...selectedContext, start: event.target.value})}/></label><label>End date (exclusive)<input type="date" required value={selectedContext.end_exclusive} onChange={event => setContext({...selectedContext, end_exclusive: event.target.value})}/></label></div><div className="form-grid two"><label>Relationship<select value={relationship} onChange={event => setRelationship(event.target.value)}><option value="candidate">Candidate</option><option value="expected_agreement">Expected agreement</option><option value="intentional_variant">Intentional variant</option><option value="unrelated">Unrelated</option></select></label><label>Unit<select value={selectedContext.unit} onChange={event => setContext({...selectedContext, unit: event.target.value as Context['unit']})}><option value="currency">Currency</option><option value="count">Count</option></select></label></div><p className="v2-help">Context fixed by snapshot: {selectedContext.currency} · {selectedContext.timezone} · {selectedContext.grain} · {selectedContext.scope}</p>{compareProblem && <Status tone="warn">{compareProblem}</Status>}<button className="button primary" disabled={!!compareProblem || busy || data.mode !== 'isolated'}>Run comparison <GitCompareArrows size={15}/></button></form>
      {activeRun && <div className="v2-result"><div className="v2-result-heading"><div><strong>Run {activeRun.id}</strong><span className={`tag ${['succeeded', 'partial'].includes(activeRun.state) ? 'same' : 'changed'}`}>{activeRun.state.replaceAll('_', ' ')}</span></div><button className="button secondary small" onClick={() => onOpenRun(activeRun)}>Open full evidence <ArrowRight size={14}/></button></div>{activeRun.error && <Status tone="warn">{activeRun.error.message || activeRun.error.code || 'Comparison failed.'}</Status>}{activeRun.result ? <><div className="v2-totals"><div><span>Reproduced baseline</span><strong>{compact(activeRun.result.baseline_value, activeRun.result.currency)}</strong></div><div><span>Reproduced target</span><strong>{compact(activeRun.result.target_value, activeRun.result.currency)}</strong></div><div><span>Reproduced target minus baseline</span><strong>{compact(activeRun.result.delta_target_minus_baseline, activeRun.result.currency)}</strong></div></div><p className="v2-help">{activeRun.result.definition_status} · {activeRun.result.value_status} · {activeRun.result.explanation_status}</p>{activeRun.result.limitations?.length > 0 && <Status tone="warn">{activeRun.result.limitations.join(' · ')}</Status>}</> : pending(activeRun.state) ? <Status>Local worker is processing this run.</Status> : null}</div>}
    </Step>

    <Step number="06" title="Save and replay the investigation" detail="Record a local decision with pinned inputs. Reopen it later without substituting newer versions.">
      <div className="v2-grid two"><form className="form-stack" onSubmit={saveCase}><label>Case title<input required maxLength={160} value={caseTitle} onChange={event => setCaseTitle(event.target.value)} placeholder="August refund variance"/></label><label>Decision and rationale<textarea required maxLength={2000} rows={5} value={decision} onChange={event => setDecision(event.target.value)} placeholder="What did the evidence support, and what remains open?"/></label><label>Recorded by<input required maxLength={160} value={actor} onChange={event => setActor(event.target.value)} placeholder="Analyst label"/></label><button className="button primary" disabled={!activeRun || !['succeeded', 'partial'].includes(activeRun.state) || busy}>Save local case <Check size={15}/></button></form><div className="v2-side"><strong>Saved investigations</strong>{cases.length ? <div className="v2-case-list">{cases.map(item => <button key={item.id} type="button" onClick={() => reopenCase(item.id)}><strong>{item.title}</strong><span>{item.actor_label} · {new Date(item.created_at).toLocaleString()}</span><small>{versionName(data.definitions, item.baseline_version_id)} → {versionName(data.definitions, item.target_version_id)}</small></button>)}</div> : <Status>No saved local investigations yet.</Status>}</div></div>{openCase && <div className="v2-open-case"><div><h3>{openCase.title}</h3><p>{openCase.decision}</p><small>{openCase.actor_label} · {openCase.review_status.replaceAll('_', ' ')}</small></div><div className="v2-case-meta"><span>Snapshot {shortHash(openCase.snapshot.manifest_hash)}</span><span>Baseline {versionName(data.definitions, openCase.baseline_version_id)}</span><span>Target {versionName(data.definitions, openCase.target_version_id)}</span><span>Period {openCase.context.start} to {openCase.context.end_exclusive} (exclusive)</span></div><button className="button secondary" disabled={!openCase.replay_available || busy || data.mode !== 'isolated'} onClick={replayCase}><RefreshCw size={15}/> Replay pinned case</button>{!openCase.replay_available && <Status tone="warn">The saved result remains viewable, but its snapshot artifact is missing, so replay is unavailable.</Status>}</div>}
    </Step>
    <Status>Choose your own local extract and review each definition before saving a decision. Source files stay on this machine.</Status>
  </div>;
}
