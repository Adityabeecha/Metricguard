import { useEffect, useState } from 'react';
import { api, type Definition, type MetricBinding, type MetricContractCheck, type MetricContractCheckRow,
  type MetricContractExpectationsV1, type MetricContractHistory, type MetricContractRevision, type MetricItem,
  type RelationalContract, type RelationalGovernanceExpectations, type Snapshot, type Version } from '../api';
import './metricContracts.css';
import { readDraft, writeDraft } from '../identity';

type Props = { projectId: string; metric: MetricItem; definitions: Definition[]; bindings: MetricBinding[]; snapshots: Snapshot[];
  relationalContracts?: RelationalContract[] };
type FieldType = 'string' | 'boolean' | 'date' | 'timestamp' | 'decimal';
const dimensionTypes = ['string', 'boolean', 'date', 'timestamp', 'decimal'] as const;
const initialExpectations = (): MetricContractExpectationsV1 => ({
  schema_version: 1, owner_label: '', meaning: '', unit_label: '', grain: '', expected_dimensions: [],
  source_expectations: { mode: 'flat_snapshot', source_label: null, contract_hash: null },
  materiality_policy: { absolute_threshold: null, relative_threshold: null },
});
const initialRelational = (contract: RelationalContract | undefined, join: 'inner' | 'left' | undefined): RelationalGovernanceExpectations => ({
  schema_version: 2, owner_label: '', meaning: '', unit_label: '',
  fact: { table: contract?.fact.table || '', key: contract?.fact.key || '', grain: '' },
  dimension: { table: contract?.dimension.table || '', key: contract?.dimension.key || '', grain: '' },
  relationship: { fact_foreign_key: contract?.relationship.fact_foreign_key || '', dimension_key: contract?.dimension.key || '',
    cardinality: 'many_to_one', allowed_join_kinds: join ? [join] : ['inner', 'left'], missing_dimension_policy: 'preserve_and_report' },
  expected_fields: [],
  source_expectations: { mode: 'relational_snapshot', execution_policy: 'relational_snapshot_v1', source_label: null, contract_hash: null },
  materiality_policy: { absolute_threshold: null, relative_threshold: null },
});
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const display = (value: unknown) => value === null || value === undefined ? 'Unavailable'
  : typeof value === 'object' ? JSON.stringify(value) : String(value);
const checkLabel = (status: MetricContractCheck['status']) => ({
  mismatch: 'Expectation mismatch', inconclusive: 'Some expectations remain unverified', metadata_match: 'Registered metadata matches',
})[status];
const categoryLabels: Record<string, string> = {
  identity: 'Metric identity and pinned baseline', policy: 'Source mode, dataset identity and execution policy',
  structure: 'Declared relational structure versus frozen contract', declaration: 'Declared unit and grain (not proven)',
  join_proof: 'Frozen structural join proof (observed at import)',
};
type VersionMeta = Version & { contract_id?: string | null; execution_policy?: string | null };
const isRelationalVersion = (v: VersionMeta | undefined) =>
  !!v && (v.execution_policy === 'relational_snapshot_v1' || (v.ir as { mode?: string } | null)?.mode === 'relational_snapshot');
const tableColumns = (contract: RelationalContract, table: string) => {
  if (table === contract.fact.table) return [...new Set([contract.fact.key, contract.relationship.fact_foreign_key, contract.measure.column, ...contract.fact.categories])];
  if (table === contract.dimension.table) return [...new Set([contract.dimension.key, ...contract.dimension.categories])];
  return [];
};

function CheckTable({ rows }: { rows: MetricContractCheckRow[] }) {
  return <div className="contract-table-wrap"><table><thead><tr><th>Expectation</th><th>Expected</th><th>Observed</th><th>Result</th></tr></thead>
    <tbody>{rows.map((c, i) => <tr key={`${c.field}-${i}`}><td>{c.field}{c.note && <small>{c.note}</small>}</td><td>{display(c.expected)}</td><td>{display(c.observed)}</td><td>{c.status}</td></tr>)}</tbody></table></div>;
}

