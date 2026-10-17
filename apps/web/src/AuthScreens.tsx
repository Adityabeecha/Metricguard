import { useEffect, useState, type FormEvent } from 'react';
import { Check, KeyRound, Layers3, RefreshCw, ServerCrash, ShieldOff, X } from 'lucide-react';
import { api, hasApiCredential, setApiCredential, setSessionSignedIn, type Workspace } from './api';
import { writeConnectedMarker, type GateState } from './authState';
import { useDialog } from './useDialog';

const sessionStore = () => { try { return window.sessionStorage; } catch { return null; } };

/**
 * Connect (or replace) the in-memory key. The key is never written to storage.
 * With `remember`, the server also issues an 8-hour HttpOnly session cookie so a
 * reload keeps you signed in; the browser cannot read that cookie.
 */
export function connectWithKey(raw: string, remember = false) {
  const value = raw.trim();
  if (!value) return;
  setApiCredential(value);
  writeConnectedMarker(sessionStore(), true);
  if (remember) api.startSession().catch(() => undefined);
}

export async function disconnectKey() {
  try { await api.endSession(); } catch { /* signing out locally still proceeds */ }
  setSessionSignedIn(false);
  setApiCredential(null);
  writeConnectedMarker(sessionStore(), false);
}

/** On load: if a valid session cookie exists, treat the browser as signed in. */
export async function restoreSession() {
  try {
    const who = await api.identity();
    if (who.verified && who.session) setSessionSignedIn(true);
  } catch { /* no session: stay signed out */ }
}

function KeyHelp() {
  return (
    <details className="gate-help">
      <summary>Where do I get an API key?</summary>
      <p>
        Each person receives their own key from a workspace owner. On the machine that runs MetricGuard,
        the owner issues one with:
      </p>
      <pre className="gate-command">.venv\Scripts\python -m scripts.issue_api_key --data-dir data\demo-app --name "Your name" --email you@example.com --workspace WORKSPACE_ID:analyst</pre>
      <p>Roles are <b>reader</b> (view), <b>analyst</b> (compare and save) and <b>owner</b> (approve and manage members).</p>
    </details>
  );
}

export function ConnectForm({ submitLabel = 'Connect to workspace', onSubmitted }: { submitLabel?: string; onSubmitted?: () => void }) {
  const [key, setKey] = useState('');
  const [remember, setRemember] = useState(true);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!key.trim()) return;
    connectWithKey(key, remember);
    setKey('');
    onSubmitted?.();
  };
  return (
    <form className="gate-form" onSubmit={submit}>
      <label>
        Your API key
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="mg_…"
          value={key}
          onChange={e => setKey(e.target.value)}
          data-autofocus
        />
      </label>
      <label className="checkbox-field-standard" style={{ fontWeight: 400 }}>
        <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />
        <span>Keep me signed in on this computer for 8 hours</span>
      </label>
      <p className="gate-note">
        Your key is never saved in the browser. {remember
          ? 'The server gives this browser a sign-in that expires after 8 hours or when you sign out.'
          : 'Without “keep me signed in”, reloading or closing the tab signs you out.'}
      </p>
      <button className="button primary" disabled={!key.trim()} title={key.trim() ? undefined : 'Paste your API key first'}>
        <KeyRound size={15} /> {submitLabel}
      </button>
    </form>
  );
}

type GateProps = {
  state: Exclude<GateState, { kind: 'ready' }>;
  workspaceId: string;
  onRetry: () => void;
  onChooseWorkspace: () => void;
};

