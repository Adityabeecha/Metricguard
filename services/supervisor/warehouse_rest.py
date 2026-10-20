"""Snowflake and BigQuery sources over their REST APIs (httpx only, no vendor drivers).

The collector runs one read-only SELECT of the dataset contract's columns, writes the rows
to a CSV exactly as the warehouse returns them as text, and hands that CSV to the normal
isolated flat import. Every row is therefore validated against the contract in the runner,
exactly like an uploaded file. Nothing here parses numbers: values stay text end to end.

* Snowflake SQL API v2 (POST /api/v2/statements), with a programmatic access token or an
  OAuth token. Result partitions are fetched in order; asynchronous statements are polled.
* BigQuery jobs.query / jobs.getQueryResults, with an OAuth access token (for example
  `gcloud auth print-access-token`). Pages are followed until the result is complete.

Dates and timestamps are formatted in SQL (YYYY-MM-DD, YYYY-MM-DD HH:MM:SS.ffffff) so
neither API's epoch representations are interpreted here. Amounts are cast to text; zeros
beyond the declared scale are trimmed (19.990 -> 19.99), which is exact; any other extra
digit is left for the import to refuse. Tokens are used for this request only, never stored,
and redacted from every error message.
"""
from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from packages.metricguard_core.free_tier import FreeTierLimit, check_rows, check_upload_size, configured_limit
from services.supervisor.collector import CollectorError, IDENTIFIER_REGEX

MAX_ROWS = 5_000_000
POLL_SECONDS = 1.0
WAIT_SECONDS = 1800
ACCOUNT_REGEX = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
PROJECT_REGEX = re.compile(r'^[a-z][a-z0-9-]{4,61}[a-z0-9]\Z')
DATASET_REGEX = re.compile(r'^[A-Za-z0-9_]{1,1024}\Z')
DECIMAL_TEXT = re.compile(r'^(-?)(\d+)(?:\.(\d+))?\Z')


@dataclass
class WarehouseSelection:
    kind: str  # 'snowflake' | 'bigquery'
    token: str = field(repr=False)
    table: str
    columns: list[tuple[str, str]]  # (contract column, type: amount | string | boolean | date | timestamp)
    row_key: str
    amount_scales: dict[str, int]
    # Snowflake
    account: str | None = None
    database: str | None = None
    schema: str | None = None
    warehouse: str | None = None
    role: str | None = None
    token_type: str = 'PROGRAMMATIC_ACCESS_TOKEN'
    # BigQuery
    project: str | None = None
    dataset: str | None = None
    location: str | None = None
    # Both
    sample_rows: int | None = None
    cutoff_column: str | None = None
    cutoff_type: str | None = None  # 'date' | 'timestamp'
    cutoff_value: str | None = None


def _ident(name, label):
    if not isinstance(name, str) or not IDENTIFIER_REGEX.match(name):
        raise CollectorError('INVALID_IDENTIFIER', f'Invalid {label}: use letters, digits and underscores only.')
    return name


def canonical_amount(text, scale):
    """Trim zeros beyond the declared scale (exact); anything else is returned unchanged for the import to judge."""
    if text is None:
        return ''
    match = DECIMAL_TEXT.match(text)
    if not match:
        return text
    sign, whole, fraction = match.group(1), match.group(2), match.group(3) or ''
    if len(fraction) > scale and set(fraction[scale:]) <= {'0'}:
        fraction = fraction[:scale]
    return f'{sign}{whole}.{fraction}' if fraction else f'{sign}{whole}'


def _redact(text, token):
    text = str(text)
    if token:
        text = text.replace(token, '[redacted]')
    return re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[redacted]', text)


