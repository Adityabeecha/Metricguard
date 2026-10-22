import { apiFetch, ApiError } from './api';
import { configuredProviders, type AuthProviders } from './authFlow';

export async function loadAuthProviders(): Promise<AuthProviders> {
  const response = await apiFetch('/auth/providers');
  if (!response.ok) throw new Error('Sign-in options could not be loaded. Please try again.');
  const value = await response.json();
  return { providers: configuredProviders(value), auth_required: value.auth_required !== false };
}

export async function deleteAccount(): Promise<void> {
  const response = await apiFetch('/auth/account', { method: 'DELETE' });
  if (response.ok) return;
  const body = await response.json().catch(() => ({}));
  throw new ApiError(body.error?.code || `HTTP_${response.status}`, response.status === 401
    ? 'Sign in with Google or GitHub before deleting your account. An API key alone cannot delete an account.'
    : body.error?.message || 'Your account could not be deleted. Please try again.');
}

