import type { GateState } from './authState';

export type OAuthProvider = 'google' | 'github';
export type AuthProviders = { providers: OAuthProvider[]; signup_mode: 'invite' | 'open'; auth_required: boolean };

export function authPage(path: string): 'signin' | 'signup' | null {
  const match = path.match(/^\/(signin|signup)\/?$/);
  return match ? match[1] as 'signin' | 'signup' : null;
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
