"""Saved warehouse connections and the "Test connection & load columns" step.

Credentials (a PostgreSQL connection string, or a Snowflake/BigQuery token) are encrypted with
a key derived from METRICGUARD_SECRET_KEY and are never returned to the browser; listings show
only where the connection points (host, database, user, account or project). Without the key,
saving is refused with a plain explanation; one-off use with typed credentials still works.
"""
from __future__ import annotations

import base64
import hashlib
import os
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, insert, select

from .auth import get_auth_context, verify_project_access
from .store import DomainError, now, warehouse_connections

KINDS = ('postgres', 'snowflake', 'bigquery')


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(pattern='^(postgres|snowflake|bigquery)$')
    label: str = Field(min_length=1, max_length=120)
    source_uri: str | None = Field(default=None, max_length=2000, repr=False)
    token: str | None = Field(default=None, max_length=8192, repr=False)
    token_type: str = Field(default='PROGRAMMATIC_ACCESS_TOKEN', pattern='^(PROGRAMMATIC_ACCESS_TOKEN|OAUTH)$')
    account: str | None = Field(default=None, max_length=128)
    database: str | None = Field(default=None, max_length=128)
    warehouse: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, max_length=128)
    project: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=32)


class InspectInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: str | None = Field(default=None, max_length=64)
    source_uri: str | None = Field(default=None, max_length=2000, repr=False)
    token: str | None = Field(default=None, max_length=8192, repr=False)
    token_type: str = Field(default='PROGRAMMATIC_ACCESS_TOKEN', pattern='^(PROGRAMMATIC_ACCESS_TOKEN|OAUTH)$')
    account: str | None = Field(default=None, max_length=128)
    database: str | None = Field(default=None, max_length=128)
    project: str | None = Field(default=None, max_length=64)
    dataset: str | None = Field(default=None, max_length=1024)
    location: str | None = Field(default=None, max_length=32)
    schema_name: str = Field(default='public', min_length=1, max_length=128)
    table: str | None = Field(default=None, max_length=128)


def _cipher():
    secret = os.environ.get('METRICGUARD_SECRET_KEY', '')
    if len(secret) < 32:
        raise DomainError('SAVING_UNAVAILABLE', 'Saved connections are switched off on this server (it has no secret key set). '
                          'You can still type the credentials each time.', 409)
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('metricguard-connections:' + secret).encode()).digest()))


def _public(kind: str, body: ConnectionInput) -> dict:
    if kind == 'postgres':
        import psycopg
        try:
            parts = psycopg.conninfo.conninfo_to_dict(body.source_uri or '')
        except Exception:
            raise DomainError('INVALID_CONNECTION', 'Enter a valid PostgreSQL connection string.') from None
        if not parts.get('host'):
            raise DomainError('INVALID_CONNECTION', 'The connection string needs a host.')
        return {k: parts.get(k) for k in ('host', 'port', 'dbname', 'user') if parts.get(k)}
    if kind == 'snowflake':
        return {k: getattr(body, k) for k in ('account', 'database', 'warehouse', 'role', 'token_type') if getattr(body, k)}
    return {k: getattr(body, k) for k in ('project', 'location') if getattr(body, k)}


def resolve(store, pid: str, connection_id: str) -> tuple[str, dict, dict]:
    """(kind, public fields, decrypted secret) for a saved connection of this workspace."""
    with store.engine.connect() as conn:
        row = conn.execute(select(warehouse_connections).where(warehouse_connections.c.id == connection_id,
                                                               warehouse_connections.c.project_id == pid)).mappings().first()
    if not row:
        raise DomainError('NOT_FOUND', 'Saved connection not found.', 404)
    import json
    from cryptography.fernet import InvalidToken
    try:
        secret = json.loads(_cipher().decrypt(row['secret'].encode()).decode())
    except InvalidToken:
        raise DomainError('SAVING_UNAVAILABLE', 'This saved connection can no longer be opened (the server key changed). Save it again.', 409) from None
    return row['kind'], dict(row['public']), secret


