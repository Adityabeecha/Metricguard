import { useState, type FormEvent } from 'react';
import { X } from 'lucide-react';
import { api, versionName, type Bootstrap, type InvestigationDetail } from '../api';
import { useDialog } from '../useDialog';

/** Pick the definition behind a version (the calculation a case pinned). */
export function definitionOfVersion(data: Pick<Bootstrap, 'definitions'>, versionId: string): string | null {
  return (data.definitions || []).find(d => d.versions.some(v => v.id === versionId))?.id || null;
}

/** Save a case's calculation as a governed metric, approved with this case as its evidence. */
export function SaveAsMetric({ data, detail, onClose, onSaved }: {
  data: Bootstrap; detail: InvestigationDetail; onClose: () => void; onSaved: (metricId: string) => void;
}) {
  const metrics = data.metrics || [];
  const [side, setSide] = useState<'target' | 'baseline'>('target');
  const [target, setTarget] = useState<'new' | string>('new');
  const versionId = side === 'target' ? detail.target_version_id : detail.baseline_version_id;
  const label = (id: string) => versionName(data.definitions, id);
  const plainName = (id: string) => (data.definitions || []).find(d => d.versions.some(v => v.id === id))?.name || '';
  const [name, setName] = useState(plainName(detail.target_version_id));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const ref = useDialog<HTMLDivElement>(true, onClose, !busy);
  const pid = data.project.id;

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const definitionId = definitionOfVersion(data, versionId);
    if (!definitionId) { setProblem('This calculation is no longer available in the workspace.'); return; }
    setBusy(true); setProblem('');
    try {
      const metricId = target === 'new'
        ? (await api.post<{ id: string }>(`/projects/${encodeURIComponent(pid)}/metrics`, { display_name: name.trim() })).id
        : target;
      const base = `/projects/${encodeURIComponent(pid)}/metrics/${encodeURIComponent(metricId)}`;
      await api.post(`${base}/bindings`, { definition_id: definitionId, status: 'candidate', approved_version_id: versionId });
      await api.post(`${base}/promote`, { definition_id: definitionId, approved_version_id: versionId, comparison_run_id: detail.run.id });
      onSaved(metricId);
    } catch (err) { setProblem(err instanceof Error ? err.message : String(err)); setBusy(false); }
  };

  return <div className="modal-backdrop" onClick={() => !busy && onClose()}>
    <div ref={ref} className="modal-card" role="dialog" aria-modal="true" aria-labelledby="save-metric-title" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
      <div className="modal-header">
        <div><h2 id="save-metric-title">Save as metric</h2>
          <p className="modal-subtitle">Make this calculation the approved definition of a metric. This case is kept as the evidence for the approval.</p></div>
        <button type="button" className="modal-close-button" onClick={onClose} aria-label="Close dialog"><X size={18} /></button>
      </div>
      <form onSubmit={save} style={{ padding: '16px 20px', display: 'grid', gap: 14 }}>
        <fieldset className="plain-fieldset">
          <legend className="form-label">Which calculation?</legend>
          <label><input type="radio" name="side" checked={side === 'target'} onChange={() => { setSide('target'); if (target === 'new') setName(plainName(detail.target_version_id)); }} /> Proposed · {label(detail.target_version_id)}</label>
          <label><input type="radio" name="side" checked={side === 'baseline'} onChange={() => { setSide('baseline'); if (target === 'new') setName(plainName(detail.baseline_version_id)); }} /> Current · {label(detail.baseline_version_id)}</label>
        </fieldset>
        <label className="form-label">Metric
          <select value={target} onChange={e => setTarget(e.target.value)}>
            <option value="new">New metric…</option>
            {metrics.map(m => <option key={m.id} value={m.id}>{m.display_name} (replace its approved definition)</option>)}
          </select>
        </label>
        {target === 'new' && <label className="form-label">Metric name
          <input autoFocus value={name} maxLength={160} onChange={e => setName(e.target.value)} required />
        </label>}
        {problem && <p className="disabled-reason" role="alert">{problem}</p>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="button primary" disabled={busy || (target === 'new' && !name.trim())}>{busy ? 'Saving…' : 'Save as metric'}</button>
        </div>
      </form>
    </div>
  </div>;
}