def _check(selection):
    if selection.kind not in ('snowflake', 'bigquery'):
        raise CollectorError('UNSUPPORTED_SOURCE', 'Choose Snowflake or BigQuery.')
    if not isinstance(selection.token, str) or not 8 <= len(selection.token) <= 8192 or any(c.isspace() for c in selection.token):
        raise CollectorError('INVALID_CREDENTIALS', 'Paste a valid access token (no spaces).')
    _ident(selection.table, 'table')
    for column, kind in selection.columns:
        _ident(column, 'column')
        if kind not in ('amount', 'string', 'boolean', 'date', 'timestamp'):
            raise CollectorError('INVALID_CONTRACT', f'Column {column} has an unsupported type for warehouse extraction.')
    if selection.sample_rows is not None and not 1 <= selection.sample_rows <= MAX_ROWS:
        raise CollectorError('INVALID_SAMPLE', f'A sample must be between 1 and {MAX_ROWS:,} rows.')
    if selection.cutoff_column is not None:
        _ident(selection.cutoff_column, 'cutoff column')
        if selection.cutoff_type not in ('date', 'timestamp') or not selection.cutoff_value or \
                not re.fullmatch(r'\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?)?', selection.cutoff_value):
            raise CollectorError('INVALID_CUTOFF', 'A cutoff needs a date or timestamp column and an ISO value such as 2026-09-30.')
    if selection.kind == 'snowflake':
        if not selection.account or not ACCOUNT_REGEX.match(selection.account):
            raise CollectorError('INVALID_ACCOUNT', 'Enter the Snowflake account identifier, for example myorg-myaccount.')
        for value, label in ((selection.database, 'database'), (selection.schema, 'schema')):
            _ident(value, label)
        for value, label in ((selection.warehouse, 'warehouse'), (selection.role, 'role')):
            if value is not None:
                _ident(value, label)
        if selection.token_type not in ('PROGRAMMATIC_ACCESS_TOKEN', 'OAUTH'):
            raise CollectorError('INVALID_CREDENTIALS', 'The Snowflake token type must be a programmatic access token or OAuth.')
    else:
        if not selection.project or not PROJECT_REGEX.match(selection.project):
            raise CollectorError('INVALID_PROJECT', 'Enter the Google Cloud project ID, for example my-analytics-123.')
        if not selection.dataset or not DATASET_REGEX.match(selection.dataset):
            raise CollectorError('INVALID_DATASET', 'Enter the BigQuery dataset name (letters, digits and underscores).')
        if selection.location is not None and not re.fullmatch(r'[A-Za-z0-9-]{2,32}', selection.location):
            raise CollectorError('INVALID_LOCATION', 'Enter a BigQuery location such as US, EU or asia-south1.')


# ---------------------------------------------------------------- queries

def snowflake_query(selection):
    """A read-only SELECT with every value converted to text in SQL; returns (sql, bindings)."""
    def text(column, kind):
        if kind == 'date':
            return f"TO_VARCHAR({column}, 'YYYY-MM-DD')"
        if kind == 'timestamp':
            return f"TO_VARCHAR({column}, 'YYYY-MM-DD HH24:MI:SS.FF6')"
        if kind == 'boolean':
            return f"IFF({column} IS NULL, NULL, IFF({column}, 'true', 'false'))"
        return f'TO_VARCHAR({column})'
    # Unquoted identifiers keep Snowflake's usual case-insensitive matching; aliases keep contract names.
    projection = ', '.join(f'{text(c, k)} AS "{c}"' for c, k in selection.columns)
    sql = f'SELECT {projection} FROM {selection.database}.{selection.schema}.{selection.table}'
    bindings = {}
    if selection.cutoff_column:
        convert = 'TO_DATE' if selection.cutoff_type == 'date' else 'TO_TIMESTAMP_NTZ'
        sql += f' WHERE {selection.cutoff_column} <= {convert}(?)'
        bindings = {'1': {'type': 'TEXT', 'value': selection.cutoff_value}}
    sql += f' ORDER BY {selection.row_key}'
    if selection.sample_rows:
        sql += f' LIMIT {int(selection.sample_rows)}'
    return sql, bindings


def bigquery_query(selection):
    def text(column, kind):
        if kind == 'timestamp':
            return f"FORMAT_TIMESTAMP('%F %H:%M:%E6S', `{column}`, 'UTC')"
        if kind == 'boolean':
            return f"IF(`{column}` IS NULL, NULL, IF(`{column}`, 'true', 'false'))"
        return f'CAST(`{column}` AS STRING)'  # DATE -> YYYY-MM-DD; NUMERIC -> exact decimal text
    projection = ', '.join(f'{text(c, k)} AS `{c}`' for c, k in selection.columns)
    sql = f'SELECT {projection} FROM `{selection.project}.{selection.dataset}.{selection.table}`'
    parameters = []
    if selection.cutoff_column:
        kind = 'DATE' if selection.cutoff_type == 'date' else 'TIMESTAMP'
        sql += f' WHERE `{selection.cutoff_column}` <= CAST(@cutoff AS {kind})'
        parameters = [{'name': 'cutoff', 'parameterType': {'type': 'STRING'}, 'parameterValue': {'value': selection.cutoff_value}}]
    sql += f' ORDER BY `{selection.row_key}`'
    if selection.sample_rows:
        sql += f' LIMIT {int(selection.sample_rows)}'
    return sql, parameters