def install_warehouse_connection_routes(app, store):
    @app.get('/api/v1/projects/{pid}/warehouse-connections')
    def listed(pid: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        with store.engine.connect() as conn:
            rows = conn.execute(select(warehouse_connections).where(warehouse_connections.c.project_id == pid)
                                .order_by(warehouse_connections.c.created_at)).mappings().all()
        return {'items': [{'id': r['id'], 'kind': r['kind'], 'label': r['label'], 'public': r['public'], 'created_at': r['created_at']}
                          for r in rows],
                'saving_available': len(os.environ.get('METRICGUARD_SECRET_KEY', '')) >= 32}

    @app.post('/api/v1/projects/{pid}/warehouse-connections', status_code=201)
    def create(pid: str, body: ConnectionInput, request: Request):
        import json
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner',), require_verified=True)
        cipher = _cipher()
        if body.kind == 'postgres' and not body.source_uri:
            raise DomainError('INVALID_CONNECTION', 'Enter the connection string.')
        if body.kind != 'postgres' and (not body.token or len(body.token.strip()) < 8):
            raise DomainError('INVALID_CONNECTION', 'Paste an access token.')
        public = _public(body.kind, body)
        secret = {'source_uri': body.source_uri} if body.kind == 'postgres' else {'token': body.token.strip()}
        item = {'id': str(uuid4()), 'project_id': pid, 'kind': body.kind, 'label': body.label.strip(), 'public': public,
                'secret': cipher.encrypt(json.dumps(secret).encode()).decode(), 'created_by': auth['user']['id'], 'created_at': now()}
        with store.engine.begin() as conn:
            conn.execute(insert(warehouse_connections).values(**item))
        return {k: item[k] for k in ('id', 'kind', 'label', 'public', 'created_at')}

    @app.delete('/api/v1/projects/{pid}/warehouse-connections/{connection_id}')
    def remove(pid: str, connection_id: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner',), require_verified=True)
        with store.engine.begin() as conn:
            gone = conn.execute(delete(warehouse_connections).where(warehouse_connections.c.id == connection_id,
                                                                    warehouse_connections.c.project_id == pid)).rowcount
        if not gone:
            raise DomainError('NOT_FOUND', 'Saved connection not found.', 404)
        return {'deleted': True, 'id': connection_id}

    @app.post('/api/v1/projects/{pid}/sources/{kind}/inspect')
    def inspect(pid: str, kind: str, body: InspectInput, request: Request):
        """Tables in a schema, and a table's columns, read from information_schema only."""
        from services.supervisor.collector import CollectorError
        from services.supervisor import warehouse_inspect as wi
        if kind not in KINDS:
            raise DomainError('NOT_FOUND', 'Choose postgres, snowflake or bigquery.', 404)
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        public, secret = {}, {}
        if body.connection_id:
            saved_kind, public, secret = resolve(store, pid, body.connection_id)
            if saved_kind != kind:
                raise DomainError('INVALID_CONNECTION', 'That saved connection is for a different kind of warehouse.')
        try:
            if kind == 'postgres':
                uri = secret.get('source_uri') or body.source_uri
                if not uri:
                    raise DomainError('INVALID_CONNECTION', 'Enter the connection string or choose a saved connection.')
                return wi.inspect_postgres(uri, body.schema_name, body.table)
            token = secret.get('token') or (body.token or '').strip()
            if len(token) < 8:
                raise DomainError('INVALID_CONNECTION', 'Paste an access token or choose a saved connection.')
            if kind == 'snowflake':
                return wi.inspect_snowflake(public.get('account') or body.account, token, public.get('token_type') or body.token_type,
                                            public.get('database') or body.database, body.schema_name, body.table)
            return wi.inspect_bigquery(public.get('project') or body.project, token, body.dataset, body.table,
                                       public.get('location') or body.location)
        except CollectorError as error:
            raise DomainError(error.code, error.message, 401 if error.code == 'AUTHENTICATION_FAILED' else 422) from None
