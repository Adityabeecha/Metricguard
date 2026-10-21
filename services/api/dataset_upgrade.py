"""Queue explicit capability upgrades; all table reads stay in the isolated runner."""
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from packages.metricguard_core.flat_upgrade import upgrade_contract
from .auth import get_auth_context, verify_object_access
from .store import DomainError


def register_routes(app, store, data_dir, execution_mode):
    @app.post('/api/v1/snapshots/{ident}/enable-averages-ratios', status_code=202)
    def enable_averages_ratios(ident: str, request: Request):
        auth = get_auth_context(request, store)
        snapshot = store.snapshot(ident)
        verify_object_access(store, snapshot, auth, allowed_roles=('owner', 'analyst'))
        requested_project = request.headers.get('x-project-id') or request.query_params.get('project_id')
        pid = snapshot.get('project_id', store.project_id)
        if requested_project and requested_project != pid:
            raise DomainError('PROJECT_MISMATCH', 'The dataset belongs to another workspace.', 403)
        if execution_mode != 'isolated':
            raise DomainError('ISOLATION_REQUIRED', 'Enabling averages and ratios requires the isolated import engine.')
        manifest = snapshot.get('manifest') or {}
        if snapshot.get('execution_policy') != 'flat_snapshot_v1' or manifest.get('contract_version') != 4:
            raise DomainError('UPGRADE_UNSUPPORTED', 'This upgrade is available for older single-table datasets. Newer datasets already have these options.')
        contract = upgrade_contract(manifest['contract'])
        lineage = {key: manifest[key] for key in ('manifest_hash', 'database_hash', 'contract_hash', 'source_hash')}
        lineage['snapshot_id'] = ident
        for existing in store.bootstrap(pid)['snapshots']:
            if (existing.get('manifest', {}).get('frozen_upgrade') == lineage and
                    existing.get('contract_hash') == contract['contract_hash']):
                from .app import public_snapshot
                return JSONResponse(public_snapshot(existing), status_code=200)
        artifact = Path(snapshot['artifact_path']).resolve()
        root = Path(data_dir).resolve()
        if not artifact.is_relative_to(root / 'snapshots'):
            raise DomainError('UPGRADE_UNAVAILABLE', 'This frozen dataset is outside the managed snapshot store.', 409)
        store.record_flat_contract(contract, project_id=pid)
        queued = store.queue_import(artifact, manifest['database_hash'],
            f"{snapshot['name'][:132]} · averages and ratios", {'flat_contract': contract, 'frozen_upgrade': lineage}, project_id=pid)
        return JSONResponse(queued, status_code=202)
