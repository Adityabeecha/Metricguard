import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Database, Plus, Save, Trash2, UploadCloud } from 'lucide-react';
import { api, ApiError, type Bootstrap, type FlatContract, type InvestigationDraft, type RelationalContract, type Run, type Snapshot } from '../api';
import {
  buildFlatSql, buildRelationalSql, describeCalculation, flatFilterColumns, formProblems, guidedFormSupported,
  OP_LABEL, operatorsFor, relationalFilterColumns, supportsAnyMatch, type CalculationForm, type Filter, type FilterColumn,
} from '../comparisonBuilder';
import { summarizeDataset } from '../datasetSummary';
import { classifyInvestigationError, type ClassifiedInvestigationError } from '../investigationUtils';
import type { Page } from '../router';
import { ResultReport } from './ResultReport';
import { TwoTableUpload } from './TwoTableUpload';

type Side = 'baseline' | 'target';
type SideState = { name: string; mode: 'form' | 'sql'; form: CalculationForm; sql: string; refusal: ClassifiedInvestigationError | null;
  /** A saved calculation this side started from; reused as-is when its SQL is unchanged. */
  reuse?: { versionId: string; sql: string } };
type Choice = { snapshot: Snapshot; flat?: FlatContract; relational?: RelationalContract };

const STAGES = [
  { title: 'Choose data', hint: 'What data will be used' },
  { title: 'Define calculations', hint: 'What is being compared' },
  { title: 'Review difference', hint: 'What changed and why' },
  { title: 'Save explanation', hint: 'What stays available for replay' },
];
const pending = (state?: string) => state === 'queued' || state === 'running' || state === 'pending';
const emptyFilter = (columns: FilterColumn[]): Filter => ({ column: columns[0]?.id || '', op: 'eq', value: '' });

type Props = {
  data: Bootstrap;
  refresh: () => Promise<Bootstrap | null>;
  onNavigate: (page: Page, tab?: string, query?: Record<string, string>) => void;
  onOpenInvestigation: (id: string, relational: boolean) => void;
  /** Open an existing saved case, draft or run (`from`), or a pair of calculation versions. */
  prefill?: { from?: string; left?: string; right?: string };
};

