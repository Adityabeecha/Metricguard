import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, Plus, Save, Trash2 } from 'lucide-react';
import { api, type Bootstrap, type ColumnValues, type FlatContract, type Run, type Snapshot, type SnapshotChangeKind } from '../api';
import {
  amountColumns, buildFlatSql, defaultConditionColumn, describeCalculation, flatFilterColumns, formProblems, OP_LABEL, operatorsFor,
  supportsAnyMatch, supportsCount, type CalculationForm, type Filter,
  datasetTableName, flatSqlColumns,
} from '../comparisonBuilder';
import { SqlEditor } from './SqlEditor';
import { comparisonRequestKey } from '../guidedWorkflow';
import { classifyInvestigationError, type ClassifiedInvestigationError } from '../investigationUtils';
import { groupDigits } from '../resultSummary';
import { ConditionHelp, ConditionValue } from './ConditionFields';
import { CHANGE_LABEL, ResultReport } from './ResultReport';
import { categoryLabel, type CategoryKey } from './evidence/categories';
import { comparisonDatasetGroups } from '../comparisonSelection';
import { useRecordedBy } from '../useRecordedBy';

const pending = (state?: string) => state === 'queued' || state === 'running' || state === 'pending';
const KINDS: SnapshotChangeKind[] = ['added', 'removed', 'value_changed', 'entered_filter', 'left_filter', 'unchanged'];
/** Flat contract versions this comparison supports: plain totals and record counts. */
const SUPPORTED = new Set([3, 4, 5, 8]);

type Props = {
  data: Bootstrap;
  refresh: () => Promise<Bootstrap | null>;
  /** Called with the new case id once the comparison is saved as a case. */
  onSaved?: (caseId: string) => void;
  prefillDataset?: string;
};

/** A load in a picker: its label, when it was loaded, and its size. Loads of one dataset often share a name. */
export function loadLabel(s: Snapshot): string {
  const when = s.created_at ? new Date(s.created_at).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
  return `${s.name} · loaded ${when} · ${s.row_count.toLocaleString('en-US')} records`;
}

