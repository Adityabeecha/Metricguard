"""Versioned governance expectations. No analytical execution or SQL rewriting."""
from __future__ import annotations

from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from .models import MetricContractExpectations, RelationalMetricContractExpectations
from .relational_governance import (POLICY_V2, SCHEMA_V2, is_relational_version, registered_contract,
                                    relational_checks, validate_proposal as validate_relational_proposal)

POLICY = 'metric-governance-v1'
REVISION = 'metric_contract_revision'
HEAD = 'metric_contract_head'
APPROVAL = 'metric_contract_approval'
CHECK = 'metric_contract_check'
# Each revision is validated with its own pinned policy and schema; nothing is upgraded.
SCHEMAS = {(POLICY, 1): MetricContractExpectations, (POLICY_V2, SCHEMA_V2): RelationalMetricContractExpectations}


def _schema_for(policy, schema_version):
    if type(schema_version) is not int:
        return None
    return SCHEMAS.get((policy, schema_version))


def _store_types():
    # The store imports this mixin; defer table lookup until metadata is ready.
    from .store import DomainError, definitions, fingerprint, metric_definition_bindings, metrics, now, records, snapshots, versions
    return DomainError, definitions, fingerprint, metric_definition_bindings, metrics, now, records, snapshots, versions


def _metric(conn, table, mid, pid, error):
    row = conn.execute(select(table).where(table.c.id == mid, table.c.project_id == pid)).mappings().first()
    if not row:
        label = 'Metric' if table.name == 'metrics' else 'Definition'
        raise error('NOT_FOUND', f'{label} not found in this workspace.', 404)
    return dict(row)


def _binding(conn, table, mid, pid, error):
    row = conn.execute(select(table).where(table.c.metric_id == mid, table.c.project_id == pid,
                                          table.c.status == 'active')).mappings().first()
    if not row or not row['approved_version_id']:
        raise error('METRIC_BASELINE_REQUIRED', 'Approve an implementation version before proposing a metric contract.', 409)
    return dict(row)


def _record(conn, table, ident, kind, pid, mid, error):
    row = conn.execute(select(table.c.data).where(table.c.id == ident, table.c.kind == kind,
                                                table.c.project_id == pid)).scalar_one_or_none()
    if not row or row.get('metric_id') != mid:
        raise error('NOT_FOUND', 'Contract record not found for this metric and workspace.', 404)
    return row


def _seal(revision, fingerprint):
    return fingerprint({k: revision[k] for k in ('policy', 'schema_version', 'project_id', 'metric_id',
                                                'version_number', 'metric_identity', 'approved_definition', 'expectations')})


