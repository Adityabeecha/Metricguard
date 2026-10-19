import { resultUnit } from '../resultSummary';
import { useEffect, useState } from 'react';
import { ArrowLeft, Download, Pencil, RefreshCw } from 'lucide-react';
import { api, ApiError, versionName, type Bootstrap, type InvestigationDetail, type Run } from '../api';
import { caseNumber, KIND_LABEL } from '../investigationLibrary';
import { ResultReport } from './ResultReport';

const pending = (state?: string) => state === 'queued' || state === 'running' || state === 'pending';

type Props = {
  data: Bootstrap;
  investigationId: string;
  onBack: () => void;
  onEdit: (relational: boolean) => void;
  /** The id is not a saved investigation (e.g. a draft or a bare run): hand over to the editor. */
  onNotSaved: () => void;
  refresh: () => Promise<Bootstrap | null>;
};

export function CaseReport({ data, investigationId, onBack, onEdit, onNotSaved, refresh }: Props) {
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [problem, setProblem] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [replay, setReplay] = useState<Run | null>(null);
  const [replayProblem, setReplayProblem] = useState('');
  const [replaying, setReplaying] = useState(false);

  useEffect(() => {
    let active = true;
    setDetail(null); setProblem('');
    api.investigation(investigationId)
      .then(d => {
        if (!active) return;
        // The same endpoint also returns drafts; only a saved case (with its pinned run and context) has a report.
        if (!d || d.record_type === 'draft' || d.is_draft || !d.run || !d.context || !d.snapshot || !d.baseline_version || !d.target_version) { onNotSaved(); return; }
        setDetail(d);
      })
      .catch(e => {
        if (!active) return;
        if (e instanceof ApiError && ['NOT_FOUND', 'HTTP_404'].includes(e.code)) { onNotSaved(); return; }
        setProblem(e instanceof ApiError ? `${e.message} (${e.code})` : e instanceof Error ? e.message : String(e));
      });
    return () => { active = false; };
  }, [investigationId, attempt]);

  useEffect(() => {
    if (!replay || !pending(replay.state)) return;
    let active = true;
    const timer = setTimeout(async () => {
      try { const next = await api.run(replay.id); if (active) { setReplay(next); if (!pending(next.state)) void refresh(); } }
      catch (e) { if (active) setReplayProblem(e instanceof Error ? e.message : String(e)); }
    }, 1500);
    return () => { active = false; clearTimeout(timer); };
  }, [replay]);

  if (problem) {
    return (
      <div className="view-container">
        <div className="alert error" role="alert">
          <span>This case could not be opened: {problem}</span>
          <button className="button secondary small" onClick={() => setAttempt(a => a + 1)}>Try again</button>
        </div>
        <button className="button secondary" onClick={onBack}><ArrowLeft size={15} /> Cases</button>
      </div>
    );
  }
  if (!detail) return <div className="view-container" role="status">Opening investigation…</div>;

  const ctx = detail.context as unknown as { mode?: string; unit_label?: string; currency?: string };
  const relational = ctx.mode === 'relational_snapshot';
  const kind = relational ? 'relational' : ctx.mode === 'flat_snapshot' ? 'single' : 'orders';
  const declaredUnit = ctx.unit_label || detail.run.result?.currency || ctx.currency || '';
  const unit = resultUnit(detail.run.result, declaredUnit);
  const defs = data.definitions;
  const runSha = (r: Run | null) => (r?.result as unknown as { evidence_artifact?: { sha256?: string } } | null)?.evidence_artifact?.sha256;
  const same = replay?.result && detail.run.result
    && replay.result.baseline_value === detail.run.result.baseline_value
    && replay.result.target_value === detail.run.result.target_value
    && replay.result.delta_target_minus_baseline === detail.run.result.delta_target_minus_baseline
    && replay.result.residual === detail.run.result.residual
    && (replay.result.ratio?.baseline.exact ?? null) === (detail.run.result.ratio?.baseline.exact ?? null)
    && (replay.result.ratio?.target.exact ?? null) === (detail.run.result.ratio?.target.exact ?? null)
    && (replay.result.ratio?.gap_exact ?? null) === (detail.run.result.ratio?.gap_exact ?? null)
    && (runSha(replay) ?? null) === (runSha(detail.run) ?? null);
  const replayBlocker = !detail.replay_available ? 'The frozen dataset is no longer available, so this case cannot be replayed. The saved result stays readable.'
    : data.mode !== 'isolated' ? 'Replay needs the isolated engine (scripts\\mg.ps1 start).' : '';

  const startReplay = async () => {
    setReplaying(true); setReplayProblem('');
    try { setReplay(await api.replayInvestigation(detail.id)); }
    catch (e) { setReplayProblem(e instanceof ApiError ? `${e.message} (${e.code})` : e instanceof Error ? e.message : String(e)); }
    finally { setReplaying(false); }
  };

  return (
    <div className="view-container guided-page">
      <div className="page-heading">
        <div>
          <h1>{detail.title}</h1>
          <p><span className="case-no">{caseNumber(detail.run.id)}</span> · {KIND_LABEL[kind]} · recorded by {detail.actor_label}, {new Date(detail.created_at).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}{unit && ` · ${unit}`}</p>
        </div>
        <div className="heading-actions">
          <button className="button secondary" onClick={onBack}><ArrowLeft size={15} /> Cases</button>
          <button className="button secondary" onClick={() => onEdit(relational)}><Pencil size={15} /> Edit comparison</button>
        </div>
      </div>

      <section aria-label="Result">
        {detail.run.result ? (
          <ResultReport run={detail.run} unit={declaredUnit} relational={relational}
            baselineLabel={versionName(defs, detail.baseline_version_id)} targetLabel={versionName(defs, detail.target_version_id)}
            baselineSql={detail.baseline_version.raw_sql} targetSql={detail.target_version.raw_sql}
            snapshot={detail.snapshot} versions={defs.flatMap(d => d.versions)}
            sealStatus={pending(replay?.state) || replaying ? 'verifying' : replay?.result ? (same ? 'intact' : 'broken') : null}
            note={<div className="case-conclusion"><h2>Conclusion</h2><p style={{ whiteSpace: 'pre-wrap' }}>{detail.decision}</p></div>} />
        ) : (
          <>
          <div className="case-conclusion"><h2>Conclusion</h2><p style={{ whiteSpace: 'pre-wrap' }}>{detail.decision}</p></div>
          <p className="disabled-reason">The saved run has no result ({detail.run.state}{detail.run.error?.message ? `: ${detail.run.error.message}` : ''}).</p>
          </>
        )}
        <div className="report-actions">
          <button className="button secondary small" onClick={() => void api.download(`/comparisons/${encodeURIComponent(detail.run.id)}/export?format=html`, `metricguard-${detail.run.id}.html`).catch(e => setReplayProblem(e instanceof Error ? e.message : String(e)))}>
            <Download size={14} /> HTML report
          </button>
          <button className="button secondary small" onClick={() => void api.download(`/comparisons/${encodeURIComponent(detail.run.id)}/export?format=json`, `metricguard-${detail.run.id}.json`).catch(e => setReplayProblem(e instanceof Error ? e.message : String(e)))}>
            <Download size={14} /> JSON report
          </button>
        </div>
      </section>

      <section className="guided-card" aria-labelledby="replay-title">
        <h2 id="replay-title">Replay</h2>
        <p className="lead">Re-runs the saved calculations on the same frozen dataset to confirm the result reproduces exactly. The saved case is not changed.</p>
        <div className="report-actions">
          <button className="button primary" disabled={replaying || pending(replay?.state) || Boolean(replayBlocker)} onClick={() => void startReplay()} aria-describedby={replayBlocker ? 'replay-blocker' : undefined}>
            <RefreshCw size={15} className={pending(replay?.state) ? 'spinning' : ''} /> {pending(replay?.state) ? 'Replaying…' : 'Replay saved inputs'}
          </button>
        </div>
        {replayBlocker && <span id="replay-blocker" className="disabled-reason">{replayBlocker}</span>}
        {replayProblem && <div className="alert error" role="alert"><span>{replayProblem}</span><button className="button secondary small" onClick={() => void startReplay()} disabled={Boolean(replayBlocker)}>Retry</button></div>}
        {replay?.state === 'failed' && <p className="disabled-reason">Replay did not complete: {replay.error?.message || 'unknown error'}.</p>}
        {replay?.result && (
          <p role="status" className={same ? '' : 'disabled-reason'}>
            {same ? `Replay reproduced the saved result exactly: same totals, same change and identical record evidence (run ${replay.id}).`
              : `Replay finished but differs from the saved result (current ${replay.result.baseline_value}, proposed ${replay.result.target_value}, change ${replay.result.delta_target_minus_baseline}). Investigate before relying on this case.`}
          </p>
        )}
      </section>
    </div>
  );
}
