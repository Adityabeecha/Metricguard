"""Read-only capabilities for one sealed case; raw tokens are never persisted.

Public routes receive an already resolved run, never a caller supplied run/workspace id.
The callbacks are the same verified evidence readers as authenticated routes, without
their HTTP/auth wrappers. Public responses use explicit DTO field allowlists.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select, update

from .auth import get_auth_context, verify_object_access
from .store import DomainError, now, records


class ShareInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expires_in_days: Literal[7, 30] | None = 7


def pick(value, fields):
    return {key: value[key] for key in fields.split() if key in (value or {})}


CONTEXT = 'mode comparison start end_exclusive timezone currency unit unit_label grain scope contract_hash mapping_hash values_comparable manifest_hash target_manifest_hash'
ROW = ('order_id row_key fact_key baseline_included target_included baseline_contribution target_contribution delta '
       'baseline_null_term target_null_term membership baseline_operands target_operands baseline_expression_value '
       'target_expression_value baseline_when_state target_when_state baseline_where_state target_where_state '
       'baseline_branch target_branch baseline_case_term target_case_term measure_value predicate_inputs '
       'baseline_supporting_rows target_supporting_rows baseline_numerator_input target_numerator_input '
       'baseline_denominator_input target_denominator_input baseline_numerator target_numerator baseline_denominator '
       'target_denominator baseline_in_numerator target_in_numerator effect effect_exact baseline_present target_present '
       'change measure categories context baseline_context target_context foreign_key dimension_key dimension_matched join_membership '
       'baseline_join_included target_join_included fact_values dimension_values lookup_values')
PROVENANCE = ('engine_version execution_policy contract_hash mapping_hash snapshot_manifest_hash database_hash '
              'baseline_ir_hash target_ir_hash baseline_raw_hash target_raw_hash preparation_manifest_hash '
              'target_snapshot_manifest_hash target_database_hash')


def public_result(result):
    dto = pick(result, 'baseline_value target_value delta_target_minus_baseline absolute_gap relative_change_percent_display '
               'residual currency unit_label numeric_tolerance method definition_status value_status explanation_status '
               'limitations aggregate value_is_rounded')
    if result.get('evidence_coverage'):
        dto['evidence_coverage'] = pick(result['evidence_coverage'], 'mode total_records listed_records unchanged_included_in_both unchanged_included_in_neither')
    dto['steps'] = [pick(row, 'position rule previous_value next_value contribution') for row in result.get('steps', [])]
    dto['definition_differences'] = [pick(row, 'rule baseline target changed') for row in result.get('definition_differences', [])]
    dto['evidence'] = [pick(row, ROW) for row in result.get('evidence', [])]
    dto['provenance'] = pick(result.get('provenance'), PROVENANCE)
    if result.get('evidence_artifact'):
        dto['evidence_artifact'] = pick(result['evidence_artifact'], 'sha256 row_count schema_version')
    if result.get('distinct'):
        dto['distinct'] = pick(result['distinct'], 'policy entity_column gained lost kept baseline_null_entity_rows target_null_entity_rows')
    if result.get('change_summary'):
        dto['change_summary'] = {key: pick(value, 'records delta') for key, value in result['change_summary'].items()
                                 if key in ('added', 'removed', 'value_changed', 'entered_filter', 'left_filter', 'unchanged')}
    if result.get('shapley'):
        value = result['shapley']
        dto['shapley'] = {**pick(value, 'status method gap evaluated_hybrids limitation'),
            'contributions': [pick(row, 'rule numerator denominator display_decimal') for row in value.get('contributions', [])]}
    if result.get('ratio'):
        value = result['ratio']
        dto['ratio'] = pick(value, 'policy display_places numerator_scale denominator_scale share_scale numerator_change '
                           'denominator_change status sigma scale_divisor scaled_gap gap_exact pooled_average unit')
        for side in ('baseline', 'target'):
            dto['ratio'][side] = pick(value.get(side), 'numerator denominator exact display undefined_reason')
        for part in ('numerator', 'denominator'):
            dto['ratio'][part] = pick(value.get(part), 'aggregate measure minus unit_label')
        dto['ratio']['effects'] = [pick(row, 'rule exact display') for row in value.get('effects', [])]
        dto['ratio']['corners'] = pick(value.get('corners'), 'n0d0 n0d1 n1d0 n1d1') if value.get('corners') else None
    return dto


def case_dto(store, case, run):
    snapshot = store.snapshot(case['snapshot_id'])
    manifest = snapshot.get('manifest') or {}
    dto = pick(case, 'id title decision created_at relationship baseline_version_id target_version_id run_id snapshot_id target_snapshot_id')
    dto['workspace_name'] = store.get_project(case['project_id'])['name']
    dto['context'] = pick(case.get('context'), CONTEXT)
    dto['snapshot'] = pick(snapshot, 'id name row_count manifest_hash source_hash contract_hash mapping_hash currency timezone scope grain cutoff_at status created_at unit_label')
    if not dto['snapshot'].get('unit_label') and manifest.get('unit_label'):
        dto['snapshot']['unit_label'] = manifest['unit_label']
    for side in ('baseline', 'target'):
        dto[f'{side}_version'] = pick(store.version(case[f'{side}_version_id']),
             'id definition_id version_number raw_sql bound_sql time_column source_time_column support_state support_reason raw_hash ir_hash observed_at change_kind')
    dto['run'] = pick(run, 'id baseline_version_id target_version_id snapshot_id target_snapshot_id relationship state created_at execution_mode started_at completed_at evidence_state evidence_expired_at')
    dto['run'].update(context=pick(run.get('context'), CONTEXT), result=public_result(run['result']), error=None)
    return dto


def read_dto(kind, value):
    common = 'available reason evidence_state total offset limit columns column total_change total_change_exact ratio other_groups other_change other_records other_changed_records abs_change_total total_records coverage_note grouping unavailable_columns'
    dto = pick(value, common)
    if kind in ('evidence', 'evidence-group'):
        dto['items'] = [pick(row, ROW) for row in value.get('items', [])]
    if kind == 'evidence-summary':
        dto.update(pick(value, 'changed blank_amount_rows unchanged_records'))
        dto['categories'] = [pick(row, 'key label records change effect effect_exact') for row in value.get('categories', [])]
        dto['top_changes'] = [pick(row, ROW) for row in value.get('top_changes', [])]
    if kind == 'evidence-breakdown':
        dto['groups'] = [pick(row, 'value no_match records changed_records current proposed change change_exact current_numerator proposed_numerator current_denominator proposed_denominator record_share difference_share') for row in value.get('groups', [])]
    if kind == 'evidence-concentration':
        # The concentration reader owns its bounded data-only contract.
        from .evidence_concentration import public_concentration
        return public_concentration(value)
    if kind == 'entity-records':
        dto.update(pick(value, 'entity entity_column next_offset truncated'))
        dto['items'] = [pick(row, 'record current_included proposed_included') for row in value.get('items', [])]
    return dto


def _not_found():
    return DomainError('NOT_FOUND', 'This shared case is unavailable.', 404)


class _RedactShareAccess(logging.Filter):
    """Uvicorn's default access log otherwise prints capability tokens in URLs."""
    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(re.sub(r'(/(?:api/v1/)?shared-cases/)[A-Za-z0-9_-]+', r'\1[redacted]', item)
                                if isinstance(item, str) else item for item in record.args)
        return True


