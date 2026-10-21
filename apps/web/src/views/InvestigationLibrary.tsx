import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, FolderOpen, Plus, X } from 'lucide-react';
import { api, versionName, type Bootstrap, type InvestigationDraft, type Run } from '../api';
import {
  buildLibraryItems, caseNumber, filterLibraryItems, hasDefinedResult, KIND_LABEL, readDismissedRuns, STAMP, unexplainedRuns, writeDismissedRuns,
  type LibraryItem, type LibraryStatus,
} from '../investigationLibrary';
import { groupDigits, resultDisplayValue, resultUnit, valueWithUnit } from '../resultSummary';
import { FirstRunGuide } from './FirstRun';
import { ItemActions } from './ItemActions';

type Props = {
  data: Bootstrap;
  workspaceId: string;
  onStart: () => void;
  /** Add a dataset (an empty workspace has nothing to compare yet). */
  onAddData?: () => void;
  onOpen: (item: LibraryItem) => void;
  onOpenRun: (run: Run) => void;
  /** The sample dataset is ready: open New comparison with it selected. */
  onSampleReady?: (snapshotId: string) => void;
  /** Reload the workspace after a case is renamed or deleted. */
  onChanged?: () => Promise<unknown>;
};

const STATUS_ORDER: LibraryStatus[] = ['saved', 'draft', 'running', 'refused'];

