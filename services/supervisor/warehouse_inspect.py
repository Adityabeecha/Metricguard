"""Read-only look at a warehouse before freezing a table: its tables and their columns.

Lets the warehouse form offer pickers instead of typed names. Nothing is extracted; every
query reads information_schema only. On a hosted server, PostgreSQL hosts that resolve to
private, loopback or link-local addresses are refused (the checked address is pinned for the
connection), so a typed connection string cannot reach the server's own network.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import time
from urllib.parse import urlsplit

import httpx

from services.supervisor.collector import CollectorError, redact_credentials
from services.supervisor.warehouse_rest import ACCOUNT_REGEX, DATASET_REGEX, PROJECT_REGEX, _response
from services.supervisor.collector import IDENTIFIER_REGEX

MAX_TABLES = 500
MAX_COLUMNS = 300


def private_sources_blocked() -> bool:
    """Hosted deployments (behind the proxy) never let a typed connection reach private addresses."""
    flag = os.environ.get('METRICGUARD_BLOCK_PRIVATE_SOURCES', '').strip().lower()
    if flag in ('true', '1', 'yes', 'on'):
        return True
    if flag in ('false', '0', 'no', 'off'):
        return False
    return bool(os.environ.get('METRICGUARD_ORIGIN_SECRET'))


def public_address(host: str, port: int) -> str:
    """Resolve `host` and return one public IP to connect to, or refuse."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise CollectorError('CONNECTION_FAILED', f'Could not find the database host {host}.') from None
    addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addresses or any(not a.is_global for a in addresses):
        raise CollectorError('PRIVATE_SOURCE', 'This server cannot connect to private or internal network addresses. '
                             'Use the database\'s public host name.')
    return str(addresses[0])


def guarded_conninfo(uri: str) -> str:
    """The connection string, with the checked public address pinned when private sources are blocked."""
    import psycopg
    if not private_sources_blocked():
        return uri
    parts = psycopg.conninfo.conninfo_to_dict(uri)
    host = parts.get('host') or ''
    if not host or host.startswith('/') or ',' in host:
        raise CollectorError('PRIVATE_SOURCE', 'Enter one public database host name.')
    port = int(str(parts.get('port') or '5432').split(',')[0])
    return psycopg.conninfo.make_conninfo(uri, hostaddr=public_address(host, port))


def _ident(name: str, label: str) -> str:
    if not IDENTIFIER_REGEX.match(name or ''):
        raise CollectorError('INVALID_IDENTIFIER', f'Enter a valid {label} name.')
    return name


def _kind(data_type: str) -> str:
    """The form's column kind for a warehouse type: amount, date, timestamp, boolean or text."""
    t = (data_type or '').lower()
    if any(word in t for word in ('numeric', 'decimal', 'number', 'money', 'bignumeric')):
        return 'amount'
    if t in ('integer', 'bigint', 'smallint', 'int', 'int64', 'int4', 'int8', 'int2'):
        return 'integer'
    if 'timestamp' in t or t == 'datetime':
        return 'timestamp'
    if t == 'date':
        return 'date'
    if t in ('boolean', 'bool'):
        return 'boolean'
    return 'text'


def inspect_postgres(uri: str, schema: str, table: str | None = None) -> dict:
    import psycopg
    _ident(schema, 'schema')
    if table:
        _ident(table, 'table')
    try:
        with psycopg.connect(guarded_conninfo(uri), connect_timeout=10, autocommit=False) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '15s'")
                cur.execute('SELECT table_name FROM information_schema.tables WHERE table_schema = %s '
                            "AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY table_name LIMIT %s", (schema, MAX_TABLES))
                tables = [row[0] for row in cur.fetchall()]
                columns = []
                if table:
                    cur.execute('SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s '
                                'AND table_name = %s ORDER BY ordinal_position LIMIT %s', (schema, table, MAX_COLUMNS))
                    columns = [{'name': n, 'type': t, 'kind': _kind(t)} for n, t in cur.fetchall()]
    except CollectorError:
        raise
    except Exception as exc:
        raise CollectorError('CONNECTION_FAILED', f'Could not read the database: {redact_credentials(str(exc))[:300]}') from None
    return {'tables': tables, 'columns': columns}


def _snowflake(account, token, token_type, sql, bindings, database, client):
    base = f'https://{account}.snowflakecomputing.com/api/v2/statements'
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json', 'X-Snowflake-Authorization-Token-Type': token_type}
    response = _response(client, 'POST', base, token, headers=headers, json={
        'statement': sql, 'timeout': 30, 'database': database.upper(), 'bindings': bindings,
        'parameters': {'MULTI_STATEMENT_COUNT': '1', 'QUERY_TAG': 'metricguard-inspect'}})
    deadline = time.monotonic() + 30
    while response.status_code == 202:
        if time.monotonic() > deadline:
            raise CollectorError('WAREHOUSE_TIMEOUT', 'Snowflake did not answer in time.')
        time.sleep(0.5)
        handle = str(response.json().get('statementHandle') or '')
        response = _response(client, 'GET', f'{base}/{handle}', token, headers=headers)
    return response.json().get('data') or []


def inspect_snowflake(account, token, token_type, database, schema, table=None, client=None) -> dict:
    if not ACCOUNT_REGEX.match(account or ''):
        raise CollectorError('INVALID_ACCOUNT', 'Enter the Snowflake account identifier, like myorg-myaccount.')
    _ident(database, 'database'); _ident(schema, 'schema')
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(40.0, connect=15.0), follow_redirects=False)
    try:
        tables = [r[0] for r in _snowflake(account, token, token_type,
            f'SELECT TABLE_NAME FROM {database}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? ORDER BY TABLE_NAME LIMIT {MAX_TABLES}',
            {'1': {'type': 'TEXT', 'value': schema.upper()}}, database, client)]
        columns = []
        if table:
            _ident(table, 'table')
            columns = [{'name': r[0].lower(), 'type': r[1], 'kind': _kind(r[1])} for r in _snowflake(account, token, token_type,
                f'SELECT COLUMN_NAME, DATA_TYPE FROM {database}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? '
                f'ORDER BY ORDINAL_POSITION LIMIT {MAX_COLUMNS}',
                {'1': {'type': 'TEXT', 'value': schema.upper()}, '2': {'type': 'TEXT', 'value': table.upper()}}, database, client)]
    finally:
        if own:
            client.close()
    return {'tables': [t.lower() for t in tables], 'columns': columns}


def inspect_bigquery(project, token, dataset, table=None, location=None, client=None) -> dict:
    if not PROJECT_REGEX.match(project or ''):
        raise CollectorError('INVALID_PROJECT', 'Enter the Google Cloud project ID, for example my-analytics-123.')
    if not DATASET_REGEX.match(dataset or ''):
        raise CollectorError('INVALID_DATASET', 'Enter the BigQuery dataset name.')
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(40.0, connect=15.0), follow_redirects=False)
    base = f'https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{dataset}/tables'
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    try:
        listing = _response(client, 'GET', base, token, headers=headers, params={'maxResults': MAX_TABLES}).json()
        tables = [t.get('tableReference', {}).get('tableId') for t in listing.get('tables') or []]
        columns = []
        if table:
            _ident(table, 'table')
            info = _response(client, 'GET', f'{base}/{table}', token, headers=headers).json()
            columns = [{'name': f.get('name'), 'type': f.get('type'), 'kind': _kind(f.get('type'))}
                       for f in (info.get('schema') or {}).get('fields') or []][:MAX_COLUMNS]
    finally:
        if own:
            client.close()
    return {'tables': [t for t in tables if isinstance(t, str)], 'columns': columns}
