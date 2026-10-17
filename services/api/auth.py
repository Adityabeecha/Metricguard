"""V3 Authentication and Multi-Role Project Authorization (RBAC)."""
from __future__ import annotations

import os
from typing import Any
from fastapi import Request

from services.api.store import DomainError, Store

SESSION_COOKIE = 'mg_session'
SESSION_SECONDS = 8 * 60 * 60


def get_auth_context(request: Request, store: Store) -> dict[str, Any]:
    """Extract and validate credentials from Request headers."""
    auth_header = request.headers.get('authorization')
    api_key_header = request.headers.get('x-api-key')

    token = None
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == 'bearer':
            token = parts[1]
        elif len(parts) == 1:
            token = parts[0]
    elif api_key_header:
        token = api_key_header.strip()

    if token:
        user = store.get_user_by_api_key(token)
        if not user:
            raise DomainError('UNAUTHORIZED', 'Invalid or inactive authentication credentials.', 401)
        return {'user': user, 'is_unverified': False}

    # Browser session from "keep me signed in" (HttpOnly, SameSite=Strict cookie).
    # An expired or revoked session is ignored, exactly as if no cookie were sent.
    session_token = request.cookies.get(SESSION_COOKIE)
    if session_token:
        user = store.get_user_by_session(session_token)
        if user:
            # Cross-site request forgery guard: other origins (even another localhost
            # port) cannot add this header without a CORS approval the API never grants.
            if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('x-metricguard-client') != 'web':
                raise DomainError('FORBIDDEN', 'Session requests must come from the MetricGuard web app.', 403)
            return {'user': user, 'is_unverified': False, 'via_session': True}

    # Explicit configuration/deployment boundary for unverified local access
    allow_unverified = os.environ.get('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'true').lower()
    if allow_unverified in ('false', '0', 'no', 'off'):
        raise DomainError('UNAUTHORIZED', 'Authentication credentials required.', 401)

    # Backward compatibility: unauthenticated requests on default local project only
    return {
        'user': {'id': 'local-analyst', 'name': 'Local analyst', 'email': 'analyst@local.internal'},
        'is_unverified': True,
    }


def verify_project_access(
    store: Store,
    project_id: str,
    auth_context: dict[str, Any],
    allowed_roles: tuple[str, ...] = ('owner', 'analyst', 'reader'),
    require_verified: bool = False,
) -> dict[str, Any]:
    """Verify that the authenticated identity has an active role in project_id."""
    if auth_context.get('is_unverified'):
        if require_verified:
            raise DomainError('FORBIDDEN', 'Authentication required for administrative and governance actions.', 403)
        if project_id == store.project_id:
            return {'role': 'owner', 'unverified': True, 'user_id': 'local-analyst', 'project_id': project_id}
        member = store.get_project_member(project_id, 'local-analyst')
        if member and member.get('revoked_at') is None:
            return {'role': member['role'], 'unverified': True, 'user_id': 'local-analyst', 'project_id': project_id}
        raise DomainError('FORBIDDEN', f"Authentication required for project '{project_id}'.", 403)

    user = auth_context['user']
    member = store.get_project_member(project_id, user['id'])
    if not member or member.get('revoked_at') is not None:
        raise DomainError('FORBIDDEN', f"Access denied to project '{project_id}'.", 403)

    if member['role'] not in allowed_roles:
        raise DomainError('FORBIDDEN', f"Role '{member['role']}' is not permitted to perform this action in project '{project_id}'.", 403)

    return member


def verify_object_access(
    store: Store,
    obj: dict[str, Any],
    auth_context: dict[str, Any],
    allowed_roles: tuple[str, ...] = ('owner', 'analyst', 'reader'),
    require_verified: bool = False,
) -> dict[str, Any]:
    """Verify access to a project-scoped object (run, snapshot, definition, etc.)."""
    project_id = obj.get('project_id', store.project_id)
    return verify_project_access(store, project_id, auth_context, allowed_roles, require_verified=require_verified)

