import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Play, RefreshCw } from 'lucide-react';
import { api, apiFetch, ApiError, type Bootstrap, type Run } from '../api';
import { executeLoadChecks, loadCheckPairOptions } from '../loadChecks';
import { resultDisplayValue, resultUnit, valueWithUnit } from '../resultSummary';

type Eligible = { metric_id: string; metric_name: string; version_id: string; raw_sql: string };
type Preview = {
  baseline_snapshot_id: string; target_snapshot_id: string; baseline_name: string; target_name: string;
  eligible: Eligible[]; skipped: Array<{ metric_id: string; metric_name: string; reason: string; reason_code: string }>;
  run_count: number; runs_available: number | null; runs_last_24h: number; max_runs_per_day: number;
  execution_available: boolean;
};
type LoadCheckRun = Run & { load_check?: { metric_id: string; metric_name: string; version_id: string } };

async function request<T>(path: string, body?: unknown, key?: string): Promise<T> {
  const response = await apiFetch(path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new ApiError(result.error?.code || `HTTP_${response.status}`, result.error?.message || 'Metric checks could not be loaded.');
  return result;
}

export function NewLoadChecksPanel({ data, refresh, onOpenRun, baselineLoadId, targetLoadId }: {
  data: Bootstrap; refresh: () => Promise<Bootstrap | null>; onOpenRun?: (run: Run) => void;
  baselineLoadId?: string; targetLoadId?: string;
}) {
  const snapshots = data.snapshots;
  const targets = useMemo(() => snapshots.filter(snapshot => loadCheckPairOptions(snapshots, snapshot.id).length > 0), [snapshots]);
  const [targetId, setTargetId] = useState(targetLoadId || targets[0]?.id || '');
  const [baselineId, setBaselineId] = useState(baselineLoadId || loadCheckPairOptions(snapshots, targetId)[0]?.id || '');
  const earlier = useMemo(() => loadCheckPairOptions(snapshots, targetId), [snapshots, targetId]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [history, setHistory] = useState<LoadCheckRun[]>([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [finished, setFinished] = useState(false);
  const epoch = useRef(0);
  const stop = useRef(false);
  const pid = data.project.id;
  const path = `/projects/${encodeURIComponent(pid)}/load-checks`;

  useEffect(() => {
    const current = ++epoch.current;
    stop.current = false;
    setPreview(null); setHistory([]); setError(''); setMessage(''); setRunning(false); setLoading(false); setFinished(false);
    return () => { if (epoch.current === current) { epoch.current += 1; stop.current = true; } };
  }, [pid]);
  useEffect(() => {
    if (targetLoadId) setTargetId(targetLoadId);
    if (baselineLoadId) setBaselineId(baselineLoadId);
    setPreview(null); setFinished(false);
  }, [baselineLoadId, targetLoadId, pid]);
  useEffect(() => {
    if (!targets.some(item => item.id === targetId)) setTargetId(targetLoadId || targets[0]?.id || '');
  }, [targets, targetId, targetLoadId]);
  useEffect(() => {
    if (!earlier.some(item => item.id === baselineId)) setBaselineId(earlier[0]?.id || '');
    setPreview(null); setFinished(false);
  }, [targetId, baselineId, earlier]);
  useEffect(() => {
    let active = true;
    void request<{ items: LoadCheckRun[] }>(path).then(result => {
      if (active) { setHistory(result.items); setHistoryError(''); }
    }).catch(reason => { if (active) setHistoryError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { active = false; };
  }, [path, data.runs]);

  async function previewChecks() {
    const current = epoch.current;
    setLoading(true); setError(''); setMessage(''); setFinished(false);
    try {
      const result = await request<Preview>(`${path}/preview`, { baseline_snapshot_id: baselineId, target_snapshot_id: targetId });
      if (current === epoch.current) setPreview(result);
    } catch (reason) { if (current === epoch.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current === epoch.current) setLoading(false); }
  }

  async function startChecks() {
    if (!preview) return;
    const current = epoch.current;
    stop.current = false;
    setRunning(true); setError(''); setMessage('');
    const result = await executeLoadChecks(preview.eligible, {
      start: item => request<LoadCheckRun>(`${path}/start`, {
        baseline_snapshot_id: preview.baseline_snapshot_id, target_snapshot_id: preview.target_snapshot_id,
        metric_id: item.metric_id, version_id: item.version_id,
      }, crypto.randomUUID()),
      poll: id => api.run(id) as Promise<LoadCheckRun>,
      pause: () => new Promise(resolve => setTimeout(resolve, 1200)),
      stopped: () => stop.current || current !== epoch.current,
      onRun: run => {
        if (current !== epoch.current) return;
        setHistory(previous => [run, ...previous.filter(item => item.id !== run.id)]);
        setMessage(`${run.load_check?.metric_name || 'Metric'}: ${run.state}`);
      },
    });
    if (current !== epoch.current) return;
    setRunning(false); setFinished(true);
    setError(result.error);
    setMessage(`${result.completed} of ${preview.run_count} checks completed.${result.completed < preview.run_count ? ' Remaining checks were not started. The current admitted check is saved in Changes.' : ''}`);
    void refresh();
  }

  return <>
    <section className="panel" style={{ marginBottom: 24 }} aria-labelledby="load-check-heading">
      <div className="panel-header">
        <h2 id="load-check-heading">Check metrics on a new load</h2>
        <p>Run each metric’s approved SQL on an earlier and a later load of its dataset.</p>
      </div>
      <div style={{ padding: '0 20px 20px' }}>
        {targets.length === 0 ? <p className="muted">Load a dataset twice to check its approved metrics here.</p> : <>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'end', gap: 14 }}>
            <label className="form-label" style={{ minWidth: 200, flex: 1, display: 'grid', gap: 6, marginBottom: 0 }}>Earlier load
              <select aria-label="Earlier load for metric checks" value={baselineId} disabled={running || loading} onChange={event => setBaselineId(event.target.value)}>
                {earlier.map(snapshot => <option key={snapshot.id} value={snapshot.id}>{snapshot.name} · {new Date(snapshot.created_at).toLocaleString()}</option>)}
              </select>
            </label>
            <ArrowRight size={16} style={{ marginBottom: 12 }} aria-hidden="true" />
            <label className="form-label" style={{ minWidth: 200, flex: 1, display: 'grid', gap: 6, marginBottom: 0 }}>Later load
              <select aria-label="Later load for metric checks" value={targetId} disabled={running || loading} onChange={event => setTargetId(event.target.value)}>
                {targets.map(snapshot => <option key={snapshot.id} value={snapshot.id}>{snapshot.name} · {new Date(snapshot.created_at).toLocaleString()}</option>)}
              </select>
            </label>
            <button className="button secondary" disabled={!baselineId || !targetId || running || loading} onClick={() => void previewChecks()}>
              <RefreshCw size={14} /> {loading ? 'Loading preview…' : 'Preview metric checks'}
            </button>
          </div>
          {preview && <div style={{ marginTop: 18 }}>
            <p className="muted">Keep this page open until the checks finish. If you leave, comparisons already started will finish and stay in Changes; the remaining checks will not start.</p>
            <p><strong>{preview.run_count} comparison{preview.run_count === 1 ? '' : 's'}</strong> will run sequentially. {preview.runs_available === null
              ? 'No daily comparison limit is configured.'
              : `${preview.runs_available} of ${preview.max_runs_per_day} comparisons available in the rolling 24-hour allowance.`}</p>
            {preview.runs_available !== null && preview.run_count > preview.runs_available && <p className="disabled-reason">Only {preview.runs_available} checks fit the current allowance. Execution stops when the allowance is reached; completed results stay saved.</p>}
            {!preview.execution_available && <p className="disabled-reason">Live comparison execution is unavailable in this workspace.</p>}
            <div className="queue-list">
              {preview.eligible.map(item => <div className="queue-row" key={item.metric_id}>
                <div className="queue-row-main"><strong>{item.metric_name}</strong><details><summary>Approved SQL</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{item.raw_sql}</pre></details></div>
                <span className="badge ready">Ready</span>
              </div>)}
              {preview.skipped.map(item => <div className="queue-row" key={item.metric_id}>
                <div className="queue-row-main"><strong>{item.metric_name}</strong><p className="queue-row-sub">{item.reason}</p></div><span className="badge warn">Skipped</span>
              </div>)}
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
              <button className="button primary" disabled={running || finished || !preview.run_count || preview.runs_available === 0 || !preview.execution_available} onClick={() => void startChecks()}><Play size={14} /> {running ? 'Checking metrics…' : finished ? 'Preview again to run more checks' : 'Start metric checks'}</button>
              {running && <button className="button secondary" onClick={() => { stop.current = true; }}>Stop remaining checks</button>}
            </div>
          </div>}
        </>}
        {message && <p role="status">{message}</p>}
        {error && <p className="disabled-reason" role="alert">{error}</p>}
      </div>
    </section>
    {(history.length > 0 || historyError) && <section className="panel" style={{ marginBottom: 24 }}>
      <div className="panel-header"><h2>Metric checks on new loads</h2><p>The same approved calculation, with each load’s exact value and difference.</p></div>
      {historyError && <p role="alert" style={{ padding: '0 20px' }}>{historyError}</p>}
      <div className="queue-list">{history.map(run => {
        const earlierLoad = snapshots.find(item => item.id === run.snapshot_id);
        const laterLoad = snapshots.find(item => item.id === run.target_snapshot_id);
        const result = run.result;
        const unit = result ? resultUnit(result, earlierLoad?.unit_label || '') : '';
        const display = (value: string | null | undefined) => result && value != null ? valueWithUnit(resultDisplayValue(result, value), unit) : 'Unavailable';
        return <div className="queue-row" key={run.id}>
          <div className="queue-row-main"><div className="queue-row-top"><strong>{run.load_check?.metric_name || 'Saved metric'}</strong><span className={`badge ${run.state === 'failed' ? 'warn' : 'ready'}`}>{run.state}</span></div>
            <p className="queue-row-sub">{earlierLoad?.name || 'Earlier load'} → {laterLoad?.name || 'Later load'} · {new Date(run.created_at).toLocaleString()}</p>
            {result && <p className="queue-row-sub mono">{display(result.baseline_value)} → {display(result.target_value)} · Difference {display(result.delta_target_minus_baseline)}</p>}
            {run.error?.message && <p className="disabled-reason">{run.error.message}</p>}
          </div>
          {onOpenRun && <button className="button secondary small" onClick={() => { void api.run(run.id).then(onOpenRun).catch(reason => setError(reason instanceof Error ? reason.message : String(reason))); }}>Review evidence <ArrowRight size={13} /></button>}
        </div>;
      })}</div>
    </section>}
  </>;
}