def _validate_revision(revision, fingerprint, error):
    try:
        model = _schema_for(revision['policy'], revision['schema_version'])
        valid = model is not None and revision['expectations'].get('schema_version') == revision['schema_version']
        valid = valid and revision['contract_hash'] == _seal(revision, fingerprint)
        valid = valid and model.model_validate(revision['expectations']).model_dump() == revision['expectations']
    except (KeyError, TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise error('METRIC_CONTRACT_INVALID', 'Stored metric contract contents do not match their pinned policy and hash.')


def _rows(conn, table, kind, pid, mid):
    return select(table.c.data).where(table.c.kind == kind, table.c.project_id == pid,
                                     table.c.data['metric_id'].as_string() == mid)


def _metadata_checks(revision, metric, version, definition, snapshot, binding):
    expected = revision['expectations']
    source = expected['source_expectations']
    manifest = snapshot.get('manifest') or {}
    dataset = manifest.get('contract') or {}
    actual_mode = manifest.get('mode', 'retail')
    checks = []

    def check(field, wanted, observed, *, unknown=False, note=''):
        status = 'unverified' if unknown or observed is None else 'match' if wanted == observed else 'mismatch'
        checks.append({'field': field, 'expected': wanted, 'observed': observed, 'status': status, 'note': note})

    identity = revision['metric_identity']
    pin = revision['approved_definition']
    check('display_name', identity['display_name'], metric['display_name'])
    check('importance', identity['importance'], metric['importance'])
    check('approved_baseline', pin['version_id'], binding['approved_version_id'])
    check('definition_revision', pin['version_id'], version['id'])
    check('definition_raw_hash', pin['raw_hash'], version.get('raw_hash'))
    check('definition_ir_hash', pin['ir_hash'], version.get('ir_hash'))
    check('definition_support', 'executable', version.get('support_state'))
    check('source_mode', source['mode'], actual_mode)
    version_mode = (version.get('ir') or {}).get('mode')
    version_mode = version_mode if version_mode in ('flat_snapshot', 'relational_snapshot') else 'retail'
    check('definition_mode', source['mode'], version_mode)
    if source['source_label'] is not None:
        check('source_label', source['source_label'], definition['source_label'])
    actual_hash = manifest.get('contract_hash') or snapshot.get('contract_hash')
    if source['contract_hash'] is not None:
        check('source_contract_hash', source['contract_hash'], actual_hash)
    if pin.get('contract_hash') is not None:
        check('definition_dataset_contract', pin['contract_hash'], actual_hash)

    # Use the original flat declaration, never the compatibility currency field.
    unit = (dataset.get('unit_label') or manifest.get('unit_label')) if actual_mode == 'flat_snapshot' else dataset.get('currency') or manifest.get('currency') or snapshot.get('currency')
    if actual_mode == 'retail' and (version.get('ir') or {}).get('aggregate') in ('count', 'count_distinct'):
        unit = 'count'
    unknown_unit = not unit or str(unit).lower() in ('unknown', 'unknown currency', 'unspecified')
    check('unit_label', expected['unit_label'], unit, unknown=unknown_unit,
          note='Declared labels are compared without currency inference or conversion.')
    grain = dataset.get('grain') or manifest.get('grain')
    check('grain', expected['grain'], grain,
          note='A matching declared grain is metadata agreement, not proof of business grain.')

    schema = manifest.get('schema') or snapshot.get('schema') or {}
    for dimension in expected['expected_dimensions']:
        physical = schema.get(dimension['name'])
        logical = {'VARCHAR': 'string', 'TEXT': 'string', 'BOOLEAN': 'boolean',
                   'DATE': 'date', 'TIMESTAMP': 'timestamp'}.get(str(physical).upper())
        if str(physical).upper().startswith('DECIMAL('):
            logical = 'decimal'
        if physical is None:
            checks.append({'field': f"dimension.{dimension['name']}", 'expected': dimension['type'],
                           'observed': None, 'status': 'mismatch', 'note': 'Expected field is absent from the registered schema.'})
        else:
            check(f"dimension.{dimension['name']}", dimension['type'], logical)
    return checks


class MetricContractStoreMixin:
    def list_metric_contracts(self, metric_id, project_id=None, limit=50, offset=0):
        error, _, fingerprint, _, metrics, _, records, _, _ = _store_types()
        pid = project_id or self.project_id
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise error('INVALID_INPUT', 'Contract history requires a limit of 1–100 and a nonnegative offset.')
        with self.engine.connect() as conn:
            _metric(conn, metrics, metric_id, pid, error)
            head = conn.execute(_rows(conn, records, HEAD, pid, metric_id)).scalar_one_or_none()
            approvals = conn.execute(_rows(conn, records, APPROVAL, pid, metric_id).order_by(records.c.created_at.desc()).limit(100)).scalars().all()
            rows = conn.execute(_rows(conn, records, REVISION, pid, metric_id)
                                .order_by(records.c.data['version_number'].as_integer().desc())
                                .limit(limit + 1).offset(offset)).scalars().all()
            active_id = head['active_contract_id'] if head else None
            page_ids = [r['id'] for r in rows[:limit]]
            approved_ids = set(conn.execute(select(records.c.data['contract_revision_id'].as_string()).where(
                records.c.kind == APPROVAL, records.c.project_id == pid,
                records.c.data['metric_id'].as_string() == metric_id,
                records.c.data['contract_revision_id'].as_string().in_(page_ids))).scalars()) if page_ids else set()
            items = []
            for revision in rows[:limit]:
                _validate_revision(revision, fingerprint, error)
                items.append({**revision, 'status': 'active' if revision['id'] == active_id else
                              'superseded' if revision['id'] in approved_ids else 'proposed'})
            active = _record(conn, records, active_id, REVISION, pid, metric_id, error) if active_id else None
            if active:
                _validate_revision(active, fingerprint, error)
            recent_checks = conn.execute(_rows(conn, records, CHECK, pid, metric_id)
                                         .order_by(records.c.created_at.desc()).limit(20)).scalars().all()
        return {'items': items, 'active_contract': active, 'version_token': head['version_token'] if head else None,
                'approvals': approvals, 'recent_checks': recent_checks,
                'next_offset': offset + limit if len(rows) > limit else None}

    def propose_metric_contract(self, metric_id, expectations, expected_version_token,
                                expected_approved_version_id, actor_id, project_id=None):
        error, definitions, fingerprint, bindings, metrics, now, records, _, versions = _store_types()
        pid = project_id or self.project_id
        version_number = expectations.get('schema_version', 1) if isinstance(expectations, dict) else None
        policy = POLICY_V2 if type(version_number) is int and version_number == SCHEMA_V2 else POLICY
        model = RelationalMetricContractExpectations if policy == POLICY_V2 else MetricContractExpectations
        try:
            expectations = model.model_validate(expectations).model_dump()
        except ValidationError as exc:
            raise error('METRIC_CONTRACT_INVALID', str(exc)) from exc
        with self.engine.begin() as conn:
            metric = _metric(conn, metrics, metric_id, pid, error)
            binding = _binding(conn, bindings, metric_id, pid, error)
            if binding['approved_version_id'] != expected_approved_version_id:
                raise error('STALE_METRIC_BASELINE', 'The approved implementation changed. Reload the metric before proposing a contract.', 409)
            version = conn.execute(select(versions).where(versions.c.id == binding['approved_version_id'])).mappings().one()
            if version['definition_id'] != binding['definition_id']:
                raise error('METRIC_CONTRACT_INVALID', 'Approved baseline does not belong to its active definition.')
            definition = _metric(conn, definitions, binding['definition_id'], pid, error)
            head = conn.execute(_rows(conn, records, HEAD, pid, metric_id)).scalar_one_or_none()
            if (head['version_token'] if head else None) != expected_version_token:
                raise error('STALE_METRIC_CONTRACT', 'Contract history changed. Reload it before proposing a revision.', 409)
            relational = is_relational_version(version['data'])
            if policy == POLICY_V2:
                if not relational:
                    raise error('GOVERNANCE_SCHEMA_MISMATCH', 'Relational governance (schema version 2) requires a relational_snapshot_v1 approved implementation.')
                contract = registered_contract(conn, records, version['data'], pid, error)
                validate_relational_proposal(expectations, contract, error)
            elif relational:
                raise error('GOVERNANCE_SCHEMA_MISMATCH', 'Relational implementations require relational governance (schema version 2); metric-governance-v1 cannot describe them.')
            timestamp = now()
            number = head['next_version'] if head else 1
            revision = {'id': f'mcr-{uuid4().hex}', 'project_id': pid, 'metric_id': metric_id,
                        'policy': policy, 'schema_version': expectations['schema_version'], 'version_number': number,
                        'metric_identity': {'display_name': metric['display_name'], 'importance': metric['importance']},
                        'approved_definition': {'definition_id': definition['id'], 'version_id': version['id'],
                                                'raw_hash': version['data'].get('raw_hash'),
                                                'ir_hash': version['data'].get('ir_hash'),
                                                'contract_hash': version['data'].get('contract_hash')},
                        'expectations': expectations, 'created_at': timestamp, 'created_by': actor_id}
            revision['contract_hash'] = _seal(revision, fingerprint)
            next_head = {'id': head['id'] if head else f'mch-{metric_id}', 'metric_id': metric_id,
                         'project_id': pid, 'active_contract_id': head['active_contract_id'] if head else None,
                         'next_version': number + 1, 'version_token': uuid4().hex}
            if head:
                self._update_contract_head(conn, records, head, next_head, pid, error)
            else:
                try:
                    conn.execute(insert(records).values(id=next_head['id'], project_id=pid, kind=HEAD,
                                                        created_at=timestamp, data=next_head))
                except IntegrityError as exc:
                    raise error('STALE_METRIC_CONTRACT', 'Another proposal created contract history. Reload before retrying.', 409) from exc
            conn.execute(insert(records).values(id=revision['id'], project_id=pid, kind=REVISION,
                                                created_at=timestamp, data=revision))
        return {**revision, 'status': 'proposed', 'version_token': next_head['version_token']}

    @staticmethod
    def _update_contract_head(conn, table, head, replacement, pid, error):
        result = conn.execute(update(table).where(table.c.id == head['id'], table.c.project_id == pid,
                                                 table.c.kind == HEAD,
                                                 table.c.data['version_token'].as_string() == head['version_token'])
                              .values(data=replacement))
        if result.rowcount != 1:
            raise error('STALE_METRIC_CONTRACT', 'Contract history changed concurrently. Reload before retrying.', 409)

    def approve_metric_contract(self, metric_id, revision_id, expected_version_token, reason, actor_id, project_id=None):
        error, _, fingerprint, bindings, metrics, now, records, _, _ = _store_types()
        pid = project_id or self.project_id
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise error('INVALID_INPUT', 'Approval requires a nonblank reason of at most 1000 characters.')
        with self.engine.begin() as conn:
            metric = _metric(conn, metrics, metric_id, pid, error)
            binding = _binding(conn, bindings, metric_id, pid, error)
            revision = _record(conn, records, revision_id, REVISION, pid, metric_id, error)
            _validate_revision(revision, fingerprint, error)
            head = conn.execute(_rows(conn, records, HEAD, pid, metric_id)).scalar_one()
            if not expected_version_token or head['version_token'] != expected_version_token:
                raise error('STALE_METRIC_CONTRACT', 'Contract history changed. Reload before approving.', 409)
            if (revision['approved_definition']['version_id'] != binding['approved_version_id'] or
                    revision['metric_identity'] != {'display_name': metric['display_name'], 'importance': metric['importance']}):
                raise error('STALE_METRIC_BASELINE', 'The approved implementation or metric metadata changed. Propose a new contract revision.', 409)
            prior = conn.execute(_rows(conn, records, APPROVAL, pid, metric_id).where(
                records.c.data['contract_revision_id'].as_string() == revision_id)).first()
            if prior:
                raise error('METRIC_CONTRACT_ALREADY_APPROVED', 'Previously approved revisions cannot be approved again.', 409)
            approval = {'id': f'mca-{uuid4().hex}', 'project_id': pid, 'metric_id': metric_id,
                        'contract_revision_id': revision_id, 'contract_hash': revision['contract_hash'],
                        'previous_contract_id': head['active_contract_id'], 'approved_by': actor_id,
                        'reason': reason.strip(), 'created_at': now()}
            new_head = {**head, 'active_contract_id': revision_id, 'version_token': uuid4().hex}
            self._update_contract_head(conn, records, head, new_head, pid, error)
            conn.execute(insert(records).values(id=approval['id'], project_id=pid, kind=APPROVAL,
                                                created_at=approval['created_at'], data=approval))
        return {'approval': approval, 'active_contract': revision, 'version_token': new_head['version_token']}

    def check_metric_contract(self, metric_id, snapshot_id, definition_version_id=None,
                              contract_revision_id=None, actor_id=None, project_id=None):
        error, definitions, fingerprint, bindings, metrics, now, records, snapshots, versions = _store_types()
        pid = project_id or self.project_id
        with self.engine.begin() as conn:
            metric = _metric(conn, metrics, metric_id, pid, error)
            binding = _binding(conn, bindings, metric_id, pid, error)
            head = conn.execute(_rows(conn, records, HEAD, pid, metric_id)).scalar_one_or_none()
            revision_id = contract_revision_id or (head['active_contract_id'] if head else None)
            if not revision_id:
                raise error('METRIC_CONTRACT_REQUIRED', 'Choose a proposed revision or approve a contract before checking it.', 409)
            revision = _record(conn, records, revision_id, REVISION, pid, metric_id, error)
            _validate_revision(revision, fingerprint, error)
            vid = definition_version_id or binding['approved_version_id']
            row = conn.execute(select(versions).where(versions.c.id == vid)).mappings().first()
            if not row:
                raise error('NOT_FOUND', 'Definition version not found.', 404)
            definition = _metric(conn, definitions, row['definition_id'], pid, error)
            linked = conn.execute(select(bindings.c.id).where(bindings.c.metric_id == metric_id,
                bindings.c.project_id == pid, bindings.c.definition_id == definition['id'])).first()
            if not linked:
                raise error('INVALID_BINDING', 'The definition version is not bound to this metric.')
            snap = conn.execute(select(snapshots.c.data).where(snapshots.c.id == snapshot_id,
                                                              snapshots.c.project_id == pid)).scalar_one_or_none()
            if not snap:
                raise error('NOT_FOUND', 'Snapshot not found in this workspace.', 404)
            version = {**row['data'], 'id': row['id']}
            if revision['policy'] == POLICY_V2:
                report = self._relational_check_report(conn, records, revision, metric, version, definition, snap,
                                                       binding, head, snapshot_id, error, now, actor_id, pid)
            else:
                checks = _metadata_checks(revision, metric, version, definition, snap, binding)
                status = 'mismatch' if any(c['status'] == 'mismatch' for c in checks) else 'inconclusive' if any(c['status'] == 'unverified' for c in checks) else 'metadata_match'
                report = {'id': f'mcc-{uuid4().hex}', 'policy': POLICY, 'project_id': pid, 'metric_id': metric_id,
                          'contract_revision_id': revision_id, 'contract_hash': revision['contract_hash'],
                          'contract_status': 'active' if head and head['active_contract_id'] == revision_id else 'historical_or_proposed',
                          'snapshot_id': snapshot_id, 'snapshot_manifest_hash': snap.get('manifest_hash'),
                          'definition_version_id': vid, 'definition_raw_hash': version.get('raw_hash'),
                          'definition_ir_hash': version.get('ir_hash'), 'status': status, 'checks': checks,
                          'declarations': {'owner_label': revision['expectations']['owner_label'],
                                           'meaning': revision['expectations']['meaning'],
                                           'grain': revision['expectations']['grain'],
                                           'materiality_policy': revision['expectations']['materiality_policy']},
                          'limitations': ['Metadata agreement does not prove business meaning, grain or source authenticity.',
                                          'Materiality expectations do not override trusted CI policies or assess numeric drift.',
                                          'No calculation was executed or historical evidence reinterpreted.'],
                          'created_at': now(), 'created_by': actor_id}
            report['report_hash'] = fingerprint(report)
            conn.execute(insert(records).values(id=report['id'], project_id=pid, kind=CHECK,
                                                created_at=report['created_at'], data=report))
        return report

    def get_metric_contract_check(self, metric_id, check_id, project_id=None):
        error, _, fingerprint, _, metrics, _, records, _, _ = _store_types()
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            _metric(conn, metrics, metric_id, pid, error)
            report = _record(conn, records, check_id, CHECK, pid, metric_id, error)
            if report.get('policy') not in (POLICY, POLICY_V2) or report.get('report_hash') != fingerprint(
                    {k: v for k, v in report.items() if k != 'report_hash'}):
                raise error('METRIC_CONTRACT_INVALID', 'Stored contract check contents do not match their pinned hash.')
            if report['policy'] == POLICY_V2 and report.get('schema_version') != SCHEMA_V2:
                raise error('METRIC_CONTRACT_INVALID', 'Stored contract check has an unsupported policy version.')
            revision = _record(conn, records, report.get('contract_revision_id'), REVISION, pid, metric_id, error)
            _validate_revision(revision, fingerprint, error)
            if revision['contract_hash'] != report.get('contract_hash') or revision['policy'] != report['policy']:
                raise error('METRIC_CONTRACT_INVALID', 'Stored contract check is not bound to its original revision.')
            if report['policy'] == POLICY_V2 and (report.get('project_id') != pid or report.get('metric_id') != metric_id
                                                  or report.get('contract_version_number') != revision['version_number']):
                raise error('METRIC_CONTRACT_INVALID', 'Stored contract check is not bound to its original revision.')
        return report

    @staticmethod
    def _relational_check_report(conn, records, revision, metric, version, definition, snap, binding, head,
                                 snapshot_id, error, now, actor_id, pid):
        """Assemble an unsealed metric-governance-v2 report; the caller seals and persists it once."""
        contract = registered_contract(conn, records, version, pid, error) if is_relational_version(version) else None
        checks, observed, assurance = relational_checks(revision, metric, version, definition, snap, binding,
                                                        contract, error)
        status = ('mismatch' if any(c['status'] == 'mismatch' for c in checks) else
                  'inconclusive' if any(c['status'] == 'unverified' for c in checks) else 'metadata_match')
        expected = revision['expectations']
        manifest = snap.get('manifest') or {}
        return {'id': f'mcc-{uuid4().hex}', 'policy': POLICY_V2, 'schema_version': SCHEMA_V2,
                'project_id': pid, 'metric_id': revision['metric_id'],
                'contract_revision_id': revision['id'], 'contract_version_number': revision['version_number'],
                'contract_hash': revision['contract_hash'],
                'contract_status': 'active' if head and head['active_contract_id'] == revision['id'] else 'historical_or_proposed',
                'definition_id': definition['id'], 'definition_version_id': version['id'],
                'definition_version_number': version.get('version_number'),
                'definition_raw_hash': version.get('raw_hash'), 'definition_ir_hash': version.get('ir_hash'),
                'definition_contract_id': version.get('contract_id'),
                'definition_contract_hash': version.get('contract_hash'),
                'definition_execution_policy': version.get('execution_policy'),
                'snapshot_id': snapshot_id, 'snapshot_manifest_hash': snap.get('manifest_hash'),
                'snapshot_contract_hash': observed['snapshot_contract_hash'],
                'snapshot_execution_policy': snap.get('execution_policy') or manifest.get('execution_policy'),
                'approved_baseline_version_id': binding['approved_version_id'],
                'status': status, 'checks': checks, 'observed': observed, 'assurance': assurance,
                'declarations': {'owner_label': expected['owner_label'], 'meaning': expected['meaning'],
                                 'unit_label': expected['unit_label'],
                                 'fact_grain': expected['fact']['grain'],
                                 'dimension_grain': expected['dimension']['grain'],
                                 'materiality_policy': expected['materiality_policy']},
                'limitations': ['Declared owner, meaning, unit and grain are not proven by metadata agreement.',
                                'Join proof was observed on the frozen snapshot only; source authenticity is not independently verified.',
                                'Unknown currency remains unverified; no currency, loss or meaning is inferred.',
                                'Materiality metadata does not override trusted CI policies or assess numeric drift.',
                                'No SQL was executed, no data recollected and no investigation recomputed.'],
                'created_at': now(), 'created_by': actor_id}
