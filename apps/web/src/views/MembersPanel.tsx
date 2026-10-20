import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../api';

type Member = { user_id: string; name?: string | null; email?: string | null; role: string; revoked_at?: string | null };
type Invite = { id: string; email: string; role: string; created_at: string };

/** The text an owner sends a teammate: no secret, just where and how to sign in. */
export function inviteMessage(workspace: string, email: string, origin: string): string {
  return `You're invited to the “${workspace}” workspace on MetricGuard. Sign in with Google or GitHub using ${email}: ${origin}/signin`;
}

/** Settings → Members: who is in this workspace, and invites by email (owners only). */
export function MembersPanel({ workspaceId, workspaceName }: { workspaceId: string; workspaceName: string }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [role, setRole] = useState<string | null>(null);
  const [selfId, setSelfId] = useState('');
  const [email, setEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('analyst');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [copied, setCopied] = useState('');
  const [local, setLocal] = useState(false);
  const owner = role === 'owner';

  const load = async () => {
    setProblem('');
    try {
      const who = await api.identity(workspaceId);
      if (!who.verified) { setLocal(true); return; }
      setRole(who.role || null); setSelfId(who.user?.id || '');
      setMembers((await api.get<Member[]>(`/projects/${encodeURIComponent(workspaceId)}/members`)).filter(m => !m.revoked_at));
      if (who.role === 'owner') setInvites((await api.get<{ items: Invite[] }>(`/projects/${encodeURIComponent(workspaceId)}/invites`)).items);
    } catch (e) { setProblem(e instanceof Error ? e.message : String(e)); }
  };
  useEffect(() => { void load(); }, [workspaceId]);

  const invite = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setProblem('');
    try {
      await api.post(`/projects/${encodeURIComponent(workspaceId)}/invites`, { email: email.trim(), role: inviteRole });
      setEmail(''); await load();
    } catch (err) { setProblem(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };
  const copy = async (item: Invite) => {
    try { await navigator.clipboard.writeText(inviteMessage(workspaceName, item.email, window.location.origin)); setCopied(item.id); setTimeout(() => setCopied(''), 2000); }
    catch { setProblem('Copy failed. Select the address and copy it yourself.'); }
  };
  const revoke = async (item: Invite) => {
    try { await api.del(`/projects/${encodeURIComponent(workspaceId)}/invites/${encodeURIComponent(item.id)}`); await load(); }
    catch (e) { setProblem(e instanceof Error ? e.message : String(e)); }
  };
  const removeMember = async (member: Member) => {
    try { await api.post(`/projects/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(member.user_id)}/revoke`, {}); await load(); }
    catch (e) { setProblem(e instanceof Error ? e.message : String(e)); }
  };

  return <section className="panel members-panel">
    <div className="panel-header">
      <h2>Members</h2>
      <p>Everyone here can open this workspace's cases. Analysts can compare and save; readers can only view.</p>
    </div>
    <div style={{ padding: '16px 20px', display: 'grid', gap: 18 }}>
      {local && <p className="hint">Members and invites need signed-in accounts. On this local copy everyone uses the same access.</p>}
      {problem && <p className="disabled-reason" role="alert">{problem}</p>}
      {!local && <table className="ledger-table members-table">
        <thead><tr><th scope="col">Name</th><th scope="col">Email</th><th scope="col">Role</th>{owner && <th scope="col"><span className="sr-only">Actions</span></th>}</tr></thead>
        <tbody>
          {members.map(m => <tr key={m.user_id}>
            <td data-label="Name">{m.name || '—'}{m.user_id === selfId && <small> (you)</small>}</td>
            <td data-label="Email">{m.email || '—'}</td>
            <td data-label="Role">{m.role}</td>
            {owner && <td>{m.user_id !== selfId && <button type="button" className="link-button" onClick={() => void removeMember(m)}>Remove</button>}</td>}
          </tr>)}
        </tbody>
      </table>}

      {owner ? <>
        <form className="invite-form" onSubmit={invite}>
          <label>Invite by email<input type="email" required maxLength={254} placeholder="teammate@company.com" value={email} onChange={e => setEmail(e.target.value)} /></label>
          <label>Role<select value={inviteRole} onChange={e => setInviteRole(e.target.value)}>
            <option value="analyst">Analyst — compare and save</option>
            <option value="reader">Reader — view only</option>
          </select></label>
          <button type="submit" className="button primary" disabled={busy || !email.trim()}>{busy ? 'Inviting…' : 'Invite'}</button>
        </form>
        {invites.length > 0 && <div>
          <h3 className="members-sub">Waiting to sign in</h3>
          <ul className="invite-list">
            {invites.map(item => <li key={item.id}>
              <span><b>{item.email}</b> · {item.role}</span>
              <span className="invite-actions">
                <button type="button" className="link-button" onClick={() => void copy(item)}>{copied === item.id ? 'Message copied' : 'Copy invite message'}</button>
                <button type="button" className="link-button" onClick={() => void revoke(item)}>Revoke</button>
              </span>
            </li>)}
          </ul>
          <p className="hint">No email is sent. Copy the message and send it yourself; they join when they sign in with that address.</p>
        </div>}
      </> : role && <p className="hint">Only workspace owners can invite people.</p>}
    </div>
  </section>;
}