function RevisionSummary({ revision }: { revision: MetricContractRevision }) {
  const e = revision.expectations;
  const common = <><dt>Owner</dt><dd>{e.owner_label}</dd><dt>Meaning</dt><dd>{e.meaning}</dd><dt>Unit</dt><dd>{e.unit_label}</dd></>;
  const tail = <><dt>Dataset hash constraint</dt><dd className="mono-cell">{e.source_expectations.contract_hash || 'None declared'}</dd>
    <dt>Materiality expectations</dt><dd>Absolute: {e.materiality_policy.absolute_threshold ?? 'Not declared'} · Relative: {e.materiality_policy.relative_threshold ?? 'Not declared'} (recorded metadata only)</dd>
    <dt>Approved implementation pin</dt><dd className="mono-cell">{revision.approved_definition.version_id}</dd>
    <dt>Policy</dt><dd>{revision.policy} · schema {revision.schema_version}</dd>
    <dt>Contract hash</dt><dd className="mono-cell">{revision.contract_hash}</dd></>;
  if (revision.policy === 'metric-governance-v2') {
    const r = revision.expectations;
    return <dl className="contract-summary">{common}
      <dt>Fact declaration</dt><dd>{r.fact.table} · key {r.fact.key} · grain (declared): {r.fact.grain}</dd>
      <dt>Dimension declaration</dt><dd>{r.dimension.table} · key {r.dimension.key} · grain (declared): {r.dimension.grain}</dd>
      <dt>Relationship</dt><dd>{r.fact.table}.{r.relationship.fact_foreign_key} → {r.dimension.table}.{r.relationship.dimension_key} · {r.relationship.cardinality} · joins: {r.relationship.allowed_join_kinds.join(', ')} · {r.relationship.missing_dimension_policy}</dd>
      <dt>Expected fields</dt><dd>{r.expected_fields.map(f => `${f.table}.${f.column}:${f.type}`).join(', ') || 'None declared'}</dd>
      <dt>Source expectations</dt><dd>{r.source_expectations.mode} · {r.source_expectations.execution_policy} · {r.source_expectations.source_label || 'No label constraint'}</dd>
      {tail}</dl>;
  }
  const v = revision.expectations;
  return <dl className="contract-summary">{common}<dt>Grain declaration</dt><dd>{v.grain}</dd>
    <dt>Expected columns</dt><dd>{v.expected_dimensions.map(d => `${d.name}:${d.type}`).join(', ') || 'None declared'}</dd>
    <dt>Source expectations</dt><dd>{v.source_expectations.mode} · {v.source_expectations.source_label || 'No label constraint'}</dd>{tail}</dl>;
}