def install_case_share_routes(app, store, read_handlers):
    logger = logging.getLogger('uvicorn.access')
    if not any(isinstance(f, _RedactShareAccess) for f in logger.filters):
        logger.addFilter(_RedactShareAccess())

    def managed_case(ident, request):
        auth = get_auth_context(request, store)
        item = store.investigation(ident)
        member = verify_object_access(store, item, auth, allowed_roles=('owner', 'analyst'))
        if member['role'] not in ('owner', 'analyst'):
            raise DomainError('FORBIDDEN', 'This workspace role cannot share cases.', 403)
        selected = [p for p in (request.headers.get('x-project-id'), request.query_params.get('project_id')) if p]
        if any(p != item['project_id'] for p in selected):
            raise DomainError('FORBIDDEN', 'The case belongs to another workspace.', 403)
        return item

    def checked_run(case):
        run = store.get_run(case['run_id'])
        if run['state'] not in ('succeeded', 'partial') or not run.get('result'):
            raise _not_found()
        if any(case.get(k) != run.get(k) for k in ('project_id', 'snapshot_id', 'baseline_version_id', 'target_version_id', 'target_snapshot_id')):
            raise _not_found()
        if case.get('context') != run.get('context'):
            raise _not_found()
        pid = case['project_id']
        objects = [store.snapshot(case['snapshot_id']), store.version(case['baseline_version_id']), store.version(case['target_version_id'])]
        if case.get('target_snapshot_id'):
            objects.append(store.snapshot(case['target_snapshot_id']))
        if any(obj.get('project_id', pid) != pid for obj in objects):
            raise _not_found()
        return run

    def resolve(token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', token):
            raise _not_found()
        digest = hashlib.sha256(token.encode()).hexdigest()
        with store.engine.connect() as conn:
            row = conn.execute(select(records.c.data).where(records.c.id == f'share-{digest}', records.c.kind == 'case_share')).scalar_one_or_none()
        if not row or row.get('revoked_at') or row.get('expires_at') and datetime.fromisoformat(row['expires_at']) <= datetime.now(timezone.utc):
            raise _not_found()
        try:
            case = store.investigation(row['case_id'])
            if case['project_id'] != row['project_id'] or case['run_id'] != row['run_id']:
                raise _not_found()
            return case, checked_run(case)
        except DomainError:
            raise _not_found() from None

    def metadata(value):
        return {**pick(value, 'created_at expires_at revoked_at'), 'id': value['share_id']}

    @app.post('/api/v1/investigations/{ident}/shares', status_code=201)
    def create(ident: str, request: Request, body: ShareInput = ShareInput()):
        case = managed_case(ident, request)
        checked_run(case)
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        value = {'share_id': str(uuid4()), 'case_id': ident, 'run_id': case['run_id'],
                 'project_id': case['project_id'], 'created_at': now(), 'revoked_at': None,
                 'expires_at': (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)).isoformat() if body.expires_in_days else None}
        with store.engine.begin() as conn:
            conn.execute(insert(records).values(id=f'share-{digest}', kind='case_share', project_id=case['project_id'], created_at=value['created_at'], data=value))
        return {**metadata(value), 'token': token}

    @app.get('/api/v1/investigations/{ident}/shares')
    def listed(ident: str, request: Request):
        case = managed_case(ident, request)
        with store.engine.connect() as conn:
            values = conn.execute(select(records.c.data).where(records.c.kind == 'case_share', records.c.project_id == case['project_id'], records.c.data['case_id'].as_string() == ident)).scalars()
            return {'items': sorted((metadata(v) for v in values), key=lambda v: v['created_at'], reverse=True)}

    @app.delete('/api/v1/investigations/{ident}/shares/{share_id}')
    def revoke(ident: str, share_id: str, request: Request):
        case = managed_case(ident, request)
        with store.engine.begin() as conn:
            row = conn.execute(select(records).where(records.c.kind == 'case_share', records.c.project_id == case['project_id'], records.c.data['case_id'].as_string() == ident, records.c.data['share_id'].as_string() == share_id).with_for_update()).mappings().first()
            if not row:
                raise _not_found()
            value = {**row['data'], 'revoked_at': row['data'].get('revoked_at') or now()}
            conn.execute(update(records).where(records.c.id == row['id']).values(data=value))
        return metadata(value)

    @app.get('/api/v1/shared-cases/{token}')
    def opened(token: str):
        case, run = resolve(token)
        return case_dto(store, case, run)

    def read(token, kind, **params):
        _, run = resolve(token)
        return read_dto(kind, read_handlers[kind](run, **params))

    @app.get('/api/v1/shared-cases/{token}/evidence')
    def evidence(token: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
                 changed_only: bool = False, q: str = Query('', max_length=256), order: str = Query('file', pattern='^(file|impact)$'),
                 with_context: bool = False, category: str = Query('', pattern='^(|excluded|added|value_changed|removed|entered_filter|left_filter)$')):
        return read(token, 'evidence', offset=offset, limit=limit, changed_only=changed_only, q=q, order=order, with_context=with_context, category=category)

    @app.get('/api/v1/shared-cases/{token}/evidence-summary')
    def summary(token: str):
        return read(token, 'evidence-summary')

    @app.get('/api/v1/shared-cases/{token}/evidence-breakdown')
    def breakdown(token: str, column: str = Query('', max_length=200), top: int = Query(50, ge=1, le=200)):
        return read(token, 'evidence-breakdown', column=column, top=top)

    @app.get('/api/v1/shared-cases/{token}/evidence-group')
    def group(token: str, column: str = Query(..., min_length=1, max_length=200), value: str = Query('null', max_length=2000),
              no_match: bool = False, q: str = Query('', max_length=256), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
              column2: str = Query('', max_length=200), value2: str = Query('null', max_length=2000), no_match2: bool = False, with_context: bool = False):
        return read(token, 'evidence-group', column=column, value=value, no_match=no_match, q=q, offset=offset, limit=limit,
                    column2=column2, value2=value2, no_match2=no_match2, with_context=with_context)

    if 'evidence-concentration' in read_handlers:
        @app.get('/api/v1/shared-cases/{token}/evidence-concentration')
        def concentration(token: str):
            return read(token, 'evidence-concentration')

    if 'entity-records' in read_handlers:
        @app.get('/api/v1/shared-cases/{token}/entity-records')
        def entities(token: str, entity: str = Query(..., max_length=256), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
            return read(token, 'entity-records', entity=entity, offset=offset, limit=limit)
