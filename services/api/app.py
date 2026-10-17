from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .models import (BaselineInput, ColumnMappingInput, ComparisonInput,
                     DatasetContractInput, DefinitionInput, DependencyInput,
                     InvestigationInput, PairReview, FlatContractInput,
                     FlatDefinitionInput, FlatComparisonInput,
                     UserCreateInput, ProjectCreateInput, ProjectMemberInput,
                     ReviewRequestInput, DecisionInput, MonitoringPolicyInput,
                     AlertAcknowledgeInput, ProjectQuotaInput, PostgresExtractionInput,
                     MetricCreateInput, MetricUpdateInput, ExternalMetricReferenceInput,
                     MetricDefinitionBindingInput, MetricPromotionInput,
                     MetricVersionApprovalInput, DbtChangeSetInput,
                     DbtMetricImpactInput, MetricImpactInvestigationInput,
                     DbtMetricDiffInput, InvestigationDraftCreateInput,
                     InvestigationDraftUpdateInput, MetricContractProposalInput,
                     MetricContractApprovalInput, MetricContractCheckInput)
from .reports import html_report
from .store import DomainError, Store, fingerprint
from .candidates import rank_candidates
from .metadata_adapters import AdapterError, import_dbt_manifest, import_tmdl
from .auth import get_auth_context, verify_project_access, verify_object_access
from .monitoring import classify_change, evaluate_materiality, compute_event_hash
from .relational_routes import verify_shared_access
from .backup import backup_project, restore_project
from services.supervisor.evidence_artifact import verify_artifact
from services.supervisor.collector import CollectorError, PostgresCollector, PostgresSourceSelection

ROOT = Path(__file__).resolve().parents[2]


def resolve_project_id(
    request: Request,
    auth: dict,
    default_pid: str | None = None,
    store: Store | None = None,
    *,
    fallback_object: dict | None = None,
) -> str:
    pid = request.headers.get('x-project-id') or request.query_params.get('project_id')
    if pid:
        return pid

    if fallback_object and fallback_object.get('project_id'):
        return fallback_object['project_id']

    if default_pid and store and default_pid != store.project_id:
        return default_pid

    if store:
        projects = store.list_projects()
        if len(projects) > 1:
            if auth.get('is_unverified') and request.method in ('GET', 'HEAD') and request.url.path.endswith('/bootstrap'):
                return store.project_id
            raise DomainError(
                'PROJECT_REQUIRED',
                'Project context is required. Specify X-Project-Id header or project_id parameter.',
                400
            )
        return store.project_id

    if default_pid:
        return default_pid

    raise DomainError(
        'PROJECT_REQUIRED',
        'Project context is required. Specify X-Project-Id header or project_id parameter.',
        400
    )


def public_snapshot(snapshot):
    public = {key: value for key, value in snapshot.items() if key not in ('artifact_path', 'manifest')}
    manifest = snapshot.get('manifest') or {}
    preparation = manifest.get('flat_preparation')
    if preparation:
        public['preparation'] = preparation['manifest']
        public['preparation_manifest_hash'] = preparation['manifest_hash']
    if 'unit_label' in manifest and not public.get('unit_label'):
        public['unit_label'] = manifest['unit_label']
    if manifest.get('mode') == 'relational_snapshot':
        public['source_hashes'] = manifest['source_hashes']
        public['join_proof'] = manifest['join_proof']
        public['null_semantics'] = manifest['null_semantics']
    if public.get('unit_label') and str(public.get('unit_label')).lower() in ('count', 'units', 'records', 'items'):
        public['currency'] = None
    return public


