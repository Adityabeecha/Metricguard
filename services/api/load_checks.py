"""Explicit checks of approved metric SQL against two loads of its bound dataset.

Checks remain ordinary comparison runs, including their quota admission, evidence and
replay. No scheduled jobs or second queue are introduced here.
"""
from datetime import datetime, timedelta, timezone

from fastapi import Header, Query, Request
from pydantic import Field
from sqlalchemy import func, select

from packages.metricguard_core.free_tier import configured_limit
from .auth import get_auth_context, verify_project_access
from .models import FlatSnapshotChangeInput, Input
from .store import DomainError, runs


class LoadCheckPair(Input):
    baseline_snapshot_id: str = Field(min_length=1, max_length=128)
    target_snapshot_id: str = Field(min_length=1, max_length=128)


class LoadCheckStart(LoadCheckPair):
    metric_id: str = Field(min_length=1, max_length=128)
    version_id: str = Field(min_length=1, max_length=128)


def load_pair(store, pid, baseline_id, target_id):
    before, after = store.snapshot(baseline_id), store.snapshot(target_id)
    if any(item.get('project_id') != pid for item in (before, after)):
        raise DomainError('FORBIDDEN', 'Both data loads must belong to this workspace.', 403)
    if any(item.get('execution_policy') != 'flat_snapshot_v1' for item in (before, after)):
        raise DomainError('POLICY_MISMATCH', 'Metric checks between loads require a single-table dataset.', 422)
    if not before.get('contract_hash') or before['contract_hash'] != after.get('contract_hash'):
        raise DomainError('SNAPSHOT_CONTRACT_MISMATCH', 'Select two loads of the same dataset.', 422)
    if (baseline_id == target_id or before['manifest']['manifest_hash'] == after['manifest']['manifest_hash']):
        raise DomainError('NOT_COMPARABLE', 'Choose two different data loads.', 422)
    if before['created_at'] > after['created_at']:
        raise DomainError('LOAD_ORDER', 'The earlier load must precede the later load.', 422)
    return before, after


def _body(before, after, version_id):
    return FlatSnapshotChangeInput(version_id=version_id, baseline_snapshot_id=before['id'],
        target_snapshot_id=after['id'], context={
            'mode': 'flat_snapshot', 'scope': before['scope'], 'contract_hash': before['contract_hash'],
            'unit_label': before['manifest']['unit_label'], 'values_comparable': True})


def _eligible_version(store, metric, before, pid):
    binding = metric.get('active_binding') or {}
    if not binding.get('approved_version_id'):
        raise DomainError('NO_APPROVED_VERSION', 'This metric has no approved SQL version.', 422)
    version = store.version(binding['approved_version_id'])
    if version.get('project_id') != pid or version.get('definition_id') != binding.get('definition_id'):
        raise DomainError('INVALID_BINDING', 'The approved SQL is not bound to this metric in this workspace.', 422)
    if not version.get('contract_hash'):
        raise DomainError('UNLINKED_SQL', 'This SQL is not linked to a dataset. Bind it before checking new loads.', 422)
    if version['contract_hash'] != before['contract_hash']:
        raise DomainError('DIFFERENT_DATASET', 'The approved SQL is bound to a different dataset.', 422)
    if version.get('support_state') != 'executable' or version.get('execution_policy') != 'flat_snapshot_v1':
        raise DomainError('UNSUPPORTED_SQL', 'This approved SQL cannot be checked between data loads.', 422)
    return version


def preview_load_checks(store, pid, baseline_id, target_id, validate, auth=None):
    before, after = load_pair(store, pid, baseline_id, target_id)
    eligible, skipped = [], []
    for metric in store.list_metrics(project_id=pid):
        identity = {'metric_id': metric['id'], 'metric_name': metric['display_name']}
        try:
            version = _eligible_version(store, metric, before, pid)
            validate(_body(before, after, version['id']), auth=auth, pid=pid)
            eligible.append({**identity, 'version_id': version['id'], 'raw_sql': version['raw_sql']})
        except DomainError as error:
            skipped.append({**identity, 'reason_code': error.code, 'reason': error.message})
    return {'baseline_snapshot_id': before['id'], 'target_snapshot_id': after['id'],
            'baseline_name': before['name'], 'target_name': after['name'],
            'eligible': eligible, 'skipped': skipped, 'run_count': len(eligible)}


