"""Owner-only view of the hosted beta: feedback, recent accounts, and promoting a guest.

Only verified accounts whose email is in METRICGUARD_ADMIN_EMAILS (comma-separated) can use these
routes; everyone else gets 404, so the routes are not advertised. This list is separate from the
full-access list: having full access does not let someone read other people's feedback or emails.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import Query, Request
from sqlalchemy import func, select

from .auth import get_auth_context
from .store import DomainError, feedback, guest_accounts, runs, users


def admin_emails() -> set[str]:
    return {e.strip().casefold() for e in os.environ.get('METRICGUARD_ADMIN_EMAILS', '').split(',') if e.strip()}


def is_admin(auth: dict) -> bool:
    user = auth.get('user') or {}
    return not auth.get('is_unverified') and bool(user.get('email')) and user['email'].strip().casefold() in admin_emails()


def install_admin_routes(app, store):
    def require_admin(request: Request) -> dict:
        auth = get_auth_context(request, store)
        if not is_admin(auth):
            raise DomainError('NOT_FOUND', 'Not found.', 404)
        return auth

    @app.get('/api/v1/admin/overview')
    def overview(request: Request):
        require_admin(request)
        now = datetime.now(timezone.utc)
        week = [(now - timedelta(days=d)).date().isoformat() for d in range(6, -1, -1)]
        with store.engine.connect() as conn:
            # Leave out erased-account placeholders and the built-in local analyst.
            real = users.c.email.not_like('%@deleted.invalid') & (users.c.id != 'local-analyst')
            total = conn.execute(select(func.count()).select_from(users).where(real)).scalar_one()
            guests = conn.execute(select(func.count()).select_from(guest_accounts)).scalar_one()
            created = conn.execute(select(users.c.created_at).where(real, users.c.created_at >= week[0])).scalars().all()
            runs_day = conn.execute(select(func.count()).select_from(runs).where(
                runs.c.created_at >= (now - timedelta(hours=24)).isoformat())).scalar_one()
            notes = conn.execute(select(func.count()).select_from(feedback)).scalar_one()
        per_day = {day: 0 for day in week}
        for stamp in created:
            day = str(stamp)[:10]
            if day in per_day:
                per_day[day] += 1
        return {'accounts': total, 'guests': guests, 'full_accounts': total - guests,
                'signups_by_day': [{'day': d, 'count': n} for d, n in per_day.items()],
                'comparisons_last_24h': runs_day, 'feedback_total': notes}

    @app.get('/api/v1/admin/feedback')
    def feedback_list(request: Request, limit: int = Query(100, ge=1, le=500)):
        require_admin(request)
        with store.engine.connect() as conn:
            rows = conn.execute(select(feedback, users.c.email, users.c.name)
                                .outerjoin(users, users.c.id == feedback.c.user_id)
                                .order_by(feedback.c.created_at.desc()).limit(limit)).mappings().all()
        return {'items': [{k: r[k] for k in ('id', 'created_at', 'page', 'message', 'email', 'name', 'project_id')} for r in rows]}

    @app.get('/api/v1/admin/accounts')
    def accounts(request: Request, limit: int = Query(200, ge=1, le=1000)):
        require_admin(request)
        with store.engine.connect() as conn:
            rows = conn.execute(select(users.c.id, users.c.email, users.c.name, users.c.created_at,
                                       guest_accounts.c.expires_at.label('guest_expires_at'))
                                .outerjoin(guest_accounts, guest_accounts.c.user_id == users.c.id)
                                .where(users.c.email.not_like('%@deleted.invalid'), users.c.id != 'local-analyst', users.c.is_active.is_(True))
                                .order_by(users.c.created_at.desc()).limit(limit)).mappings().all()
        return {'items': [dict(r) for r in rows]}

    @app.post('/api/v1/admin/accounts/{user_id}/promote')
    def promote(user_id: str, request: Request):
        """Full access for one guest now: no erase time and no guest storage limit."""
        require_admin(request)
        if store.guest_expiry(user_id) is None:
            raise DomainError('NOT_A_GUEST', 'This account already has full access.', 409)
        store.promote_guest(user_id)
        return {'id': user_id, 'guest_expires_at': None}
