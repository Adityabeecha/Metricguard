import { resultUnit } from '../resultSummary';
import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, BookmarkCheck, Copy, Download, Link2, Pencil, Printer, RefreshCw } from 'lucide-react';
import { CaseShareDialog } from './CaseShareDialog';
import { CasePrintCustody } from './evidence/CasePrintCustody';
import { SaveAsMetric } from './SaveAsMetric';
import { api, ApiError, versionName, type Bootstrap, type InvestigationDetail, type Run } from '../api';
import { caseNumber, KIND_LABEL } from '../investigationLibrary';
import { ResultReport } from './ResultReport';
import { CustodyLog, replayMatches } from './evidence/CustodyLog';
import { caseSummaryMarkdown } from './evidence/copySummary';
import { loadSummaryBreakdown, savedFilterColumns } from './evidence/breakdownSelection';

/** Copy text to the clipboard, with a fallback for browsers without the async clipboard API. */
async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); return; } catch { /* fall back below */ }
  const area = document.createElement('textarea');
  area.value = text; area.setAttribute('readonly', ''); area.style.position = 'fixed'; area.style.opacity = '0';
  document.body.appendChild(area); area.select();
  try { document.execCommand('copy'); } finally { area.remove(); }
}

const pending = (state?: string) => state === 'queued' || state === 'running' || state === 'pending';

type Props = {
  data: Bootstrap;
  investigationId: string;
  onBack: () => void;
  onEdit: (relational: boolean) => void;
  /** The id is not a saved investigation (e.g. a draft or a bare run): hand over to the editor. */
  onNotSaved: () => void;
  refresh: () => Promise<Bootstrap | null>;
  /** Open a metric (after "Save as metric"). */
  onOpenMetric?: (metricId: string) => void;
};

export function CaseReport({ data, investigationId, onBack, onEdit, onNotSaved, refresh, onOpenMetric }: Props) {
  const [savingMetric, setSavingMetric] = useState(false);
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [problem, setProblem] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [replay, setReplay] = useState<Run | null>(null);
  const [replayProblem, setReplayProblem] = useState('');
  const [replaying, setReplaying] = useState(false);
  const [custodyOpen, setCustodyOpen] = useState(false);
  const [copied, setCopied] = useState<'' | 'summary' | 'link'>('');
  const [shareOpen, setShareOpen] = useState(false);
  const [canShare, setCanShare] = useState(false);
  const copyRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    let active = true;
    api.identity().then(identity => { if (active) setCanShare(!identity.verified || identity.role === 'owner' || identity.role === 'analyst'); }).catch(() => {});
    return () => { active = false; };
  }, [data.project.id]);

  // "C" copies the case summary (ignored while typing or with a modifier key).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (e.key.toLowerCase() !== 'c' || e.metaKey || e.ctrlKey || e.altKey) return;
      if (target && (target.closest('input, textarea, select, [contenteditable="true"]'))) return;
      e.preventDefault();
      copyRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(''), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

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
  if (!detail) return <div className="view-container" role="status">Opening case…</div>;

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

  const link = `${window.location.origin}/w/${encodeURIComponent(data.project.id)}/cases/${encodeURIComponent(detail.id)}`;
  // Replays of this case recorded on the server, plus the one running now.
  const replays = [...(data.runs || []).filter(r => r.investigation_id === detail.id && r.id !== replay?.id), ...(replay ? [replay] : [])]
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const verified = [...replays].reverse().find(r => r.result && replayMatches(detail.run, r));
  const breakdownColumns = savedFilterColumns(detail.target_version, detail.baseline_version);
  const copySummary = async () => {
    try {
      const [summary, breakdown] = await Promise.all([
        api.evidenceSummary(detail.run.id).catch(() => null),
        loadSummaryBreakdown((column, top) => api.evidenceBreakdown(detail.run.id, column, top), breakdownColumns).catch(() => null),
      ]);
      await copyText(caseSummaryMarkdown({ detail, unit, summary, breakdown, link,
        replayVerifiedAt: verified?.completed_at || verified?.created_at || null }));
      setCopied('summary');
    } catch (e) { setReplayProblem(`The summary could not be copied: ${e instanceof Error ? e.message : String(e)}`); }
  };
  copyRef.current = () => { void copySummary(); };

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
          <p><span className="case-no">{caseNumber(detail.run.id)}</span> · {KIND_LABEL[kind]}{(detail.run as { target_snapshot_id?: string }).target_snapshot_id ? ' · two loads' : ''} · recorded by {detail.actor_label}, {new Date(detail.created_at).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}{unit && ` · ${unit}`}</p>
        </div>
        <div className="heading-actions">
          <button className="button secondary small" onClick={onBack}><ArrowLeft size={15} /> Cases</button>
          <button className="button secondary small" onClick={() => void copySummary()} title="Copy a summary for Slack, Jira or a pull request (shortcut: C)">
            <Copy size={15} /> Copy summary
          </button>
          {canShare && <button className="button secondary small" title="Manage public read-only case links" onClick={() => setShareOpen(true)}><Link2 size={15} /> Share</button>}
          <button className="button secondary small" onClick={() => window.print()}><Printer size={15} /> Print / Save PDF</button>
          <button className="button secondary small" onClick={() => onEdit(relational)}><Pencil size={15} /> Edit &amp; re-run</button>
          {!relational && detail.run.result && <button className="button secondary small" onClick={() => setSavingMetric(true)} title="Make one of these calculations the approved definition of a metric"><BookmarkCheck size={15} /> Save as metric</button>}
          {copied && <span className="copied-note" role="status">{copied === 'summary' ? 'Summary copied' : 'Link copied'}</span>}
        </div>
      </div>

      <section aria-label="Result">
        {detail.run.result ? (
          <ResultReport savedCase run={detail.run} unit={declaredUnit} relational={relational}
            baselineLabel={versionName(defs, detail.baseline_version_id)} targetLabel={versionName(defs, detail.target_version_id)}
            baselineSql={detail.baseline_version.raw_sql} targetSql={detail.target_version.raw_sql}
            snapshot={detail.snapshot} versions={defs.flatMap(d => d.versions)} breakdownColumns={breakdownColumns}
            sealStatus={pending(replay?.state) || replaying ? 'verifying' : replay?.result ? (same ? 'intact' : 'broken') : null}
            onCustody={() => setCustodyOpen(true)}
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
          <button className="button secondary small" onClick={() => void api.download(`/comparisons/${encodeURIComponent(detail.run.id)}/evidence.csv`, `metricguard-${detail.run.id}-records.csv`).catch(e => setReplayProblem(e instanceof Error ? e.message : String(e)))}>
            <Download size={14} /> Records (CSV)
          </button>
        </div>
      </section>

      <section className="guided-card" aria-labelledby="replay-title">
        <h2 id="replay-title">Replay &amp; verify</h2>
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
      {savingMetric && <SaveAsMetric data={data} detail={detail} onClose={() => setSavingMetric(false)}
        onSaved={async metricId => { setSavingMetric(false); await refresh(); onOpenMetric?.(metricId); }} />}
      {shareOpen && <CaseShareDialog caseId={detail.id} memberLink={link} onClose={() => setShareOpen(false)} />}
      <div className="print-only print-custody"><CasePrintCustody detail={detail} replays={replays} /></div>
      {custodyOpen && (
        <CustodyLog detail={detail} replays={replays} live={replay} replaying={replaying} blocker={replayBlocker}
          onReplay={() => void startReplay()} onClose={() => setCustodyOpen(false)} />
      )}
    </div>
  );
}
