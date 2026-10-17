"""Minimal V8.1 endpoints; analytical work remains in the fixed runner."""

import hashlib
from pathlib import Path
from uuid import uuid4
import zipfile

from fastapi import File, Form, Header, Request, UploadFile

from .auth import get_auth_context, verify_object_access, verify_project_access
from .models import FlatDefinitionInput, RelationalContractInput, RelationalComparisonInput
from .store import DomainError
from packages.metricguard_core.relational import EXECUTION_POLICY, MAX_BYTES, context_for, validate_ir
from packages.metricguard_core.relational_snapshot import validate_manifest


def clean_contract(record):
    return {k: v for k, v in record.items() if k not in {'id', 'created_at', 'version_number', 'project_id'}}


def authorize(store, pid, auth, roles=('owner', 'analyst', 'reader')):
    member = verify_project_access(store, pid, auth, allowed_roles=roles)
    # The legacy unverified-local path returns a membership without applying
    # allowed_roles. The new mode must enforce the returned role as well.
    if member['role'] not in roles:
        raise DomainError('FORBIDDEN', 'This workspace role cannot perform this action.', 403)
    return member


def verify_shared_access(request, store, auth, item, roles=('owner', 'analyst', 'reader')):
    """Object routes stay within the selected workspace for this new policy."""
    if item.get('execution_policy') != EXECUTION_POLICY and item.get('context', {}).get('mode') != 'relational_snapshot':
        return
    contexts = [v for v in (request.headers.get('x-project-id'), request.query_params.get('project_id')) if v]
    if len(set(contexts)) > 1:
        raise DomainError('PROJECT_MISMATCH', 'Header and query workspace context must agree.', 400)
    pid = item['project_id']
    if contexts and contexts[0] != pid:
        raise DomainError('FORBIDDEN', 'Selected relational record belongs to another workspace.', 403)
    authorize(store, pid, auth, roles)


def validate_comparison(store, body, auth, pid):
    payload = body.model_dump(mode='json')
    a, b = store.version(body.baseline_version_id), store.version(body.target_version_id)
    snapshot = store.snapshot(body.snapshot_id)
    for item in (a, b, snapshot):
        if item.get('project_id') != pid:
            raise DomainError('FORBIDDEN', 'Both definitions and the snapshot must belong to this workspace.', 403)
        verify_object_access(store, item, auth)
        if item.get('execution_policy') != EXECUTION_POLICY:
            raise DomainError('POLICY_MISMATCH', 'Use only relational v1 definitions and a relational v1 snapshot.')
    contract = validate_manifest(snapshot['manifest'], snapshot['manifest']['contract'])
    for version in (a, b):
        if (version.get('support_state') != 'executable' or version.get('contract_hash') != contract['contract_hash'] or
                version.get('ir_hash') != version.get('ir', {}).get('ir_hash')):
            raise DomainError('NOT_COMPARABLE', 'Select executable definitions bound to the snapshot contract.')
        validate_ir(version['ir'], contract)
    if payload['context'] != context_for(contract):
        raise DomainError('NOT_COMPARABLE', 'Context must match the frozen whole-snapshot scope and declared unit.')
    if not Path(snapshot['artifact_path']).is_file():
        raise DomainError('SNAPSHOT_MISSING', 'Stored results remain readable, but this snapshot is unavailable for replay.')
    payload.update(baseline_ir_hash=a['ir_hash'], target_ir_hash=b['ir_hash'], snapshot_manifest_hash=snapshot['manifest_hash'])
    return payload


