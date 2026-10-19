import { useEffect, useMemo, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { api, type Bootstrap, type FlatContract, type Run, type Snapshot, type SnapshotChangeKind } from '../api';
import { comparisonRequestKey } from '../guidedWorkflow';
import { classifyInvestigationError, type ClassifiedInvestigationError } from '../investigationUtils';
import { CHANGE_LABEL, ResultReport } from './ResultReport';

const pending = (state?: string) => state === 'queued' || state === 'running' || state === 'pending';
const KINDS: SnapshotChangeKind[] = ['added', 'removed', 'value_changed', 'entered_filter', 'left_filter', 'unchanged'];
/** Flat contract versions this comparison supports: plain totals and record counts. */
const SUPPORTED = new Set([3, 4, 5, 8]);

type Props = { data: Bootstrap; refresh: () => Promise<Bootstrap | null> };

/** One calculation run on two data loads of the same dataset (e.g. September vs October). */
export function SnapshotChange({ data, refresh }: Props) {
  const groups = useMemo(() => (data.flat_contracts || []).filter(c => SUPPORTED.has(c.version)).map(contract => ({
    contract,
    snapshots: data.snapshots.filter(s => s.execution_policy !== 'relational_snapshot_v1' && s.contract_hash === contract.contract_hash)
      .sort((a, b) => a.created_at.localeCompare(b.created_at)),
  })).filter(g => g.snapshots.length >= 2), [data.flat_contracts, data.snapshots]);
  const [contractId, setContractId] = useState('');
  const group = groups.find(g => g.contract.id === contractId) || null;
  const [baselineId, setBaselineId] = useState('');
  const [targetId, setTargetId] = useState('');
  const [versionId, setVersionId] = useState('');
  const [sql, setSql] = useState('');
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ClassifiedInvestigationError | null>(null);
  const engineReady = data.mode === 'isolated' && data.runner_available;

  const saved = useMemo(() => group ? data.definitions.flatMap(d => d.versions
    .filter(v => v.contract_hash === group.contract.contract_hash && v.support_state === 'executable')
    .map(v => ({ id: v.id, label: `${d.name} · v${v.version_number}`, sql: v.raw_sql }))) : [], [data.definitions, group]);

  // Choosing a dataset defaults to its two most recent loads and a plain total.
  useEffect(() => {
    if (!group) return;
    const n = group.snapshots.length;
    setBaselineId(group.snapshots[n - 2].id); setTargetId(group.snapshots[n - 1].id);
    setVersionId(''); setSql(defaultSql(group.contract)); setRun(null); setProblem(null);
  }, [contractId]);

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

  const baseline = group?.snapshots.find(s => s.id === baselineId), target = group?.snapshots.find(s => s.id === targetId);
  const blocker = !engineReady ? 'The comparison engine is not running. Start it with scripts\\mg.ps1 start.'
    : !group ? 'Choose a dataset with at least two loads.'
    : !baseline || !target ? 'Choose both loads.'
    : baseline.id === target.id ? 'Choose two different loads.'
    : !versionId && !sql.trim() ? 'Choose a saved calculation or enter its SQL.' : '';

  async function compare() {
    if (blocker || !group || busy) return;
    setBusy(true); setProblem(null); setRun(null);
    try {
      let id = versionId;
      if (!id) {
        const existing = saved.find(s => s.sql.trim() === sql.trim());
        if (existing) id = existing.id;
        else {
          const def = await api.post<{ versions: Array<{ id: string; support_state: string; support_reason?: string | null }> }>(
            '/flat/definitions', { name: `Change between loads: ${sql.trim()}`.slice(0, 160), raw_sql: sql.trim(), contract_id: group.contract.id });
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
        context: { mode: 'flat_snapshot', scope: c.scope, contract_hash: c.contract_hash, unit_label: c.unit_label, values_comparable: true } };
      const key = await comparisonRequestKey(data.project.id, { snapshot_change: request });
      setRun(await api.post<Run>('/flat/snapshot-changes', request, { 'Idempotency-Key': key }));
      await refresh();
    } catch (e) {
      setProblem(classifyInvestigationError(e));
    } finally {
      setBusy(false);
    }
  }

  const summary = run?.result?.change_summary;
  const label = (s?: Snapshot) => s ? `${s.name} (${new Date(s.created_at).toLocaleDateString()})` : '';
  return (
    <section className="guided-card" aria-labelledby="snapshot-change">
      <h2 id="snapshot-change">Same calculation, two data loads</h2>
      <p className="lead">Run one calculation on an earlier and a later load of the same dataset and see which records were added, removed or changed. Records are matched by the dataset's row key, and their changes add up exactly to the change in the total.</p>
      {groups.length === 0 ? (
        <div className="guided-explain">No dataset has two loads yet. Upload a second file under the same dataset definition (same row key and columns) to compare loads.</div>
      ) : (
        <>
          <div className="dataset-options" role="group" aria-label="Datasets with several loads">
            {groups.map(g => (
              <button key={g.contract.id} type="button" className="dataset-option" aria-pressed={g.contract.id === contractId} onClick={() => setContractId(g.contract.id)}>
                <strong>{g.contract.name}</strong>
                <span>{g.snapshots.length} loads · row key <code>{g.contract.row_key}</code></span>
              </button>
            ))}
          </div>
          {group && (
            <div style={{ display: 'grid', gap: 12, marginTop: 12 }}>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <label>Earlier load (baseline)
                  <select value={baselineId} onChange={e => { setBaselineId(e.target.value); setRun(null); }}>
                    {group.snapshots.map(s => <option key={s.id} value={s.id}>{label(s)} · {s.row_count.toLocaleString('en-US')} records</option>)}
                  </select>
                </label>
                <label>Later load (target)
                  <select value={targetId} onChange={e => { setTargetId(e.target.value); setRun(null); }}>
                    {group.snapshots.map(s => <option key={s.id} value={s.id}>{label(s)} · {s.row_count.toLocaleString('en-US')} records</option>)}
                  </select>
                </label>
              </div>
              <label>Calculation
                <select value={versionId} onChange={e => { setVersionId(e.target.value); setRun(null); }}>
                  <option value="">Write SQL below</option>
                  {saved.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </label>
              {!versionId && (
                <label>SQL (a total with SUM or a record count with COUNT(*), optionally with WHERE)
                  <textarea rows={3} value={sql} onChange={e => { setSql(e.target.value); setRun(null); }} spellCheck={false} />
                </label>
              )}
              <div className="guided-nav">
                <span className="example">{blocker}</span>
                <button className="button primary" disabled={Boolean(blocker) || busy || (run !== null && pending(run.state))} onClick={compare}>
                  {busy || (run && pending(run.state)) ? 'Comparing…' : 'Compare the two loads'} <ArrowRight size={15} />
                </button>
              </div>
            </div>
          )}
        </>
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
            <thead><tr><th>What happened to the record</th><th>Records</th><th>Contribution to the change</th></tr></thead>
            <tbody>
              {KINDS.filter(k => summary[k]?.records).map(k => (
                <tr key={k}><td>{CHANGE_LABEL[k]}</td><td>{summary[k].records.toLocaleString('en-US')}</td><td>{summary[k].delta}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {run?.result && group && (
        <ResultReport run={run} unit={run.result.aggregate === 'count' ? '' : group.contract.unit_label} relational={false}
          baselineLabel={label(baseline)} targetLabel={label(target)}
          baselineSql={versionId ? saved.find(s => s.id === versionId)?.sql || '' : sql} targetSql={versionId ? saved.find(s => s.id === versionId)?.sql || '' : sql}
          snapshot={baseline} versions={data.definitions.flatMap(d => d.versions)} />
      )}
    </section>
  );
}

function defaultSql(contract: FlatContract) {
  const measure = contract.version === 8 ? contract.decimal_columns?.[0]?.name : contract.measure;
  return measure ? `SELECT SUM(${measure}) FROM snapshot` : 'SELECT COUNT(*) FROM snapshot';
}
