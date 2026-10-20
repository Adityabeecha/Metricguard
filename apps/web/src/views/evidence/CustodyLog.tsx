import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import type { InvestigationDetail, Run } from '../../api';
import { caseNumber } from '../../investigationLibrary';
import { groupDigits } from '../../resultSummary';
import { HashPrint } from './HashPrint';
import { Stamp } from './Stamp';

const when = (iso?: string | null) => iso
  ? new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
const evidenceHash = (run: Run | null | undefined) =>
  (run?.result as unknown as { evidence_artifact?: { sha256?: string } } | null)?.evidence_artifact?.sha256 ?? null;
const pending = (state?: string) => state === 'queued' || state === 'running' || state === 'pending';

/** Whether a replay reproduced the sealed run: same totals and byte-identical evidence. */
export function replayMatches(original: Run, replay: Run): boolean {
  const a = original.result, b = replay.result;
  if (!a || !b) return false;
  return a.baseline_value === b.baseline_value && a.target_value === b.target_value
    && a.delta_target_minus_baseline === b.delta_target_minus_baseline && a.residual === b.residual
    && (a.ratio?.gap_exact ?? null) === (b.ratio?.gap_exact ?? null)
    && evidenceHash(original) === evidenceHash(replay);
}

type Props = {
  detail: InvestigationDetail;
  replays: Run[];
  live: Run | null;
  replaying: boolean;
  blocker: string;
  onReplay: () => void;
  onClose: () => void;
};

/** The custody log (docs/design/evidence-ui-spec.md §5): what happened to this evidence, in order. */
export function CustodyLog({ detail, replays, live, replaying, blocker, onReplay, onClose }: Props) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const run = detail.run;
  const provenance = (run.result?.provenance || {}) as { database_hash?: string };
  const started = run.started_at ? Date.parse(run.started_at) : NaN;
  const finished = run.completed_at ? Date.parse(run.completed_at) : NaN;
  const duration = Number.isFinite(started) && Number.isFinite(finished) ? Math.max(0, Math.round((finished - started) / 1000)) : null;
  const active = live && (pending(live.state) || replaying);
  const step = !active ? 0 : live?.state === 'running' ? 2 : 1;

  return (
    <>
      <div className="custody-backdrop" onClick={onClose} />
      <div className="custody-drawer" role="dialog" aria-modal="true" aria-labelledby="custody-title" tabIndex={-1} ref={panel}>
        <header>
          <div>
            <h2 id="custody-title">Custody log</h2>
            <span className="case-no">{caseNumber(run.id)}</span>
          </div>
          <button type="button" className="button secondary small" onClick={onClose} aria-label="Close the custody log"><X size={14} /></button>
        </header>
        <ol className="custody-log">
          <li className="done">
            <div>
              <div className="what">Dataset frozen</div>
              <div className="when">{when(detail.snapshot.created_at)}</div>
              <div className="detail">{detail.snapshot.name} · {groupDigits(String(detail.snapshot.row_count))} rows <HashPrint hash={provenance.database_hash} /></div>
            </div>
          </li>
          <li className="done">
            <div>
              <div className="what">Calculations pinned</div>
              <div className="detail">Current <HashPrint hash={detail.baseline_version.ir_hash as string | undefined} /></div>
              <div className="detail">Proposed <HashPrint hash={detail.target_version.ir_hash as string | undefined} /></div>
            </div>
          </li>
          <li className="done">
            <div>
              <div className="what">Compared on the isolated engine</div>
              <div className="when">{when(run.completed_at || run.created_at)}{duration != null ? ` · ${duration} s` : ''}</div>
              <div className="detail">Evidence <HashPrint hash={evidenceHash(run)} /></div>
            </div>
          </li>
          <li className="done">
            <div>
              <div className="what">Sealed as case {caseNumber(run.id)}</div>
              <div className="when">{when(detail.created_at)} · {detail.actor_label}</div>
            </div>
          </li>
          {replays.map(r => {
            const ok = replayMatches(run, r);
            const failed = r.state === 'failed';
            return (
              <li key={r.id} className={pending(r.state) ? '' : ok ? 'ok' : 'bad'}>
                <div>
                  <div className="what">{pending(r.state) ? 'Replay running' : failed ? 'Replay did not complete' : ok ? 'Replayed: identical' : 'Replayed: differs'}</div>
                  <div className="when">{when(r.completed_at || r.created_at)}</div>
                  {r.result && <div className="detail">Evidence <HashPrint hash={evidenceHash(r)} /> {ok ? <Stamp kind="replayed" /> : <Stamp kind="differs" />}</div>}
                  {failed && r.error?.message && <div className="detail">{r.error.message}</div>}
                </div>
              </li>
            );
          })}
        </ol>

        <div className="custody-verify">
          <button type="button" className="button primary" onClick={onReplay} disabled={Boolean(blocker) || Boolean(active)}>
            {active ? 'Verifying…' : 'Replay & verify'}
          </button>
          {blocker && <p className="disabled-reason">{blocker}</p>}
          {active && (
            <ol className="custody-steps" aria-live="polite">
              <li className={step >= 1 ? 'done' : ''}>Fetching the frozen dataset</li>
              <li className={step >= 2 ? 'done' : ''}>Running both calculations</li>
              <li>Comparing evidence hashes</li>
            </ol>
          )}
        </div>
      </div>
    </>
  );
}