/** One calculation run on two data loads of the same dataset (e.g. September vs October). */
export function SnapshotChange({ data, refresh, onSaved, prefillDataset }: Props) {
  const sourceChoices = useMemo(() => data.snapshots.flatMap(snapshot => {
    const flat = (data.flat_contracts || []).find(c => SUPPORTED.has(c.version) && c.contract_hash === snapshot.contract_hash);
    return flat && snapshot.execution_policy !== 'relational_snapshot_v1' ? [{ snapshot, flat }] : [];
  }), [data.flat_contracts, data.snapshots]);
  const groups = useMemo(() => comparisonDatasetGroups(sourceChoices).filter(g => g.loads.length >= 2).map(g => ({
    contract: g.latest.flat, snapshots: g.loads.map(c => c.snapshot).reverse(),
  })), [sourceChoices]);
  // 'loads': two points in time of one dataset; 'sources': two separately uploaded datasets for the same period.
  const [kind, setKind] = useState<'loads' | 'sources'>('loads');
  const sources = kind === 'sources';
  const [contractId, setContractId] = useState('');
  const [baselineId, setBaselineId] = useState('');
  const [targetId, setTargetId] = useState('');
  const sourceA = sources ? sourceChoices.find(c => c.snapshot.id === baselineId) : undefined;
  const group = sources
    ? (sourceA ? { contract: sourceA.flat, snapshots: sourceChoices.map(c => c.snapshot) } : null)
    : groups.find(g => g.contract.id === contractId) || null;
  const [versionId, setVersionId] = useState('');
  const [mode, setMode] = useState<'form' | 'sql'>('form');
  const [form, setForm] = useState<CalculationForm>({ filters: [] });
  const [sql, setSql] = useState('');
  const [known, setKnown] = useState<Record<string, ColumnValues | null>>({});
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ClassifiedInvestigationError | null>(null);
  const [title, setTitle] = useState('');
  const [decision, setDecision] = useState('');
  const [actor, setActor] = useRecordedBy(data.project.id);
  const engineReady = data.mode === 'isolated' && data.runner_available;
  const contract = group?.contract;
  const columns = useMemo(() => contract ? flatFilterColumns(contract) : [], [contract]);

  useEffect(() => {
    const preferred = groups.find(g => g.snapshots.some(s => s.id === prefillDataset));
    setContractId(current => groups.some(g => g.contract.id === current) ? current : preferred?.contract.id || (groups.length === 1 ? groups[0].contract.id : ''));
  }, [groups, prefillDataset]);

  const saved = useMemo(() => group ? data.definitions.flatMap(d => d.versions
    .filter(v => v.contract_hash === group.contract.contract_hash && v.support_state === 'executable')
    .map(v => ({ id: v.id, label: `${d.name} · v${v.version_number}`, sql: v.raw_sql }))) : [], [data.definitions, group]);

  // Choosing a dataset defaults to its two most recent loads and a plain total.
  useEffect(() => {
    if (!group) return;
    const n = group.snapshots.length;
    setBaselineId(group.snapshots[n - 2].id); setTargetId(group.snapshots[n - 1].id);
    setVersionId(''); setMode('form'); setForm({ filters: [] }); setSql(defaultSql(group.contract)); setRun(null); setProblem(null);
  }, [contractId]);

  // Offer the real values of each filter column, read from the later load.
  useEffect(() => {
    setKnown({});
    if (!targetId) return;
    let active = true;
    (async () => {
      const ordered = [...columns].sort((a, b) => Number(a.type === 'date' || a.type === 'timestamp') - Number(b.type === 'date' || b.type === 'timestamp'));
      for (const c of ordered.slice(0, 40)) {
        const values = await api.columnValues(targetId, c.column).catch(() => null);
        if (!active) return;
        setKnown(k => ({ ...k, [c.id]: values }));
      }
    })();
    return () => { active = false; };
  }, [targetId, columns]);

  useEffect(() => {
    if (!run || !pending(run.state)) return;
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const next = await api.run(run.id);
        if (!active) return;
        setRun(next);
        if (!pending(next.state)) {
          void refresh();
          if (next.state === 'failed') setProblem(classifyInvestigationError(next.error || { code: 'RUNNER_FAILED', message: 'The comparison did not complete.' }));
        }
      } catch (e) { if (active) setProblem(classifyInvestigationError(e)); }
    }, 1500);
    return () => { active = false; clearTimeout(timer); };
  }, [run]);

  const amounts = contract ? amountColumns(contract) : [];
  const amountLabel = form.amountColumn || amounts[0] || contract?.measure || 'amount';
  const formSql = (): string => { try { return contract ? buildFlatSql(contract, form) : ''; } catch { return ''; } };
  const currentSql = versionId ? saved.find(s => s.id === versionId)?.sql || '' : mode === 'form' ? formSql() : sql;
  const description = mode === 'form' && !versionId ? describeCalculation(form, columns, amountLabel, false) : '';
  const formIssue = mode === 'form' && !versionId && contract ? formProblems(form, columns)[0] || (formSql() ? '' : 'Complete the conditions.') : '';

  const baseline = group?.snapshots.find(s => s.id === baselineId), target = group?.snapshots.find(s => s.id === targetId);
  const blocker = !engineReady ? 'The comparison engine is not running. Start it with scripts\\mg.ps1 start.'
    : !group ? (sources ? 'Choose source A.' : 'Choose a dataset with at least two loads.')
    : !baseline || !target ? (sources ? 'Choose both sources.' : 'Choose both loads.')
    : baseline.id === target.id ? (sources ? 'Choose two different datasets.' : 'Choose two different loads.')
    : formIssue || (!currentSql.trim() ? 'Choose a saved calculation or describe one.' : '');

  const update = (next: CalculationForm) => { setForm(next); setRun(null); };
  const setFilter = (i: number, patch: Partial<Filter>) => update({ ...form, filters: form.filters.map((f, j) => j === i ? { ...f, ...patch } : f) });

  async function compare() {
    if (blocker || !group || busy) return;
    setBusy(true); setProblem(null); setRun(null);
    try {
      let id = versionId;
      const text = currentSql.trim();
      if (!id) {
        const existing = saved.find(s => s.sql.trim() === text);
        if (existing) id = existing.id;
        else {
          const name = (description ? description.replace(/\.$/, '') : `Calculation: ${text}`).slice(0, 160);
          const def = await api.post<{ versions: Array<{ id: string; support_state: string; support_reason?: string | null }> }>(
            '/flat/definitions', { name, raw_sql: text, contract_id: group.contract.id });
          const version = def.versions.at(-1);
          if (!version || version.support_state !== 'executable') {
            setProblem(classifyInvestigationError({ code: 'UNSUPPORTED_SQL', message: version?.support_reason || 'This calculation is outside the supported grammar.' }));
            return;
          }
          id = version.id;
        }
      }
      const c = group.contract;
      const request = { version_id: id, baseline_snapshot_id: baselineId, target_snapshot_id: targetId,
        context: { mode: 'flat_snapshot', scope: c.scope, contract_hash: c.contract_hash, unit_label: c.unit_label, values_comparable: true },
        ...(sources ? { labels: 'sources' } : {}) };
      const key = await comparisonRequestKey(data.project.id, { snapshot_change: request });
      setRun(await api.post<Run>('/flat/snapshot-changes', request, { 'Idempotency-Key': key }));
      const calc = description ? description.replace(/\.$/, '') : saved.find(s => s.id === id)?.label || 'Calculation';
      setTitle(`${calc}: ${baseline?.name} → ${target?.name}`.slice(0, 160));
      await refresh();
    } catch (e) {
      setProblem(classifyInvestigationError(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveCase() {
    if (!run?.result || busy) return;
    setBusy(true); setProblem(null);
    try {
      try { localStorage.setItem('mg.actor', actor.trim()); } catch { /* per-browser convenience only */ }
      const created = await api.post<{ id: string }>('/investigations', { title: title.trim(), decision: decision.trim(), actor_label: actor.trim(), run_id: run.id, snapshot_id: run.snapshot_id });
      await refresh();
      onSaved?.(created.id);
    } catch (e) {
      setProblem(classifyInvestigationError(e));
    } finally {
      setBusy(false);
    }
  }

  const summary = run?.result?.change_summary;
  const unit = run?.result?.aggregate === 'count' ? '' : contract?.unit_label || '';
  const money = (v: string) => `${groupDigits(v).replace(/^-/, '−')}${unit ? ` ${unit}` : ''}`;
  const saveBlocker = !title.trim() ? 'Give the case a name.' : !decision.trim() ? 'Write your conclusion.' : !actor.trim() ? 'Enter who is recording this.' : '';
  return (
    <section className="guided-card comparison-loads" aria-labelledby="snapshot-change">
      <h2 id="snapshot-change">Same calculation, two data loads</h2>
      <p className="lead">{sources
        ? 'Run one calculation on two separately uploaded datasets for the same period (e.g. a Finance export and a Product export) and see which records exist in only one of them or differ in value.'
        : 'Run one calculation on an earlier and a later load of the same dataset and see which records were added, removed or changed.'} Records are matched by their ID column, and their changes add up exactly to the difference in the total.</p>
      {sourceChoices.length >= 2 && (
        <fieldset className="plain-fieldset kind-switch">
          <legend className="form-label">What are the two files?</legend>
          <label><input type="radio" name="load-kind" checked={!sources} onChange={() => { setKind('loads'); setRun(null); setProblem(null); setContractId(''); }} /> Two points in time (earlier → later)</label>
          <label><input type="radio" name="load-kind" checked={sources} onChange={() => {
            setKind('sources'); setRun(null); setProblem(null);
            setBaselineId(sourceChoices[0]?.snapshot.id || ''); setTargetId(sourceChoices[1]?.snapshot.id || '');
            setVersionId(''); setMode('form'); setForm({ filters: [] }); setSql(defaultSql(sourceChoices[0].flat));
          }} /> Two sources for the same period (A → B)</label>
        </fieldset>
      )}
      {sources ? (
        <div style={{ display: 'grid', gap: 12, marginTop: 4 }}>
          <div className="comparison-load-pair">
            <label>Source A
              <select value={baselineId} onChange={e => { setBaselineId(e.target.value); setVersionId(''); setRun(null); setProblem(null); }}>
                {sourceChoices.map(c => <option key={c.snapshot.id} value={c.snapshot.id}>{loadLabel(c.snapshot)}</option>)}
              </select>
            </label>
            <label>Source B
              <select value={targetId} onChange={e => { setTargetId(e.target.value); setRun(null); setProblem(null); }}>
                {sourceChoices.map(c => <option key={c.snapshot.id} value={c.snapshot.id}>{loadLabel(c.snapshot)}</option>)}
              </select>
            </label>
          </div>
          <p className="hint">The calculation is written for source A and run on both. Source B needs the same record ID column, the same amount (unit and decimals) and the filter columns you use; you'll be told plainly if anything differs.</p>
        </div>
      ) : groups.length === 0 ? (
        <div className="guided-explain">No dataset has two loads yet. On the Datasets page, open a dataset and choose <b>Add a newer load</b>; or upload a file with the same columns and settings and keep “Add this file as a new load” ticked.</div>
      ) : (
          <div className="dataset-list" role="group" aria-label="Datasets with several loads">
            {groups.map(g => (
              <button key={g.contract.id} type="button" className="dataset-row" aria-pressed={g.contract.id === contractId} onClick={() => setContractId(g.contract.id)}>
                <strong>{g.contract.name} <span className="example">· {g.snapshots.length} loads</span></strong>
                <span className="detail">Records matched by <code>{g.contract.row_key}</code></span>
              </button>
            ))}
          </div>
      )}
          {group && contract && (
            <div style={{ display: 'grid', gap: 12, marginTop: 12 }}>
              {!sources && <div className="comparison-load-pair">
                <label>Earlier load
                  <select value={baselineId} onChange={e => { setBaselineId(e.target.value); setRun(null); }}>
                    {group.snapshots.map(s => <option key={s.id} value={s.id}>{loadLabel(s)}</option>)}
                  </select>
                </label>
                <label>Later load
                  <select value={targetId} onChange={e => { setTargetId(e.target.value); setRun(null); }}>
                    {group.snapshots.map(s => <option key={s.id} value={s.id}>{loadLabel(s)}</option>)}
                  </select>
                </label>
              </div>}
              {saved.length > 0 && (
                <label>Start from a saved calculation (optional)
                  <select value={versionId} onChange={e => { setVersionId(e.target.value); setRun(null); }}>
                    <option value="">— Describe the calculation below —</option>
                    {saved.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                  </select>
                </label>
              )}
              {!versionId && (
                <div className="calc-card">
                  <div className="mode-switch" role="group" aria-label="Calculation editor">
                    <button type="button" aria-pressed={mode === 'form'} onClick={() => { setMode('form'); setRun(null); }}>Guided form</button>
                    <button type="button" aria-pressed={mode === 'sql'} onClick={() => { setSql(formSql() || sql); setMode('sql'); setRun(null); }}>SQL editor</button>
                  </div>
                  {mode === 'form' ? (
                    <>
                      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                        <label>What do you want to calculate?
                          <select value={form.aggregate || 'sum'} onChange={e => update({ ...form, aggregate: e.target.value as 'sum' | 'count' })}>
                            <option value="sum">Total {amountLabel} (SUM)</option>
                            {supportsCount(contract) && <option value="count">Number of records (COUNT)</option>}
                          </select>
                        </label>
                        {amounts.length > 1 && form.aggregate !== 'count' && (
                          <label>Amount column
                            <select value={form.amountColumn || amounts[0]} onChange={e => update({ ...form, amountColumn: e.target.value })}>
                              {amounts.map(a => <option key={a} value={a}>{a}</option>)}
                            </select>
                          </label>
                        )}
                      </div>
                      <fieldset style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: 6 }}>
                        <legend style={{ fontSize: 13, fontWeight: 600 }}>Include only records where… <span className="example">(no conditions = every record)</span></legend>
                        {form.filters.length > 1 && supportsAnyMatch(contract) && (
                          <label>Records must match
                            <select value={form.match || 'all'} onChange={e => update({ ...form, match: e.target.value as 'all' | 'any' })}>
                              <option value="all">All conditions (AND)</option>
                              <option value="any">Any condition (OR)</option>
                            </select>
                          </label>
                        )}
                        {form.filters.map((f, i) => {
                          const col = columns.find(c => c.id === f.column);
                          return (
                            <div className="condition" key={i}>
                              <div className="filter-row">
                                <label>Column
                                  <select value={f.column} onChange={e => setFilter(i, { column: e.target.value, op: 'eq', value: '' })}>
                                    {columns.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                                  </select>
                                </label>
                                <label>Condition
                                  <select value={f.op} onChange={e => setFilter(i, { op: e.target.value as Filter['op'] })}>
                                    {operatorsFor(col?.type || 'string', false).map(op => <option key={op} value={op}>{OP_LABEL[op]}</option>)}
                                  </select>
                                </label>
                                <ConditionValue id={`load-values-${i}`} filter={f} column={col} known={known[f.column]} onChange={value => setFilter(i, { value })} />
                                <button type="button" className="icon-button button secondary small" aria-label={`Remove condition ${i + 1}`}
                                  onClick={() => update({ ...form, filters: form.filters.filter((_, j) => j !== i) })}><Trash2 size={14} /></button>
                              </div>
                              <ConditionHelp filter={f} column={col} known={known[f.column]} onChange={value => setFilter(i, { value })} />
                            </div>
                          );
                        })}
                        <button type="button" className="button secondary small" style={{ width: 'fit-content' }} disabled={columns.length === 0}
                          onClick={() => update({ ...form, filters: [...form.filters, { column: defaultConditionColumn(columns, known) || columns[0]?.id || '', op: 'eq', value: '' }] })}>
                          <Plus size={14} /> Add condition
                        </button>
                      </fieldset>
                      <p style={{ margin: 0, fontSize: 13 }}>{description}</p>
                    </>
                  ) : (
                    <label>SQL (a total with SUM or a record count with COUNT(*), optionally with WHERE)
                      <SqlEditor rows={3} value={sql} onChange={next => { setSql(next); setRun(null); }}
                        columns={contract ? flatSqlColumns(contract) : []}
                        tables={contract ? ['snapshot', ...(datasetTableName(contract) ? [datasetTableName(contract)] : [])] : []} />
                    </label>
                  )}
                </div>
              )}
              <div className="guided-nav">
                <span className="example">{blocker}</span>
                <button className="button primary" disabled={Boolean(blocker) || busy || (run !== null && pending(run.state))} onClick={compare}>
                  {busy || (run && pending(run.state)) ? 'Comparing…' : sources ? 'Compare the two sources' : 'Compare the two loads'} <ArrowRight size={15} />
                </button>
              </div>
            </div>
          )}
      {problem && (
        <div className="alert error" role="alert">
          <span><strong>{problem.title}:</strong> {problem.message}{problem.guidance && <> — {problem.guidance}</>}</span>
          <button onClick={() => setProblem(null)} aria-label="Dismiss message">×</button>
        </div>
      )}
      {summary && (
        <div className="table-scroll">
          <table>
            <thead><tr><th>What happened to the record</th><th className="figure-cell">Records</th><th className="figure-cell">Change in the total</th></tr></thead>
            <tbody>
              {KINDS.filter(k => summary[k]?.records).map(k => (
                <tr key={k}><td>{sources && k !== 'unchanged' ? categoryLabel(k as CategoryKey, { loads: true, sources: true }) : CHANGE_LABEL[k]}</td><td className="figure-cell">{summary[k].records.toLocaleString('en-US')}</td><td className="figure-cell">{money(summary[k].delta)}</td></tr>
              ))}
            </tbody>
          </table>
          <small>A record can be new or changed without moving the total, for example when it falls outside the calculation’s conditions.</small>
        </div>
      )}
      {run?.result && group && (
        <ResultReport run={run} unit={unit} relational={false}
          baselineLabel={baseline ? loadLabel(baseline) : ''} targetLabel={target ? loadLabel(target) : ''}
          baselineSql={currentSql} targetSql={currentSql} baselineDescription={description || undefined} targetDescription={description || undefined}
          snapshot={baseline} versions={data.definitions.flatMap(d => d.versions)} />
      )}
      {run?.result && (
        <div className="save-loads-case">
          <h3>Save as a case</h3>
          <label>Case name<input maxLength={160} value={title} onChange={e => setTitle(e.target.value)} /></label>
          <label>Your conclusion
            <textarea rows={3} maxLength={2000} value={decision} onChange={e => setDecision(e.target.value)}
              placeholder="e.g. The later load adds 11,720 late trips; wait for the final delivery before reporting January." />
          </label>
          <label>Recorded by<input maxLength={160} value={actor} onChange={e => setActor(e.target.value)} placeholder="Your name" /></label>
          <div className="guided-nav">
            <span className="example">{saveBlocker}</span>
            <button className="button primary" disabled={busy || Boolean(saveBlocker)} onClick={() => void saveCase()}><Save size={15} /> {busy ? 'Saving…' : 'Save case'}</button>
          </div>
        </div>
      )}
    </section>
  );
}

function defaultSql(contract: FlatContract) {
  const measure = contract.version === 8 ? contract.decimal_columns?.[0]?.name : contract.measure;
  return measure ? `SELECT SUM(${measure}) FROM snapshot` : 'SELECT COUNT(*) FROM snapshot';
}
