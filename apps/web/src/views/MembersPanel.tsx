import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from '../api';

type Member = { user_id: string; name?: string | null; email?: string | null; role: string; revoked_at?: string | null; guest_expires_at?: number | null };
type Invite = { id: string; email: string; role: string; created_at: string };

/** The text an owner sends a teammate: no secret, just where and how to sign in. */
export function inviteMessage(workspace: string, role: string, email: string, origin: string): string {
  return `You're invited to the MetricGuard workspace '${workspace}' as ${role}. Sign in at ${origin}/signin with Google or GitHub using ${email}, and you'll join it automatically.`;
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
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current); }, []);
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
    const text = inviteMessage(workspaceName, item.role, item.email, window.location.origin);
    try {
      try { await navigator.clipboard.writeText(text); }
      catch {
        const opener = document.activeElement as HTMLElement | null;
        const area = document.createElement('textarea'); area.value = text; area.readOnly = true;
        area.style.position = 'fixed'; area.style.left = '-9999px';
        document.body.appendChild(area); area.select();
        try { if (!document.execCommand('copy')) throw new Error('Copy failed'); }
        finally { area.remove(); opener?.focus(); }
      }
      setProblem(''); setCopied(item.id);
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(''), 2000);
    } catch { setProblem('Copy failed. Please try again.'); }
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
            <td data-label="Role">{m.role}{m.guest_expires_at != null && <span className="example" title="Guest accounts are erased with their own data at this time; they leave this workspace then.">
              {' '}· guest, leaves {new Date(m.guest_expires_at * 1000).toLocaleString()}</span>}</td>
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
                <button type="button" className="link-button" onClick={() => void copy(item)}>{copied === item.id ? 'Copied' : 'Copy invite message'}</button>
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
