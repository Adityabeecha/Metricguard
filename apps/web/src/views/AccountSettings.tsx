import { useEffect, useState } from 'react';
import { LogOut, Trash2, X } from 'lucide-react';
import { api, type ConnectedIdentity } from '../api';
import { deleteAccount } from '../authApi';
import { clearConnectedIdentity } from '../AuthScreens';
import { useDialog } from '../useDialog';
import '../auth.css';

export function AccountSettings() {
  const [identity, setIdentity] = useState<ConnectedIdentity | null>(null);
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState<'signout' | 'delete' | null>(null);
  const [problem, setProblem] = useState('');
  const close = () => { if (!busy) { setOpen(false); setConfirmation(''); setProblem(''); } };
  const dialog = useDialog<HTMLFormElement>(open, close, !busy);

  useEffect(() => {
    let active = true;
    api.identity().then(value => { if (active) setIdentity(value); }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  const signOut = async () => {
    setBusy('signout'); setProblem('');
    try {
      await api.endSession();
      clearConnectedIdentity();
      window.location.replace('/signin');
    } catch { setProblem('We couldn’t sign you out. Please try again.'); setBusy(null); }
  };

  const removeAccount = async () => {
    if (confirmation !== 'DELETE' || busy) return;
    setBusy('delete'); setProblem('');
    try {
      await deleteAccount();
      clearConnectedIdentity();
      window.location.replace('/signin');
    } catch (error) {
      setProblem(error instanceof Error ? error.message : 'Your account could not be deleted. Please try again.');
      setBusy(null);
    }
  };

  return (
    <section className="panel" aria-labelledby="account-title">
      <div className="panel-header"><h2 id="account-title">Account</h2><p>Manage your sign-in and personal account.</p></div>
      <div className="account-body">
        <p>{identity?.verified ? identity.user?.email || identity.user?.name || 'Signed in' : 'Using local access. Sign in to manage a personal account.'}</p>
        {problem && !open && <div className="auth-message error" role="alert">{problem}</div>}
        <div className="account-actions">
          {identity?.verified && <button type="button" className="button secondary" disabled={!!busy} onClick={() => void signOut()}><LogOut size={15} />{busy === 'signout' ? 'Signing out…' : 'Sign out'}</button>}
          {identity?.verified && <button type="button" className="button account-delete" disabled={!!busy || !identity.session} title={!identity.session ? 'Account deletion requires a browser sign-in with Google or GitHub.' : undefined} onClick={() => { setProblem(''); setOpen(true); }}><Trash2 size={15} /> Delete account</button>}
          {!identity?.verified && <a className="button secondary" href="/signin">Sign in</a>}
        </div>
        {identity?.verified && !identity.session && <p>Account deletion requires a browser sign-in with Google or GitHub. <a href="/signin">Sign in to continue</a>.</p>}
      </div>
      {open && <div className="modal-backdrop" onClick={close}>
        <form ref={dialog} data-dialog-managed="" className="modal-card" role="dialog" aria-modal="true" aria-labelledby="delete-account-title" aria-describedby="delete-account-description" onClick={event => event.stopPropagation()} onSubmit={event => { event.preventDefault(); void removeAccount(); }}>
          <div className="modal-header"><h2 id="delete-account-title">Delete your account?</h2><button type="button" className="modal-close-button" onClick={close} disabled={!!busy} aria-label="Close dialog"><X size={18} /></button></div>
          <div className="modal-body form-stack">
            <p id="delete-account-description">This permanently deletes your account, sign-ins, API keys, and workspaces you own with no other members, including their uploaded data and evidence. Shared workspace records remain available to other members.</p>
            {problem && <p className="auth-message error" role="alert">{problem}</p>}
            <label className="account-delete-label">Type DELETE to confirm<input type="text" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" spellCheck={false} disabled={!!busy} data-autofocus /></label>
          </div>
          <div className="modal-footer"><button type="button" className="button secondary" onClick={close} disabled={!!busy}>Cancel</button><button className="button account-delete" disabled={confirmation !== 'DELETE' || !!busy}><Trash2 size={15} />{busy === 'delete' ? 'Deleting account…' : 'Delete account permanently'}</button></div>
        </form>
      </div>}
    </section>
  );
}