def create_app(database_url=None, data_dir=None, mode=None):
    data_dir = Path(data_dir or os.environ.get('METRICGUARD_DATA_DIR', ROOT / 'data')).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(database_url, Store):
        store = database_url
    else:
        store = Store(database_url or os.environ.get('METRICGUARD_DATABASE_URL', f"sqlite:///{data_dir / 'metricguard.db'}"))
    execution_mode = mode or os.environ.get('METRICGUARD_MODE', 'demo')
    if execution_mode not in ('demo', 'isolated'):
        raise ValueError('METRICGUARD_MODE must be demo or isolated')
    app = FastAPI(title='MetricGuard', version='1.0.0')
    app.state.store = store
    from .relational_routes import install_relational_routes
    install_relational_routes(app, store, data_dir, execution_mode, resolve_project_id)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver', 'api'])

    @app.middleware('http')
    async def local_origin_and_limits(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
            from urllib.parse import urlparse
            if urlparse(origin).hostname not in ('localhost', '127.0.0.1', '::1'):
                return JSONResponse({'error': {'code': 'LOCAL_ONLY', 'message': 'MetricGuard accepts local browser requests only.'}}, status_code=403)
        try:
            if int(request.headers.get('content-length', '0')) > 26 * 1024 * 1024:
                return JSONResponse({'error': {'code': 'UPLOAD_LIMIT', 'message': 'Upload exceeds the 25 MiB limit.'}}, status_code=413)
        except ValueError:
            return JSONResponse({'error': {'code': 'BAD_REQUEST', 'message': 'Invalid content length.'}}, status_code=400)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(DomainError)
    async def domain_error(_, error):
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=error.status)

    from packages.metricguard_core.common import CoreError

    @app.exception_handler(CoreError)
    async def core_error(_, error):
        return JSONResponse({'error': {'code': error.code.upper(), 'message': error.message}}, status_code=422)

    @app.exception_handler(CollectorError)
    async def collector_error(_, error):
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def input_error(_, error):
        messages = ['.'.join(str(p) for p in e['loc']) + ': ' + e['msg'] for e in error.errors()]
        return JSONResponse({'error': {'code': 'VALIDATION_ERROR', 'message': '; '.join(messages)}}, status_code=422)

    @app.get('/api/v1/health')
    def health():
        is_runner_available = True if execution_mode == 'demo' else store.worker_available()
        return {'status': 'ok', 'mode': execution_mode, 'runner_available': is_runner_available}

    @app.get('/api/v1/evaluation')
    def evaluation():
        report = data_dir / 'evaluation' / 'release_report.json'
        if not report.is_file():
            return {'status': 'not_run', 'message': 'No local evaluation report is available.'}
        if report.stat().st_size > 100_000:
            raise DomainError('EVALUATION_INVALID', 'Evaluation report exceeds the size limit.')
        try:
            payload = json.loads(report.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise DomainError('EVALUATION_INVALID', 'Evaluation report cannot be read.') from error
        if not isinstance(payload, dict) or payload.get('status') not in ('passed', 'failed'):
            raise DomainError('EVALUATION_INVALID', 'Evaluation report is invalid.')
        return payload

    @app.get('/api/v1/evaluation/candidates')
    def candidate_evaluation():
        report = data_dir / 'evaluation' / 'candidate_report.json'
        if not report.is_file():
            return {'status': 'not_run', 'message': 'No local candidate evaluation is available.'}
        if report.stat().st_size > 100_000:
            raise DomainError('EVALUATION_INVALID', 'Candidate evaluation exceeds the size limit.')
        try:
            payload = json.loads(report.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise DomainError('EVALUATION_INVALID', 'Candidate evaluation cannot be read.') from error
        if not isinstance(payload, dict) or not isinstance(payload.get('pair_count'), int):
            raise DomainError('EVALUATION_INVALID', 'Candidate evaluation is invalid.')
        return payload

    @app.get('/api/v1/bootstrap')
    def bootstrap(request: Request, project_id: str | None = None):
        auth = get_auth_context(request, store)
        pid = resolve_project_id(request, auth, project_id, store=store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        result = store.bootstrap(pid)
        result['snapshots'] = [public_snapshot(s) for s in result['snapshots']]
        result['candidates'] = rank_candidates(result['definitions'], result['reviews'])[:50]
        is_runner_available = True if execution_mode == 'demo' else store.worker_available()
        result.update(mode=execution_mode, runner_available=is_runner_available)
        return result

    @app.get('/api/v1/candidates')
    def candidates(request: Request):
        auth = get_auth_context(request, store)
        pid = resolve_project_id(request, auth, store=store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        data = store.bootstrap(pid)
        return {'method': 'deterministic_heuristic_v1', 'items': rank_candidates(data['definitions'], data['reviews'])}

    @app.post('/api/v1/metadata-imports/{kind}', status_code=201)
    async def import_metadata(kind: str, request: Request, file: UploadFile = File(...)):
        auth = get_auth_context(request, store)
        pid = resolve_project_id(request, auth, store=store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        formats = {'dbt': ('.json', import_dbt_manifest), 'tmdl': ('.tmdl', import_tmdl)}
        if kind not in formats or not file.filename or not file.filename.lower().endswith(formats.get(kind, ('', None))[0]):
            raise DomainError('UNSUPPORTED_FORMAT', 'Upload a dbt manifest.json or a .tmdl metadata document.')
        content = await file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise DomainError('UPLOAD_LIMIT', 'Metadata artifact exceeds 5 MiB.', 413)
        if len(store.bootstrap(pid)['metadata_imports']) >= 20:
            raise DomainError('IMPORT_LIMIT', 'This local workspace supports at most 20 metadata artifacts.')
        try:
            parsed = formats[kind][1](content)
        except AdapterError as error:
            raise DomainError('INVALID_METADATA_ARTIFACT', str(error)) from error
        return store.record('metadata_import', {'filename': Path(file.filename).name[:160], **parsed}, project_id=pid)

    # -----------------------------------------------------------------------
    # V4 Connected SQL: dbt Revisions & Lineage API
    # -----------------------------------------------------------------------

    @app.post('/api/v1/projects/{pid}/dbt/imports', status_code=201)
    async def import_dbt_revision(pid: str, request: Request,
                                  manifest: UploadFile = File(...),
                                  run_results: UploadFile | None = File(None),
                                  git_sha: str | None = Form(None),
                                  branch: str | None = Form(None),
                                  dirty: bool = Form(False)):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))

        if not manifest.filename or not manifest.filename.lower().endswith('.json'):
            raise DomainError('UNSUPPORTED_FORMAT', 'manifest.json must be a JSON file.')

        manifest_content = await manifest.read(10 * 1024 * 1024 + 1)
        if len(manifest_content) > 10 * 1024 * 1024:
            raise DomainError('UPLOAD_LIMIT', 'dbt manifest exceeds 10 MiB limit.', 413)

        run_results_content = None
        if run_results:
            if not run_results.filename or not run_results.filename.lower().endswith('.json'):
                raise DomainError('UNSUPPORTED_FORMAT', 'run_results.json must be a JSON file.')
            run_results_content = await run_results.read(5 * 1024 * 1024 + 1)
            if len(run_results_content) > 5 * 1024 * 1024:
                raise DomainError('UPLOAD_LIMIT', 'run_results.json exceeds 5 MiB limit.', 413)

        source_rev = {}
        if git_sha:
            source_rev['git_sha'] = str(git_sha)[:128]
        if branch:
            source_rev['branch'] = str(branch)[:128]
        if dirty:
            source_rev['dirty'] = bool(dirty)

        try:
            return store.record_dbt_import(
                manifest_content=manifest_content,
                run_results_content=run_results_content,
                source_revision=source_rev,
                project_id=resolved_pid
            )
        except AdapterError as err:
            raise DomainError('INVALID_METADATA_ARTIFACT', str(err)) from err

    @app.get('/api/v1/projects/{pid}/dbt/revisions')
    def list_dbt_revisions(pid: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'items': store.list_dbt_import_revisions(resolved_pid)}

    @app.get('/api/v1/projects/{pid}/dbt/revisions/{ident}')
    def get_dbt_revision(pid: str, ident: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.get_dbt_import_revision(ident, resolved_pid)

    @app.get('/api/v1/projects/{pid}/dbt/resources/{unique_id}/history')
    def get_dbt_resource_history(pid: str, unique_id: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'unique_id': unique_id, 'history': store.get_dbt_resource_history(unique_id, resolved_pid)}

    @app.get('/api/v1/projects/{pid}/dbt/resources/{unique_id}/lineage')
    def get_dbt_resource_lineage(pid: str, unique_id: str, request: Request,
                                 import_revision_id: str | None = None):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.get_dbt_lineage(unique_id, import_revision_id=import_revision_id, project_id=resolved_pid)

    # -----------------------------------------------------------------------
    # V5-01: dbt Revision Change Sets & Metric Identity / Lifecycle API
    # -----------------------------------------------------------------------

    @app.post('/api/v1/projects/{pid}/dbt/change-sets')
    def compute_dbt_change_set_post(pid: str, body: DbtChangeSetInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.compute_dbt_change_set(
            baseline_revision_id=body.baseline_revision_id,
            target_revision_id=body.target_revision_id,
            project_id=resolved_pid,
        )

    @app.get('/api/v1/projects/{pid}/dbt/change-sets')
    def compute_dbt_change_set_get(pid: str, baseline_revision_id: str, target_revision_id: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.compute_dbt_change_set(
            baseline_revision_id=baseline_revision_id,
            target_revision_id=target_revision_id,
            project_id=resolved_pid,
        )

    # -----------------------------------------------------------------------
    # V5-02: Affected Metric Resolution & Controlled Metric Impact Execution
    # -----------------------------------------------------------------------

    @app.post('/api/v1/projects/{pid}/dbt/metric-impacts', status_code=200)
    def execute_metric_impact(pid: str, body: DbtMetricImpactInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        return store.execute_metric_impact(
            baseline_revision_id=body.baseline_revision_id,
            target_revision_id=body.target_revision_id,
            snapshot_id=body.snapshot_id,
            context=body.context,
            materiality_policy=body.materiality_policy,
            project_id=resolved_pid,
        )

    @app.get('/api/v1/projects/{pid}/dbt/metric-impacts/{impact_id}')
    def get_metric_impact_report(pid: str, impact_id: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.get_metric_impact_report(impact_id=impact_id, project_id=resolved_pid)

    @app.get('/api/v1/projects/{pid}/dbt/metric-impacts')
    def list_metric_impact_reports(pid: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'items': store.list_metric_impact_reports(project_id=resolved_pid)}

    @app.post('/api/v1/projects/{pid}/dbt/metric-impacts/{impact_id}/investigations', status_code=201)
    def promote_metric_impact_to_investigation(pid: str, impact_id: str, body: MetricImpactInvestigationInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        actor_label = body.actor_label or (auth.get('user', {}).get('name') if auth.get('user') else 'CI / Analyst')
        return store.promote_metric_impact_to_investigation(
            impact_id=impact_id,
            metric_id=body.metric_id,
            title=body.title,
            decision=body.decision,
            actor_label=actor_label,
            project_id=resolved_pid,
        )

    # -----------------------------------------------------------------------
    # V5-03: Revision-Level Metric Diff Orchestration & Coverage Report
    # -----------------------------------------------------------------------

    @app.post('/api/v1/projects/{pid}/dbt/metric-diffs', status_code=200)
    def execute_metric_diff(pid: str, body: DbtMetricDiffInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        return store.execute_metric_diff(
            baseline_revision_id=body.baseline_revision_id,
            target_revision_id=body.target_revision_id,
            snapshot_id=body.snapshot_id,
            mode=body.mode,
            context=body.context,
            materiality_policy=body.materiality_policy,
            project_id=resolved_pid,
        )

    @app.get('/api/v1/projects/{pid}/dbt/metric-diffs/{diff_id}')
    def get_metric_diff_report(pid: str, diff_id: str, request: Request, format: str | None = Query(None)):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        report = store.get_metric_diff_report(diff_id=diff_id, project_id=resolved_pid)
        if format == 'markdown' or request.headers.get('accept') == 'text/markdown':
            from services.diff_report import format_metric_diff_markdown
            return PlainTextResponse(format_metric_diff_markdown(report), media_type='text/markdown')
        return report

    @app.get('/api/v1/projects/{pid}/dbt/metric-diffs')
    def list_metric_diff_reports(pid: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'items': store.list_metric_diff_reports(project_id=resolved_pid)}

    # -----------------------------------------------------------------------
    # V5.2-05: Investigation Drafts
    # -----------------------------------------------------------------------

    @app.post('/api/v1/projects/{pid}/investigation-drafts', status_code=201)
    def create_investigation_draft(pid: str, body: InvestigationDraftCreateInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        user_name = auth.get('user', {}).get('name') if auth.get('user') else None
        return store.record_investigation_draft(
            payload=body.model_dump(mode='json'),
            project_id=resolved_pid,
            created_by=user_name,
        )

    @app.get('/api/v1/projects/{pid}/investigation-drafts')
    def list_investigation_drafts(pid: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'items': store.list_investigation_drafts(project_id=resolved_pid)}

    @app.get('/api/v1/projects/{pid}/investigation-drafts/{draft_id}')
    def get_investigation_draft(pid: str, draft_id: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.get_investigation_draft(draft_id=draft_id, project_id=resolved_pid)

    @app.put('/api/v1/projects/{pid}/investigation-drafts/{draft_id}')
    def update_investigation_draft(pid: str, draft_id: str, body: InvestigationDraftUpdateInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        return store.update_investigation_draft(
            draft_id=draft_id,
            updates=body.model_dump(mode='json', exclude_unset=True),
            expected_version_token=body.version_token,
            project_id=resolved_pid,
        )

    @app.delete('/api/v1/projects/{pid}/investigation-drafts/{draft_id}')
    def delete_investigation_draft(pid: str, draft_id: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        store.delete_investigation_draft(draft_id=draft_id, project_id=resolved_pid)
        return {'deleted': True, 'id': draft_id}

    @app.post('/api/v1/projects/{pid}/metrics', status_code=201)
    def create_metric(pid: str, body: MetricCreateInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        return store.create_metric(
            display_name=body.display_name,
            importance=body.importance,
            metric_id=body.id,
            project_id=resolved_pid,
        )

    @app.get('/api/v1/projects/{pid}/metrics')
    def list_metrics(pid: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'items': store.list_metrics(project_id=resolved_pid)}

    @app.get('/api/v1/projects/{pid}/metrics/{metric_id}')
    def get_metric(pid: str, metric_id: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.get_metric(metric_id=metric_id, project_id=resolved_pid)

    @app.patch('/api/v1/projects/{pid}/metrics/{metric_id}')
    def update_metric(pid: str, metric_id: str, body: MetricUpdateInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        return store.update_metric(
            metric_id=metric_id,
            display_name=body.display_name,
            importance=body.importance,
            project_id=resolved_pid,
        )

    def metric_contract_access(pid, request, allowed_roles, require_verified=False):
        # These routes carry an explicit workspace; headers cannot redirect it.
        for supplied in (request.headers.get('x-project-id'), request.query_params.get('project_id')):
            if supplied and supplied != pid:
                raise DomainError('PROJECT_CONTEXT_MISMATCH', 'Workspace context must match the contract URL.', 400)
        auth = get_auth_context(request, store)
        access = verify_project_access(store, pid, auth, allowed_roles=allowed_roles, require_verified=require_verified)
        return auth, access

    @app.get('/api/v1/projects/{pid}/metrics/{metric_id}/contracts')
    def list_metric_contracts(pid: str, metric_id: str, request: Request,
                              limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        auth, access = metric_contract_access(pid, request, ('owner', 'analyst', 'reader'))
        result = store.list_metric_contracts(metric_id, pid, limit, offset)
        verified = not auth.get('is_unverified')
        result['capabilities'] = {'can_propose': verified and access['role'] in ('owner', 'analyst'),
                                  'can_check': verified and access['role'] in ('owner', 'analyst'),
                                  'can_approve': verified and access['role'] == 'owner'}
        return result

    @app.post('/api/v1/projects/{pid}/metrics/{metric_id}/contracts', status_code=201)
    def propose_metric_contract(pid: str, metric_id: str, body: MetricContractProposalInput, request: Request):
        auth, _ = metric_contract_access(pid, request, ('owner', 'analyst'), require_verified=True)
        return store.propose_metric_contract(metric_id, body.expectations.model_dump(),
            body.expected_version_token, body.expected_approved_version_id, auth['user']['id'], pid)

    @app.post('/api/v1/projects/{pid}/metrics/{metric_id}/contracts/{revision_id}/approve')
    def approve_metric_contract(pid: str, metric_id: str, revision_id: str,
                                body: MetricContractApprovalInput, request: Request):
        auth, _ = metric_contract_access(pid, request, ('owner',), require_verified=True)
        return store.approve_metric_contract(metric_id, revision_id, body.expected_version_token,
            body.reason, auth['user']['id'], pid)

    @app.post('/api/v1/projects/{pid}/metrics/{metric_id}/contracts/checks', status_code=201)
    def check_metric_contract(pid: str, metric_id: str, body: MetricContractCheckInput, request: Request):
        auth, _ = metric_contract_access(pid, request, ('owner', 'analyst'), require_verified=True)
        return store.check_metric_contract(metric_id, **body.model_dump(), actor_id=auth['user']['id'], project_id=pid)

    @app.get('/api/v1/projects/{pid}/metrics/{metric_id}/contracts/checks/{check_id}')
    def get_metric_contract_check(pid: str, metric_id: str, check_id: str, request: Request):
        metric_contract_access(pid, request, ('owner', 'analyst', 'reader'))
        return store.get_metric_contract_check(metric_id, check_id, pid)

    @app.post('/api/v1/projects/{pid}/metrics/{metric_id}/external-references', status_code=201)
    def attach_external_reference(pid: str, metric_id: str, body: ExternalMetricReferenceInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        return store.attach_external_metric_reference(
            metric_id=metric_id,
            provider=body.provider,
            external_id=body.external_id,
            project_id=resolved_pid,
        )

    @app.get('/api/v1/projects/{pid}/metrics/{metric_id}/external-references')
    def list_external_references(pid: str, metric_id: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'items': store.list_external_metric_references(metric_id=metric_id, project_id=resolved_pid)}

    @app.post('/api/v1/projects/{pid}/metrics/{metric_id}/bindings', status_code=201)
    def bind_definition(pid: str, metric_id: str, body: MetricDefinitionBindingInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        actor_id = auth.get('user', {}).get('id')
        return store.bind_metric_definition(
            metric_id=metric_id,
            definition_id=body.definition_id,
            status=body.status,
            approved_version_id=body.approved_version_id,
            project_id=resolved_pid,
            actor_id=actor_id,
        )

    @app.get('/api/v1/projects/{pid}/metrics/{metric_id}/bindings')
    def list_bindings(pid: str, metric_id: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'items': store.list_metric_definition_bindings(metric_id=metric_id, project_id=resolved_pid)}

    @app.post('/api/v1/projects/{pid}/metrics/{metric_id}/promote')
    def promote_candidate(pid: str, metric_id: str, body: MetricPromotionInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        actor_id = auth.get('user', {}).get('id')
        return store.promote_metric_definition(
            metric_id=metric_id,
            definition_id=body.definition_id,
            approved_version_id=body.approved_version_id,
            comparison_run_id=body.comparison_run_id,
            review_decision_id=body.review_decision_id,
            actor_id=actor_id,
            project_id=resolved_pid,
        )

    @app.post('/api/v1/projects/{pid}/metrics/{metric_id}/approve-version')
    def approve_version(pid: str, metric_id: str, body: MetricVersionApprovalInput, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst'))
        actor_id = auth.get('user', {}).get('id')
        return store.approve_metric_version(
            metric_id=metric_id,
            approved_version_id=body.approved_version_id,
            comparison_run_id=body.comparison_run_id,
            review_decision_id=body.review_decision_id,
            actor_id=actor_id,
            project_id=resolved_pid,
        )

    @app.get('/api/v1/projects/{pid}/metrics/{metric_id}/transitions')
    def list_transitions(pid: str, metric_id: str, request: Request):
        auth = get_auth_context(request, store)
        resolved_pid = resolve_project_id(request, auth, pid)
        verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'items': store.list_metric_state_transitions(metric_id=metric_id, project_id=resolved_pid)}

    @app.post('/api/v1/definitions', status_code=201)
    def import_definition(body: DefinitionInput, request: Request):
        auth = get_auth_context(request, store)
        pid = resolve_project_id(request, auth, store=store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        return store.record_definition(**body.model_dump(mode='json'), project_id=pid)

    @app.post('/api/v1/definitions/{ident}/versions', status_code=201)
    def import_version(ident: str, body: DefinitionInput, request: Request):
        auth = get_auth_context(request, store)
        item = store.definition(ident)
        verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst'))
        return store.record_definition(**body.model_dump(mode='json'), definition_id=ident, project_id=item.get('project_id'))['versions'][-1]

    @app.get('/api/v1/definitions/{ident}')
    @app.get('/api/v1/definitions/{ident}/history')
    def history(ident: str, request: Request):
        auth = get_auth_context(request, store)
        item = store.definition(ident)
        verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return item

    @app.get('/api/v1/snapshots/template')
    def template():
        return FileResponse(ROOT / 'MetricGuard_Planning_Pack/examples/orders_golden.csv', filename='orders-template.csv', media_type='text/csv')

    @app.get('/api/v1/snapshots/{ident}')
    def snapshot_detail(ident: str, request: Request):
        auth = get_auth_context(request, store)
        item = store.snapshot(ident)
        verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return public_snapshot(item)

    @app.post('/api/v1/dataset-contracts', status_code=201)
    def approve_contract(body: DatasetContractInput, request: Request):
        auth = get_auth_context(request, store)
        pid = resolve_project_id(request, auth, store=store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        return store.record_dataset_contract(body.model_dump(mode='json', by_alias=True), project_id=pid)

    @app.get('/api/v1/dataset-contracts/{ident}')
    def dataset_contract(ident: str, request: Request):
        auth = get_auth_context(request, store)
        item = store.dataset_contract(ident)
        verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return item

    @app.post('/api/v1/flat/contracts', status_code=201)
    def approve_flat_contract(body: FlatContractInput, request: Request):
        auth = get_auth_context(request, store)
        pid = resolve_project_id(request, auth, store=store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        return store.record_flat_contract(body.to_contract_payload(), project_id=pid)

    @app.post('/api/v1/flat/definitions', status_code=201)
    def import_flat_definition(body: FlatDefinitionInput, request: Request):
        auth = get_auth_context(request, store)
        contract = store.flat_contract(body.contract_id)
        verify_object_access(store, contract, auth, allowed_roles=('owner', 'analyst'))
        pid = resolve_project_id(request, auth, store=store, fallback_object=contract)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        return store.record_flat_definition(**body.model_dump(mode='json'), project_id=pid)

    @app.post('/api/v1/flat/snapshots', status_code=202)
    async def flat_snapshot(request: Request, file: UploadFile = File(...), preparation_manifest: UploadFile | None = File(None), contract_id: str = Form(...),
                            name: str = Form('Flat extract'), project_id: str | None = Form(None)):
        auth = get_auth_context(request, store)
        if execution_mode != 'isolated':
            raise DomainError('ISOLATION_REQUIRED', 'Flat datasets require the local isolated runner.')
        contract_record = store.flat_contract(contract_id)
        verify_object_access(store, contract_record, auth, allowed_roles=('owner', 'analyst'))
        pid = project_id or resolve_project_id(request, auth, store=store, fallback_object=contract_record)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        contract = {key: value for key, value in contract_record.items()
                    if key not in {'id', 'created_at', 'version_number', 'project_id'}}
        suffix = Path(file.filename).suffix.lower() if file.filename else ''
        if suffix not in {'.csv', '.parquet'}:
            raise DomainError('UNSUPPORTED_FORMAT', 'Flat ingestion accepts CSV or Parquet extracts.')
        if ((suffix == '.csv' and contract['null_policy'] == 'native_null') or
                (suffix == '.parquet' and contract['null_policy'] != 'native_null')):
            raise DomainError('INVALID_CONTRACT', 'The approved NULL policy does not match the source format.')
        if not 1 <= len(name) <= 160:
            raise DomainError('INVALID_METADATA', 'A dataset name of 1–160 characters is required.')
        content = await file.read(25 * 1024 * 1024 + 1)
        if not content or len(content) > 25 * 1024 * 1024:
            raise DomainError('UPLOAD_LIMIT', 'Dataset must contain 1 byte to 25 MiB.', 413)
        store.check_storage_quota(pid, len(content), data_dir)
        source_hash = hashlib.sha256(content).hexdigest()
        preparation = None
        if preparation_manifest is not None:
            from packages.metricguard_core.flat import validate_preparation
            preparation = validate_preparation(await preparation_manifest.read(16 * 1024 + 1), source_hash)
        for existing in store.bootstrap(pid)['snapshots']:
            if (existing['source_hash'] == source_hash and
                    existing.get('contract_hash') == contract['contract_hash'] and
                    existing.get('execution_policy') == 'flat_snapshot_v1' and
                    existing.get('manifest', {}).get('flat_preparation') == preparation):
                return public_snapshot(existing)
        stage = data_dir / 'uploads' / f'{uuid4()}{suffix}'
        stage.parent.mkdir(parents=True, exist_ok=True)
        try:
            with stage.open('xb') as output:
                output.write(content)
            queued = store.queue_import(stage, source_hash, name, {'flat_contract': contract, **({'flat_preparation': preparation} if preparation else {})}, project_id=pid)
            if queued['reused']:
                stage.unlink(missing_ok=True)
            return queued
        except Exception:
            stage.unlink(missing_ok=True)
            raise

    @app.post('/api/v1/column-mappings', status_code=201)
    def approve_mapping(body: ColumnMappingInput, request: Request):
        auth = get_auth_context(request, store)
        contract = store.dataset_contract(body.contract_id)
        verify_object_access(store, contract, auth, allowed_roles=('owner', 'analyst'))
        pid = resolve_project_id(request, auth, store=store, fallback_object=contract)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        return store.record_column_mapping(body.contract_id, {'columns': body.columns}, project_id=pid)

    @app.get('/api/v1/column-mappings/{ident}')
    def column_mapping(ident: str, request: Request):
        auth = get_auth_context(request, store)
        item = store.column_mapping(ident)
        verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return item

    @app.post('/api/v1/mapped-snapshots', status_code=202)
    async def mapped_snapshot(request: Request, file: UploadFile = File(...), mapping_id: str = Form(...),
                              name: str = Form('Mapped extract'), project_id: str | None = Form(None)):
        auth = get_auth_context(request, store)
        if execution_mode != 'isolated':
            raise DomainError('ISOLATION_REQUIRED', 'Mapped datasets require the local isolated runner.')
        mapping = store.column_mapping(mapping_id)
        verify_object_access(store, mapping, auth, allowed_roles=('owner', 'analyst'))
        contract_record = store.dataset_contract(mapping['contract_id'])
        verify_object_access(store, contract_record, auth, allowed_roles=('owner', 'analyst'))
        pid = project_id or resolve_project_id(request, auth, store=store, fallback_object=mapping)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        contract = {key: value for key, value in contract_record.items()
                    if key not in {'id', 'created_at', 'version_number', 'project_id'}}
        approved_mapping = {key: value for key, value in mapping.items()
                            if key in {'columns', 'contract_hash', 'mapping_hash'}}
        suffix = Path(file.filename).suffix.lower() if file.filename else ''
        if suffix not in {'.csv', '.parquet'}:
            raise DomainError('UNSUPPORTED_FORMAT', 'Mapped v2 ingestion accepts CSV or Parquet extracts.')
        if ((suffix == '.csv' and contract['null_policy'] == 'native_null') or
                (suffix == '.parquet' and contract['null_policy'] != 'native_null')):
            raise DomainError('INVALID_CONTRACT', 'The approved NULL policy does not match the source format.')
        if not 1 <= len(name) <= 160:
            raise DomainError('INVALID_METADATA', 'A dataset name of 1–160 characters is required.')
        content = await file.read(25 * 1024 * 1024 + 1)
        if not content or len(content) > 25 * 1024 * 1024:
            raise DomainError('UPLOAD_LIMIT', 'Dataset must contain 1 byte to 25 MiB.', 413)
        store.check_storage_quota(pid, len(content), data_dir)
        source_hash = hashlib.sha256(content).hexdigest()
        for existing in store.bootstrap(pid)['snapshots']:
            if (existing['source_hash'] == source_hash and
                    existing.get('contract_hash') == contract['contract_hash'] and
                    existing.get('mapping_hash') == mapping['mapping_hash']):
                return public_snapshot(existing)
        stage = data_dir / 'uploads' / f'{uuid4()}{suffix}'
        stage.parent.mkdir(parents=True, exist_ok=True)
        try:
            with stage.open('xb') as output:
                output.write(content)
            queued = store.queue_import(stage, source_hash, name,
                                        {'contract': contract, 'mapping': approved_mapping}, project_id=pid)
            if queued['reused']:
                stage.unlink(missing_ok=True)
            return queued
        except Exception:
            stage.unlink(missing_ok=True)
            raise

    @app.post('/api/v1/dataset-previews', status_code=202)
    async def queue_dataset_preview(request: Request, file: UploadFile = File(...), project_id: str | None = Form(None)):
        auth = get_auth_context(request, store)
        pid = project_id or resolve_project_id(request, auth, store=store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        if execution_mode != 'isolated':
            raise DomainError('ISOLATION_REQUIRED', 'Source previews require the local isolated runner.')
        suffix = Path(file.filename).suffix.lower() if file.filename else ''
        if suffix not in {'.csv', '.parquet'}:
            raise DomainError('UNSUPPORTED_FORMAT', 'Preview accepts CSV or Parquet extracts.')
        content = await file.read(25 * 1024 * 1024 + 1)
        if not content or len(content) > 25 * 1024 * 1024:
            raise DomainError('UPLOAD_LIMIT', 'Dataset must contain 1 byte to 25 MiB.', 413)
        store.check_storage_quota(pid, len(content), data_dir)
        source_hash = hashlib.sha256(content).hexdigest()
        stage = data_dir / 'uploads' / f'{uuid4()}{suffix}'
        stage.parent.mkdir(parents=True, exist_ok=True)
        try:
            with stage.open('xb') as output:
                output.write(content)
            queued = store.queue_import(stage, source_hash,
                                        Path(file.filename).name[:160], {'purpose': 'preview'}, project_id=pid)
            if queued['reused']:
                stage.unlink(missing_ok=True)
            return queued
        except Exception:
            stage.unlink(missing_ok=True)
            raise

    @app.get('/api/v1/dataset-previews/{ident}')
    def dataset_preview(ident: str, request: Request):
        auth = get_auth_context(request, store)
        item = store.get_import(ident)
        verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst', 'reader'))
        if item['metadata'].get('purpose') != 'preview':
            raise DomainError('NOT_FOUND', 'The requested preview is not available.', 404)
        return item

    @app.get('/api/v1/snapshot-imports/{ident}')
    def snapshot_import_detail(ident: str, request: Request):
        auth = get_auth_context(request, store)
        item = store.get_import(ident)
        verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return item

    @app.post('/api/v1/snapshots', status_code=202)
    async def upload_snapshot(request: Request, file: UploadFile = File(...), name: str = Form('Imported orders'), currency: str = Form('INR'),
                              timezone: str = Form('Asia/Kolkata'), scope: str = Form('synthetic_retail_all_orders'), cutoff_at: str = Form('2026-09-02'),
                              project_id: str | None = Form(None)):
        auth = get_auth_context(request, store)
        pid = project_id or resolve_project_id(request, auth, store=store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        suffix = Path(file.filename).suffix.lower() if file.filename else ''
        if suffix not in {'.csv', '.parquet'}:
            raise DomainError('UNSUPPORTED_FORMAT', 'v1 accepts the documented orders CSV or Parquet schema only.')
        content = await file.read(25 * 1024 * 1024 + 1)
        if len(content) > 25 * 1024 * 1024:
            raise DomainError('UPLOAD_LIMIT', 'Dataset exceeds 25 MiB.', 413)
        if not content:
            raise DomainError('INVALID_CSV' if suffix == '.csv' else 'INVALID_PARQUET', 'Dataset is empty.')
        store.check_storage_quota(pid, len(content), data_dir)
        if not 1 <= len(name) <= 160 or any(len(value) > limit or not value for value, limit in
                ((currency, 8), (timezone, 64), (scope, 200), (cutoff_at, 10))):
            raise DomainError('INVALID_METADATA', 'Snapshot metadata is missing or too long.')
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', cutoff_at):
                raise ValueError('cutoff format')
            date.fromisoformat(cutoff_at)
        except ValueError:
            raise DomainError('INVALID_METADATA', 'A valid cutoff date is required.')
        metadata = {'currency': currency, 'timezone': timezone, 'scope': scope,
                    'cutoff_at': cutoff_at, 'grain': 'one_row_per_order_id',
                    'null_policy': 'blank_nullable' if suffix == '.csv' else 'native_null'}
        # Exact already-ingested bytes may reuse their existing frozen snapshot without execution.
        digest = hashlib.sha256(content).hexdigest()
        for snapshot in store.bootstrap(pid)['snapshots']:
            if snapshot['source_hash'] == digest and all(snapshot[k] == v for k, v in {'currency': currency, 'timezone': timezone, 'scope': scope, 'cutoff_at': cutoff_at}.items()):
                return public_snapshot(snapshot)
        if execution_mode != 'isolated':
            raise DomainError('ISOLATION_REQUIRED', 'New datasets require isolated ingestion. Start the Docker runner and local supervisor, then switch to isolated mode.')
        stage = data_dir / 'uploads' / f'{uuid4()}{suffix}'
        stage.parent.mkdir(parents=True, exist_ok=True)
        try:
            with stage.open('xb') as output:
                output.write(content)
            queued = store.queue_import(stage, digest, name, metadata, project_id=pid)
            if queued['reused']:
                stage.unlink(missing_ok=True)
            return queued
        except Exception:
            stage.unlink(missing_ok=True)
            raise

    def validated(body, auth=None, pid=None):
        payload = body.model_dump(mode='json', exclude_none=True)
        a, b = store.version(payload['baseline_version_id']), store.version(payload['target_version_id'])
        snapshot = store.snapshot(payload['snapshot_id'])
        if pid:
            if a.get('project_id', store.project_id) != pid or b.get('project_id', store.project_id) != pid:
                raise DomainError('FORBIDDEN', 'Definitions do not belong to the authorized project.', 403)
            if snapshot.get('project_id', store.project_id) != pid:
                raise DomainError('FORBIDDEN', 'Snapshot does not belong to the authorized project.', 403)
        if auth:
            verify_object_access(store, a, auth, allowed_roles=('owner', 'analyst', 'reader'))
            verify_object_access(store, b, auth, allowed_roles=('owner', 'analyst', 'reader'))
            verify_object_access(store, snapshot, auth, allowed_roles=('owner', 'analyst', 'reader'))
        if a.get('project_id', store.project_id) != b.get('project_id', store.project_id) or a.get('project_id', store.project_id) != snapshot.get('project_id', store.project_id):
            raise DomainError('FORBIDDEN', 'Baseline, target, and snapshot must belong to the same project.', 403)
        if any(item.get('execution_policy') in ('flat_snapshot_v1', 'relational_snapshot_v1') for item in (a, b, snapshot)):
            raise DomainError('POLICY_MISMATCH', 'Use the comparison policy pinned to these definitions and snapshot.')
        for version in (a, b):
            if (version.get('contract_hash') != snapshot.get('contract_hash') or
                    version.get('mapping_hash') != snapshot.get('mapping_hash')):
                raise DomainError('NOT_COMPARABLE', 'Definition binding differs from the frozen dataset contract or mapping.')
        if a['support_state'] == 'unsupported' or b['support_state'] == 'unsupported':
            raise DomainError('UNSUPPORTED_CONSTRUCT', 'Both definitions must fit the supported SQL grammar before comparing.')
        if not a['ir'] or not b['ir'] or not a['ir'].get('time_column') or not b['ir'].get('time_column'):
            raise DomainError('CONTEXT_REQUIRED', 'Confirm a known business date column for both definitions.')
        context = payload['context']
        if context['start'] >= context['end_exclusive']:
            raise DomainError('CONTEXT_REQUIRED', 'The period start must precede its exclusive end.')
        for field in ('currency', 'timezone', 'scope', 'grain'):
            if context[field] != snapshot[field]:
                raise DomainError('NOT_COMPARABLE', f'The requested {field} does not match the frozen snapshot.')
        for field in ('contract_hash', 'mapping_hash'):
            if context.get(field) != snapshot.get(field):
                raise DomainError('NOT_COMPARABLE', f'The requested {field} does not match the frozen mapping policy.')
        if a['ir']['aggregate'] != b['ir']['aggregate']:
            raise DomainError('NOT_COMPARABLE', 'Choose two definitions from the same aggregate family.')
        unit = 'currency' if a['ir']['aggregate'] == 'sum' else 'count'
        if context['unit'] != unit:
            raise DomainError('NOT_COMPARABLE', f'This comparison requires unit {unit}.')
        for version in (a, b):
            if version['ir'].get('period') and version['ir']['period'] != {k: context[k] for k in ('start', 'end_exclusive')}:
                raise DomainError('PERIOD_CONFLICT', 'The SQL literal period differs from the selected reporting window.')
        if not Path(snapshot['artifact_path']).is_file():
            raise DomainError('SNAPSHOT_MISSING', 'Historical results remain available, but this snapshot is missing and cannot be replayed.')
        return payload, a, b, snapshot

    def validated_flat(body, auth=None, pid=None):
        payload = body.model_dump(mode='json', exclude_none=True)
        a, b = store.version(payload['baseline_version_id']), store.version(payload['target_version_id'])
        snapshot = store.snapshot(payload['snapshot_id'])
        if pid:
            if a.get('project_id', store.project_id) != pid or b.get('project_id', store.project_id) != pid:
                raise DomainError('FORBIDDEN', 'Definitions do not belong to the authorized project.', 403)
            if snapshot.get('project_id', store.project_id) != pid:
                raise DomainError('FORBIDDEN', 'Snapshot does not belong to the authorized project.', 403)
        if auth:
            verify_object_access(store, a, auth, allowed_roles=('owner', 'analyst', 'reader'))
            verify_object_access(store, b, auth, allowed_roles=('owner', 'analyst', 'reader'))
            verify_object_access(store, snapshot, auth, allowed_roles=('owner', 'analyst', 'reader'))
        if a.get('project_id', store.project_id) != b.get('project_id', store.project_id) or a.get('project_id', store.project_id) != snapshot.get('project_id', store.project_id):
            raise DomainError('FORBIDDEN', 'Baseline, target, and snapshot must belong to the same project.', 403)
        if (snapshot.get('execution_policy') != 'flat_snapshot_v1' or
                any(version.get('execution_policy') != 'flat_snapshot_v1' for version in (a, b))):
            raise DomainError('POLICY_MISMATCH', 'Both definitions and the snapshot must use flat-snapshot policy v1.')
        contract_hash = snapshot.get('contract_hash')
        if (not contract_hash or any(version.get('contract_hash') != contract_hash for version in (a, b)) or
                payload['context']['contract_hash'] != contract_hash):
            raise DomainError('NOT_COMPARABLE', 'The flat contract changed; select definitions bound to this snapshot.')
        flat_contract = snapshot['manifest']['contract']
        if flat_contract.get('version') == 7:
            from packages.metricguard_core.common import CoreError
            from packages.metricguard_core.flat_case import check_case_ir
            try:
                for version in (a, b):
                    check_case_ir(version['ir'], flat_contract)
            except (CoreError, KeyError, TypeError, ValueError) as error:
                raise DomainError('NOT_COMPARABLE', 'CASE definitions must match the frozen v7 contract and execution policy.', 422) from error
        elif flat_contract.get('version') == 6:
            if (a['ir'].get('measure_expression') != b['ir'].get('measure_expression') or
                    a['ir'].get('measure_expression') != snapshot['manifest']['contract'].get('measure_expression')):
                raise DomainError('DIFFERENT_MEASURE_EXPRESSIONS', 'Both definitions must evaluate the identical declared measure expression.', 422)
        elif (a['ir']['measure'] != b['ir']['measure'] or
                a['ir']['measure'] != snapshot['manifest']['contract']['measure']):
            raise DomainError('NOT_COMPARABLE', 'Both definitions must SUM the same declared measure.')
        if (payload['context']['unit_label'] != snapshot['manifest']['unit_label'] or
                payload['context']['scope'] != snapshot['scope']):
            raise DomainError('NOT_COMPARABLE', 'Unit or whole-snapshot scope differs from the frozen contract.')
        if not Path(snapshot['artifact_path']).is_file():
            raise DomainError('SNAPSHOT_MISSING', 'This snapshot is missing and cannot be replayed.')
        return payload, a, b, snapshot

    @app.post('/api/v1/flat/comparisons/validate')
    def validate_flat_comparison(body: FlatComparisonInput, request: Request):
        auth = get_auth_context(request, store)
        snap = None
        try:
            snap = store.snapshot(body.snapshot_id)
        except Exception:
            pass
        pid = resolve_project_id(request, auth, store=store, fallback_object=snap)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        validated_flat(body, auth=auth, pid=pid)
        if execution_mode != 'isolated':
            raise DomainError('ISOLATION_REQUIRED', 'Flat comparisons require the local isolated runner.')
        return {'comparability_status': 'comparable', 'execution_mode': execution_mode}

    @app.post('/api/v1/flat/comparisons', status_code=202)
    def compare_flat(body: FlatComparisonInput, request: Request,
                     idempotency_key: str = Header(..., min_length=1, max_length=200)):
        auth = get_auth_context(request, store)
        snap = None
        try:
            snap = store.snapshot(body.snapshot_id)
        except Exception:
            pass
        pid = resolve_project_id(request, auth, store=store, fallback_object=snap)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        payload, _, _, _ = validated_flat(body, auth=auth, pid=pid)
        if execution_mode != 'isolated':
            raise DomainError('ISOLATION_REQUIRED', 'Flat comparisons require the local isolated runner.')
        payload['purpose'] = 'flat_comparison'
        payload['execution_policy'] = 'flat_snapshot_v1'
        return store.queue(payload, idempotency_key, execution_mode, project_id=pid)

    def demo_result(a, b, snapshot, context):
        catalog_file = data_dir / 'demo-catalog.json'
        if not catalog_file.is_file():
            raise DomainError('DEMO_NOT_READY', 'Generate the verified demo evidence with python -m scripts.seed_demo.')
        catalog = json.loads(catalog_file.read_text(encoding='utf-8'))
        key = fingerprint({'a': a['ir_hash'], 'b': b['ir_hash'], 'snapshot': snapshot['manifest_hash'], 'context': context})
        entry = catalog.get(key)
        if not entry:
            raise DomainError('ISOLATION_REQUIRED', 'This combination has no precomputed demo evidence. Start the isolated runner to execute new definitions or reporting periods.')
        file = data_dir / 'evidence' / (entry['hash'] + '.json')
        raw = file.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['hash']:
            raise DomainError('EVIDENCE_INTEGRITY', 'Saved evidence failed its integrity check.')
        result = json.loads(raw)
        result['provenance'].update(baseline_source_hash=a['raw_hash'], target_source_hash=b['raw_hash'], context_fingerprint=fingerprint(context),
                                    application_version='1.0.0', mapping_version='orders-v1', execution_mode='precomputed_synthetic_demo')
        result['limitations'].append('Precomputed synthetic demonstration. This request did not execute uploaded SQL.')
        return result

    @app.post('/api/v1/comparisons/validate')
    def validate_comparison(body: ComparisonInput, request: Request):
        auth = get_auth_context(request, store)
        snap = None
        try:
            snap = store.snapshot(body.snapshot_id)
        except Exception:
            pass
        pid = resolve_project_id(request, auth, store=store, fallback_object=snap)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        payload, a, b, snapshot = validated(body, auth=auth, pid=pid)
        if execution_mode == 'demo':
            demo_result(a, b, snapshot, payload['context'])
        return {'comparability_status': 'comparable', 'execution_mode': execution_mode}

    @app.post('/api/v1/comparisons', status_code=202)
    def compare(body: ComparisonInput, request: Request,
                idempotency_key: str = Header(..., min_length=1, max_length=200)):
        auth = get_auth_context(request, store)
        snap = None
        try:
            snap = store.snapshot(body.snapshot_id)
        except Exception:
            pass
        pid = resolve_project_id(request, auth, store=store, fallback_object=snap)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        payload, a, b, snapshot = validated(body, auth=auth, pid=pid)
        payload['purpose'] = 'comparison'
        result = demo_result(a, b, snapshot, payload['context']) if execution_mode == 'demo' else None
        run = store.queue(payload, idempotency_key, execution_mode, project_id=pid)
        if result is not None and run['state'] == 'queued':
            result['relationship_status'] = payload['relationship']
            result['comparability_status'] = 'comparable'
            run = store.complete_demo(run['id'], result)
        return run

    @app.post('/api/v1/definitions/{ident}/replays', status_code=202)
    def replay(ident: str, body: ComparisonInput, request: Request,
               idempotency_key: str = Header(..., min_length=1, max_length=200)):
        auth = get_auth_context(request, store)
        defn = store.definition(ident)
        verify_object_access(store, defn, auth, allowed_roles=('owner', 'analyst'))
        pid = defn.get('project_id', store.project_id)
        payload, a, b, snapshot = validated(body, auth=auth, pid=pid)
        if a['definition_id'] != ident or b['definition_id'] != ident or a['version_number'] >= b['version_number']:
            raise DomainError('INVALID_REPLAY', 'Choose an older and a newer version of this same definition.')
        payload['purpose'] = 'definition_replay'
        result = demo_result(a, b, snapshot, payload['context']) if execution_mode == 'demo' else None
        run = store.queue(payload, idempotency_key, execution_mode, project_id=pid)
        if result is not None and run['state'] == 'queued':
            result['relationship_status'] = payload['relationship']
            result['comparability_status'] = 'comparable'
            run = store.complete_demo(run['id'], result)
        return run

    @app.get('/api/v1/comparisons/{ident}')
    def run_detail(ident: str, request: Request):
        auth = get_auth_context(request, store)
        run = store.get_run(ident)
        verify_shared_access(request, store, auth, run)
        verify_object_access(store, run, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return run

    @app.post('/api/v1/investigations', status_code=201)
    def save_investigation(body: InvestigationInput, request: Request):
        auth = get_auth_context(request, store)
        run = store.get_run(body.run_id)
        verify_shared_access(request, store, auth, run, ('owner', 'analyst'))
        verify_object_access(store, run, auth, allowed_roles=('owner', 'analyst'))
        pid = run.get('project_id', store.project_id)
        return store.record_investigation(body.model_dump(mode='json'), project_id=pid)

    @app.get('/api/v1/investigations')
    def investigations(request: Request):
        auth = get_auth_context(request, store)
        pid = resolve_project_id(request, auth, store=store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return {'items': store.bootstrap(pid)['investigations']}

    @app.get('/api/v1/investigations/{ident}')
    def investigation_detail(ident: str, request: Request):
        auth = get_auth_context(request, store)
        try:
            item = store.investigation(ident)
        except DomainError as err:
            if err.code == 'NOT_FOUND':
                header_pid = request.headers.get('x-project-id') or request.query_params.get('project_id')
                fallback_obj = None
                if not header_pid:
                    for p in store.list_projects():
                        try:
                            d = store.get_investigation_draft(ident, project_id=p['id'])
                            if d:
                                fallback_obj = d
                                break
                        except Exception:
                            pass
                resolved_pid = resolve_project_id(request, auth, store=store, fallback_object=fallback_obj)
                draft = store.get_investigation_draft(ident, project_id=resolved_pid)
                verify_project_access(store, resolved_pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
                run = None
                if draft.get('comparison_run_id'):
                    try:
                        run = store.get_run(draft['comparison_run_id'])
                    except DomainError:
                        run = None
                snapshot = None
                if draft.get('snapshot_id'):
                    try:
                        snapshot = public_snapshot(store.snapshot(draft['snapshot_id']))
                    except DomainError:
                        snapshot = None
                b_ver = None
                if draft.get('baseline_version_id'):
                    try:
                        b_ver = store.version(draft['baseline_version_id'])
                    except DomainError:
                        b_ver = None
                t_ver = None
                if draft.get('target_version_id'):
                    try:
                        t_ver = store.version(draft['target_version_id'])
                    except DomainError:
                        t_ver = None
                replay_available = False
                if snapshot and snapshot.get('artifact_path'):
                    try:
                        replay_available = Path(snapshot['artifact_path']).is_file()
                    except Exception:
                        replay_available = False
                return {
                    **draft,
                    'is_draft': True,
                    'run_id': draft.get('comparison_run_id'),
                    'snapshot': snapshot,
                    'run': run,
                    'baseline_version': b_ver,
                    'target_version': t_ver,
                    'replay_available': replay_available,
                }
            raise
        verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst', 'reader'))
        verify_shared_access(request, store, auth, item)
        snapshot = store.snapshot(item['snapshot_id'])
        run = store.get_run(item['run_id'])
        return {**item, 'snapshot': public_snapshot(snapshot), 'run': run,
                'baseline_version': store.version(item['baseline_version_id']),
                'target_version': store.version(item['target_version_id']),
                'replay_available': Path(snapshot['artifact_path']).is_file()}

    @app.post('/api/v1/investigations/{ident}/replays', status_code=202)
    def investigation_replay(ident: str, request: Request,
                             idempotency_key: str = Header(..., min_length=1, max_length=200)):
        auth = get_auth_context(request, store)
        item = store.investigation(ident)
        verify_shared_access(request, store, auth, item, ('owner', 'analyst'))
        verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst'))
        pid = item.get('project_id', store.project_id)
        if item['context'].get('mode') == 'relational_snapshot':
            from .models import RelationalComparisonInput
            from .relational_routes import validate_comparison
            body = RelationalComparisonInput.model_validate({
                'baseline_version_id': item['baseline_version_id'], 'target_version_id': item['target_version_id'],
                'snapshot_id': item['snapshot_id'], 'context': item['context'], 'relationship': item['relationship']})
            payload = validate_comparison(store, body, auth, pid)
            if any(item.get(k) != payload[k] for k in ('baseline_ir_hash', 'target_ir_hash', 'snapshot_manifest_hash')):
                raise DomainError('REPLAY_PIN_MISMATCH', 'Saved relational definitions or snapshot identity changed; replay is unavailable.')
            if execution_mode != 'isolated':
                raise DomainError('ISOLATION_REQUIRED', 'Relational replay requires local isolated execution.')
            payload.update(purpose='investigation_replay', execution_policy='relational_snapshot_v1')
            return store.queue(payload, idempotency_key, execution_mode, project_id=pid)
        if item['context'].get('mode') == 'flat_snapshot':
            body = FlatComparisonInput.model_validate({
                'baseline_version_id': item['baseline_version_id'],
                'target_version_id': item['target_version_id'],
                'snapshot_id': item['snapshot_id'], 'context': item['context'],
                'relationship': item['relationship']})
            payload, _, _, _ = validated_flat(body, auth=auth, pid=pid)
            if execution_mode != 'isolated':
                raise DomainError('ISOLATION_REQUIRED', 'Flat replay requires the local isolated runner.')
            payload['purpose'] = 'investigation_replay'
            payload['execution_policy'] = 'flat_snapshot_v1'
            return store.queue(payload, idempotency_key, execution_mode, project_id=pid)
        body = ComparisonInput.model_validate({
            'baseline_version_id': item['baseline_version_id'],
            'target_version_id': item['target_version_id'],
            'snapshot_id': item['snapshot_id'], 'context': item['context'],
            'relationship': item['relationship']})
        payload, a, b, snapshot = validated(body, auth=auth, pid=pid)
        payload['purpose'] = 'investigation_replay'
        result = demo_result(a, b, snapshot, payload['context']) if execution_mode == 'demo' else None
        run = store.queue(payload, idempotency_key, execution_mode, project_id=pid)
        if result is not None and run['state'] == 'queued':
            run = store.complete_demo(run['id'], result)
        return run

    @app.post('/api/v1/comparisons/{ident}/cancel')
    def cancel(ident: str, request: Request):
        auth = get_auth_context(request, store)
        run = store.get_run(ident)
        verify_shared_access(request, store, auth, run, ('owner', 'analyst'))
        verify_object_access(store, run, auth, allowed_roles=('owner', 'analyst'))
        return store.cancel(ident)

    @app.get('/api/v1/comparisons/{ident}/evidence')
    def evidence(ident: str, request: Request, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        auth = get_auth_context(request, store)
        run = store.get_run(ident)
        verify_shared_access(request, store, auth, run)
        verify_object_access(store, run, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return evidence_page(run, offset, limit)

    @app.get('/api/v1/comparisons/{ident}/join-evidence')
    def join_evidence(ident: str, request: Request, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        auth = get_auth_context(request, store)
        run = store.get_run(ident)
        verify_shared_access(request, store, auth, run)
        verify_object_access(store, run, auth, allowed_roles=('owner', 'analyst', 'reader'))
        if run.get('execution_policy') != 'relational_snapshot_v1':
            raise DomainError('POLICY_MISMATCH', 'Join evidence is available only for relational snapshot runs.')
        if run['state'] not in ('succeeded', 'partial') or not run.get('result'):
            raise DomainError('RESULT_UNAVAILABLE', 'Complete this comparison before inspecting join evidence.')
        return {**evidence_page(run, offset, limit, include_join_evidence=True),
                'run_id': run['id'], 'snapshot_id': run['snapshot_id'],
                'baseline_version_id': run['baseline_version_id'], 'target_version_id': run['target_version_id'],
                'offset': offset, 'limit': limit}

    def evidence_page(run, offset, limit, *, include_join_evidence=False):
        result = run.get('result') or {}
        manifest = result.get('evidence_artifact')
        if manifest is None:
            if include_join_evidence:
                raise DomainError('EVIDENCE_UNAVAILABLE', 'This run has no isolated join-evidence artifact. Its stored report remains readable.')
            rows = result.get('evidence', [])
            return {'items': rows[offset:offset + limit], 'total': len(rows)}
        artifact = data_dir / 'evidence' / f"{run['id']}-{run['attempt']}.jsonl"
        try:
            # Executable identities come from frozen store records, never from
            # a manifest supplied alongside the evidence being checked.
            baseline_ir = store.version(run['baseline_version_id'])['ir']
            target_ir = store.version(run['target_version_id'])['ir']
            measure_expr = baseline_ir.get('measure_expression')
            case_context = None
            relational_context = None
            if run.get('execution_policy') == 'relational_snapshot_v1':
                frozen_snapshot = store.snapshot(run['snapshot_id'])
                frozen_manifest = frozen_snapshot['manifest']
                from packages.metricguard_core.relational import context_for
                if (run['context'] != context_for(frozen_manifest['contract']) or
                        frozen_snapshot.get('execution_policy') != run['execution_policy']):
                    raise ValueError('EVIDENCE_INVALID')
                relational_context = {'contract': frozen_manifest['contract'], 'manifest': frozen_manifest,
                                      'baseline': baseline_ir, 'target': target_ir}
                measure_expr = None
            elif baseline_ir.get('contract_version') == 7 or target_ir.get('contract_version') == 7:
                contract = store.snapshot(run['snapshot_id'])['manifest']['contract']
                case_context = {'contract': contract, 'baseline': baseline_ir, 'target': target_ir}
                measure_expr = None
            verified = verify_artifact(artifact, manifest, result['delta_target_minus_baseline'], offset, limit,
                                   measure_expression=measure_expr, case_context=case_context, expected_result=result, relational_context=relational_context,
                                   include_join_evidence=include_join_evidence)
        except (ValueError, KeyError, TypeError) as error:
            raise DomainError('EVIDENCE_INTEGRITY', 'Saved evidence failed its integrity check.') from error
        return verified if include_join_evidence else {'items': verified, 'total': manifest['row_count']}

    @app.get('/api/v1/comparisons/{ident}/export')
    def export(ident: str, request: Request, format: str = Query('json', pattern='^(json|html)$')):
        auth = get_auth_context(request, store)
        run = store.get_run(ident)
        verify_shared_access(request, store, auth, run)
        verify_object_access(store, run, auth, allowed_roles=('owner', 'analyst', 'reader'))
        if not run['result']:
            raise DomainError('RESULT_UNAVAILABLE', 'This run does not have completed evidence to export.')
        a, b = store.version(run['baseline_version_id']), store.version(run['target_version_id'])
        baseline, target = store.definition(a['definition_id']), store.definition(b['definition_id'])
        snapshot = public_snapshot(store.snapshot(run['snapshot_id']))
        if 'evidence_artifact' in run['result']:
            page = evidence_page(run, 0, 100, include_join_evidence=run.get('execution_policy') == 'relational_snapshot_v1')
            exported_result = {**run['result'], 'evidence': page['items']}
            total_rows = page['total']
        else:
            exported_result = run['result']
            total_rows = len(exported_result.get('evidence', []))
        included_rows = min(len(exported_result['evidence']), 100000) if format == 'html' else len(exported_result['evidence'])
        bundle = {**run, 'result': exported_result,
                  'evidence_coverage': {'total_rows': total_rows, 'included_rows': included_rows,
                                        'omitted_rows': total_rows - included_rows, 'detail': 'first_rows'},
                  'baseline': {'name': baseline['name'], 'version': a}, 'target': {'name': target['name'], 'version': b}, 'snapshot': snapshot,
                  'reproducibility': {'baseline_version': a, 'target_version': b, 'snapshot': snapshot, 'context': run['context'], 'provenance': run['result']['provenance']}}
        if run.get('execution_policy') == 'relational_snapshot_v1' and 'evidence_artifact' in run['result']:
            bundle['join_evidence'] = page['join_evidence']
        content = html_report(bundle) if format == 'html' else json.dumps(bundle, indent=2, ensure_ascii=False)
        if len(content.encode()) > 25 * 1024 * 1024:
            raise DomainError('EXPORT_LIMIT', 'Report exceeds 25 MiB; use paginated evidence instead.', 413)
        return Response(content, media_type='text/html' if format == 'html' else 'application/json',
                        headers={'Content-Disposition': f'attachment; filename="metricguard-{ident}.{format}"'})

    @app.post('/api/v1/pair-reviews', status_code=201)
    def pair_review(body: PairReview, request: Request):
        auth = get_auth_context(request, store)
        left = store.definition(body.left_definition_id)
        right = store.definition(body.right_definition_id)
        verify_object_access(store, left, auth, allowed_roles=('owner', 'analyst'))
        verify_object_access(store, right, auth, allowed_roles=('owner', 'analyst'))
        if left.get('project_id', store.project_id) != right.get('project_id', store.project_id):
            raise DomainError('FORBIDDEN', 'Definitions must belong to the same project.', 403)
        pid = left.get('project_id', store.project_id)
        return store.record('review', body.model_dump(), project_id=pid)

    @app.post('/api/v1/baselines', status_code=201)
    def baseline(body: BaselineInput, request: Request):
        auth = get_auth_context(request, store)
        v = store.version(body.version_id)
        verify_object_access(store, v, auth, allowed_roles=('owner', 'analyst'))
        pid = v.get('project_id', store.project_id)
        return store.record_baseline(body.model_dump(mode='json'), project_id=pid)

    @app.post('/api/v1/dependencies', status_code=201)
    def dependency(body: DependencyInput, request: Request):
        auth = get_auth_context(request, store)
        d = store.definition(body.definition_id)
        verify_object_access(store, d, auth, allowed_roles=('owner', 'analyst'))
        pid = d.get('project_id', store.project_id)
        return store.record('dependency', {**body.model_dump(), 'origin': 'manually_registered', 'coverage': 'registered_consumers_only'}, project_id=pid)

    # -----------------------------------------------------------------------
    # V3 Identity, Projects & RBAC
    # -----------------------------------------------------------------------

    @app.post('/api/v1/users', status_code=201)
    def create_user_route(body: UserCreateInput):
        user, raw_key = store.create_user(body.name, body.email)
        return {'user': user, 'api_key': raw_key}

    @app.post('/api/v1/projects', status_code=201)
    def create_project_route(body: ProjectCreateInput, request: Request):
        auth = get_auth_context(request, store)
        if auth.get('is_unverified'):
            raise DomainError('FORBIDDEN', 'Authentication required to create new projects.', 403)
        creator_id = auth['user']['id']
        project = store.create_project(body.name, owner_id=creator_id, project_id=body.id)
        return project

    @app.get('/api/v1/auth/identity')
    def auth_identity_route(request: Request, project_id: str | None = None):
        """Return only safe identity fields; never echoes credentials or key hashes."""
        auth = get_auth_context(request, store)  # invalid/inactive key -> 401
        if auth.get('is_unverified'):
            return {'verified': False, 'user': None, 'project_id': project_id, 'role': None}
        user = auth['user']
        role = None
        if project_id:
            member = verify_project_access(store, project_id, auth)  # non/revoked member -> 403
            role = member.get('role')
        return {
            'verified': True,
            'user': {'id': user.get('id'), 'name': user.get('name'), 'email': user.get('email')},
            'project_id': project_id,
            'role': role,
        }

    @app.get('/api/v1/projects')
    def list_projects_route(request: Request):
        auth = get_auth_context(request, store)
        if auth.get('is_unverified'):
            # Mirrors verify_project_access: the default project plus active local-analyst memberships.
            default = {**store.get_project(store.project_id), 'role': 'owner'}
            local = [p for p in store.list_projects_for_user('local-analyst') if p['id'] != store.project_id]
            return [default, *local]
        return store.list_projects_for_user(auth['user']['id'])

    @app.get('/api/v1/workspaces')
    def list_workspaces_route(request: Request):
        auth = get_auth_context(request, store)
        return store.list_projects()

    @app.post('/api/v1/workspaces', status_code=201)
    def create_workspace_route(body: ProjectCreateInput, request: Request):
        auth = get_auth_context(request, store)
        owner_id = 'local-analyst' if auth.get('is_unverified') else auth['user']['id']
        return store.create_project(body.name, owner_id=owner_id, project_id=body.id)

    @app.post('/api/v1/projects/{pid}/members', status_code=201)
    def add_project_member_route(pid: str, body: ProjectMemberInput, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner',), require_verified=True)
        return store.add_project_member(pid, body.user_id, body.role)

    @app.post('/api/v1/projects/{pid}/members/{uid}/revoke')
    def revoke_project_member_route(pid: str, uid: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner',), require_verified=True)
        return store.revoke_project_member(pid, uid)

    @app.get('/api/v1/projects/{pid}/members')
    def list_project_members_route(pid: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'), require_verified=True)
        return store.list_project_members(pid)

    # -----------------------------------------------------------------------
    # V3 Reviews & Decisions
    # -----------------------------------------------------------------------

    @app.post('/api/v1/projects/{pid}/review-requests', status_code=201)
    def create_review_request_route(pid: str, body: ReviewRequestInput, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        requester_id = auth['user']['id'] if not auth.get('is_unverified') else 'local-analyst'
        return store.create_review_request(pid, requester_id, body.model_dump())

    @app.get('/api/v1/projects/{pid}/review-requests')
    def list_review_requests_route(pid: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.list_review_requests(pid)

    @app.post('/api/v1/projects/{pid}/decisions', status_code=201)
    def record_decision_route(pid: str, body: DecisionInput, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner',), require_verified=True)
        decider_id = auth['user']['id'] if not auth.get('is_unverified') else 'local-analyst'
        return store.record_decision(pid, decider_id, body.model_dump())

    @app.get('/api/v1/projects/{pid}/decisions')
    def list_decisions_route(pid: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.list_decisions(pid)

    # -----------------------------------------------------------------------
    # V3 Monitoring & Alerts
    # -----------------------------------------------------------------------

    @app.post('/api/v1/projects/{pid}/monitoring-policies', status_code=201)
    def create_monitoring_policy_route(pid: str, body: MonitoringPolicyInput, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        creator_id = auth['user']['id'] if not auth.get('is_unverified') else 'local-analyst'
        return store.create_monitoring_policy(pid, creator_id, body.model_dump())

    @app.get('/api/v1/projects/{pid}/monitoring-policies')
    def list_monitoring_policies_route(pid: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.list_monitoring_policies(pid)

    @app.post('/api/v1/projects/{pid}/monitoring-checks', status_code=200)
    def run_monitoring_check_route(pid: str, request: Request, policy_id: str = Query(...), run_id: str = Query(...)):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))
        policy = store.get_monitoring_policy(policy_id)
        if not policy or policy['project_id'] != pid:
            raise DomainError('NOT_FOUND', 'Monitoring policy not found in project.', 404)
        run = store.get_run(run_id)
        if not run or run.get('project_id', store.project_id) != pid:
            raise DomainError('NOT_FOUND', 'Comparison run not found in project.', 404)
        result = run.get('result')
        if not result:
            raise DomainError('RESULT_UNAVAILABLE', 'Run has no completed result to evaluate.')

        b_ver = store.version(run['baseline_version_id'])
        t_ver = store.version(run['target_version_id'])
        snap = store.snapshot(run['snapshot_id'])
        target_snap_id = run.get('target_snapshot_id') or run['snapshot_id']
        t_snap = store.snapshot(target_snap_id) if target_snap_id != run['snapshot_id'] else snap

        change_type = classify_change(
            baseline_version=b_ver,
            target_version=t_ver,
            baseline_snapshot=snap,
            target_snapshot=t_snap,
        )

        decisions = store.list_decisions(pid)
        is_intentional_variant = any(
            d.get('approved_version_id') == run.get('target_version_id')
            and d.get('decision') == 'variant_accepted'
            for d in decisions
        ) or run.get('relationship') == 'intentional_variant'

        mat = evaluate_materiality(
            delta_str=result.get('delta_target_minus_baseline'),
            baseline_val_str=result.get('baseline_value'),
            target_val_str=result.get('target_value'),
            absolute_threshold=policy.get('absolute_threshold'),
            relative_threshold=policy.get('relative_threshold'),
            is_intentional_variant=is_intentional_variant,
            suppress_intentional_variants=policy.get('suppress_intentional_variants', True),
        )

        alert = None
        is_new_alert = False
        if mat['is_material']:
            event_hash = compute_event_hash(
                policy_id=policy_id,
                baseline_version_id=run['baseline_version_id'],
                target_version_id=run['target_version_id'],
                snapshot_id=run['snapshot_id'],
                delta=result.get('delta_target_minus_baseline'),
            )
            alert_data = {
                'run_id': run_id,
                'change_type': change_type,
                'delta': result.get('delta_target_minus_baseline'),
                'baseline_value': result.get('baseline_value'),
                'target_value': result.get('target_value'),
                'materiality': mat,
            }
            alert, is_new_alert = store.record_alert(
                project_id=pid,
                policy_id=policy_id,
                event_hash=event_hash,
                alert_data=alert_data,
            )

        return {
            'policy_id': policy_id,
            'run_id': run_id,
            'change_type': change_type,
            'materiality': mat,
            'alert': alert,
            'is_new_alert': is_new_alert,
        }

    @app.get('/api/v1/projects/{pid}/alerts')
    def list_alerts_route(pid: str, request: Request, status: str | None = Query(None)):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.list_alerts(pid, state=status)

    @app.post('/api/v1/alerts/{ident}/acknowledge')
    def acknowledge_alert_route(ident: str, body: AlertAcknowledgeInput, request: Request):
        auth = get_auth_context(request, store)
        alert = store.get_alert(ident)
        if not alert:
            raise DomainError('NOT_FOUND', 'Alert not found.', 404)
        verify_project_access(store, alert['project_id'], auth, allowed_roles=('owner', 'analyst'))
        user_id = auth['user']['id'] if not auth.get('is_unverified') else 'local-analyst'
        return store.acknowledge_alert(ident, user_id, note=body.note)

    # -----------------------------------------------------------------------
    # V3 Quotas, Governance, Backup & Restore
    # -----------------------------------------------------------------------

    @app.post('/api/v1/projects/{pid}/quotas')
    def set_project_quota_route(pid: str, body: ProjectQuotaInput, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner',), require_verified=True)
        return store.set_project_quota(pid, body.model_dump(exclude_none=True))

    @app.get('/api/v1/projects/{pid}/quotas')
    def get_project_quota_route(pid: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.get_project_quota(pid)

    @app.get('/api/v1/projects/{pid}/audit-logs')
    def list_audit_logs_route(pid: str, request: Request, limit: int = Query(100, ge=1, le=500)):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner',), require_verified=True)
        return store.list_audit_logs(pid, limit=limit)

    @app.post('/api/v1/projects/{pid}/backup')
    def backup_project_route(pid: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner',))
        backup_dir = data_dir / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        archive_path = backup_dir / f"backup-{pid}-{uuid4().hex[:8]}.tar.gz"
        backup_project(store, pid, data_dir, archive_path)
        return FileResponse(archive_path, filename=archive_path.name, media_type='application/gzip')

    @app.post('/api/v1/projects/{pid}/restore')
    async def restore_project_route(pid: str, request: Request, file: UploadFile = File(...)):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner',), require_verified=True)
        stage = data_dir / 'uploads' / f"restore-{uuid4().hex}.tar.gz"
        stage.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = await file.read()
            stage.write_bytes(content)
            restored = restore_project(store, stage, data_dir)
            return {'status': 'restored', 'project_id': pid, 'details': restored}
        finally:
            stage.unlink(missing_ok=True)

    # -----------------------------------------------------------------------
    # V4-02 PostgreSQL Source Collector & Extraction Endpoints
    # -----------------------------------------------------------------------

    @app.post('/api/v1/projects/{pid}/sources/postgres/extract')
    def extract_postgres_source(pid: str, payload: PostgresExtractionInput, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst'))

        collector = PostgresCollector(store=store, data_dir=data_dir)
        selection = PostgresSourceSelection(
            source_uri=payload.source_uri,
            schema_name=payload.schema_name,
            table_name=payload.table_name,
            columns=payload.columns,
            contract_id=payload.contract_id,
            mapping_id=payload.mapping_id,
            cutoff_column=payload.cutoff_column,
            cutoff_value=payload.cutoff_value,
            extraction_id=payload.extraction_id,
            snapshot_name=payload.snapshot_name,
            row_limit=payload.row_limit or 1_000_000,
        )
        result = collector.collect(selection, project_id=pid)
        return store.get_extraction(result['extraction_id'], project_id=pid)

    @app.get('/api/v1/projects/{pid}/extractions/{id}')
    def get_postgres_extraction(pid: str, id: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.get_extraction(id, project_id=pid)

    @app.get('/api/v1/projects/{pid}/extractions')
    def list_postgres_extractions(pid: str, request: Request):
        auth = get_auth_context(request, store)
        verify_project_access(store, pid, auth, allowed_roles=('owner', 'analyst', 'reader'))
        return store.list_extractions(pid)

    web_dist = ROOT / 'apps/web/dist'
    if web_dist.is_dir():
        index_file = web_dist / 'index.html'

        @app.get('/w/{path:path}')
        def spa_fallback(path: str):
            return FileResponse(index_file)

        app.mount('/', StaticFiles(directory=web_dist, html=True), name='web')
    return app


app = create_app()