/** Cases: what still needs a conclusion, then every case in the workspace. */
export function InvestigationLibrary({ data, workspaceId, onStart, onAddData, onOpen, onOpenRun, onSampleReady, onChanged }: Props) {
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
  const [dismissed, setDismissed] = useState<Set<string>>(() => readDismissedRuns(workspaceId));
  useEffect(() => { setDismissed(readDismissedRuns(workspaceId)); }, [workspaceId]);
  const [showHidden, setShowHidden] = useState(false);
  const [showAllOpen, setShowAllOpen] = useState(false);
  const needing = useMemo(() => unexplainedRuns(data.runs || [], data.investigations || [], { dismissed }), [data, dismissed]);
  // Undefined results and dismissed runs, shown on request so nothing is lost.
  const hidden = useMemo(() => {
    const shown = new Set(needing.map(r => r.id));
    return unexplainedRuns(data.runs || [], data.investigations || [], { dismissed, includeUndefined: true, includeDismissed: true })
      .filter(r => !shown.has(r.id));
  }, [data, dismissed, needing]);
  const listed = showHidden ? [...needing, ...hidden] : needing;
  const open = showAllOpen ? listed : listed.slice(0, 4);
  const setDismissedRun = (id: string, value: boolean) => setDismissed(prev => {
    const next = new Set(prev);
    if (value) next.add(id); else next.delete(id);
    writeDismissedRuns(workspaceId, next);
    return next;
  });
  const datasetName = (id: string) => data.snapshots.find(s => s.id === id)?.name || 'Dataset';

  return (
    <div className="view-container">
      <div className="page-heading">
        <h1>Cases</h1>
        <div className="heading-actions">
          <button className="button primary" onClick={onStart}><Plus size={16} /> New comparison</button>
        </div>
      </div>

      {onAddData && onSampleReady && <FirstRunGuide data={data} projectId={workspaceId} onAddData={onAddData} onCompare={onStart} onSampleReady={onSampleReady} />}

      {(listed.length > 0 || hidden.length > 0) && (
        <section className="attention" aria-labelledby="attention-title">
          <div className="section-title"><h2 id="attention-title">Needs a conclusion</h2><span>{needing.length}</span></div>
          {open.map(run => {
            const unit = resultUnit(run.result, data.snapshots.find(s => s.id === run.snapshot_id)?.unit_label || '');
            const change = resultDisplayValue(run.result, run.result?.delta_target_minus_baseline);
            const defined = hasDefinedResult(run);
            const isDismissed = dismissed.has(run.id);
            return (
              <div className={`attention-row${isDismissed ? ' dismissed' : ''}`} key={run.id}>
                <span className="case-no">{caseNumber(run.id)}</span>
                <div>
                  <div className="title">{run.target_snapshot_id
                    ? <>{versionName(data.definitions, run.baseline_version_id)} · two loads</>
                    : <>{versionName(data.definitions, run.baseline_version_id)} → {versionName(data.definitions, run.target_version_id)}</>}</div>
                  <div className="meta">{datasetName(run.snapshot_id)}{run.target_snapshot_id && <> → {datasetName(run.target_snapshot_id)}</>}{!defined && ' · result Undefined (no records matched)'}{isDismissed && ' · dismissed'}</div>
                </div>
                <span className="change">{defined ? valueWithUnit(change, unit).replace(/^-/, '−') : 'Undefined'}</span>
                <span className="attention-actions">
                  <button className="button secondary small" onClick={() => onOpenRun(run)}>Write conclusion <ArrowRight size={12} /></button>
                  {isDismissed
                    ? <button className="button secondary small" onClick={() => setDismissedRun(run.id, false)}>Restore</button>
                    : <button className="button secondary small" aria-label={`Hide ${caseNumber(run.id)} from this list`}
                        title="Hide from this list. Nothing is deleted; you can restore it below."
                        onClick={() => setDismissedRun(run.id, true)}><X size={14} /> Hide</button>}
                </span>
              </div>
            );
          })}
          {listed.length === 0 && <p className="meta" style={{ margin: 0 }}>Nothing needs a conclusion.</p>}
          {(listed.length > 4 || hidden.length > 0) && (
            <div className="attention-more">
              {listed.length > 4 && (
                <button type="button" className="link-button" onClick={() => setShowAllOpen(v => !v)}>
                  {showAllOpen ? 'Show fewer' : `Show all ${listed.length}`}
                </button>
              )}
              {hidden.length > 0 && (
                <button type="button" className="link-button" aria-pressed={showHidden} onClick={() => setShowHidden(v => !v)}>
                  {showHidden ? 'Hide dismissed and Undefined results' : `Show ${hidden.length} dismissed or Undefined`}
                </button>
              )}
            </div>
          )}
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
            <p>{items.length ? 'Try another search or status.'
              : data.snapshots.length === 0 && onAddData && onSampleReady ? 'Your cases will appear here. Follow the three steps above to make the first one.'
              : data.snapshots.length === 0 && onAddData ? 'Start by adding the data to compare: a CSV, Parquet or Excel file, two connected tables, or a warehouse table. Then compare two calculations of a metric; the result becomes a case you can seal and replay.'
              : 'Compare two calculations of a metric; the result becomes a case you can seal and replay.'}</p>
            {items.length ? <button className="button secondary" onClick={() => { setQuery(''); setStatus('all'); }}>Show all cases</button>
              : data.snapshots.length === 0 && onAddData && onSampleReady ? null
              : data.snapshots.length === 0 && onAddData ? <button className="button primary" onClick={onAddData}><Plus size={15} /> Add data</button>
              : <button className="button primary" onClick={onStart}><Plus size={15} /> New comparison</button>}
          </div>
        ) : (
          <div className="table-scroll">
            <table className="inv-landing-table cases-table">
              <caption className="sr-only">Cases</caption>
              <thead><tr><th>Case</th><th>Name</th><th className="c-data">Dataset</th><th className="change-cell">Change</th><th>Status</th><th>Updated</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {visible.map(item => (
                  <tr key={`${item.source}:${item.id}`} onClick={() => onOpen(item)}>
                    <td className="c-case"><span className="case-no">{item.number}</span></td>
                    <td className="c-name">
                      <button type="button" className="name-link" onClick={e => { e.stopPropagation(); onOpen(item); }}>{item.title}</button>
                      <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                        {item.metricNames.length ? `Metric: ${item.metricNames.join(', ')}` : `${item.currentLabel} → ${item.proposedLabel}`}
                        {item.kind !== 'single' && ` · ${KIND_LABEL[item.kind]}`}
                        {item.twoLoads && ' · Two loads'}
                        <span className="c-data-inline"> · {item.datasetName}</span>
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
                    <td className="c-actions">{item.source === 'saved' && onChanged && (
                      <ItemActions name={item.title} kind="case"
                        deleteNote="The comparison and its evidence stay; only this saved case is removed."
                        onRename={async title => { await api.renameInvestigation(item.id, title); await onChanged(); }}
                        onDelete={async () => { await api.deleteInvestigation(item.id); await onChanged(); }} />
                    )}{item.source === 'draft' && (
                      <ItemActions name={item.title} kind="draft"
                        deleteNote="Only the unfinished draft is removed; any comparison it ran stays in your history."
                        onDelete={async () => { await api.deleteInvestigationDraft(workspaceId, item.id); await reload(); }} />
                    )}</td>
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
