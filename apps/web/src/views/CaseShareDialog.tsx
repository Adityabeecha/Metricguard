import { useEffect, useState } from 'react';
import { Copy, X } from 'lucide-react';
import { api, type CaseShare } from '../api';
import { useDialog } from '../useDialog';
import '../publicCase.css';

export function CaseShareDialog({ caseId, memberLink, onClose }: { caseId: string; memberLink: string; onClose: () => void }) {
  const [shares, setShares] = useState<CaseShare[]>([]);
  const [expires, setExpires] = useState('7');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState('');
  const [link, setLink] = useState('');
  const [note, setNote] = useState('');
  const ref = useDialog(true, onClose, !busy);
  const base = `/investigations/${encodeURIComponent(caseId)}/shares`;
  const load = () => api.get<{ items: CaseShare[] }>(base).then(value => setShares(value.items));
  useEffect(() => {
    let active = true;
    api.get<{ items: CaseShare[] }>(base).then(value => { if (active) setShares(value.items); })
      .catch(error => { if (active) setProblem(error instanceof Error ? error.message : String(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [base]);
  const copy = async (value: string) => {
    try { await navigator.clipboard.writeText(value); setNote('Link copied'); }
    catch { setProblem('The browser could not copy the link. Select and copy it below.'); }
  };
  const create = async () => {
    setBusy(true); setProblem(''); setNote(''); setLink('');
    try {
      const value = await api.post<CaseShare & { token: string }>(base, { expires_in_days: expires === 'never' ? null : Number(expires) });
      setLink(`${window.location.origin}/shared-cases/${value.token}`);
      await load();
    } catch (error) { setProblem(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  const revoke = async (id: string) => {
    setBusy(true); setProblem('');
    try { await api.del(`${base}/${encodeURIComponent(id)}`); setLink(''); await load(); setNote('Public link revoked'); }
    catch (error) { setProblem(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  return <div className="modal-backdrop" onClick={() => !busy && onClose()}>
    <div ref={ref} className="modal-card" role="dialog" aria-modal="true" aria-labelledby="case-share-title" onClick={event => event.stopPropagation()} style={{ maxWidth: 600 }}>
      <div className="modal-header"><div><h2 id="case-share-title">Share sealed case</h2><p className="modal-subtitle">Give someone a read-only view of this case.</p></div>
        <button className="modal-close-button" aria-label="Close share dialog" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <div className="case-share-body">
        <div><h3>Workspace members</h3><p>Members can open the case with their existing access.</p><button className="button secondary small" onClick={() => void copy(memberLink)}><Copy size={14} /> Copy member link</button></div>
        <section aria-labelledby="public-share-heading"><h3 id="public-share-heading">Public read-only link</h3>
          <p>Anyone with the link can read this case, its calculations and record evidence. Choose an expiry; you can revoke access at any time.</p>
          <label htmlFor="case-share-expiry">Link expires</label>{' '}
          <select id="case-share-expiry" disabled={busy || loading} value={expires} onChange={event => setExpires(event.target.value)}>
            <option value="7">After 7 days</option><option value="30">After 30 days</option><option value="never">Never</option>
          </select>{' '}<button className="button primary small" disabled={busy || loading} onClick={() => void create()}>{busy ? 'Working…' : 'Create public link'}</button>
        </section>
        {link && <div className="case-share-new-link"><label htmlFor="created-case-link">New public link — copy it now; it is only shown once</label>
          <input id="created-case-link" readOnly value={link} onFocus={event => event.target.select()} />
          <div><button className="button secondary small" onClick={() => void copy(link)}><Copy size={14} /> Copy public link</button></div></div>}
        {note && <p role="status">{note}</p>}
        {problem && <div className="alert error" role="alert"><span>{problem}</span></div>}
        {loading ? <p role="status">Loading public links…</p> : <section aria-label="Existing public links"><h3>Public links</h3>
          {!shares.length ? <p className="hint">No public links have been created.</p> : <ul className="case-share-links">{shares.map(share => {
            const expired = Boolean(share.expires_at && new Date(share.expires_at).getTime() <= Date.now());
            return <li key={share.id}><span>Created {new Date(share.created_at).toLocaleString('en-GB')}<br /><small>{share.revoked_at ? 'Revoked' : expired ? 'Expired' : share.expires_at ? `Expires ${new Date(share.expires_at).toLocaleString('en-GB')}` : 'No expiry'}</small></span>
              {!share.revoked_at && !expired && <button className="button secondary small" disabled={busy} onClick={() => void revoke(share.id)}>Revoke link</button>}</li>;
          })}</ul>}
        </section>}
        <div><button className="button secondary" disabled={busy} onClick={onClose}>Done</button></div>
      </div>
    </div>
  </div>;
}