def install_relational_routes(app, store, data_dir, execution_mode, resolve_project_id):
    def project(request, auth, fallback=None, supplied=None):
        contexts = [v for v in (request.headers.get('x-project-id'), request.query_params.get('project_id'), supplied) if v]
        if len(set(contexts)) > 1:
            raise DomainError('PROJECT_MISMATCH', 'Header, query and form workspace context must agree.', 400)
        pid = supplied or resolve_project_id(request, auth, store=store, fallback_object=fallback)
        if fallback is not None and fallback['project_id'] != pid:
            raise DomainError('FORBIDDEN', 'Selected object belongs to another workspace.', 403)
        return pid

    def isolated():
        if execution_mode != 'isolated':
            raise DomainError('ISOLATION_REQUIRED', 'Relational snapshots require the local isolated execution mode.')

    @app.post('/api/v1/relational/contracts', status_code=201)
    def register_contract(body: RelationalContractInput, request: Request):
        auth = get_auth_context(request, store)
        pid = project(request, auth)
        authorize(store, pid, auth, ('owner', 'analyst'))
        return store.record_relational_contract(body.model_dump(mode='json'), pid)

    @app.post('/api/v1/relational/definitions', status_code=201)
    def register_definition(body: FlatDefinitionInput, request: Request):
        auth = get_auth_context(request, store)
        record = store.relational_contract(body.contract_id)
        pid = project(request, auth, record)
        authorize(store, pid, auth, ('owner', 'analyst'))
        return store.record_relational_definition(**body.model_dump(mode='json'), project_id=pid)

    @app.post('/api/v1/relational/snapshots', status_code=202)
    async def import_snapshot(request: Request, fact_file: UploadFile = File(...), dimension_file: UploadFile = File(...),
                              contract_id: str = Form(...), name: str = Form('Relational snapshot'), project_id: str | None = Form(None)):
        isolated()
        auth = get_auth_context(request, store)
        record = store.relational_contract(contract_id)
        pid = project(request, auth, record, project_id)
        authorize(store, pid, auth, ('owner', 'analyst'))
        if not name.strip() or len(name) > 160:
            raise DomainError('INVALID_METADATA', 'Enter a snapshot name of 1–160 characters.')
        contract = clean_contract(record)
        content, total = {}, 0
        for role, upload in (('fact', fact_file), ('dimension', dimension_file)):
            if Path(upload.filename or '').suffix.lower() != '.csv':
                raise DomainError('UNSUPPORTED_FORMAT', 'The initial relational mode accepts two UTF-8 CSV extracts.')
            blob = await upload.read(MAX_BYTES - total + 1)
            total += len(blob)
            if not blob or total > MAX_BYTES:
                raise DomainError('UPLOAD_LIMIT', 'Both non-empty CSV files must fit within 25 MiB combined.', 413)
            content[role] = blob
        store.check_storage_quota(pid, total + 4096, data_dir)
        hashes = {role: hashlib.sha256(blob).hexdigest() for role, blob in content.items()}
        for existing in store.bootstrap(pid)['snapshots']:
            if (existing.get('execution_policy') == EXECUTION_POLICY and existing.get('contract_hash') == contract['contract_hash'] and
                    existing.get('manifest', {}).get('source_hashes') == hashes and Path(existing['artifact_path']).is_file()):
                from .app import public_snapshot
                return public_snapshot(existing)
        stage = data_dir / 'uploads' / f'{uuid4()}.zip'
        stage.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Fixed entry names and timestamp: deterministic identity, no user paths.
            with zipfile.ZipFile(stage, 'x', compression=zipfile.ZIP_STORED) as bundle:
                for role in ('fact', 'dimension'):
                    entry = zipfile.ZipInfo(role + '.csv', date_time=(1980, 1, 1, 0, 0, 0))
                    bundle.writestr(entry, content[role])
            from packages.metricguard_core.common import file_hash
            job = store.queue_import(stage, file_hash(stage), name, {'relational_contract': contract, 'source_hashes': hashes}, project_id=pid)
            if job['reused']:
                stage.unlink(missing_ok=True)
            return job
        except Exception:
            stage.unlink(missing_ok=True)
            raise

    @app.post('/api/v1/relational/comparisons/validate')
    def validate(body: RelationalComparisonInput, request: Request):
        auth = get_auth_context(request, store)
        pid = project(request, auth, store.snapshot(body.snapshot_id))
        authorize(store, pid, auth)
        isolated()
        validate_comparison(store, body, auth, pid)
        return {'comparability_status': 'comparable', 'execution_mode': execution_mode}

    @app.post('/api/v1/relational/comparisons', status_code=202)
    def compare(body: RelationalComparisonInput, request: Request, idempotency_key: str = Header(..., min_length=1, max_length=200)):
        auth = get_auth_context(request, store)
        pid = project(request, auth, store.snapshot(body.snapshot_id))
        authorize(store, pid, auth, ('owner', 'analyst'))
        isolated()
        payload = validate_comparison(store, body, auth, pid)
        payload.update(purpose='relational_comparison', execution_policy=EXECUTION_POLICY)
        return store.queue(payload, idempotency_key, execution_mode, project_id=pid)
