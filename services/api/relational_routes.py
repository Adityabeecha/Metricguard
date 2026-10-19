"""Minimal V8.1 endpoints; analytical work remains in the fixed runner."""

import hashlib
from pathlib import Path
from uuid import uuid4
import zipfile
import tempfile

from .relational_prepared_models import RelationalPreparedContractInput, PrepareRelationalInput

from fastapi import File, Form, Header, Request, UploadFile
from starlette.datastructures import UploadFile as FormUploadFile

from .auth import get_auth_context, verify_object_access, verify_project_access
from .models import FlatDefinitionInput, RelationalContractInput, RelationalComparisonInput, RelationalStarContractInput
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
            raise DomainError('POLICY_MISMATCH', 'Use only relational definitions and a relational snapshot.')
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
    def register_contract(body: RelationalContractInput | RelationalStarContractInput | RelationalPreparedContractInput, request: Request):
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
    async def import_snapshot(request: Request, fact_file: UploadFile = File(...), dimension_file: UploadFile | None = File(None),
                              contract_id: str = Form(...), name: str = Form('Relational snapshot'), project_id: str | None = Form(None)):
        """Contract v1: fact_file + dimension_file. Contract v2: fact_file + one lookup_<role> file per lookup table."""
        isolated()
        auth = get_auth_context(request, store)
        record = store.relational_contract(contract_id)
        pid = project(request, auth, record, project_id)
        authorize(store, pid, auth, ('owner', 'analyst'))
        if not name.strip() or len(name) > 160:
            raise DomainError('INVALID_METADATA', 'Enter a snapshot name of 1–160 characters.')
        contract = clean_contract(record)
        star = contract.get('version') in (2, 3)
        if star:
            form = await request.form()
            required = {'fact_file', *('lookup_' + d['role'] for d in contract['dimensions'])}
            if (any(len(form.getlist(field)) != 1 for field in required) or
                    any(isinstance(value, FormUploadFile) and field not in required for field, value in form.multi_items())):
                raise DomainError('INVALID_METADATA', 'Attach exactly one fact CSV and one CSV per declared lookup, without extra files.')
            uploads = [('fact', fact_file)]
            for lookup in contract['dimensions']:
                upload = form.get('lookup_' + lookup['role'])
                if upload is None or isinstance(upload, str):
                    raise DomainError('INVALID_METADATA', f"Attach the CSV for lookup table {lookup['role']} as lookup_{lookup['role']}.")
                uploads.append((lookup['role'], upload))
        else:
            if dimension_file is None:
                raise DomainError('INVALID_METADATA', 'Attach the dimension CSV.')
            uploads = [('fact', fact_file), ('dimension', dimension_file)]
        limit = 512 * 1024 * 1024 if contract.get('version') == 3 else MAX_BYTES
        stage = data_dir / 'uploads' / f'{uuid4()}.zip'
        stage.parent.mkdir(parents=True, exist_ok=True)
        total, hashes = 0, {}
        try:
            with tempfile.TemporaryDirectory(prefix='relational-upload-', dir=stage.parent) as folder:
                paths = {}
                for role, upload in uploads:
                    if Path(upload.filename or '').suffix.lower() != '.csv':
                        raise DomainError('UNSUPPORTED_FORMAT', 'Relational snapshots accept UTF-8 CSV extracts.')
                    path = Path(folder) / (role + '.csv')
                    sha, size = hashlib.sha256(), 0
                    with path.open('wb') as output:
                        while blob := await upload.read(min(1024 * 1024, limit - total + 1)):
                            total += len(blob); size += len(blob)
                            if total > limit:
                                raise DomainError('UPLOAD_LIMIT', f'CSV files must fit within {limit // (1024 * 1024)} MiB combined.', 413)
                            sha.update(blob); output.write(blob)
                    if not size:
                        raise DomainError('UPLOAD_LIMIT', 'Every CSV must be non-empty.', 413)
                    hashes[role], paths[role] = sha.hexdigest(), path
                store.check_storage_quota(pid, total + 4096 * len(paths), data_dir)
                for existing in store.bootstrap(pid)['snapshots']:
                    if (existing.get('execution_policy') == EXECUTION_POLICY and existing.get('contract_hash') == contract['contract_hash'] and
                            existing.get('manifest', {}).get('source_hashes') == hashes and Path(existing['artifact_path']).is_file()):
                        from .app import public_snapshot
                        return public_snapshot(existing)
                with zipfile.ZipFile(stage, 'x', compression=zipfile.ZIP_STORED) as bundle:
                    for role, path in paths.items():
                        entry_name = (role + '.csv') if not star or role == 'fact' else f'dim_{role}.csv'
                        entry = zipfile.ZipInfo(entry_name, date_time=(1980, 1, 1, 0, 0, 0))
                        with bundle.open(entry, 'w') as output, path.open('rb') as source:
                            while blob := source.read(1024 * 1024):
                                output.write(blob)
            from packages.metricguard_core.common import file_hash
            job = store.queue_import(stage, file_hash(stage), name, {'relational_contract': contract, 'source_hashes': hashes}, project_id=pid)
            if job['reused']:
                stage.unlink(missing_ok=True)
            return job
        except Exception:
            stage.unlink(missing_ok=True)
            raise

    @app.post('/api/v1/relational/snapshots/{snapshot_id}/prepare-flat', status_code=201)
    def prepare_flat(snapshot_id: str, body: PrepareRelationalInput, request: Request):
        from packages.metricguard_core.relational_prepared import prepare, specification
        from packages.metricguard_core.relational_snapshot import open_snapshot
        from packages.metricguard_core.snapshot import manifest_path
        from .app import public_snapshot
        auth = get_auth_context(request, store)
        source = store.snapshot(snapshot_id)
        pid = project(request, auth, source)
        authorize(store, pid, auth, ('owner', 'analyst'))
        isolated()
        if source.get('execution_policy') != EXECUTION_POLICY:
            raise DomainError('POLICY_MISMATCH', 'Select a validated relational snapshot.')
        c, manifest = open_snapshot(source['artifact_path'])
        if manifest != source['manifest']:
            raise DomainError('SNAPSHOT_INTEGRITY', 'Stored relational source differs from its frozen manifest.')
        specification(c, body.selected_columns, body.joins, body.name)
        # Reserve a conservative artifact allowance before generated-query preparation.
        store.check_storage_quota(pid, Path(source['artifact_path']).stat().st_size * 3, data_dir)
        destination = data_dir / 'snapshots' / f'{uuid4()}.duckdb'
        try:
            flat, prepared = prepare(source['artifact_path'], destination,
                selected_columns=body.selected_columns, joins=body.joins, name=body.name)
            store.check_storage_quota(pid, 0, data_dir)
            registered = store.record_flat_contract(flat, project_id=pid)
            snapshot = store.record_snapshot(body.name, prepared, destination, project_id=pid)
            lineage = prepared['relational_preparation']
            return {'contract': registered, 'snapshot': public_snapshot(snapshot),
                    'column_mapping': lineage['column_mapping'], 'lineage': lineage}
        except Exception:
            destination.unlink(missing_ok=True)
            manifest_path(destination).unlink(missing_ok=True)
            destination.with_suffix('.lineage.jsonl').unlink(missing_ok=True)
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