# ---------------------------------------------------------------- fetching

def _response(client, method, url, token, **kwargs):
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError as error:
        raise CollectorError('CONNECTION_FAILED', f'Could not reach the warehouse: {_redact(error, token)}') from None
    if response.status_code in (401, 403):
        raise CollectorError('AUTHENTICATION_FAILED', 'The warehouse refused the token (expired, or missing access to this table).')
    if response.status_code >= 400 and response.status_code not in (202,):
        detail = ''
        try:
            body = response.json()
            detail = body.get('message') or (body.get('error') or {}).get('message') or ''
        except ValueError:
            pass
        raise CollectorError('WAREHOUSE_ERROR', f'The warehouse returned HTTP {response.status_code}: {_redact(detail, token)[:300]}')
    return response


def snowflake_rows(selection, client):
    """Yields each result row (a list of text or None) in partition order."""
    base = f'https://{selection.account}.snowflakecomputing.com/api/v2/statements'
    headers = {'Authorization': f'Bearer {selection.token}', 'Accept': 'application/json',
               'X-Snowflake-Authorization-Token-Type': selection.token_type}
    sql, bindings = snowflake_query(selection)
    body = {'statement': sql, 'timeout': WAIT_SECONDS, 'database': selection.database.upper(),
            'schema': selection.schema.upper(), **({'warehouse': selection.warehouse.upper()} if selection.warehouse else {}),
            **({'role': selection.role.upper()} if selection.role else {}), **({'bindings': bindings} if bindings else {}),
            'parameters': {'MULTI_STATEMENT_COUNT': '1', 'QUERY_TAG': 'metricguard-read-only-extract'}}
    response = _response(client, 'POST', base, selection.token, headers=headers, json=body)
    deadline = time.monotonic() + WAIT_SECONDS
    while response.status_code == 202:  # still running: poll its status URL
        if time.monotonic() > deadline:
            raise CollectorError('WAREHOUSE_TIMEOUT', 'The Snowflake statement did not finish in time.')
        time.sleep(POLL_SECONDS)
        handle = response.json().get('statementHandle')
        if not isinstance(handle, str) or not re.fullmatch(r'[0-9a-fA-F-]{1,64}', handle):
            raise CollectorError('WAREHOUSE_ERROR', 'Snowflake returned an invalid statement handle.')
        response = _response(client, 'GET', f'{base}/{handle}', selection.token, headers=headers)
    result = response.json()
    meta = result.get('resultSetMetaData') or {}
    names = [column.get('name') for column in meta.get('rowType') or []]
    if [name.lower() if isinstance(name, str) else name for name in names] != [c.lower() for c, _ in selection.columns]:
        raise CollectorError('SCHEMA_MISMATCH', 'Snowflake returned different columns than the dataset contract.')
    handle = result.get('statementHandle')
    for index, _ in enumerate(meta.get('partitionInfo') or [{}]):
        if index == 0:
            data = result.get('data') or []
        else:
            page = _response(client, 'GET', f'{base}/{handle}', selection.token, headers=headers, params={'partition': index})
            data = page.json().get('data') or []
        for row in data:
            yield row