def start_load_check(store, pid, baseline_id, target_id, metric_id, version_id, validate,
                     execution_mode, idempotency_key, auth=None):
    if execution_mode != 'isolated':
        raise DomainError('ISOLATION_REQUIRED', 'Checking a new data load requires live comparison execution.', 422)
    before, after = load_pair(store, pid, baseline_id, target_id)
    metric = next((item for item in store.list_metrics(project_id=pid) if item['id'] == metric_id), None)
    if metric is None:
        raise DomainError('NOT_FOUND', 'Metric not found in this workspace.', 404)
    version = _eligible_version(store, metric, before, pid)
    if version['id'] != version_id:
        raise DomainError('APPROVAL_CHANGED', 'The approved SQL changed after preview. Preview these checks again.', 409)
    payload = validate(_body(before, after, version_id), auth=auth, pid=pid)
    payload.update(purpose='snapshot_change', execution_policy='flat_snapshot_v1',
                   load_check={'metric_id': metric['id'], 'metric_name': metric['display_name'], 'version_id': version_id})
    return store.queue(payload, idempotency_key, execution_mode, project_id=pid)


def daily_availability(store, pid):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with store.engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(runs).where(
            runs.c.project_id == pid, runs.c.created_at >= cutoff)).scalar_one()
    limit = configured_limit('METRICGUARD_MAX_RUNS_PER_DAY')
    return {'max_runs_per_day': limit, 'runs_last_24h': count,
            'runs_available': max(0, limit - count) if limit else None}


def install_load_check_routes(app, store, resolve_project_id, validate, execution_mode):
    def access(request, pid, roles):
        auth = get_auth_context(request, store)
        contexts = [value for value in (pid, request.headers.get('x-project-id'), request.query_params.get('project_id')) if value]
        if len(set(contexts)) != 1:
            raise DomainError('PROJECT_MISMATCH', 'The selected workspace must match this request.', 400)
        resolved = resolve_project_id(request, auth, pid, store=store)
        if resolved != pid:
            raise DomainError('PROJECT_MISMATCH', 'The selected workspace must match this request.', 400)
        member = verify_project_access(store, pid, auth, allowed_roles=roles)
        if member['role'] not in roles:
            raise DomainError('FORBIDDEN', 'This workspace role cannot start metric checks.', 403)
        return auth

    @app.post('/api/v1/projects/{pid}/load-checks/preview')
    def preview(pid: str, body: LoadCheckPair, request: Request):
        auth = access(request, pid, ('owner', 'analyst', 'reader'))
        result = preview_load_checks(store, pid, body.baseline_snapshot_id, body.target_snapshot_id, validate, auth)
        return {**result, **daily_availability(store, pid), 'execution_available': execution_mode == 'isolated'}

    @app.post('/api/v1/projects/{pid}/load-checks/start', status_code=202)
    def start(pid: str, body: LoadCheckStart, request: Request,
              idempotency_key: str = Header(..., min_length=1, max_length=200)):
        auth = access(request, pid, ('owner', 'analyst'))
        return start_load_check(store, pid, body.baseline_snapshot_id, body.target_snapshot_id,
            body.metric_id, body.version_id, validate, execution_mode, idempotency_key, auth)

    @app.get('/api/v1/projects/{pid}/load-checks')
    def history(pid: str, request: Request, limit: int = Query(100, ge=1, le=200)):
        access(request, pid, ('owner', 'analyst', 'reader'))
        with store.engine.connect() as conn:
            items = conn.execute(select(runs).where(runs.c.project_id == pid,
                runs.c.request['load_check']['metric_id'].as_string().is_not(None))
                .order_by(runs.c.created_at.desc()).limit(limit)).mappings().all()
        # The full sealed result stays on /comparisons/{id}; history needs only endpoint figures.
        results = []
        for row in items:
            item = store._run(row)
            if item.get('result'):
                item['result'] = {key: value for key, value in item['result'].items() if key in (
                    'baseline_value', 'target_value', 'delta_target_minus_baseline', 'currency', 'unit_label',
                    'method', 'value_status', 'aggregate', 'ratio')}
            results.append(item)
        return {'items': results}