function Report({ report }: { report: MetricContractCheck }) {
  const v2 = report.policy === 'metric-governance-v2' ? report : null;
  const groups = v2 ? Object.keys(categoryLabels).map(k => [k, report.checks.filter(c => c.category === k)] as const).filter(([, rows]) => rows.length) : [];
  return <div className={`contract-card contract-result ${report.status}`}>
    <h3>{checkLabel(report.status)}</h3>
    <p>Check: <span className="mono-cell">{report.id}</span> · Snapshot: <span className="mono-cell">{report.snapshot_id}</span> · Policy: {report.policy ?? 'metric-governance-v1'}{report.created_at && <> · Saved {new Date(report.created_at).toLocaleString()}</>}</p>
    {v2 ? <>
      <h4>Declared business semantics (not proven)</h4>
      <dl className="contract-summary"><dt>Owner</dt><dd>{v2.declarations.owner_label}</dd><dt>Meaning</dt><dd>{v2.declarations.meaning}</dd>
        <dt>Unit</dt><dd>{v2.declarations.unit_label}</dd><dt>Declared fact grain</dt><dd>{v2.declarations.fact_grain}</dd>
        <dt>Declared dimension grain</dt><dd>{v2.declarations.dimension_grain}</dd>
        <dt>Materiality (metadata)</dt><dd>Absolute: {v2.declarations.materiality_policy.absolute_threshold ?? 'Not declared'} · Relative: {v2.declarations.materiality_policy.relative_threshold ?? 'Not declared'}</dd></dl>
      <h4>Observed frozen structure</h4>
      <dl className="contract-summary"><dt>Fact</dt><dd>{v2.observed.fact ? `${v2.observed.fact.table} · key ${v2.observed.fact.key}` : 'Not evaluated'}</dd>
        <dt>Dimension</dt><dd>{v2.observed.dimension ? `${v2.observed.dimension.table} · key ${v2.observed.dimension.key}` : 'Not evaluated'}</dd>
        <dt>Selected join kind</dt><dd>{v2.observed.definition_join_kind ?? 'Not evaluated'}</dd>
        <dt>Join proof</dt><dd>{v2.observed.join_proof ? Object.entries(v2.observed.join_proof).map(([k, x]) => `${k}: ${String(x)}`).join(' · ') : 'Not evaluated'}</dd></dl>
      {groups.map(([k, rows]) => <div key={k}><h4>{categoryLabels[k]}</h4><CheckTable rows={rows} /></div>)}
      <h4>Assurance</h4>
      <ul>{Object.entries(v2.assurance).map(([k, a]) => <li key={k}><strong>{k.replace(/_/g, ' ')}</strong>: {a.status.replace(/_/g, ' ')}. {a.note}</li>)}</ul>
    </> : <CheckTable rows={report.checks} />}
    <ul>{report.limitations.map(l => <li key={l}>{l}</li>)}</ul><p className="mono-cell">Check hash: {report.report_hash}</p>
    <details><summary>Pinned provenance</summary><dl className="contract-summary">
      <dt>Contract revision</dt><dd className="mono-cell">{report.contract_revision_id}{v2 && ` (revision ${v2.contract_version_number}, ${report.contract_status})`}</dd>
      <dt>Contract hash</dt><dd className="mono-cell">{report.contract_hash}</dd>
      <dt>Definition version</dt><dd className="mono-cell">{report.definition_version_id}{v2?.definition_version_number != null && ` (v${v2.definition_version_number})`}</dd>
      {report.definition_raw_hash !== undefined && <><dt>Definition raw / IR hash</dt><dd className="mono-cell">{display(report.definition_raw_hash)} / {display(report.definition_ir_hash)}</dd></>}
      <dt>Snapshot manifest hash</dt><dd className="mono-cell">{display(report.snapshot_manifest_hash)}</dd>
      {v2 && <><dt>Dataset contract (snapshot / definition)</dt><dd className="mono-cell">{display(v2.snapshot_contract_hash)} / {display(v2.definition_contract_hash)}</dd>
        <dt>Execution policy (snapshot / definition)</dt><dd>{display(v2.snapshot_execution_policy)} / {display(v2.definition_execution_policy)}</dd>
        <dt>Approved baseline at check</dt><dd className="mono-cell">{v2.approved_baseline_version_id}</dd>
        <dt>Checked by</dt><dd>{display(v2.created_by)}</dd></>}
    </dl></details>
  </div>;
}

