import { apiFetch, ApiError } from './api';
import { configuredProviders, type AuthProviders } from './authFlow';

export async function loadAuthProviders(): Promise<AuthProviders> {
  const response = await apiFetch('/auth/providers');
  if (!response.ok) throw new Error('Sign-in options could not be loaded. Please try again.');
  const value = await response.json();
  return { providers: configuredProviders(value), signup_mode: value.signup_mode === 'open' ? 'open' : 'invite', auth_required: value.auth_required !== false, key_signin: value.key_signin === true };
}

export async function deleteAccount(): Promise<void> {
  const response = await apiFetch('/auth/account', { method: 'DELETE' });
  if (response.ok) return;
  const body = await response.json().catch(() => ({}));
  throw new ApiError(body.error?.code || `HTTP_${response.status}`, response.status === 401
    ? 'Sign in with Google or GitHub before deleting your account. An API key alone cannot delete an account.'
    : body.error?.message || 'Your account could not be deleted. Please try again.');
}

/** Join the beta waitlist. The server answers the same way whether or not the email is already known. */
export async function requestAccess(email: string): Promise<void> {
  const response = await apiFetch('/auth/access-requests', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email.trim() }) });
  if (response.ok) return;
  const body = await response.json().catch(() => ({}));
  throw new ApiError(body.error?.code || `HTTP_${response.status}`, body.error?.message || 'Your request could not be sent. Please try again.');
}
