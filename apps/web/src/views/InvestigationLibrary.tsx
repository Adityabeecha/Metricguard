import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, FolderOpen, Plus } from 'lucide-react';
import { api, versionName, type Bootstrap, type InvestigationDraft, type Run } from '../api';
import {
  buildLibraryItems, caseNumber, filterLibraryItems, KIND_LABEL, STAMP, unexplainedRuns,
  type LibraryItem, type LibraryStatus,
} from '../investigationLibrary';
import { groupDigits, resultDisplayValue, resultUnit, valueWithUnit } from '../resultSummary';

type Props = {
  data: Bootstrap;
  workspaceId: string;
  onStart: () => void;
  onOpen: (item: LibraryItem) => void;
  onOpenRun: (run: Run) => void;
};

const STATUS_ORDER: LibraryStatus[] = ['saved', 'draft', 'running', 'refused'];

/** Cases: what still needs a conclusion, then every case in the workspace. */
export function InvestigationLibrary({ data, workspaceId, onStart, onOpen, onOpenRun }: Props) {
  const [drafts, setDrafts] = useState<InvestigationDraft[]>(data.investigation_drafts || []);
  const [draftProblem, setDraftProblem] = useState('');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<LibraryStatus | 'all'>('all');

  const reload = async () => {
    setDraftProblem('');
    try { setDrafts((await api.listInvestigationDrafts(workspaceId)).items); }
    catch (e) { setDraftProblem(e instanceof Error ? e.message : String(e)); }
  };
  useEffect(() => { void reload(); }, [workspaceId]);

  const items = useMemo(() => buildLibraryItems(data, drafts), [data, drafts]);
  const visible = filterLibraryItems(items, { query, kind: 'all', status });
  const open = useMemo(() => unexplainedRuns(data.runs || [], data.investigations || []).slice(0, 4), [data]);
  const datasetName = (id: string) => data.snapshots.find(s => s.id === id)?.name || 'Dataset';

  return (
    <div className="view-container">
      <div className="page-heading">
        <h1>Cases</h1>
        <div className="heading-actions">
          <button className="button primary" onClick={onStart}><Plus size={16} /> New comparison</button>
        </div>
      </div>

      {open.length > 0 && (
        <section className="attention" aria-labelledby="attention-title">
          <div className="section-title"><h2 id="attention-title">Needs a conclusion</h2><span>{open.length}</span></div>
          {open.map(run => {
            const unit = resultUnit(run.result, data.snapshots.find(s => s.id === run.snapshot_id)?.unit_label || '');
            const change = resultDisplayValue(run.result, run.result?.delta_target_minus_baseline);
            return (
              <div className="attention-row" key={run.id}>
                <span className="case-no">{caseNumber(run.id)}</span>
                <div>
                  <div className="title">{versionName(data.definitions, run.baseline_version_id)} → {versionName(data.definitions, run.target_version_id)}</div>
                  <div className="meta">{datasetName(run.snapshot_id)}</div>
                </div>
                <span className="change">{valueWithUnit(change, unit)}</span>
                <button className="button secondary small" onClick={() => onOpenRun(run)}>Write conclusion <ArrowRight size={12} /></button>
              </div>
            );
          })}
        </section>
      )}

      <section aria-labelledby="cases-title">
        <div className="section-title"><h2 id="cases-title">All cases</h2><span>{items.length}</span></div>
        {draftProblem && <div className="alert error" role="alert"><span>Drafts could not be loaded ({draftProblem}). Saved cases are still listed.</span><button className="button secondary small" onClick={() => void reload()}>Retry</button></div>}
        <div className="cases-toolbar" role="search">
          <input type="search" aria-label="Search cases" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search by name, metric, dataset or case number" />
          <div className="chip-row" role="group" aria-label="Status">
            <button type="button" className="chip" aria-pressed={status === 'all'} onClick={() => setStatus('all')}>All<span className="n">{items.length}</span></button>
            {STATUS_ORDER.filter(s => items.some(i => i.status === s)).map(s => (
              <button type="button" key={s} className="chip" aria-pressed={status === s} onClick={() => setStatus(s)}>
                {STAMP[s].label}<span className="n">{items.filter(i => i.status === s).length}</span>
              </button>
            ))}
          </div>
        </div>

        {visible.length === 0 ? (
          <div className="empty-panel" style={{ padding: 40, textAlign: 'center', background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8 }}>
            <FolderOpen size={28} style={{ color: 'var(--kraft)' }} />
            <h2 style={{ fontSize: 16 }}>{items.length ? 'No cases match' : 'No cases yet'}</h2>
            <p>{items.length ? 'Try another search or status.' : 'Compare two calculations of a metric; the result becomes a case you can seal and replay.'}</p>
            {items.length ? <button className="button secondary" onClick={() => { setQuery(''); setStatus('all'); }}>Show all cases</button>
              : <button className="button primary" onClick={onStart}><Plus size={15} /> New comparison</button>}
          </div>
        ) : (
          <div className="table-scroll">
            <table className="inv-landing-table cases-table">
              <caption className="sr-only">Cases</caption>
              <thead><tr><th>Case</th><th>Name</th><th>Dataset</th><th className="change-cell">Change</th><th>Status</th><th>Updated</th></tr></thead>
              <tbody>
                {visible.map(item => (
                  <tr key={`${item.source}:${item.id}`} onClick={() => onOpen(item)}>
                    <td className="c-case"><span className="case-no">{caseNumber(item.id)}</span></td>
                    <td className="c-name">
                      <button type="button" className="name-link" onClick={e => { e.stopPropagation(); onOpen(item); }}>{item.title}</button>
                      <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                        {item.metricNames.length ? `Metric: ${item.metricNames.join(', ')}` : `${item.currentLabel} → ${item.proposedLabel}`}
                        {item.kind !== 'single' && ` · ${KIND_LABEL[item.kind]}`}
                      </div>
                    </td>
                    <td className="c-data">{item.datasetName}</td>
                    <td className="change-cell c-change">{item.change == null ? '—' : valueWithUnit(groupDigits(item.change).replace(/^-/, '\u2212'), item.unit)}</td>
                    <td className="c-status">
                      <span className={`stamp ${STAMP[item.status].tone}`}>{STAMP[item.status].label}</span>
                      {item.status === 'saved' && !item.hasConclusion && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>No conclusion</div>}
                      {item.problem && <div className="disabled-reason">{item.problem}</div>}
                    </td>
                    <td className="mono-cell c-updated" style={{ whiteSpace: 'nowrap' }}>{item.updatedAt ? new Date(item.updatedAt).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
