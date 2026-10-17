import { useEffect, useState, type FormEvent } from 'react';
import { connectWithKey, disconnectKey } from './AuthScreens';
import { useDialog } from './useDialog';
import { KeyRound, LogOut } from 'lucide-react';
import { api, ApiError, getIdentityEpoch, hasApiCredential, onIdentityChange, type ConnectedIdentity } from './api';

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
        : 'This server requires authentication. Use “Connect with API key” with an issued key.';
    }
    if (error.code === 'FORBIDDEN' || error.code === 'HTTP_403') {
      return hasApiCredential()
        ? `The connected identity is not an active member of workspace “${workspaceId ?? ''}” or its role does not permit this action. Ask a workspace owner to grant or restore membership, or switch workspace. (${error.message})`
        : `Verified credentials are required. Use “Connect with API key”. (${error.message})`;
    }
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

type Props = { workspaceId: string };

/** Minimal connect/disconnect control. Always rendered, so it remains reachable when bootstrap fails. */
export function ConnectionControl({ workspaceId }: Props) {
  const epoch = useIdentityEpoch();
  const [open, setOpen] = useState(false);
  const [keyInput, setKeyInput] = useState('');
  const [identity, setIdentity] = useState<ConnectedIdentity | null>(null);
  const [problem, setProblem] = useState('');
  const formRef = useDialog<HTMLFormElement>(open, () => { setKeyInput(''); setOpen(false); });

  useEffect(() => {
    let active = true;
    setIdentity(null);
    setProblem('');
    if (!hasApiCredential()) return;
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

  const connect = (event: FormEvent) => {
    event.preventDefault();
    const value = keyInput.trim();
    setKeyInput('');
    if (!value) return;
    setOpen(false);
    connectWithKey(value);
  };

  const connected = hasApiCredential();
  const label = connected
    ? identity?.user ? `${identity.user.name || identity.user.email || identity.user.id}${identity.role ? ` · ${identity.role}` : ' · no workspace role'}` : problem ? 'Key not accepted' : 'Verifying…'
    : 'Local access (no key)';

  return (
    <div className="connection-control" style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className={`topbar-status ${connected && identity?.role && !problem ? 'ready' : 'warning'}`} title={problem || undefined}>
        <KeyRound size={13} /> {label}
      </span>
      {connected ? (
        <button type="button" className="button secondary small" onClick={disconnectKey}>
          <LogOut size={13} /> Disconnect
        </button>
      ) : null}
      <button type="button" className="button secondary small" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        {connected ? 'Replace key' : 'Connect with API key'}
      </button>
      {open && (
        <form ref={formRef} role="dialog" aria-label="Connect with API key" onSubmit={connect} className="panel" style={{ position: 'absolute', right: 0, top: '110%', zIndex: 50, padding: 12, width: 320, display: 'grid', gap: 8 }}>
          <label>
            Issued API key
            <input type="password" autoComplete="off" spellCheck={false} value={keyInput} onChange={e => setKeyInput(e.target.value)} data-autofocus />
          </label>
          <small>Held in this tab’s memory only; it is never saved. Reloading the page signs you out.</small>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" className="button secondary small" onClick={() => { setKeyInput(''); setOpen(false); }}>Cancel</button>
            <button className="button primary small" disabled={!keyInput.trim()} title={keyInput.trim() ? undefined : 'Paste your API key first'}>Connect</button>
          </div>
        </form>
      )}
      {problem && <span role="alert" className="sr-only">{problem}</span>}
    </div>
  );
}
