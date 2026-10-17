// Pure classification of why a workspace could not be opened. No runtime imports,
// so `node --experimental-strip-types` can test it directly.

export type GateState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  /** No key (or the key ended on reload) and the server requires one. */
  | { kind: 'signin'; reason: 'required' | 'invalid_key' | 'reload_ended' }
  /** Identity is valid (or local) but has no role in this workspace, or the workspace does not exist. */
  | { kind: 'denied'; reason: 'not_member' | 'not_found' | 'needs_key'; message: string }
  /** The API could not be reached or failed internally. */
  | { kind: 'unavailable'; message: string }
  /** Anything else: shown verbatim with a retry. */
  | { kind: 'error'; message: string };

type ErrorLike = { code?: unknown; message?: unknown; name?: unknown };

export function classifyLoadError(error: unknown, opts: { connected: boolean; reloadEnded: boolean }): GateState {
  const e = (error && typeof error === 'object' ? error : {}) as ErrorLike;
  const code = typeof e.code === 'string' ? e.code : '';
  const message = typeof e.message === 'string' ? e.message : String(error);

  if (code === 'UNAUTHORIZED' || code === 'HTTP_401') {
    if (opts.connected) return { kind: 'signin', reason: 'invalid_key' };
    return { kind: 'signin', reason: opts.reloadEnded ? 'reload_ended' : 'required' };
  }
  if (code === 'FORBIDDEN' || code === 'HTTP_403') {
    // Without a key, the local fallback identity only reaches the default workspace.
    if (!opts.connected && opts.reloadEnded) return { kind: 'signin', reason: 'reload_ended' };
    if (!opts.connected) return { kind: 'denied', reason: 'needs_key', message };
    return { kind: 'denied', reason: 'not_member', message };
  }
  if (code === 'NOT_FOUND' || code === 'HTTP_404' || code === 'PROJECT_NOT_FOUND') {
    return { kind: 'denied', reason: 'not_found', message };
  }
  // fetch() rejects with a TypeError when the server is down or unreachable.
  if (e.name === 'TypeError' || /^HTTP_5\d\d$/.test(code) || code === 'INTERNAL_ERROR') {
    return { kind: 'unavailable', message };
  }
  return { kind: 'error', message };
}

/** sessionStorage marker (never the key itself) used to explain why a reload signed the user out. */
export const CONNECTED_MARKER = 'metricguard.connectedThisTab';

export function readReloadEnded(storage: Pick<Storage, 'getItem'> | null, connected: boolean): boolean {
  if (connected || !storage) return false;
  try { return storage.getItem(CONNECTED_MARKER) === '1'; } catch { return false; }
}

export function writeConnectedMarker(storage: Pick<Storage, 'setItem' | 'removeItem'> | null, connected: boolean) {
  if (!storage) return;
  try {
    if (connected) storage.setItem(CONNECTED_MARKER, '1');
    else storage.removeItem(CONNECTED_MARKER);
  } catch { /* storage blocked: the explanation is a convenience only */ }
}

/** After connecting, pick where to go: stay if allowed, auto-enter a sole workspace, else ask. */
export function chooseWorkspaceAfterConnect(current: string, allowed: Array<{ id: string }>):
  { action: 'stay' } | { action: 'go'; workspaceId: string } | { action: 'choose' } | { action: 'none' } {
  if (allowed.some(w => w.id === current)) return { action: 'stay' };
  if (allowed.length === 1) return { action: 'go', workspaceId: allowed[0].id };
  if (allowed.length === 0) return { action: 'none' };
  return { action: 'choose' };
}