export function StartComparison({ data, refresh, onNavigate, onOpenInvestigation, prefill }: Props) {
  const [stage, setStage] = useState(0);
  const [snapshotId, setSnapshotId] = useState('');
  const [sides, setSides] = useState<Record<Side, SideState>>({
    baseline: { name: 'Current calculation', mode: 'form', form: { filters: [], join: 'left' }, sql: '', refusal: null },
    target: { name: 'Proposed calculation', mode: 'form', form: { filters: [], join: 'left' }, sql: '', refusal: null },
  });
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ClassifiedInvestigationError | null>(null);
  const [title, setTitle] = useState('');
  const [decision, setDecision] = useState('');
  const [actor, setActor] = useState('');
  const [draft, setDraft] = useState<InvestigationDraft | null>(null);
  const [origin, setOrigin] = useState<string>('');
  const [showTwoTable, setShowTwoTable] = useState(false);
  const [loadingPrefill, setLoadingPrefill] = useState(Boolean(prefill?.from || prefill?.left));
  const skipReset = useRef(false);

  const isolated = data.mode === 'isolated';
  const engineReady = isolated && data.runner_available;

  const choices: Choice[] = useMemo(() => data.snapshots.flatMap<Choice>(snapshot => {
    if (snapshot.execution_policy === 'relational_snapshot_v1') {
      const relational = (data.relational_contracts || []).find(c => c.contract_hash === snapshot.contract_hash);
      return relational ? [{ snapshot, relational }] : [];
    }
    const flat = (data.flat_contracts || []).find(c => c.contract_hash === snapshot.contract_hash);
    return flat ? [{ snapshot, flat }] : [];
  }), [data.snapshots, data.flat_contracts, data.relational_contracts]);

  const choice = choices.find(c => c.snapshot.id === snapshotId) || null;
  const contract = choice?.flat || choice?.relational || null;
  const relational = Boolean(choice?.relational);
  const columns = useMemo<FilterColumn[]>(() => choice?.flat ? flatFilterColumns(choice.flat) : choice?.relational ? relationalFilterColumns(choice.relational) : [], [choice]);
  const summary = choice ? summarizeDataset(choice.snapshot, data) : null;
  const formAllowed = contract ? guidedFormSupported(contract) : true;

  const allVersions = useMemo(() => data.definitions.flatMap(d => d.versions.map(v => ({ definition: d, version: v }))), [data.definitions]);
  const savedCalcs = useMemo(() => contract ? allVersions.filter(x => x.version.contract_hash === contract.contract_hash && x.version.support_state === 'executable') : [],
    [allVersions, contract]);

  // Open an existing case, draft, run or version pair inside the guided flow.
  useEffect(() => {
    if (!prefill?.from && !prefill?.left) return;
    let active = true;
    const load = async () => {
      let snapshot = '', baseId = prefill.left || '', targetId = prefill.right || '', existingRun: Run | null = null;
      let editor = null as null | { baseline_sql?: string; target_sql?: string; baseline_name?: string; target_name?: string };
      let label = '';
      if (prefill.from) {
        try {
          const found = await api.investigation(prefill.from) as unknown as Record<string, unknown>;
          if (found.record_type === 'draft' || found.is_draft) {
            const d = found as unknown as InvestigationDraft & { run?: Run | null };
            setDraft(d); snapshot = d.snapshot_id || ''; baseId = d.baseline_version_id || ''; targetId = d.target_version_id || '';
            existingRun = d.run || null; editor = (d as unknown as { editor_state?: typeof editor }).editor_state || null;
            setTitle(d.title && d.title !== 'Untitled investigation' ? d.title : '');
            label = `Continuing draft “${d.title || 'Untitled'}”.`;
          } else {
            const c = found as unknown as { title: string; decision: string; actor_label: string; snapshot_id: string; baseline_version_id: string; target_version_id: string; run: Run };
            snapshot = c.snapshot_id; baseId = c.baseline_version_id; targetId = c.target_version_id; existingRun = c.run;
            setTitle(`${c.title} (edited)`); setDecision(c.decision); setActor(c.actor_label);
            label = `Editing a copy of “${c.title}”. Saving creates a new investigation; the original stays unchanged and replayable.`;
          }
        } catch (e) {
          if (!(e instanceof ApiError) || !['NOT_FOUND', 'HTTP_404'].includes(e.code)) throw e;
          existingRun = await api.run(prefill.from);
          snapshot = existingRun.snapshot_id; baseId = existingRun.baseline_version_id; targetId = existingRun.target_version_id;
          label = 'Opened a comparison run. Review it, adjust the calculations, or save it as an investigation.';
        }
      }
      if (!active) return;
      const base = allVersions.find(x => x.version.id === baseId), target = allVersions.find(x => x.version.id === targetId);
      if (!snapshot) {
        const hash = base?.version.contract_hash || target?.version.contract_hash;
        snapshot = choices.find(c => (c.flat || c.relational)!.contract_hash === hash)?.snapshot.id || '';
      }
      skipReset.current = true;
      setSnapshotId(snapshot);
      const sideFrom = (x: typeof base, sql: string | undefined, name: string | undefined, fallback: string): SideState => ({
        name: name || x?.definition.name || fallback, mode: 'sql', form: { filters: [], join: 'left' },
        sql: sql || x?.version.raw_sql || '', refusal: null,
        reuse: x && (!sql || sql === x.version.raw_sql) ? { versionId: x.version.id, sql: x.version.raw_sql } : undefined,
      });
      setSides({
        baseline: sideFrom(base, editor?.baseline_sql, editor?.baseline_name, 'Current calculation'),
        target: sideFrom(target, editor?.target_sql, editor?.target_name, 'Proposed calculation'),
      });
      if (existingRun) { setRun(existingRun); setStage(2); } else setStage(snapshot ? 1 : 0);
      setOrigin(label);
    };
    load().catch(e => { if (active) setProblem(classifyInvestigationError(e)); }).finally(() => { if (active) setLoadingPrefill(false); });
    return () => { active = false; };
  }, [prefill?.from, prefill?.left, prefill?.right]);

  // Choosing a dataset resets calculations to sensible starting points.
  useEffect(() => {
    if (!choice) return;
    if (skipReset.current) { skipReset.current = false; return; }
    const forceSql = !guidedFormSupported(choice.flat || choice.relational!);
    setSides(s => ({
      baseline: { ...s.baseline, mode: forceSql ? 'sql' : 'form', form: { filters: [], join: 'left' }, sql: '', refusal: null },
      target: { ...s.target, mode: forceSql ? 'sql' : 'form', form: { filters: [emptyFilter(columns)], join: 'left' }, sql: '', refusal: null },
    }));
    setRun(null);
  }, [snapshotId]);

  // Poll the comparison until the isolated engine finishes.
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

  const sqlFor = (side: Side): string => {
    const s = sides[side];
    if (s.mode === 'sql') return s.sql;
    if (!contract) return '';
    return choice?.flat ? buildFlatSql(choice.flat, s.form) : buildRelationalSql(choice!.relational!, s.form);
  };
  const safeSql = (side: Side) => { try { return sqlFor(side); } catch { return ''; } };
  const describe = (side: Side) => sides[side].mode === 'form' && summary
    ? describeCalculation(sides[side].form, columns, summary.measureLabel, relational) : undefined;
  const update = (side: Side, patch: Partial<SideState>) => setSides(s => ({ ...s, [side]: { ...s[side], ...patch, refusal: null } }));
  const updateForm = (side: Side, form: CalculationForm) => update(side, { form });

  const sideProblems = (side: Side) => sides[side].mode === 'form' ? formProblems(sides[side].form, columns)
    : sides[side].sql.trim() ? [] : ['Enter the SQL for this calculation.'];
  const calcBlocker = !engineReady ? (isolated ? 'The comparison engine is not running. Start it with scripts\\mg.ps1 start.' : 'New comparisons need the isolated engine. Start MetricGuard with scripts\\mg.ps1 start.')
    : [...sideProblems('baseline'), ...sideProblems('target')][0] || (safeSql('baseline').trim() && safeSql('baseline').trim() === safeSql('target').trim()
      && !(sides.baseline.reuse && sides.baseline.reuse.versionId === sides.target.reuse?.versionId)
      ? 'The two calculations are identical; change one of them.' : '');

  async function registerAndCompare() {
    if (!choice || !contract) return;
    setBusy(true); setProblem(null); setRun(null);
    try {
      const endpoint = relational ? '/relational/definitions' : '/flat/definitions';
      const ids: Partial<Record<Side, string>> = {};
      for (const side of ['baseline', 'target'] as Side[]) {
        let sql: string;
        try { sql = sqlFor(side); } catch (e) { update(side, {}); throw e; }
        const reuse = sides[side].reuse;
        if (reuse && sides[side].mode === 'sql' && reuse.sql.trim() === sql.trim()) { ids[side] = reuse.versionId; continue; }
        try {
          const def = await api.post<{ versions: Array<{ id: string; support_state: string; support_reason?: string | null }> }>(
            endpoint, { name: sides[side].name.trim() || (side === 'baseline' ? 'Current calculation' : 'Proposed calculation'), raw_sql: sql, contract_id: contract.id });
          const version = def.versions.at(-1);
          if (!version || version.support_state !== 'executable') {
            const refusal = classifyInvestigationError({ code: 'UNSUPPORTED_SQL', message: version?.support_reason || 'This calculation is outside the supported grammar.' });
            setSides(s => ({ ...s, [side]: { ...s[side], refusal, mode: s[side].mode === 'form' ? 'form' : 'sql', sql: s[side].sql || sql } }));
            return;
          }
          ids[side] = version.id;
        } catch (e) {
          const refusal = classifyInvestigationError(e);
          if (refusal.isRefusal) { setSides(s => ({ ...s, [side]: { ...s[side], refusal, sql: s[side].sql || sql } })); return; }
          throw e;
        }
      }
      const context = relational
        ? { mode: 'relational_snapshot', scope: choice.relational!.scope, contract_hash: choice.relational!.contract_hash, unit_label: choice.relational!.unit_label, values_comparable: true }
        : { mode: 'flat_snapshot', scope: choice.flat!.scope, contract_hash: choice.flat!.contract_hash, unit_label: choice.flat!.unit_label, values_comparable: true };
      const started = await api.post<Run>(relational ? '/relational/comparisons' : '/flat/comparisons', {
        baseline_version_id: ids.baseline, target_version_id: ids.target, snapshot_id: choice.snapshot.id, relationship: 'candidate', context,
      }, { 'Idempotency-Key': crypto.randomUUID() });
      setRun(started);
      setStage(2);
      void rememberDraft(started, ids.baseline!, ids.target!);
      void refresh();
    } catch (e) {
      setProblem(classifyInvestigationError(e));
    } finally {
      setBusy(false);
    }
  }

  // Keep unsaved work findable in the investigation list until it is saved.
  async function rememberDraft(started: Run, baselineId: string, targetId: string) {
    const editor_state = { mode: 'adhoc' as const, baseline_name: sides.baseline.name, target_name: sides.target.name,
      baseline_sql: safeSql('baseline'), target_sql: safeSql('target') };
    const fields = { title: title.trim() || `${sides.baseline.name} vs ${sides.target.name}`, snapshot_id: started.snapshot_id,
      baseline_version_id: baselineId, target_version_id: targetId, comparison_run_id: started.id, editor_state };
    try {
      if (draft) setDraft(await api.updateInvestigationDraft(undefined, draft.id, { ...fields, status: 'needs_decision', version_token: draft.version_token }));
      else setDraft(await api.createInvestigationDraft(undefined, { ...fields, status: 'needs_decision' }));
    } catch { /* a draft is a convenience; the run itself is already recorded */ }
  }

  async function save() {
    if (!run?.result) return;
    setBusy(true); setProblem(null);
    try {
      const saved = await api.post<{ id: string }>('/investigations', { title: title.trim(), decision: decision.trim(), actor_label: actor.trim(), run_id: run.id, snapshot_id: run.snapshot_id });
      if (draft) await api.deleteInvestigationDraft(undefined, draft.id).catch(() => undefined);
      await refresh();
      onOpenInvestigation(saved.id, relational);
    } catch (e) {
      setProblem(classifyInvestigationError(e));
    } finally {
      setBusy(false);
    }
  }

  const canEnter = (i: number) => i === 0 || (i === 1 && !!choice) || (i >= 2 && !!run) || (i === 3 && !!run?.result);
  const saveBlocker = !run?.result ? 'Run the comparison first.' : !title.trim() ? 'Give the investigation a name.' : !decision.trim() ? 'Write your conclusion.' : !actor.trim() ? 'Enter who is recording this.' : '';

  return (
    <div className="guided-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">START A COMPARISON <span className="eyebrow-line" /></div>
          <h1>Compare two calculations of the same metric</h1>
          <p>Choose a dataset, describe the current and proposed calculations, see exactly which records explain the difference, and save your explanation.</p>
        </div>
        <div className="heading-actions">
          <button className="button secondary" onClick={() => onNavigate('investigations')}>Open saved investigation</button>
        </div>
      </div>

      <ol className="stage-track" aria-label="Comparison stages">
        {STAGES.map((s, i) => (
          <li key={s.title} className={i === stage ? 'current' : i < stage ? 'done' : ''} aria-current={i === stage ? 'step' : undefined}>
            <button type="button" disabled={!canEnter(i)} onClick={() => setStage(i)}>
              <b>{i + 1}. {s.title}</b>{s.hint}
            </button>
          </li>
        ))}
      </ol>

      {!engineReady && (
        <div className="alert error" role="status">
          <span>
            {isolated ? 'The comparison engine is not running, so new comparisons will wait until it starts.' : 'This server is in demo mode: it can show saved results, but new comparisons need the isolated engine.'}
            {' '}Start the full application with <code>scripts\mg.ps1 start</code>.
          </span>
        </div>
      )}
      {loadingPrefill && <p role="status">Opening the comparison…</p>}
      {origin && <div className="guided-explain" role="status">{origin}</div>}
      {problem && (
        <div className="alert error" role="alert">
          <span><strong>{problem.title}:</strong> {problem.message}{problem.guidance && <> — {problem.guidance}</>}</span>
          <button onClick={() => setProblem(null)} aria-label="Dismiss message">×</button>
        </div>
      )}

      {stage === 0 && (
        <section className="guided-card" aria-labelledby="stage-data">
          <h2 id="stage-data">1. Choose data</h2>
          <p className="lead">Pick the frozen dataset both calculations will read. The same records are used for both, so any difference comes from the calculations alone.</p>
          {choices.length === 0 ? (
            <div className="guided-explain">No datasets are ready yet. Upload one below; it will appear here once validated.</div>
          ) : (
            <div className="dataset-options" role="group" aria-label="Datasets">
              {choices.map(c => {
                const s = summarizeDataset(c.snapshot, data);
                return (
                  <button key={c.snapshot.id} type="button" className="dataset-option" aria-pressed={c.snapshot.id === snapshotId} onClick={() => setSnapshotId(c.snapshot.id)}>
                    <strong>{c.snapshot.name}</strong>
                    <span>{s.kindLabel} · {c.snapshot.row_count.toLocaleString()} {c.relational ? 'fact records' : 'rows'}</span>
                    <span>Totals <code>{s.measureLabel}</code> in {s.unitLabel}</span>
                    {s.filterColumns.length > 0 && <span>Filter by: {s.filterColumns.join(', ')}</span>}
                  </button>
                );
              })}
            </div>
          )}
          {summary && (
            <div className="guided-explain">
              <b>You will compare totals of {summary.measureLabel}</b> ({summary.measureMeaning || 'amount'}, unit {summary.unitLabel}) over {choice!.snapshot.row_count.toLocaleString()} records.
              {summary.joinLabel && <> Records link to the dimension through {summary.joinLabel}.</>}
              <details style={{ marginTop: 8 }}>
                <summary>Advanced details</summary>
                <p>Contract “{summary.contractName}” · row key {summary.keyLabel} · policy {choice!.snapshot.execution_policy} · contract hash <code className="mono-cell">{choice!.snapshot.contract_hash}</code></p>
              </details>
            </div>
          )}
          <div className="guided-explain">
            <b>Need new data?</b> Upload a CSV or Parquet file and answer a few questions: which column identifies each row, which amount to total, its unit, and which columns you want to filter on.
            <div className="report-actions" style={{ marginTop: 8 }}>
              <button className="button secondary small" onClick={() => onNavigate('data', undefined, { add: '1' })}><UploadCloud size={14} /> Upload one table</button>
              <button className="button secondary small" aria-expanded={showTwoTable} onClick={() => setShowTwoTable(v => !v)}><Database size={14} /> Upload two connected tables</button>
            </div>
          </div>
          {showTwoTable && (
            <TwoTableUpload refresh={refresh} canUpload={engineReady}
              blocker={isolated ? 'The comparison engine is not running. Start it with scripts\\mg.ps1 start.' : 'Uploads need the isolated engine. Start MetricGuard with scripts\\mg.ps1 start.'}
              onCreated={id => { setShowTwoTable(false); setSnapshotId(id); }} />
          )}
          <div className="guided-nav">
            <span />
            <button className="button primary" disabled={!choice} onClick={() => setStage(1)} aria-describedby={!choice ? 'need-data' : undefined}>
              Next: define calculations <ArrowRight size={15} />
            </button>
          </div>
          {!choice && <span id="need-data" className="disabled-reason">Choose a dataset to continue.</span>}
        </section>
      )}

      {stage === 1 && choice && contract && (
        <section className="guided-card" aria-labelledby="stage-calc">
          <h2 id="stage-calc">2. Define calculations</h2>
          <p className="lead">Describe the calculation in use today and the one being proposed. Both add up <code>{summary?.measureLabel}</code>; they differ only in which records they include.</p>
          {!formAllowed && <div className="guided-explain">This dataset uses a conditional (CASE) total, which is written in the SQL editor.</div>}
          <div className="calc-grid">
            {(['baseline', 'target'] as Side[]).map(side => {
              const s = sides[side];
              const sideSql = safeSql(side);
              return (
                <div className="calc-card" key={side}>
                  <h3>{side === 'baseline' ? 'Current calculation' : 'Proposed calculation'}</h3>
                  <label>Name<input value={s.name} maxLength={160} onChange={e => update(side, { name: e.target.value })} /></label>
                  {savedCalcs.length > 0 && (
                    <label>Start from a saved calculation (optional)
                      <select value={s.reuse?.versionId || ''} onChange={e => {
                        const pick = savedCalcs.find(x => x.version.id === e.target.value);
                        if (!pick) { update(side, { reuse: undefined }); return; }
                        update(side, { mode: 'sql', sql: pick.version.raw_sql, name: pick.definition.name, reuse: { versionId: pick.version.id, sql: pick.version.raw_sql } });
                      }}>
                        <option value="">— Write a new calculation —</option>
                        {savedCalcs.map(x => <option key={x.version.id} value={x.version.id}>{x.definition.name} · v{x.version.version_number}</option>)}
                      </select>
                    </label>
                  )}
                  <div className="mode-switch" role="group" aria-label="Calculation editor">
                    <button type="button" aria-pressed={s.mode === 'form'} disabled={!formAllowed} onClick={() => update(side, { mode: 'form' })}>Guided form</button>
                    <button type="button" aria-pressed={s.mode === 'sql'} onClick={() => update(side, { mode: 'sql', sql: s.sql || sideSql })}>SQL editor</button>
                  </div>

                  {s.mode === 'form' ? (
                    <>
                      {relational && (
                        <label>Records without a matching dimension row
                          <select value={s.form.join || 'left'} onChange={e => updateForm(side, { ...s.form, join: e.target.value as 'left' | 'inner' })}>
                            <option value="left">Keep them (LEFT JOIN)</option>
                            <option value="inner">Leave them out (INNER JOIN)</option>
                          </select>
                        </label>
                      )}
                      <fieldset style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: 6 }}>
                        <legend style={{ fontSize: 13, fontWeight: 600 }}>Include only records where… <span className="example">(no conditions = every record)</span></legend>
                        {s.form.filters.length > 1 && supportsAnyMatch(contract) && (
                          <label>Records must match
                            <select value={s.form.match || 'all'} onChange={e => updateForm(side, { ...s.form, match: e.target.value as 'all' | 'any' })}>
                              <option value="all">All conditions (AND)</option>
                              <option value="any">Any condition (OR)</option>
                            </select>
                          </label>
                        )}
                        {s.form.filters.length > 1 && !supportsAnyMatch(contract) && <span className="example">All conditions must be true. This dataset was not set up for OR conditions.</span>}
                        {s.form.filters.map((f, i) => {
                          const col = columns.find(c => c.id === f.column);
                          const ops = operatorsFor(col?.type || 'string', relational);
                          const needsValue = f.op !== 'is_null' && f.op !== 'is_not_null';
                          const setFilter = (patch: Partial<Filter>) => updateForm(side, { ...s.form, filters: s.form.filters.map((x, j) => j === i ? { ...x, ...patch } : x) });
                          return (
                            <div className="filter-row" key={i}>
                              <label>Column
                                <select value={f.column} onChange={e => setFilter({ column: e.target.value, op: 'eq', value: '' })}>
                                  {columns.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                                </select>
                              </label>
                              <label>Condition
                                <select value={f.op} onChange={e => setFilter({ op: e.target.value as Filter['op'] })}>
                                  {ops.map(op => <option key={op} value={op}>{OP_LABEL[op]}</option>)}
                                </select>
                              </label>
                              <label>Value
                                <input disabled={!needsValue} value={needsValue ? f.value : ''} onChange={e => setFilter({ value: e.target.value })}
                                  placeholder={col?.type === 'boolean' ? 'true or false' : col?.type === 'date' ? '2026-09-01' : f.op === 'in' ? 'completed, shipped' : 'completed'} />
                              </label>
                              <button type="button" className="icon-button button secondary small" aria-label={`Remove condition ${i + 1}`} onClick={() => updateForm(side, { ...s.form, filters: s.form.filters.filter((_, j) => j !== i) })}><Trash2 size={14} /></button>
                            </div>
                          );
                        })}
                        <button type="button" className="button secondary small" style={{ width: 'fit-content' }} disabled={columns.length === 0} onClick={() => updateForm(side, { ...s.form, filters: [...s.form.filters, emptyFilter(columns)] })}>
                          <Plus size={14} /> Add condition
                        </button>
                        {columns.length === 0 && <span className="disabled-reason">This dataset declares no filter columns, so the form can only total every record.</span>}
                      </fieldset>
                      <p style={{ margin: 0, fontSize: 13 }}>{describe(side)}</p>
                      <details><summary>SQL this form produces</summary><pre className="sql-preview">{sideSql || 'Complete the conditions to see the SQL.'}</pre></details>
                    </>
                  ) : (
                    <label>SQL
                      <textarea rows={6} spellCheck={false} value={s.sql} onChange={e => update(side, { sql: e.target.value })}
                        placeholder={relational ? 'SELECT SUM(f.amount) FROM fact f LEFT JOIN dim d ON f.key = d.key WHERE …' : 'SELECT SUM(amount) FROM snapshot WHERE status = \'completed\''} />
                      <span className="example">One SUM over {relational ? 'the fact table with one declared join' : <code>snapshot</code>}, with optional WHERE conditions on the filter columns.</span>
                    </label>
                  )}

                  {sideProblems(side).map(p => <span key={p} className="disabled-reason">{p}</span>)}
                  {s.refusal && (
                    <div className="alert error" role="alert">
                      <span><strong>{s.refusal.title}:</strong> {s.refusal.message}{s.refusal.guidance && <><br />{s.refusal.guidance}</>}<br />Your calculation is kept so you can edit it.</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="guided-nav">
            <button className="button secondary" onClick={() => setStage(0)}><ArrowLeft size={15} /> Back</button>
            <button className="button primary" disabled={busy || Boolean(calcBlocker)} onClick={() => void registerAndCompare()} aria-describedby={calcBlocker ? 'calc-blocker' : undefined}>
              {busy ? 'Checking…' : <>Check and compare <ArrowRight size={15} /></>}
            </button>
          </div>
          {calcBlocker && <span id="calc-blocker" className="disabled-reason">{calcBlocker}</span>}
        </section>
      )}

      {stage === 2 && run && (
        <section className="guided-card" aria-labelledby="stage-review">
          <h2 id="stage-review">3. Review difference</h2>
          {pending(run.state) && <p role="status">Comparing on the frozen dataset… ({run.state})</p>}
          {run.state === 'failed' && <p className="disabled-reason">The comparison did not complete. Go back to adjust the calculations, or try again.</p>}
          {run.result && (
            <ResultReport run={run} unit={summary?.unitLabel === 'Not declared' ? '' : summary?.unitLabel || ''} relational={relational}
              baselineLabel={sides.baseline.name} targetLabel={sides.target.name}
              baselineSql={safeSql('baseline')} targetSql={safeSql('target')}
              baselineDescription={describe('baseline')} targetDescription={describe('target')}
              snapshot={choice?.snapshot} versions={data.definitions.flatMap(d => d.versions)} />
          )}
          <div className="guided-nav">
            <button className="button secondary" onClick={() => setStage(1)}><ArrowLeft size={15} /> Edit calculations</button>
            <button className="button primary" disabled={!run.result} onClick={() => { setTitle(t => t || `${sides.baseline.name} vs ${sides.target.name} · ${choice?.snapshot.name || ''}`); setStage(3); }}>
              Next: save explanation <ArrowRight size={15} />
            </button>
          </div>
        </section>
      )}

      {stage === 3 && run?.result && (
        <section className="guided-card" aria-labelledby="stage-save">
          <h2 id="stage-save">4. Save explanation</h2>
          <p className="lead">Saving pins the dataset, both calculations and this result. Anyone with access can reopen it and replay it later to confirm it reproduces exactly.</p>
          <label>Investigation name<input required maxLength={160} value={title} onChange={e => setTitle(e.target.value)} /></label>
          <label>Your conclusion
            <textarea required rows={4} maxLength={2000} value={decision} onChange={e => setDecision(e.target.value)}
              placeholder="e.g. The proposed calculation excludes pending, returned and cancelled orders (340.00 INR). Adopt it for the September report." />
            <span className="example">Explain what the difference means for the metric. Saying a difference is explained does not by itself make either calculation correct.</span>
          </label>
          <label>Recorded by<input required maxLength={160} value={actor} onChange={e => setActor(e.target.value)} placeholder="Your name" /></label>
          <div className="guided-nav">
            <button className="button secondary" onClick={() => setStage(2)}><ArrowLeft size={15} /> Back to result</button>
            <button className="button primary" disabled={busy || Boolean(saveBlocker)} onClick={() => void save()} aria-describedby={saveBlocker ? 'save-blocker' : undefined}>
              <Save size={15} /> {busy ? 'Saving…' : 'Save investigation'}
            </button>
          </div>
          {saveBlocker && <span id="save-blocker" className="disabled-reason">{saveBlocker}</span>}
          {problem && <p className="disabled-reason">Nothing was lost: your result and text are still here. Fix the issue above and save again.</p>}
        </section>
      )}
    </div>
  );
}
