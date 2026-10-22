import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { clearConnectedIdentity, connectWithKey } from './AuthScreens';
import { useDialog } from './useDialog';
import { KeyRound, LogOut, Settings, UserRound, X } from 'lucide-react';
import { api, ApiError, getIdentityEpoch, hasApiCredential, onIdentityChange, type ConnectedIdentity } from './api';
import { accountInitials, accountMenuActions, endAccountSession, guestNotice, signOutDestination } from './authFlow';
import './account-menu.css';

/** Re-renders when the in-memory credential is connected, replaced or disconnected. */
export function useIdentityEpoch(): number {
  const [epoch, setEpoch] = useState(getIdentityEpoch);
  useEffect(() => onIdentityChange(() => setEpoch(getIdentityEpoch())), []);
  return epoch;
}

/**
 * Unsaved, client-only input that should survive identity changes (memory only,
 * never sent anywhere until the user submits it). Server data is never stored here.
 */
const drafts = new Map<string, unknown>();
export function readDraft<T>(key: string): T | undefined { return drafts.get(key) as T | undefined; }
export function writeDraft(key: string, value: unknown) { drafts.set(key, value); }

export function describeAuthError(error: unknown, workspaceId?: string): string {
  if (error instanceof ApiError) {
    if (error.code === 'UNAUTHORIZED' || error.code === 'HTTP_401') {
      return hasApiCredential()
        ? 'The connected API key is invalid or inactive. Disconnect, or connect with a different issued key.'
        : 'This server requires authentication. Sign in to continue.';
    }
    if (error.code === 'FORBIDDEN' || error.code === 'HTTP_403') {
      return hasApiCredential()
        ? `The connected identity is not an active member of workspace “${workspaceId ?? ''}” or its role does not permit this action. Ask a workspace owner to grant or restore membership, or switch workspace. (${error.message})`
        : `Sign in with an account that has access to this workspace. (${error.message})`;
    }
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

/** Shared identity lookup for the account menu and new-case author defaults. */
export function useConnectedIdentity(workspaceId: string) {
  const epoch = useIdentityEpoch();
  const [identity, setIdentity] = useState<ConnectedIdentity | null>(null);
  const [problem, setProblem] = useState('');
  const identityKey = `${epoch}:${workspaceId}`;
  const [loadedKey, setLoadedKey] = useState(identityKey);
  useEffect(() => {
    let active = true;
    setIdentity(null);
    setProblem('');
    setLoadedKey(identityKey);
    api.identity(workspaceId).then(result => { if (active) setIdentity(result); }).catch(async e => {
      if (!active || (e instanceof ApiError && e.code === 'IDENTITY_CHANGED')) return;
      // Distinguish an invalid key from missing workspace membership.
      try {
        const base = await api.identity();
        if (active) setIdentity(base);
      } catch { /* invalid key: keep identity empty */ }
      if (active) setProblem(describeAuthError(e, workspaceId));
    });
    return () => { active = false; };
  }, [epoch, workspaceId]);

  return loadedKey === identityKey ? { identity, problem } : { identity: null, problem: '' };
}

type AccountProps = { workspaceId: string; authRequired: boolean | null; onSettings: () => void };

/** A compact identity entry point; local key controls live inside the menu. */
export function AccountMenu({ workspaceId, authRequired, onSettings }: AccountProps) {
  const { identity, problem: identityProblem } = useConnectedIdentity(workspaceId);
  const epoch = useIdentityEpoch();
  const [open, setOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [keyInput, setKeyInput] = useState('');
  const [problem, setProblem] = useState('');
  const [signingOut, setSigningOut] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const initialMenuFocus = useRef<'first' | 'last'>('first');
  const menuId = useId();
  const actions = accountMenuActions(identity, authRequired, hasApiCredential(), Boolean(identityProblem));
  const closeKey = () => { setKeyInput(''); setKeyOpen(false); };
  const formRef = useDialog<HTMLFormElement>(keyOpen, closeKey);

  const close = (returnFocus = true) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  };

  useEffect(() => {
    setOpen(false);
    setKeyOpen(false);
    setKeyInput('');
    setProblem('');
  }, [epoch, workspaceId]);

  useEffect(() => {
    if (!actions.includes('key')) closeKey();
  }, [authRequired, identity?.session]);

  useEffect(() => {
    if (!open) return;
    const items = menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]');
    items?.[initialMenuFocus.current === 'last' ? items.length - 1 : 0]?.focus();
    const outside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close();
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  const menuKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Tab') { close(); return; }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') || []);
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  };

  const connect = (event: FormEvent) => {
    event.preventDefault();
    const value = keyInput.trim();
    setKeyInput('');
    if (!value) return;
    closeKey();
    connectWithKey(value);
  };

  const signOut = async () => {
    setSigningOut(true);
    setProblem('');
    try {
      await endAccountSession(api.endSession, clearConnectedIdentity);
      close();
      const destination = signOutDestination(authRequired);
      if (destination) {
        window.history.replaceState(null, '', destination);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }
    } catch {
      setProblem('Sign out could not be completed. Please try again.');
    } finally { setSigningOut(false); }
  };
  const name = identity?.user?.name?.trim() || identity?.user?.email || (identity?.verified ? 'Signed-in account' : identity ? 'Local access' : 'Account');

  return (
    <div className="account-control" ref={rootRef}>
      <button ref={triggerRef} type="button" className="avatar account-trigger" aria-label={`Account menu: ${name}`}
        aria-haspopup="menu" aria-controls={open ? menuId : undefined} aria-expanded={open}
        onClick={() => { initialMenuFocus.current = 'first'; setOpen(value => !value); }} onKeyDown={event => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault(); initialMenuFocus.current = event.key === 'ArrowUp' ? 'last' : 'first'; setOpen(true);
          }
        }}>
        {identity?.user ? accountInitials(identity.user) : <UserRound size={17} aria-hidden="true" />}
      </button>
      {open && <div className="account-popover">
        <div className="account-details"><strong>{name}</strong>{identity?.user?.email && <span>{identity.user.email}</span>}
          {identity?.role && <small>{identity.role}</small>}
        </div>
        <div id={menuId} ref={menuRef} role="menu" aria-label="Account" onKeyDown={menuKeys}>
          <button type="button" role="menuitem" tabIndex={-1} onClick={() => { close(); onSettings(); }}><Settings size={15} />Settings</button>
          {actions.includes('key') && <button type="button" role="menuitem" tabIndex={-1} onClick={() => { close(); setKeyOpen(true); }}><KeyRound size={15} />{hasApiCredential() ? 'Replace API key' : 'Use an API key'}</button>}
          {actions.includes('signout') && <button type="button" role="menuitem" tabIndex={-1} disabled={signingOut} onClick={() => void signOut()}><LogOut size={15} />{signingOut ? 'Signing out…' : 'Sign out'}</button>}
        </div>
        {(problem || identityProblem) && <p className="account-problem" role="alert">{problem || identityProblem}</p>}
      </div>}
      {keyOpen && actions.includes('key') && (
        <form ref={formRef} role="dialog" aria-modal="true" aria-label="Connect with API key" onSubmit={connect} className="account-key-dialog">
          <div className="account-key-heading"><strong>Connect with API key</strong><button type="button" className="icon-button" aria-label="Close API key form" onClick={closeKey}><X size={16} /></button></div>
          <label>
            Issued API key
            <input type="password" autoComplete="off" spellCheck={false} value={keyInput} onChange={e => setKeyInput(e.target.value)} data-autofocus />
          </label>
          <small>Held in this tab’s memory only; it is never saved. Reloading the page signs you out.</small>
          <div className="account-key-actions">
            <button type="button" className="button secondary small" onClick={closeKey}>Cancel</button>
            <button className="button primary small" disabled={!keyInput.trim()} title={keyInput.trim() ? undefined : 'Paste your API key first'}>Connect</button>
          </div>
        </form>
      )}
    </div>
  );
}

/** Shown to guest accounts (open sign-up) on every workspace page. */
export function GuestNotice({ workspaceId }: { workspaceId: string }) {
  const { identity } = useConnectedIdentity(workspaceId);
  const [, tick] = useState(0);
  useEffect(() => { const t = window.setInterval(() => tick(n => n + 1), 60000); return () => window.clearInterval(t); }, []);
  const text = guestNotice(identity?.guest_expires_at);
  return text ? <div className="alert notice guest-notice" role="status"><span>{text}</span></div> : null;
}
