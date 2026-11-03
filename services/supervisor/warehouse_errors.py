"""Safe, actionable warehouse errors. Known cases get plain advice; anything else adds the database's own
first line (credentials masked, at most 200 characters) so it can be acted on."""

def warehouse_error(error, status=None):
    text = str(error).lower()
    state = getattr(error, 'sqlstate', None)
    if state == '28000' or state == '28P01' or status == 401 or any(x in text for x in ('password authentication failed', 'authentication failed', 'invalid credentials', 'invalid token', 'login failed')):
        return 'AUTHENTICATION_FAILED', 'The database refused the login. Check the user and password or token.'
    if 'ssl' in text and any(x in text for x in ('required', 'require', 'no encryption', 'unencrypted')):
        return 'SSL_REQUIRED', 'The database requires SSL. Add sslmode=require to the connection string.'
    if state == '42501' or status == 403 or any(x in text for x in ('permission denied', 'access denied', 'insufficient privilege', 'not authorized')):
        return 'PERMISSION_DENIED', "The database user can't read this table. Grant it SELECT."
    if state == '42703' or ('column' in text and any(x in text for x in ('does not exist', 'not found', 'unknown', 'invalid identifier'))):
        return 'COLUMN_NOT_FOUND', 'A column was not found. Check the column names against the table.'
    if state in ('42P01', '3F000') or status == 404 or any(x in text for x in ('does not exist', 'not found', 'unknown table', 'unknown schema')):
        return 'TABLE_NOT_FOUND', 'Table not found. Check the schema and table name.'
    if isinstance(error, (TimeoutError, OSError)) or any(x in text for x in ('timeout', 'timed out', 'connection refused', 'could not connect', 'could not translate host', 'network is unreachable', 'connection failed', 'connecterror')):
        return 'CONNECTION_FAILED', "Couldn't reach the database. It must accept connections from the internet."
    return 'WAREHOUSE_ERROR', 'The database could not complete the request. Check the connection and table settings.' + _detail(error, state)


def _detail(error, state) -> str:
    """The database's own reason, short and with any credentials masked, so an unexpected refusal can be fixed."""
    from services.supervisor.collector import redact_credentials
    diag = getattr(error, 'diag', None)
    reason = (getattr(diag, 'message_primary', None) or str(error) or type(error).__name__).strip().splitlines()[0]
    reason = redact_credentials(reason)[:200]
    return f' Database said: {reason}' + (f' (code {state})' if state else '') + '.'