export function GateScreen({ state, workspaceId, onRetry, onChooseWorkspace }: GateProps) {
  if (state.kind === 'loading') {
    return (
      <div className="gate-screen" role="status" aria-live="polite">
        <RefreshCw className="spinning" size={26} />
        <h1>Opening workspace</h1>
        <p>Loading datasets, calculations and saved investigations for <b>{workspaceId}</b>…</p>
      </div>
    );
  }

  if (state.kind === 'signin') {
    const heading = state.reason === 'invalid_key' ? 'That API key was not accepted'
      : state.reason === 'reload_ended' ? 'Your connection ended when the page reloaded'
      : 'Connect to your workspace';
    const lead = state.reason === 'invalid_key'
      ? 'The key is unknown or has been deactivated. Check that you pasted the whole key, or ask a workspace owner for a new one.'
      : state.reason === 'reload_ended'
      ? 'For safety, MetricGuard keeps your key in memory only. Paste it again to continue where you were.'
      : 'This MetricGuard server asks everyone to sign in with a personal API key before showing any workspace data.';
    return (
      <div className="gate-screen">
        <div className="gate-card" role="region" aria-labelledby="gate-title">
          <div className="gate-icon"><KeyRound size={22} /></div>
          <h1 id="gate-title">{heading}</h1>
          <p>{lead}</p>
          <ConnectForm submitLabel={state.reason === 'invalid_key' ? 'Try another key' : 'Connect to workspace'} />
          <KeyHelp />
        </div>
      </div>
    );
  }

  if (state.kind === 'denied') {
    const heading = state.reason === 'not_found' ? `Workspace “${workspaceId}” was not found` : `You can’t open “${workspaceId}”`;
    const lead = state.reason === 'needs_key'
      ? 'Without a key you can only use the default local workspace. Connect with your API key to open this one, or choose another workspace.'
      : state.reason === 'not_found'
      ? 'It may have been renamed or the link may be mistyped. Choose one of the workspaces you can access.'
      : 'Your key is valid, but it has no active role in this workspace. Choose a workspace you belong to, or ask its owner to add you.';
    return (
      <div className="gate-screen">
        <div className="gate-card" role="region" aria-labelledby="gate-title">
          <div className="gate-icon warn"><ShieldOff size={22} /></div>
          <h1 id="gate-title">{heading}</h1>
          <p>{lead}</p>
          <div className="gate-actions">
            <button className="button primary" onClick={onChooseWorkspace} data-autofocus>
              <Layers3 size={15} /> Choose a workspace
            </button>
            {hasApiCredential() && (
              <button className="button secondary" onClick={disconnectKey}>Use a different key</button>
            )}
          </div>
          {state.reason === 'needs_key' && <ConnectForm />}
          <details className="gate-help"><summary>Technical detail</summary><p className="mono-cell">{state.message}</p></details>
        </div>
      </div>
    );
  }

  const unavailable = state.kind === 'unavailable';
  return (
    <div className="gate-screen">
      <div className="gate-card" role="alert" aria-labelledby="gate-title">
        <div className="gate-icon warn"><ServerCrash size={22} /></div>
        <h1 id="gate-title">{unavailable ? 'MetricGuard service is unavailable' : 'The workspace could not be opened'}</h1>
        <p>
          {unavailable
            ? 'The browser could not reach the MetricGuard API. Check that the application is running (scripts\\mg.ps1 status) and try again. Nothing you saved is lost.'
            : 'The server returned an unexpected response. Try again; if it keeps happening, share the detail below with whoever runs MetricGuard.'}
        </p>
        <div className="gate-actions">
          <button className="button primary" onClick={onRetry}><RefreshCw size={15} /> Try again</button>
          <button className="button secondary" onClick={onChooseWorkspace}>Choose a workspace</button>
        </div>
        <details className="gate-help"><summary>Technical detail</summary><p className="mono-cell">{state.message}</p></details>
      </div>
    </div>
  );
}

type ChooserProps = {
  open: boolean;
  currentWorkspaceId: string;
  onClose: () => void;
  onSelect: (workspaceId: string) => void;
};

/** Lists only workspaces the current identity can open (server-filtered `/projects`). */
export function WorkspaceChooser({ open, currentWorkspaceId, onClose, onSelect }: ChooserProps) {
  const ref = useDialog<HTMLDivElement>(open, onClose);
  const [items, setItems] = useState<Workspace[] | null>(null);
  const [problem, setProblem] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setItems(null); setProblem('');
    api.listWorkspaces()
      .then(list => { if (active) setItems(list); })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, [open, attempt]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div ref={ref} data-dialog-managed="" className="modal-card" role="dialog" aria-modal="true" aria-labelledby="chooser-title" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2 id="chooser-title">Choose a workspace</h2>
          <button type="button" className="modal-close-button" onClick={onClose} aria-label="Close dialog"><X size={18} /></button>
        </div>
        <div className="modal-body">
          <p className="modal-helper-text">
            {hasApiCredential() ? 'Workspaces your key has an active role in.' : 'Without a key, only local workspaces are available.'}
          </p>
          {problem && (
            <div className="alert error" role="alert">
              <span>Could not load workspaces: {problem}</span>
              <button className="button secondary small" onClick={() => setAttempt(a => a + 1)}>Retry</button>
            </div>
          )}
          {!items && !problem && <p role="status">Loading workspaces…</p>}
          {items && items.length === 0 && (
            <p>This key has no workspace roles yet. Ask a workspace owner to add you.</p>
          )}
          {items && items.length > 0 && (
            <ul className="chooser-list">
              {items.map(ws => (
                <li key={ws.id}>
                  <button type="button" className={`workspace-menu-item ${ws.id === currentWorkspaceId ? 'selected' : ''}`} onClick={() => onSelect(ws.id)}>
                    <div className="menu-item-avatar">{ws.name.slice(0, 1).toUpperCase()}</div>
                    <div className="menu-item-details">
                      <div className="menu-item-title-row"><span>{ws.name}</span>{ws.role && <span className="demo-chip-small">{ws.role}</span>}</div>
                      <small>{ws.id}</small>
                    </div>
                    {ws.id === currentWorkspaceId && <Check size={15} className="selected-check" aria-label="Current workspace" />}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
