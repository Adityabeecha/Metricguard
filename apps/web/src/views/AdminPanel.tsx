import { useEffect, useState } from 'react';
import { api } from '../api';

type Overview = { accounts: number; guests: number; full_accounts: number; signups_by_day: Array<{ day: string; count: number }>;
  comparisons_last_24h: number; feedback_total: number };
type Note = { id: string; created_at: string; page: string | null; message: string; email: string | null; name: string | null };
type Account = { id: string; email: string; name: string; created_at: string; guest_expires_at: number | null };

/** Owner-only beta view (Settings → Beta admin): feedback, recent accounts, and promoting a guest. */
export function AdminPanel() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState('');
  const load = () => Promise.all([
    api.get<Overview>('/admin/overview'), api.get<{ items: Note[] }>('/admin/feedback'), api.get<{ items: Account[] }>('/admin/accounts'),
  ]).then(([o, f, a]) => { setOverview(o); setNotes(f.items); setAccounts(a.items); setProblem(''); })
    .catch(e => setProblem(e instanceof Error ? e.message : String(e)));
  useEffect(() => { void load(); }, []);
  const promote = async (account: Account) => {
    setBusy(account.id);
    try { await api.post(`/admin/accounts/${encodeURIComponent(account.id)}/promote`, {}); await load(); }
    catch (e) { setProblem(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(''); }
  };
  const when = (value: string) => new Date(value).toLocaleString();
  return <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
    {problem && <p className="disabled-reason" role="alert">{problem}</p>}
    {overview && <section className="panel">
      <div className="panel-header"><h2>Beta at a glance</h2><p>Every workspace on this server. Only owners listed in METRICGUARD_ADMIN_EMAILS see this tab.</p></div>
      <dl className="form-stack" style={{ padding: "16px 20px" }}>
        <div><dt>Accounts</dt><dd className="n">{overview.accounts} ({overview.full_accounts} full, {overview.guests} guests)</dd></div>
        <div><dt>Comparisons in the last 24 hours</dt><dd className="n">{overview.comparisons_last_24h}</dd></div>
        <div><dt>Feedback notes</dt><dd className="n">{overview.feedback_total}</dd></div>
        <div><dt>Sign-ups, last 7 days</dt><dd className="n">{overview.signups_by_day.map(d => `${d.day.slice(5)}: ${d.count}`).join(' · ')}</dd></div>
      </dl>
    </section>}
    <section className="panel">
      <div className="panel-header"><h2>Feedback</h2><p>Newest first.</p></div>
      {notes.length === 0 ? <p className="example">No feedback yet.</p> : <div className="table-scroll"><table className="ledger-table">
        <thead><tr><th>When</th><th>From</th><th>Page</th><th>Message</th></tr></thead>
        <tbody>{notes.map(n => <tr key={n.id}><td data-label="When">{when(n.created_at)}</td><td data-label="From">{n.email || n.name || 'Erased account'}</td>
          <td data-label="Page">{n.page || '—'}</td><td data-label="Message" style={{ whiteSpace: 'pre-wrap' }}>{n.message}</td></tr>)}</tbody>
      </table></div>}
    </section>
    <section className="panel">
      <div className="panel-header"><h2>Accounts</h2><p>Newest first. Promoting a guest gives full access now, with no redeploy.</p></div>
      <div className="table-scroll"><table className="ledger-table">
        <thead><tr><th>Email</th><th>Name</th><th>Signed up</th><th>Access</th></tr></thead>
        <tbody>{accounts.map(a => <tr key={a.id}><td data-label="Email">{a.email}</td><td data-label="Name">{a.name}</td><td data-label="Signed up">{when(a.created_at)}</td>
          <td data-label="Access">{a.guest_expires_at == null ? 'Full' : <>Guest, erased {new Date(a.guest_expires_at * 1000).toLocaleString()}{' '}
            <button type="button" className="button secondary small" disabled={busy === a.id} onClick={() => void promote(a)}>{busy === a.id ? 'Promoting…' : 'Give full access'}</button></>}</td></tr>)}</tbody>
      </table></div>
    </section>
  </div>;
}