def bigquery_rows(selection, client):
    base = f'https://bigquery.googleapis.com/bigquery/v2/projects/{selection.project}'
    headers = {'Authorization': f'Bearer {selection.token}', 'Accept': 'application/json'}
    sql, parameters = bigquery_query(selection)
    body = {'query': sql, 'useLegacySql': False, 'timeoutMs': 60_000, 'maxResults': 50_000,
            'labels': {'source': 'metricguard'}, **({'location': selection.location} if selection.location else {}),
            **({'parameterMode': 'NAMED', 'queryParameters': parameters} if parameters else {})}
    page = _response(client, 'POST', f'{base}/queries', selection.token, headers=headers, json=body).json()
    job = page.get('jobReference') or {}
    job_id, location = job.get('jobId'), job.get('location') or selection.location
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,1024}', job_id):
        raise CollectorError('WAREHOUSE_ERROR', 'BigQuery returned an invalid job reference.')
    deadline = time.monotonic() + WAIT_SECONDS
    params = {'maxResults': 50_000, **({'location': location} if location else {})}
    while not page.get('jobComplete'):
        if time.monotonic() > deadline:
            raise CollectorError('WAREHOUSE_TIMEOUT', 'The BigQuery job did not finish in time.')
        time.sleep(POLL_SECONDS)
        page = _response(client, 'GET', f'{base}/queries/{job_id}', selection.token, headers=headers,
                         params={**params, 'timeoutMs': 60_000}).json()
    names = [f.get('name') for f in ((page.get('schema') or {}).get('fields') or [])]
    if [n.lower() if isinstance(n, str) else n for n in names] != [c.lower() for c, _ in selection.columns]:
        raise CollectorError('SCHEMA_MISMATCH', 'BigQuery returned different columns than the dataset contract.')
    while True:
        for row in page.get('rows') or []:
            yield [cell.get('v') for cell in row.get('f') or []]
        token = page.get('pageToken')
        if not token:
            return
        page = _response(client, 'GET', f'{base}/queries/{job_id}', selection.token, headers=headers,
                         params={**params, 'pageToken': token}).json()


def extract_to_csv(selection, destination, client=None):
    """Write the selection's rows to `destination` (CSV with contract column names); returns the row count."""
    _check(selection)
    check_rows(0)
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0), follow_redirects=False)
    rows = 0
    bounded_upload = configured_limit('METRICGUARD_MAX_UPLOAD_MB') is not None
    try:
        fetch = snowflake_rows if selection.kind == 'snowflake' else bigquery_rows
        with Path(destination).open('w', encoding='utf-8', newline='') as output:
            writer = csv.writer(output, lineterminator='\n')
            writer.writerow([c for c, _ in selection.columns])
            for row in fetch(selection, client):
                check_rows(rows + 1, exact=False)
                if not isinstance(row, list) or len(row) != len(selection.columns):
                    raise CollectorError('SCHEMA_MISMATCH', 'The warehouse returned a row with the wrong number of values.')
                cells = []
                for (column, kind), value in zip(selection.columns, row):
                    if value is not None and not isinstance(value, str):
                        raise CollectorError('WAREHOUSE_ERROR', f'The warehouse returned a non-text value for {column}.')
                    cells.append(canonical_amount(value, selection.amount_scales[column]) if kind == 'amount'
                                 else '' if value is None else value)
                writer.writerow(cells)
                rows += 1
                if bounded_upload:
                    check_upload_size(output.tell())
                if rows > MAX_ROWS:
                    raise CollectorError('TOO_MANY_ROWS', f'The table has more than {MAX_ROWS:,} rows; take a sample or add a cutoff.')
    except (CollectorError, FreeTierLimit) as error:
        raise CollectorError(error.code, _redact(error.message, selection.token), status=error.status) from None
    finally:
        if own:
            client.close()
    return rows


def selection_for_contract(contract, kind, body):
    """Build a WarehouseSelection from a validated flat contract and the request body."""
    if contract['version'] in (6, 7):
        raise CollectorError('UNSUPPORTED_CONTRACT', 'Warehouse extraction supports datasets with plain amount columns.')
    if contract['null_policy'] != 'blank_nullable':
        raise CollectorError('INVALID_CONTRACT', 'Warehouse extracts use blank values for NULL (blank_nullable contracts).')
    amounts = ([(c['name'], c['scale']) for c in contract['decimal_columns']] if contract['version'] == 8
               else [(contract['measure'], contract['scale'])])
    types = contract.get('dimension_types') or {}
    columns = [(contract['row_key'], 'string'), *[(name, 'amount') for name, _ in amounts],
               *[(name, types.get(name, 'string')) for name in contract['categories']]]
    return WarehouseSelection(kind=kind, columns=columns, row_key=contract['row_key'], amount_scales=dict(amounts), **body)
