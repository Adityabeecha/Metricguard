import type { GateState } from './authState';

export type OAuthProvider = 'google' | 'github';
export type AuthProviders = { providers: OAuthProvider[]; signup_mode: 'invite' | 'open'; auth_required: boolean; key_signin?: boolean };

/** The sign-in page offers an API key locally, or on a hosted server that explicitly enabled key sign-in. */
export function keySignInAllowed(providers: AuthProviders | null | undefined): boolean {
  return Boolean(providers && (localKeyAccess(providers.auth_required) || providers.key_signin === true));
}

/** Only an explicit server capability enables the local key workflow. */
export function localKeyAccess(authRequired: boolean | null | undefined): boolean {
  return authRequired === false;
}

export function accountInitials(user: { name?: string | null; email?: string | null } | null | undefined): string {
  const name = user?.name?.trim();
  if (name) {
    const parts = name.split(/\s+/);
    return (Array.from(parts[0])[0] + (parts.length > 1 ? Array.from(parts[parts.length - 1])[0] : '')).toLocaleUpperCase();
  }
  return Array.from(user?.email?.trim() || '')[0]?.toLocaleUpperCase() || '?';
}

export function accountMenuActions(identity: { session?: boolean; verified?: boolean } | null, authRequired: boolean | null | undefined, connected = false, verificationFailed = false): Array<'settings' | 'key' | 'signout'> {
  const actions: Array<'settings' | 'key' | 'signout'> = ['settings'];
  if (localKeyAccess(authRequired) && identity?.session !== true && (identity !== null || verificationFailed)) actions.push('key');
  if (identity?.verified || connected) actions.push('signout');
  return actions;
}

export function signOutDestination(authRequired: boolean | null | undefined): string | null {
  return localKeyAccess(authRequired) ? null : '/signin';
}

/** Keep the current account recoverable when the server cannot end its cookie. */
export async function endAccountSession(endSession: () => Promise<unknown>, clearIdentity: () => void): Promise<void> {
  await endSession();
  clearIdentity();
}

export function authPage(path: string): 'signin' | 'signup' | null {
  const match = path.match(/^\/(signin|signup)\/?$/);
  return match ? match[1] as 'signin' | 'signup' : null;
}

export function authPageTitle(mode: 'signin' | 'signup'): string {
  return mode === 'signup' ? 'Create your account — MetricGuard' : 'Sign in — MetricGuard';
}

/** Cookie identities use account settings; key controls remain available for key/local access. */
export function connectionControls(identity: { session?: boolean } | null, connected: boolean, verificationFailed: boolean):
  { disconnect: boolean; connect: boolean } {
  if (identity?.session === true || (identity === null && !verificationFailed)) {
    return { disconnect: false, connect: false };
  }
  return { disconnect: connected, connect: true };
}

export function oauthErrorMessage(code: string | null): string {
  if (!code) return '';
  if (code === 'waitlist') return "You're on the waitlist — we'll email you when your invite is ready";
  if (code === 'cancelled') return 'Sign-in was cancelled. Choose a provider to try again.';
  return 'We couldn’t sign you in. Please try again.';
}

export function configuredProviders(value: unknown): OAuthProvider[] {
  const values = value && typeof value === 'object' ? (value as { providers?: unknown }).providers : null;
  return ['google', 'github'].filter(provider => Array.isArray(values) && values.includes(provider)) as OAuthProvider[];
}

/** Keep return navigation on an ordinary workspace path; reject URL and path-normalization escapes. */
export function safeWorkspaceReturn(value: string | null): string | null {
  if (!value || !/^\/w\/[a-zA-Z0-9_-]+(?:\/|\?|$)/.test(value)) return null;
  if (/[\\\u0000-\u0020]/.test(value) || /%(?:0[0-9a-f]|1[0-9a-f]|7f|2e|2f|5c)/i.test(value)) return null;
  if (value.split(/[/?#]/).some(part => part === '.' || part === '..')) return null;
  return value;
}

export function shouldRedirectToSignIn(gate: Pick<GateState, 'kind'>, authRequired: boolean, connected: boolean): boolean {
  return gate.kind === 'signin' && authRequired && !connected;
}
