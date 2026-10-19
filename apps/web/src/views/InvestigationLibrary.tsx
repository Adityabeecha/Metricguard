import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, GitCompareArrows, Plus, RefreshCw } from 'lucide-react';
import { api, type Bootstrap, type InvestigationDraft } from '../api';
import {
  buildLibraryItems, filterLibraryItems, KIND_LABEL, STATUS_LABEL,
  type LibraryItem, type LibraryKind, type LibraryStatus,
} from '../investigationLibrary';

type Props = {
  data: Bootstrap;
  workspaceId: string;
  onStart: () => void;
  onOpen: (item: LibraryItem) => void;
};

export function InvestigationLibrary({ data, workspaceId, onStart, onOpen }: Props) {
  const [drafts, setDrafts] = useState<InvestigationDraft[]>(data.investigation_drafts || []);
  const [loading, setLoading] = useState(false);
  const [draftProblem, setDraftProblem] = useState('');
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<LibraryKind | 'all'>('all');
  const [status, setStatus] = useState<LibraryStatus | 'all'>('all');

  const reload = async () => {
    setLoading(true); setDraftProblem('');
    try { setDrafts((await api.listInvestigationDrafts(workspaceId)).items); }
    catch (e) { setDraftProblem(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  };
  useEffect(() => { void reload(); }, [workspaceId]);

  const items = useMemo(() => buildLibraryItems(data, drafts), [data, drafts]);
  const visible = filterLibraryItems(items, { query, kind, status });
  const kinds = (Object.keys(KIND_LABEL) as LibraryKind[]).filter(k => items.some(i => i.kind === k));

  return (
    <div className="view-container">
      <div className="page-heading">
        <div>
          <div className="eyebrow">INVESTIGATIONS <span className="eyebrow-line" /></div>
          <h1>Investigations</h1>
          <p>Every saved comparison and draft in this workspace, from single tables and two-table datasets alike.</p>
        </div>
        <div className="heading-actions">
          <button className="button secondary" onClick={() => void reload()} disabled={loading}>
            <RefreshCw size={15} className={loading ? 'spinning' : ''} /> Refresh
          </button>
          <button className="button primary" onClick={onStart}><Plus size={16} /> Start new comparison</button>
        </div>
      </div>

      {draftProblem && <div className="alert error" role="alert"><span>Drafts could not be loaded ({draftProblem}). Saved investigations are still listed.</span><button className="button secondary small" onClick={() => void reload()}>Retry</button></div>}

      <div className="library-toolbar" role="search">
        <label>Search
          <input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Case name, metric or dataset" />
        </label>
        <label>Dataset type
          <select value={kind} onChange={e => setKind(e.target.value as LibraryKind | 'all')}>
            <option value="all">All types ({items.length})</option>
            {kinds.map(k => <option key={k} value={k}>{KIND_LABEL[k]} ({items.filter(i => i.kind === k).length})</option>)}
          </select>
        </label>
        <label>Status
          <select value={status} onChange={e => setStatus(e.target.value as LibraryStatus | 'all')}>
            <option value="all">Any status</option>
            {(Object.keys(STATUS_LABEL) as LibraryStatus[]).map(s => <option key={s} value={s}>{STATUS_LABEL[s]} ({items.filter(i => i.status === s).length})</option>)}
          </select>
        </label>
      </div>

      {visible.length === 0 ? (
        <div className="empty-panel" style={{ padding: 40, textAlign: 'center', background: '#fff', border: '1px solid #E4E7EC', borderRadius: 8, marginTop: 14 }}>
          <GitCompareArrows size={32} style={{ color: '#245BFF' }} />
          <h2 style={{ fontSize: 16 }}>{items.length ? 'No investigations match these filters' : 'No investigations yet'}</h2>
          <p>{items.length ? 'Clear the search or choose “All types”.' : 'Start a comparison to see how two calculations of a metric differ, then save your explanation here.'}</p>
          {items.length ? <button className="button secondary" onClick={() => { setQuery(''); setKind('all'); setStatus('all'); }}>Clear filters</button>
            : <button className="button primary" onClick={onStart}><Plus size={15} /> Start new comparison</button>}
        </div>
      ) : (
        <div className="table-scroll" style={{ marginTop: 14 }}>
          <table className="inv-landing-table">
            <caption className="sr-only">Investigations</caption>
            <thead><tr><th>Investigation</th><th>Type</th><th>Dataset</th><th>Change</th><th>Status</th><th>Updated</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {visible.map(item => (
                <tr key={`${item.source}:${item.id}`}>
                  <td>
                    <strong>{item.title}</strong>
                    <div style={{ fontSize: 12, color: '#667085' }}>
                      {item.metricNames.length ? `Metric: ${item.metricNames.join(', ')}` : `${item.currentLabel} → ${item.proposedLabel}`}
                    </div>
                  </td>
                  <td><span className={`library-type ${item.kind === 'relational' ? 'relational' : ''}`}>{KIND_LABEL[item.kind]}</span></td>
                  <td>{item.datasetName}</td>
                  <td className="mono-cell">{item.change == null ? '—' : `${item.change}${item.unit ? ` ${item.unit}` : ''}`}</td>
                  <td>{STATUS_LABEL[item.status]}{item.status === 'saved' && !item.hasConclusion && ' · no conclusion'}
                    {item.problem && <div className="disabled-reason">{item.problem}</div>}</td>
                  <td>{item.updatedAt ? new Date(item.updatedAt).toLocaleDateString() : '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="button secondary small" onClick={() => onOpen(item)} aria-label={`Open ${item.title}`}>
                      {item.source === 'saved' ? 'Open' : 'Continue'} <ArrowRight size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