export function MetricContractsPanel({ projectId, metric, definitions, bindings, snapshots, relationalContracts = [] }: Props) {
  const draftKey = `metric-contract:${projectId}:${metric.id}`;
  const draft = readDraft<{ form: MetricContractExpectationsV1; relForm: RelationalGovernanceExpectations; dimensionText: string; editing: boolean; reason: string; snapshotId: string; versionId: string }>(draftKey);
  const [history, setHistory] = useState<MetricContractHistory | null>(null);
  const [revisionId, setRevisionId] = useState('');
  const [offset, setOffset] = useState(0);
  const [form, setForm] = useState<MetricContractExpectationsV1>(() => draft?.form ?? initialExpectations());
  const [relForm, setRelForm] = useState<RelationalGovernanceExpectations>(() => draft?.relForm ?? initialRelational(undefined, undefined));
  const [dimensionText, setDimensionText] = useState(draft?.dimensionText ?? '');
  const [editing, setEditing] = useState(draft?.editing ?? false);
  const [reason, setReason] = useState(draft?.reason ?? '');
  const [snapshotId, setSnapshotId] = useState(draft?.snapshotId ?? '');
  const [versionId, setVersionId] = useState(draft?.versionId ?? '');
  useEffect(() => { writeDraft(draftKey, { form, relForm, dimensionText, editing, reason, snapshotId, versionId }); },
    [draftKey, form, relForm, dimensionText, editing, reason, snapshotId, versionId]);
  const [report, setReport] = useState<MetricContractCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.listMetricContracts(projectId, metric.id, offset).then(result => {
      if (!active) return;
      setHistory(result);
      setRevisionId(previous => result.items.some(r => r.id === previous) ? previous : result.items[0]?.id || result.active_contract?.id || '');
    }).catch(e => { if (active) setError(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId, metric.id, offset]);

  const revision = history?.items.find(r => r.id === revisionId) ||
    (history?.active_contract?.id === revisionId ? history.active_contract : null);
  const boundIds = new Set(bindings.map(b => b.definition_id));
  if (metric.active_binding) boundIds.add(metric.active_binding.definition_id);
  const choices = definitions.filter(d => boundIds.has(d.id));
  const approvedId = metric.active_binding?.approved_version_id;
  const approvedVersion = choices.find(d => d.id === metric.active_binding?.definition_id)?.versions.find(v => v.id === approvedId) as VersionMeta | undefined;
  const relational = isRelationalVersion(approvedVersion);
  const registered = relational ? relationalContracts.find(c => c.id === approvedVersion?.contract_id) : undefined;
  const approvedJoin = (approvedVersion?.ir as { join?: 'inner' | 'left' } | null)?.join;
  const implementationLabel = relational ? 'Relational snapshot (relational_snapshot_v1)'
    : (approvedVersion?.ir as { mode?: string } | null)?.mode === 'flat_snapshot' ? 'Flat snapshot' : approvedVersion ? 'Retail' : 'Unknown';

  async function reload() {
    const result = await api.listMetricContracts(projectId, metric.id, offset);
    setHistory(result);
    return result;
  }

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  function startProposal(copy = false) {
    if (relational) {
      const source = copy && revision?.policy === 'metric-governance-v2' ? clone(revision.expectations) : initialRelational(registered, approvedJoin);
      setRelForm(source);
    } else {
      const expectations = copy && revision?.policy === 'metric-governance-v1' ? clone(revision.expectations) : initialExpectations();
      if (!(copy && revision?.policy === 'metric-governance-v1')) {
        expectations.source_expectations.mode = (approvedVersion?.ir as { mode?: string } | null)?.mode === 'flat_snapshot' ? 'flat_snapshot' : 'retail';
      }
      setForm(expectations);
      setDimensionText(expectations.expected_dimensions.map(d => `${d.name}:${d.type}`).join('\n'));
    }
    setEditing(true); setError(''); setNotice('');
  }

  async function propose(event: React.FormEvent) {
    event.preventDefault();
    await perform(async () => {
      if (!approvedId) throw new Error('Approve an implementation version first.');
      let expectations: MetricContractExpectationsV1 | RelationalGovernanceExpectations;
      if (relational) {
        if (!relForm.relationship.allowed_join_kinds.length) throw new Error('Allow at least one join kind.');
        expectations = { ...relForm, relationship: { ...relForm.relationship, dimension_key: relForm.dimension.key } };
      } else {
        const dimensions = dimensionText.split(/\r?\n/).filter(line => line.trim()).map(line => {
          const match = line.trim().match(/^([A-Za-z_][A-Za-z0-9_]{0,127}):(string|boolean|date|timestamp|decimal)$/);
          if (!match) throw new Error('Use one column:type per line, for example region:string or flag:boolean.');
          return { name: match[1], type: match[2] as FieldType };
        });
        expectations = { ...form, expected_dimensions: dimensions };
      }
      const proposed = await api.proposeMetricContract(projectId, metric.id, expectations, history?.version_token || null, approvedId);
      // Return to page one so the newly proposed revision is visible.
      const latest = await api.listMetricContracts(projectId, metric.id);
      setHistory(latest); setOffset(0); setRevisionId(proposed.id); setEditing(false); setReport(null);
      setNotice('Revision proposed. The approved contract and implementation baseline remain unchanged until an owner approves this revision.');
    });
  }

  function approve() {
    if (!revision || !history?.version_token) return;
    void perform(async () => {
      await api.approveMetricContract(projectId, metric.id, revision.id, history.version_token!, reason);
      await reload(); setReason(''); setNotice('Contract revision approved; the previous revision remains in history.');
    });
  }

  function check() {
    if (!revision || !snapshotId) return;
    void perform(async () => {
      const result = await api.checkMetricContract(projectId, metric.id, snapshotId, revision.id, versionId || approvedId || undefined);
      setReport(result); await reload();
    });
  }

  const setTable = (role: 'fact' | 'dimension', table: string) => {
    if (!registered) return;
    const cols = tableColumns(registered, table);
    const key = table === registered.fact.table ? registered.fact.key : table === registered.dimension.table ? registered.dimension.key : '';
    setRelForm(f => ({ ...f, [role]: { ...f[role], table, key: cols.includes(f[role].key) ? f[role].key : key },
      expected_fields: f.expected_fields.filter(x => x.table !== f[role].table) }));
  };
  const toggleField = (table: string, column: string, on: boolean) => setRelForm(f => ({ ...f,
    expected_fields: on ? [...f.expected_fields, { table, column, type: 'string' }] : f.expected_fields.filter(x => !(x.table === table && x.column === column)) }));
  const setFieldType = (table: string, column: string, type: FieldType) => setRelForm(f => ({ ...f,
    expected_fields: f.expected_fields.map(x => x.table === table && x.column === column ? { ...x, type } : x) }));
  const toggleJoin = (kind: 'inner' | 'left', on: boolean) => setRelForm(f => ({ ...f, relationship: { ...f.relationship,
    allowed_join_kinds: on ? [...new Set([...f.relationship.allowed_join_kinds, kind])].sort() as Array<'inner' | 'left'> : f.relationship.allowed_join_kinds.filter(k => k !== kind) } }));
  const registeredTables = registered ? [registered.fact.table, registered.dimension.table] : [];
  const categorical = registered ? [
    ...registered.fact.categories.map(c => ({ table: registered.fact.table, column: c })),
    ...registered.dimension.categories.map(c => ({ table: registered.dimension.table, column: c }))] : [];

  const sharedFields = (value: { owner_label: string; unit_label: string; meaning: string }, update: (patch: Partial<typeof value>) => void) => <>
    <label>Owner<input required maxLength={160} value={value.owner_label} onChange={e => update({ owner_label: e.target.value })} /></label>
    <label>Unit label<input required maxLength={200} placeholder="For example points, USD, or unknown currency" value={value.unit_label} onChange={e => update({ unit_label: e.target.value })} /></label>
    <label className="contract-wide">Business meaning<textarea required maxLength={600} value={value.meaning} onChange={e => update({ meaning: e.target.value })} /></label>
  </>;
  const materialityFields = (value: MetricContractExpectationsV1['materiality_policy'], update: (m: MetricContractExpectationsV1['materiality_policy']) => void) => <>
    <label>Absolute materiality threshold (optional)<input maxLength={128} value={value.absolute_threshold || ''} onChange={e => update({ ...value, absolute_threshold: e.target.value || null })} /></label>
    <label>Relative threshold (fraction; 0.01 = 1%)<input maxLength={128} value={value.relative_threshold || ''} onChange={e => update({ ...value, relative_threshold: e.target.value || null })} /></label>
  </>;

  return <div className="metric-contracts">
    <div className="metric-contract-heading">
      <div><h2>Metric contract</h2><p>Record what this metric is expected to mean, then review changes against those expectations.</p></div>
      <button className="button secondary" disabled={busy || loading} onClick={() => void perform(async () => { await reload(); })}>Reload history</button>
      <button className="button primary" disabled={busy || loading || editing || !approvedId || !history?.capabilities.can_propose || (relational && !registered)} onClick={() => startProposal()}>Propose revision</button>
    </div>
    {error && <div className="alert error" role="alert">{error} Your entered values have been retained.</div>}
    {notice && <div className="alert success" role="status">{notice}</div>}
    {loading && <p role="status">Loading contract history…</p>}
    {!approvedId && <p className="contract-caution">Approve an implementation version before creating its governance contract.</p>}
    {relational && !registered && <p className="contract-caution">The approved relational implementation's registered contract is not available in this workspace view. Reload the workspace before proposing.</p>}
    {history && !history.capabilities.can_propose && <p className="contract-caution">Workspace credentials are required to propose or check contracts. Approval requires a workspace owner. Use "Connect with API key" in the top bar with an issued key; capabilities shown here come from the server.</p>}
    {history && <p>Approved contract: <strong>{history.active_contract ? `Revision ${history.active_contract.version_number}` : 'None'}</strong>. Approved implementation: {implementationLabel}. Proposals do not approve themselves.</p>}

    {editing && <form className="contract-card contract-form" onSubmit={propose}>
      <h3>Propose {relational ? 'relational governance expectations (metric-governance-v2)' : 'governance expectations'}</h3>
      <p>Metric: {metric.display_name} · Importance: {metric.importance} · Approved implementation: <span className="mono-cell">{approvedId}</span></p>
      {relational && registered ? <fieldset className="contract-fieldset" disabled={busy}><div className="contract-fields">
        {sharedFields(relForm, patch => setRelForm(f => ({ ...f, ...patch })))}
        {(['fact', 'dimension'] as const).map(role => <div key={role} className="contract-wide contract-fields">
          <label>{role === 'fact' ? 'Fact table' : 'Dimension table'}<select value={relForm[role].table} onChange={e => setTable(role, e.target.value)}>{registeredTables.map(t => <option key={t} value={t}>{t}</option>)}</select></label>
          <label>{role === 'fact' ? 'Fact key' : 'Dimension key'}<select value={relForm[role].key} onChange={e => { const key = e.target.value; setRelForm(f => ({ ...f, [role]: { ...f[role], key }, ...(role === 'dimension' ? { relationship: { ...f.relationship, dimension_key: key } } : {}) })); }}>
            {tableColumns(registered, relForm[role].table).map(c => <option key={c} value={c}>{c}</option>)}</select></label>
          <label className="contract-wide">Declared {role} grain<input required maxLength={300} placeholder={`Registered: ${registered[role].grain}`} value={relForm[role].grain} onChange={e => { const grain = e.target.value; setRelForm(f => ({ ...f, [role]: { ...f[role], grain } })); }} /><span>Your declaration; it is compared as text, not proven.</span></label>
        </div>)}
        <label>Fact foreign key<select value={relForm.relationship.fact_foreign_key} onChange={e => { const fk = e.target.value; setRelForm(f => ({ ...f, relationship: { ...f.relationship, fact_foreign_key: fk } })); }}>
          {tableColumns(registered, relForm.fact.table).map(c => <option key={c} value={c}>{c}</option>)}</select><span>May equal the fact key.</span></label>
        <label>Relationship<input readOnly value={`many_to_one → ${relForm.dimension.table}.${relForm.dimension.key} · preserve_and_report`} /></label>
        <fieldset className="contract-wide"><legend>Allowed join kinds</legend>{(['inner', 'left'] as const).map(k => <label key={k}><input type="checkbox" checked={relForm.relationship.allowed_join_kinds.includes(k)} onChange={e => toggleJoin(k, e.target.checked)} /> {k.toUpperCase()}{approvedJoin === k && ' (approved implementation)'}</label>)}</fieldset>
        <fieldset className="contract-wide"><legend>Expected qualified fields (registered categorical fields; maximum 16)</legend>
          {categorical.length === 0 && <p>No categorical fields are registered.</p>}
          {categorical.map(({ table, column }) => { const chosen = relForm.expected_fields.find(x => x.table === table && x.column === column);
            const allowed = table === relForm.fact.table || table === relForm.dimension.table;
            return <div key={`${table}.${column}`} className="contract-history-row"><label><input type="checkbox" disabled={!allowed} checked={!!chosen} onChange={e => toggleField(table, column, e.target.checked)} /> <span className="mono-cell">{table}.{column}</span></label>
              {chosen && <select aria-label={`Type for ${table}.${column}`} value={chosen.type} onChange={e => setFieldType(table, column, e.target.value as FieldType)}>{dimensionTypes.map(t => <option key={t} value={t}>{t}</option>)}</select>}</div>; })}
        </fieldset>
        <label>Source label (optional)<input maxLength={160} value={relForm.source_expectations.source_label || ''} onChange={e => { const v = e.target.value || null; setRelForm(f => ({ ...f, source_expectations: { ...f.source_expectations, source_label: v } })); }} /></label>
        <label className="contract-wide">Dataset contract hash (optional)<input maxLength={64} placeholder={registered.contract_hash} value={relForm.source_expectations.contract_hash || ''} onChange={e => { const v = e.target.value || null; setRelForm(f => ({ ...f, source_expectations: { ...f.source_expectations, contract_hash: v } })); }} /></label>
        {materialityFields(relForm.materiality_policy, m => setRelForm(f => ({ ...f, materiality_policy: m })))}
      </div></fieldset> : <fieldset className="contract-fieldset" disabled={busy}><div className="contract-fields">
        {sharedFields(form, patch => setForm(f => ({ ...f, ...patch })))}
        <label className="contract-wide">Expected grain<input required maxLength={200} placeholder="Describe what one source row represents" value={form.grain} onChange={e => setForm({ ...form, grain: e.target.value })} /></label>
        <label>Source mode<select value={form.source_expectations.mode} onChange={e => setForm({ ...form, source_expectations: { ...form.source_expectations, mode: e.target.value as 'retail' | 'flat_snapshot' } })}><option value="flat_snapshot">Flat snapshot</option><option value="retail">Retail</option></select></label>
        <label>Source label (optional)<input maxLength={160} value={form.source_expectations.source_label || ''} onChange={e => setForm({ ...form, source_expectations: { ...form.source_expectations, source_label: e.target.value || null } })} /></label>
        <label className="contract-wide">Dataset contract hash (optional)<input maxLength={64} value={form.source_expectations.contract_hash || ''} onChange={e => setForm({ ...form, source_expectations: { ...form.source_expectations, contract_hash: e.target.value || null } })} /></label>
        <label className="contract-wide">Expected columns<textarea placeholder={'region:string\nflag:boolean'} value={dimensionText} onChange={e => setDimensionText(e.target.value)} /><span>One column:type per line. Types: {dimensionTypes.join(', ')}. Maximum 24 columns.</span></label>
        {materialityFields(form.materiality_policy, m => setForm({ ...form, materiality_policy: m }))}
      </div></fieldset>}
      <p className="contract-caution">These are declared governance expectations. Meaning and business grain require human review. Thresholds do not replace CI policy; unknown currency stays unknown.</p>
      <div className="contract-actions"><button className="button secondary" type="button" disabled={busy} onClick={() => setEditing(false)}>Cancel</button><button className="button primary" disabled={busy || !history?.capabilities.can_propose}>Save proposal</button></div>
    </form>}

    {history && history.items.length > 0 && <div className="contract-card">
      <label className="contract-selection">Review revision<select disabled={busy || loading || editing} value={revisionId} onChange={e => { setRevisionId(e.target.value); setReport(null); setReason(''); }}>
        {history.items.map(r => <option key={r.id} value={r.id}>Revision {r.version_number} · {r.policy === 'metric-governance-v2' ? 'relational' : 'v1'} · {r.status}</option>)}
      </select></label>
      <div className="contract-actions"><button className="button secondary" disabled={busy || loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Newer revisions</button><button className="button secondary" disabled={busy || loading || history.next_offset === null} onClick={() => setOffset(history.next_offset || 0)}>Older revisions</button></div>
      {revision && <>
        <RevisionSummary revision={revision} />
        <button className="button secondary" disabled={busy || editing || !history.capabilities.can_propose || (revision.policy === 'metric-governance-v2') !== relational || (relational && !registered)} onClick={() => startProposal(true)}>Use expectations in new revision</button>
        {revision.status === 'proposed' && <div className="contract-approval"><label>Approval reason<textarea disabled={busy} maxLength={1000} value={reason} onChange={e => setReason(e.target.value)} /></label><button className="button primary" disabled={busy || loading || editing || !reason.trim() || !history.capabilities.can_approve} onClick={approve}>Approve this revision</button><p>Owner approval records expectations; it does not establish that the data or business meaning is correct.</p></div>}
      </>}
    </div>}

    {revision && <div className="contract-card">
      <h3>Check registered metadata</h3><p>Compare this revision with a frozen snapshot and a bound implementation. This check runs no SQL.</p>
      <div className="contract-fields"><label>Snapshot<select disabled={busy} value={snapshotId} onChange={e => { setSnapshotId(e.target.value); setReport(null); }}><option value="">Select snapshot</option>{snapshots.map(s => <option key={s.id} value={s.id}>{s.name} · {s.id.slice(0, 8)}{(s as Snapshot & { execution_policy?: string }).execution_policy === 'relational_snapshot_v1' ? ' · relational' : ''}</option>)}</select></label>
        <label>Implementation<select disabled={busy} value={versionId} onChange={e => { setVersionId(e.target.value); setReport(null); }}><option value="">Current approved implementation</option>{choices.flatMap(d => d.versions.map(v => <option key={v.id} value={v.id}>{d.name} · v{v.version_number}</option>))}</select></label></div>
      <button className="button primary" disabled={busy || loading || !snapshotId || !history?.capabilities.can_check} onClick={check}>Save metadata check</button>
    </div>}
    {report && <Report report={report} />}
    {history && history.recent_checks.length > 0 && <div className="contract-card"><h3>Recent saved checks</h3><p>Latest 20 checks. Reopening a check shows its original pinned result.</p>{history.recent_checks.map(c => <div key={c.id} className="contract-history-row"><span>{new Date(c.created_at).toLocaleString()} · {checkLabel(c.status)} · {c.policy ?? 'metric-governance-v1'}</span><button className="button secondary" disabled={busy} onClick={() => void perform(async () => { setReport(await api.getMetricContractCheck(projectId, metric.id, c.id)); })}>Open saved check</button></div>)}</div>}
    {history && history.approvals.length > 0 && <div className="contract-card"><h3>Approval history</h3><p>Latest 100 approval events.</p>{history.approvals.map(a => <div key={a.id} className="contract-history-row"><span><strong>{a.reason}</strong><br />{a.approved_by} · {new Date(a.created_at).toLocaleString()}<br /><span className="mono-cell">{a.previous_contract_id || 'No prior contract'} → {a.contract_revision_id}</span></span></div>)}</div>}
  </div>;
}
