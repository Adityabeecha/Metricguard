import { useEffect, useState } from 'react';
import { Github, RefreshCw } from 'lucide-react';
import { api, ApiError, hasApiCredential, setSessionSignedIn } from './api';
import { ConnectForm, KeyHelp } from './AuthScreens';
import { loadAuthProviders } from './authApi';
import { oauthErrorMessage, safeWorkspaceReturn, type AuthProviders } from './authFlow';
import { useIdentityEpoch } from './identity';
import { Logo } from './Logo';
import { ThemeSwitch } from './ThemeSwitch';
import { buildRouteUrl, parseRoute } from './router';
import './auth.css';

export function SignInScreen({ mode }: { mode: 'signin' | 'signup' }) {
  const params = new URLSearchParams(window.location.search);
  const [useKey, setUseKey] = useState(params.get('method') === 'api_key');
  const [providers, setProviders] = useState<AuthProviders | null>(null);
  const [providerProblem, setProviderProblem] = useState('');
  const [problem, setProblem] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [connecting, setConnecting] = useState(false);
  const epoch = useIdentityEpoch();
  const callbackMessage = oauthErrorMessage(params.get('error'));
  const returnTo = safeWorkspaceReturn(params.get('return_to'));

  useEffect(() => {
    let active = true;
    setProviderProblem('');
    loadAuthProviders().then(result => { if (active) setProviders(result); })
      .catch(error => { if (active) setProviderProblem(error.message); });
    return () => { active = false; };
  }, [attempt]);

  useEffect(() => {
    let active = true;
    const openWorkspace = async () => {
      try {
        const identity = await api.identity();
        if (!active || !identity.verified) return;
        if (identity.session) setSessionSignedIn(true);
        const workspaces = await api.listWorkspaces();
        if (!active) return;
        if (!workspaces.length) {
          setProblem('Your account has no active workspace yet. Ask a workspace owner to add you.');
          return;
        }
        const requested = returnTo ? parseRoute(returnTo.split('?')[0]).workspaceId : null;
        const destination = requested && workspaces.some(item => item.id === requested)
          ? returnTo! : buildRouteUrl(workspaces[0].id, 'investigations');
        // API keys live only in memory, so enter the app without reloading the document.
        window.history.replaceState(null, '', destination);
        window.dispatchEvent(new PopStateEvent('popstate'));
      } catch (error) {
        if (!active || (error instanceof ApiError && error.code === 'IDENTITY_CHANGED')) return;
        if (hasApiCredential()) setProblem('That API key was not accepted. Check the key and try again.');
      } finally { if (active) setConnecting(false); }
    };
    void openWorkspace();
    return () => { active = false; };
  }, [epoch]);

  const switchParams = new URLSearchParams();
  if (returnTo) switchParams.set('return_to', returnTo);
  const alternateUrl = `/${mode === 'signup' ? 'signin' : 'signup'}${switchParams.size ? `?${switchParams}` : ''}`;
  return (
    <div className="auth-page">
      <header className="auth-header">
        <a className="auth-brand" href="/signin"><Logo size={32} /><strong>MetricGuard</strong></a>
        <ThemeSwitch compact />
      </header>
      <main className="auth-main" id="main-content">
        <section className="auth-card" aria-labelledby="auth-heading">
          <h1 id="auth-heading">{useKey ? 'Connect with an API key' : mode === 'signup' ? 'Create your account' : 'Sign in to MetricGuard'}</h1>
          <p className="auth-purpose">Explain metric changes with evidence you can verify.</p>
          {callbackMessage && <p className={`auth-message ${params.get('error') === 'waitlist' ? 'notice' : 'error'}`} role="status">{callbackMessage}</p>}
          {problem && <p className="auth-message error" role="alert">{problem}</p>}
          {useKey ? (
            <>
              <ConnectForm submitLabel={connecting ? 'Connecting…' : 'Connect to workspace'} onSubmitted={() => { setProblem(''); setConnecting(true); }} />
              <KeyHelp />
              <button type="button" className="auth-text-button" onClick={() => setUseKey(false)}>Back to sign in</button>
            </>
          ) : (
            <>
              <div className="auth-providers">
                {providerProblem && <div className="auth-message error" role="alert"><p>{providerProblem}</p><button className="button secondary small" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
                {!providers && !providerProblem && <p role="status"><RefreshCw size={14} className="spinning" /> Loading sign-in options…</p>}
                {providers?.providers.map(provider => (
                  <a key={provider} className={`button ${provider === providers.providers[0] ? 'primary' : 'secondary'} auth-provider`} href={`/api/v1/auth/oauth/${provider}/start`}>
                    {provider === 'github' ? <Github size={18} aria-hidden="true" /> : <span className="auth-google-mark" aria-hidden="true">G</span>}
                    Continue with {provider === 'google' ? 'Google' : 'GitHub'}
                  </a>
                ))}
                {providers && !providers.providers.length && <p className="auth-empty">Social sign-in is not configured on this server. Connect with an issued API key below.</p>}
              </div>
              {providers?.signup_mode === 'invite' && <p className="auth-invite">Access is by invitation. Continue with your invited email address.</p>}
              <p className="auth-alternate">{mode === 'signup' ? 'Already have an account?' : 'New to MetricGuard?'} <a href={alternateUrl}>{mode === 'signup' ? 'Sign in' : 'Create an account'}</a></p>
              <button type="button" className="auth-text-button" onClick={() => { setUseKey(true); setProblem(''); }}>Use an API key instead</button>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
