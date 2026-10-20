"""Bounded authorization-code sign-in; provider tokens never become app credentials."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse, JSONResponse

from .auth import SESSION_COOKIE, SESSION_SECONDS
from .store import DomainError

STATE_SECONDS = 600
ACCESS_REQUEST_WINDOW = 3600
ACCESS_REQUESTS_PER_WINDOW = 5
EMAIL_PATTERN = re.compile(r'^[^@\s<>"]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$')
PROVIDERS = {
    'google': {'authorize': 'https://accounts.google.com/o/oauth2/v2/auth',
               'token': 'https://oauth2.googleapis.com/token', 'scope': 'openid email profile'},
    'github': {'authorize': 'https://github.com/login/oauth/authorize',
               'token': 'https://github.com/login/oauth/access_token', 'scope': 'read:user user:email'},
}


def http_client():
    return httpx.Client(timeout=httpx.Timeout(15), follow_redirects=False)


def settings():
    app_url = os.environ.get('METRICGUARD_APP_URL', 'http://127.0.0.1:8000').rstrip('/')
    parsed = urlsplit(app_url)
    valid_url = (parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and
                 not parsed.password and not parsed.query and not parsed.fragment and parsed.path in ('', '/') and
                 (parsed.scheme == 'https' or parsed.hostname in ('localhost', '127.0.0.1', '::1')))
    secret = os.environ.get('METRICGUARD_SESSION_SECRET', '')
    mode = os.environ.get('METRICGUARD_SIGNUP', 'invite').strip().lower()
    # A typo must never accidentally open registration.
    mode = 'open' if mode == 'open' else 'invite'
    configured = {}
    if valid_url and len(secret.encode()) >= 32:
        for provider in PROVIDERS:
            prefix = provider.upper()
            client_id, client_secret = os.environ.get(prefix + '_CLIENT_ID', '').strip(), os.environ.get(prefix + '_CLIENT_SECRET', '').strip()
            if client_id and client_secret:
                configured[provider] = {'client_id': client_id, 'client_secret': client_secret}
    return {'app_url': app_url if valid_url else 'http://127.0.0.1:8000', 'secret': secret,
            'providers': configured, 'signup_mode': mode,
            'invited': {e.strip().casefold() for e in os.environ.get('METRICGUARD_INVITE_EMAILS', '').split(',') if e.strip()}}


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _sign(payload, secret):
    body = _b64(json.dumps(payload, separators=(',', ':')).encode())
    return body + '.' + _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def _read_state(cookie, secret, provider, state):
    if not isinstance(cookie, str) or len(cookie) > 4096 or not state or len(state) > 256:
        raise ValueError('state')
    body, signature = cookie.split('.')
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise ValueError('signature')
    payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    if (payload.get('provider') != provider or not isinstance(payload.get('state'), str) or
            not hmac.compare_digest(payload['state'].encode(), state.encode()) or
            type(payload.get('expires')) is not int or not time.time() < payload['expires'] <= time.time() + STATE_SECONDS + 5):
        raise ValueError('state')
    return payload


def _json(response):
    response.raise_for_status()
    if len(response.content) > 1024 * 1024:
        raise ValueError('provider response too large')
    return response.json()


def verified_profile(provider, code, payload, config):
    credentials = config['providers'][provider]
    form = {**credentials, 'code': code, 'redirect_uri': config['app_url'] + f'/api/v1/auth/oauth/{provider}/callback'}
    if provider == 'google':
        form.update(grant_type='authorization_code', code_verifier=payload['verifier'])
    with http_client() as client:
        token = _json(client.post(PROVIDERS[provider]['token'], data=form, headers={'Accept': 'application/json'}))
        access = token.get('access_token')
        if not isinstance(access, str) or not access or len(access) > 8192:
            raise ValueError('provider token')
        headers = {'Authorization': 'Bearer ' + access, 'Accept': 'application/json'}
        if provider == 'google':
            # Fetch identity over TLS using the access token from this code exchange.
            # An unverified/unsigned id_token payload is never decoded or trusted.
            profile = _json(client.get('https://openidconnect.googleapis.com/v1/userinfo', headers=headers))
            if profile.get('email_verified') is not True:
                raise ValueError('unverified')
            email, subject = profile.get('email'), profile.get('sub')
            name = profile.get('name')
        else:
            headers.update({'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'MetricGuard'})
            profile = _json(client.get('https://api.github.com/user', headers=headers))
            emails = _json(client.get('https://api.github.com/user/emails', headers=headers, params={'per_page': 100}))
            primary = [e for e in emails if isinstance(e, dict) and e.get('primary') is True and e.get('verified') is True]
            if len(primary) != 1 or type(profile.get('id')) is not int:
                raise ValueError('unverified')
            email, subject = primary[0].get('email'), str(profile['id'])
            name = profile.get('name') or profile.get('login')
        if (not isinstance(email, str) or not 3 <= len(email) <= 320 or email.count('@') != 1 or
                any(c.isspace() or ord(c) < 32 for c in email) or
                not isinstance(subject, str) or not 1 <= len(subject) <= 255):
            raise ValueError('identity')
        return subject, email.strip().casefold(), name[:160] if isinstance(name, str) and name.strip() else 'Analyst'


def install_oauth_routes(app, store, data_dir, secure_cookies=False):
    def state_cookie(provider):
        return 'mg_oauth_state_' + provider

    def finish(response, provider):
        if provider not in PROVIDERS:
            return response
        response.delete_cookie(state_cookie(provider), path=f'/api/v1/auth/oauth/{provider}',
                               secure=secure_cookies, httponly=True, samesite='lax')
        return response

    @app.get('/api/v1/auth/providers')
    def providers():
        config = settings()
        return {'providers': list(config['providers']), 'signup_mode': config['signup_mode'],
                'auth_required': os.environ.get('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'true').lower() in ('false', '0', 'no', 'off')}

    request_times: dict[str, deque] = {}
    request_lock = threading.Lock()

    def allow_request(client: str) -> bool:
        cutoff = time.monotonic() - ACCESS_REQUEST_WINDOW
        with request_lock:
            if len(request_times) > 10_000:
                for key in [k for k, v in request_times.items() if not v or v[-1] < cutoff]:
                    del request_times[key]
            times = request_times.setdefault(client, deque())
            while times and times[0] < cutoff:
                times.popleft()
            if len(times) >= ACCESS_REQUESTS_PER_WINDOW:
                return False
            times.append(time.monotonic())
            return True

    @app.post('/api/v1/auth/access-requests', status_code=202)
    async def request_access(request: Request):
        """Join the beta waitlist. The answer is the same whether or not the email is already known."""
        try:
            body = await request.json()
        except ValueError:
            body = None
        email = body.get('email') if isinstance(body, dict) else None
        if not isinstance(email, str) or len(email.strip()) > 254 or not EMAIL_PATTERN.match(email.strip()):
            raise DomainError('INVALID_EMAIL', 'Enter a valid email address.', 422)
        # Behind the Vercel proxy the visitor's address is the first forwarded hop.
        forwarded = request.headers.get('x-forwarded-for', '').split(',')[0].strip()
        client = forwarded or (request.client.host if request.client else 'unknown')
        if not allow_request(client):
            raise DomainError('RATE_LIMITED', 'Too many requests. Please try again in an hour.', 429)
        store.request_access(email)
        return {'status': 'received'}

    @app.get('/api/v1/auth/oauth/{provider}/start')
    def start(provider: str):
        config = settings()
        if provider not in config['providers']:
            return RedirectResponse(config['app_url'] + '/signin?error=failed', status_code=302)
        state = secrets.token_urlsafe(32)
        payload = {'provider': provider, 'state': state, 'expires': int(time.time()) + STATE_SECONDS,
                   'redirect_uri': config['app_url'] + f'/api/v1/auth/oauth/{provider}/callback'}
        params = {'client_id': config['providers'][provider]['client_id'], 'redirect_uri': payload['redirect_uri'],
                  'state': state, 'scope': PROVIDERS[provider]['scope'], 'response_type': 'code'}
        if provider == 'google':
            payload['verifier'] = secrets.token_urlsafe(48)
            params.update(code_challenge=_b64(hashlib.sha256(payload['verifier'].encode()).digest()), code_challenge_method='S256')
        store.start_oauth_state(state)
        response = RedirectResponse(PROVIDERS[provider]['authorize'] + '?' + urlencode(params), status_code=302)
        response.set_cookie(state_cookie(provider), _sign(payload, config['secret']), max_age=STATE_SECONDS,
                            path=f'/api/v1/auth/oauth/{provider}', secure=secure_cookies, httponly=True, samesite='lax')
        return response

    @app.get('/api/v1/auth/oauth/{provider}/callback')
    def callback(provider: str, request: Request):
        config = settings()
        error = 'failed'
        try:
            if provider not in config['providers']:
                raise ValueError('provider')
            states, codes = request.query_params.getlist('state'), request.query_params.getlist('code')
            if len(states) != 1:
                raise ValueError('state')
            payload = _read_state(request.cookies.get(state_cookie(provider)), config['secret'], provider, states[0])
            if payload.get('redirect_uri') != config['app_url'] + f'/api/v1/auth/oauth/{provider}/callback':
                raise ValueError('redirect')
            if not store.consume_oauth_state(states[0]):
                raise ValueError('replayed state')
            if request.query_params.get('error'):
                error = 'cancelled' if request.query_params['error'] == 'access_denied' else 'failed'
                raise ValueError('provider refused')
            if len(codes) != 1 or not 1 <= len(codes[0]) <= 4096:
                raise ValueError('code')
            subject, email, name = verified_profile(provider, codes[0], payload, config)
            user, pid = store.oauth_sign_in(provider, subject, email, name, config['signup_mode'], config['invited'])
            session = store.create_session(user['id'], SESSION_SECONDS)
            old = request.cookies.get(SESSION_COOKIE)
            if old:
                store.revoke_session(old)
            response = RedirectResponse(config['app_url'] + f'/w/{pid}/investigations', status_code=302)
            response.set_cookie(SESSION_COOKIE, session, max_age=SESSION_SECONDS, path='/api',
                                httponly=True, secure=secure_cookies, samesite='strict')
            return finish(response, provider)
        except DomainError as exception:
            error = 'waitlist' if exception.code == 'OAUTH_WAITLIST' else 'failed'
            if error == 'waitlist':
                try:  # the provider verified this email: keep "you're on the waitlist" true
                    store.request_access(email)
                except Exception:
                    pass
        except Exception:
            # Provider errors, transport failures and malformed inputs get one safe
            # public code; no tokens, emails or provider exception text reach URLs.
            pass
        return finish(RedirectResponse(config['app_url'] + '/signin?' + urlencode({'error': error}), status_code=302), provider)

    @app.delete('/api/v1/auth/account')
    def delete_own_account(request: Request):
        from .account_deletion import delete_account
        token = request.cookies.get(SESSION_COOKIE)
        user = store.get_user_by_session(token) if token else None
        if not user:
            raise DomainError('UNAUTHORIZED', 'Sign in with a browser session to delete your account.', 401)
        if request.headers.get('x-metricguard-client') != 'web':
            raise DomainError('FORBIDDEN', 'Session requests must come from the MetricGuard web app.', 403)
        result = delete_account(store, user['id'], data_dir)
        response = JSONResponse(result)
        response.delete_cookie(SESSION_COOKIE, path='/api', secure=secure_cookies, httponly=True, samesite='strict')
        return response
