"""Transactional application records; analytical SQL never executes here."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4

from sqlalchemy import (JSON, Boolean, Column, Float, ForeignKey, Integer, MetaData,
                        String, Table, UniqueConstraint, and_, create_engine, delete, event,
                        insert, or_, select, update, func)
from sqlalchemy.exc import IntegrityError

from .metric_contracts import MetricContractStoreMixin
from .relational_store import RelationalStoreMixin
from services import durable


class DomainError(Exception):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


metadata = MetaData()
projects = Table('projects', metadata, Column('id', String, primary_key=True), Column('name', String, nullable=False))
definitions = Table('definitions', metadata,
    Column('id', String, primary_key=True), Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('name', String, nullable=False), Column('source_label', String, nullable=False),
    Column('owner_label', String, nullable=False), Column('created_at', String, nullable=False))
versions = Table('definition_versions', metadata,
    Column('id', String, primary_key=True), Column('definition_id', ForeignKey('definitions.id'), nullable=False),
    Column('version_number', Integer, nullable=False), Column('data', JSON, nullable=False),
    UniqueConstraint('definition_id', 'version_number'))
snapshots = Table('snapshots', metadata,
    Column('id', String, primary_key=True), Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('manifest_hash', String, nullable=False), Column('data', JSON, nullable=False),
    UniqueConstraint('project_id', 'manifest_hash'))
snapshot_imports = Table('snapshot_imports', metadata,
    Column('id', String, primary_key=True), Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('state', String, nullable=False), Column('attempt', Integer, nullable=False, default=0),
    Column('lease_owner', String), Column('lease_expires_at', Float),
    Column('created_at', String, nullable=False), Column('completed_at', String),
    Column('source_path', String, nullable=False), Column('data', JSON, nullable=False),
    Column('snapshot_id', ForeignKey('snapshots.id')), Column('error', JSON))
runs = Table('comparison_runs', metadata,
    Column('id', String, primary_key=True), Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('baseline_version_id', ForeignKey('definition_versions.id'), nullable=False),
    Column('target_version_id', ForeignKey('definition_versions.id'), nullable=False),
    Column('snapshot_id', ForeignKey('snapshots.id'), nullable=False),
    Column('idempotency_key', String, unique=True, nullable=False), Column('request_hash', String, nullable=False),
    Column('state', String, nullable=False), Column('execution_mode', String, nullable=False),
    Column('attempt', Integer, nullable=False, default=0), Column('lease_owner', String),
    Column('lease_expires_at', Float), Column('heartbeat_at', Float),
    Column('cancel_requested', Boolean, nullable=False, default=False),
    Column('created_at', String, nullable=False), Column('started_at', String), Column('completed_at', String),
    Column('request', JSON, nullable=False), Column('result', JSON), Column('error', JSON))
records = Table('records', metadata,
    Column('id', String, primary_key=True), Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('kind', String, nullable=False), Column('created_at', String, nullable=False), Column('data', JSON, nullable=False))
worker_status = Table('worker_status', metadata,
    Column('id', String, primary_key=True), Column('last_seen', Float, nullable=False))

users = Table('users', metadata,
    Column('id', String, primary_key=True),
    Column('email', String, unique=True, nullable=False),
    Column('name', String, nullable=False),
    Column('api_key_hash', String, unique=True, nullable=False),
    Column('is_active', Boolean, nullable=False, default=True),
    Column('created_at', String, nullable=False))

# Browser sign-in sessions. Only a SHA-256 of the random token is stored; the token
# lives in an HttpOnly cookie. Sessions expire and end when the user is deactivated.
auth_sessions = Table('auth_sessions', metadata,
    Column('token_hash', String, primary_key=True),
    Column('user_id', ForeignKey('users.id'), nullable=False),
    Column('created_at', Float, nullable=False),
    Column('expires_at', Float, nullable=False))

oauth_identities = Table('oauth_identities', metadata,
    Column('provider', String, primary_key=True), Column('subject', String, primary_key=True),
    Column('user_id', ForeignKey('users.id'), nullable=False))
oauth_states = Table('oauth_states', metadata,
    Column('nonce_hash', String, primary_key=True), Column('expires_at', Float, nullable=False))
# Beta waitlist: an email asking for an invitation. Requesting never grants access.
access_requests = Table('access_requests', metadata,
    Column('email', String, primary_key=True), Column('created_at', String, nullable=False),
    Column('last_requested_at', String, nullable=False), Column('request_count', Integer, nullable=False))

project_members = Table('project_members', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('user_id', ForeignKey('users.id'), nullable=False),
    Column('role', String, nullable=False),
    Column('created_at', String, nullable=False),
    Column('revoked_at', String),
    UniqueConstraint('project_id', 'user_id'))

review_requests = Table('review_requests', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('requester_id', ForeignKey('users.id'), nullable=False),
    Column('status', String, nullable=False),
    Column('data', JSON, nullable=False),
    Column('created_at', String, nullable=False),
    Column('updated_at', String, nullable=False))

decisions = Table('decisions', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('decider_id', ForeignKey('users.id'), nullable=False),
    Column('data', JSON, nullable=False),
    Column('created_at', String, nullable=False))

monitoring_policies = Table('monitoring_policies', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('name', String, nullable=False),
    Column('data', JSON, nullable=False),
    Column('created_at', String, nullable=False))

alerts = Table('alerts', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('policy_id', ForeignKey('monitoring_policies.id'), nullable=False),
    Column('event_hash', String, nullable=False),
    Column('state', String, nullable=False),
    Column('data', JSON, nullable=False),
    Column('created_at', String, nullable=False),
    Column('acknowledged_at', String),
    Column('acknowledged_by', String))

audit_logs = Table('audit_logs', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('actor_id', String, nullable=False),
    Column('action', String, nullable=False),
    Column('target_type', String, nullable=False),
    Column('target_id', String, nullable=False),
    Column('data', JSON, nullable=False),
    Column('created_at', String, nullable=False))

dbt_import_revisions = Table('dbt_import_revisions', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('manifest_hash', String, nullable=False),
    Column('source_revision', JSON, nullable=False),
    Column('invocation_id', String),
    Column('dbt_version', String),
    Column('generated_at', String),
    Column('created_at', String, nullable=False),
    Column('data', JSON, nullable=False))

dbt_resource_revisions = Table('dbt_resource_revisions', metadata,
    Column('id', String, primary_key=True),
    Column('import_revision_id', ForeignKey('dbt_import_revisions.id'), nullable=False),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('unique_id', String, nullable=False),
    Column('resource_type', String, nullable=False),
    Column('name', String, nullable=False),
    Column('definition_id', ForeignKey('definitions.id')),
    Column('definition_version_id', ForeignKey('definition_versions.id')),
    Column('compiled_sql_present', Boolean, nullable=False),
    Column('support_state', String, nullable=False),
    Column('diagnostics', JSON, nullable=False),
    Column('observed_lineage', JSON, nullable=False),
    Column('run_results_status', JSON),
    Column('data', JSON, nullable=False))

postgres_extractions = Table('postgres_extractions', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('state', String, nullable=False),
    Column('attempt', Integer, nullable=False, default=1),
    Column('cutoff_value', String, nullable=False),
    Column('cutoff_column', String, nullable=False),
    Column('source_sanitized', String, nullable=False),
    Column('schema_name', String, nullable=False),
    Column('table_name', String, nullable=False),
    Column('contract_id', String, nullable=False),
    Column('mapping_id', String),
    Column('snapshot_id', ForeignKey('snapshots.id')),
    Column('row_count', Integer),
    Column('manifest_hash', String),
    Column('error', JSON),
    Column('created_at', String, nullable=False),
    Column('started_at', String),
    Column('completed_at', String),
    Column('data', JSON, nullable=False))

metrics = Table('metrics', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('display_name', String, nullable=False),
    Column('importance', String, nullable=False, default='unclassified'),
    Column('created_at', String, nullable=False),
    Column('data', JSON, nullable=False))

external_metric_references = Table('external_metric_references', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('metric_id', ForeignKey('metrics.id'), nullable=False),
    Column('provider', String, nullable=False),
    Column('external_id', String, nullable=False),
    Column('created_at', String, nullable=False),
    Column('data', JSON, nullable=False),
    UniqueConstraint('project_id', 'provider', 'external_id', name='uq_ext_ref_proj_prov_extid'))

metric_definition_bindings = Table('metric_definition_bindings', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('metric_id', ForeignKey('metrics.id'), nullable=False),
    Column('definition_id', ForeignKey('definitions.id'), nullable=False),
    Column('status', String, nullable=False),
    Column('approved_version_id', ForeignKey('definition_versions.id'), nullable=True),
    Column('activated_at', String, nullable=True),
    Column('retired_at', String, nullable=True),
    Column('created_at', String, nullable=False),
    Column('data', JSON, nullable=False))

metric_state_transitions = Table('metric_state_transitions', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('metric_id', ForeignKey('metrics.id'), nullable=False),
    Column('transition_kind', String, nullable=False),
    Column('definition_id', ForeignKey('definitions.id'), nullable=False),
    Column('approved_version_id', ForeignKey('definition_versions.id'), nullable=True),
    Column('previous_definition_id', ForeignKey('definitions.id'), nullable=True),
    Column('previous_approved_version_id', ForeignKey('definition_versions.id'), nullable=True),
    Column('comparison_run_id', ForeignKey('comparison_runs.id'), nullable=True),
    Column('review_decision_id', ForeignKey('decisions.id'), nullable=True),
    Column('actor_id', String, nullable=True),
    Column('metadata', JSON, nullable=False),
    Column('created_at', String, nullable=False))

metric_impact_reports = Table('metric_impact_reports', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('baseline_revision_id', ForeignKey('dbt_import_revisions.id'), nullable=False),
    Column('target_revision_id', ForeignKey('dbt_import_revisions.id'), nullable=False),
    Column('snapshot_id', ForeignKey('snapshots.id'), nullable=False),
    Column('request_hash', String, nullable=False),
    Column('created_at', String, nullable=False),
    Column('data', JSON, nullable=False))

metric_diff_reports = Table('metric_diff_reports', metadata,
    Column('id', String, primary_key=True),
    Column('project_id', ForeignKey('projects.id'), nullable=False),
    Column('baseline_revision_id', ForeignKey('dbt_import_revisions.id'), nullable=False),
    Column('target_revision_id', ForeignKey('dbt_import_revisions.id'), nullable=False),
    Column('snapshot_id', ForeignKey('snapshots.id'), nullable=False),
    Column('request_hash', String, nullable=False),
    Column('created_at', String, nullable=False),
    Column('data', JSON, nullable=False))



def _with_flat_policy(snapshot):
    """Expose declared metadata on older snapshots whose stored summary omitted it."""
    manifest = snapshot.get('manifest') or {}
    if not snapshot.get('unit_label') and manifest.get('unit_label'):
        snapshot = {**snapshot, 'unit_label': manifest['unit_label']}
    if (not snapshot.get('execution_policy') and manifest.get('mode') == 'flat_snapshot' and
            manifest.get('execution_policy') == 'flat_snapshot_v1'):
        return {**snapshot, 'execution_policy': 'flat_snapshot_v1'}
    return snapshot


class Store(MetricContractStoreMixin, RelationalStoreMixin):
    project_id = 'local-retail'

    def __init__(self, database_url):
        if database_url.startswith('sqlite:///'):
            Path(database_url.removeprefix('sqlite:///')).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(database_url, connect_args={'check_same_thread': False, 'timeout': 15} if database_url.startswith('sqlite') else {})
        if database_url.startswith('sqlite'):
            @event.listens_for(self.engine, 'connect')
            def sqlite_pragmas(connection, _):
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('PRAGMA journal_mode=WAL')
        metadata.create_all(self.engine)
        if database_url.startswith('sqlite'):
            with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                res = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name='snapshots'").fetchone()
                if res and 'UNIQUE (manifest_hash)' in res[0]:
                    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                    conn.exec_driver_sql("""
                        CREATE TABLE snapshots_migrated (
                            id VARCHAR NOT NULL,
                            project_id VARCHAR NOT NULL,
                            manifest_hash VARCHAR NOT NULL,
                            data JSON NOT NULL,
                            PRIMARY KEY (id),
                            FOREIGN KEY(project_id) REFERENCES projects (id),
                            UNIQUE (project_id, manifest_hash)
                        )
                    """)
                    conn.exec_driver_sql("INSERT INTO snapshots_migrated SELECT id, project_id, manifest_hash, data FROM snapshots")
                    conn.exec_driver_sql("DROP TABLE snapshots")
                    conn.exec_driver_sql("ALTER TABLE snapshots_migrated RENAME TO snapshots")
                    conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        with self.engine.begin() as conn:
            if not conn.execute(select(projects).where(projects.c.id == self.project_id)).first():
                conn.execute(insert(projects).values(id=self.project_id, name='Retail analytics'))
            if not conn.execute(select(users).where(users.c.id == 'local-analyst')).first():
                local_key_hash = hashlib.sha256('local-insecure-key'.encode()).hexdigest()
                conn.execute(insert(users).values(id='local-analyst', email='analyst@local.internal',
                                                   name='Local analyst', api_key_hash=local_key_hash,
                                                   is_active=True, created_at=now()))
            if not conn.execute(select(project_members).where(project_members.c.project_id == self.project_id,
                                                              project_members.c.user_id == 'local-analyst')).first():
                conn.execute(insert(project_members).values(id=str(uuid4()), project_id=self.project_id,
                                                             user_id='local-analyst', role='owner',
                                                             created_at=now(), revoked_at=None))

    def _row(self, table, ident):
        with self.engine.connect() as conn:
            found = conn.execute(select(table).where(table.c.id == ident)).mappings().first()
        if found is None:
            raise DomainError('NOT_FOUND', 'The requested record is not available.', 404)
        return dict(found)

    def definition(self, ident):
        row = self._row(definitions, ident)
        with self.engine.connect() as conn:
            row['versions'] = [r['data'] for r in conn.execute(select(versions).where(versions.c.definition_id == ident).order_by(versions.c.version_number)).mappings()]
        return row

    def version(self, ident):
        row = self._row(versions, ident)
        defn = self._row(definitions, row['definition_id'])
        return {**row['data'], 'project_id': defn['project_id']}

    def snapshot(self, ident):
        row = self._row(snapshots, ident)
        return _with_flat_policy({**row['data'], 'project_id': row['project_id']})

    def _record_of_kind(self, ident, kind):
        row = self._row(records, ident)
        if row['kind'] != kind:
            raise DomainError('NOT_FOUND', 'The requested record is not available.', 404)
        return {**row['data'], 'project_id': row['project_id']}

    def dataset_contract(self, ident):
        return self._record_of_kind(ident, 'dataset_contract')

    def flat_contract(self, ident):
        return self._record_of_kind(ident, 'flat_contract')

    def record_flat_contract(self, payload, project_id=None):
        from packages.metricguard_core.flat import validate_contract

        pid = project_id or self.project_id
        contract = validate_contract(payload)
        with self.engine.begin() as conn:
            prior = [row['data'] for row in conn.execute(select(records).where(
                records.c.kind == 'flat_contract', records.c.project_id == pid)).mappings()]
            for item in prior:
                if item['contract_hash'] == contract['contract_hash']:
                    return item
            if len(prior) >= 100:
                raise DomainError('IMPORT_LIMIT', 'This workspace supports at most 100 flat contracts.')
            item = {**contract, 'id': str(uuid4()), 'created_at': now(), 'project_id': pid,
                    'version_number': 1 + sum(old['name'] == contract['name'] for old in prior)}
            conn.execute(insert(records).values(id=item['id'], project_id=pid,
                kind='flat_contract', created_at=item['created_at'], data=item))
        return item

    def _definition_on(self, conn, ident):
        row = dict(conn.execute(select(definitions).where(definitions.c.id == ident)).mappings().one())
        row['versions'] = [r['data'] for r in conn.execute(select(versions).where(
            versions.c.definition_id == ident).order_by(versions.c.version_number)).mappings()]
        return row

    def _same_snapshot_definition(self, conn, pid, contract_id, execution_policy, raw_sql):
        """The definition that already holds this exact SQL for this snapshot contract, if any.

        Guided comparisons register a calculation per run; repeating a comparison must reuse it, not
        add another definition. Locks the project row so concurrent identical requests serialize."""
        conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
        raw_hash = hashlib.sha256(raw_sql.encode()).hexdigest()
        rows = conn.execute(select(versions.c.definition_id, versions.c.data).join(
            definitions, definitions.c.id == versions.c.definition_id).where(definitions.c.project_id == pid)
            .order_by(definitions.c.created_at)).mappings()
        for row in rows:
            data = row['data']
            if (data.get('contract_id') == contract_id and data.get('execution_policy') == execution_policy
                    and data.get('raw_hash') == raw_hash and data.get('support_state') == 'executable'):
                return row['definition_id']
        return None

    def record_flat_definition(self, name, raw_sql, contract_id, project_id=None):
        from packages.metricguard_core.flat import parse_flat

        pid = project_id or self.project_id
        contract_record = self.flat_contract(contract_id)
        contract = {key: value for key, value in contract_record.items()
                    if key not in {'id', 'created_at', 'version_number', 'project_id'}}
        ir = parse_flat(raw_sql, contract)
        with self.engine.begin() as conn:
            existing = self._same_snapshot_definition(conn, pid, contract_id, 'flat_snapshot_v1', raw_sql)
            if existing:
                return self._definition_on(conn, existing)
            if len(conn.execute(select(definitions.c.id).where(definitions.c.project_id == pid)).all()) >= 100:
                raise DomainError('IMPORT_LIMIT', 'This local project supports at most 100 definitions.')
            definition_id, version_id = str(uuid4()), str(uuid4())
            conn.execute(insert(definitions).values(id=definition_id, project_id=pid,
                name=name, source_label='Flat snapshot SQL', owner_label='Local analyst', created_at=now()))
            version = dict(id=version_id, definition_id=definition_id, version_number=1,
                raw_sql=raw_sql, bound_sql=None, mapping_id=None,
                contract_id=contract_id, contract_hash=contract['contract_hash'], mapping_hash=None,
                execution_policy='flat_snapshot_v1', support_state='executable', support_reason=None,
                raw_hash=hashlib.sha256(raw_sql.encode()).hexdigest(), ir_hash=ir['ir_hash'], ir=ir,
                observed_at=now(), source_changed_at=None, effective_from=None, change_kind='initial',
                project_id=pid)
            conn.execute(insert(versions).values(id=version_id, definition_id=definition_id,
                version_number=1, data=version))
        return self.definition(definition_id)

    def column_mapping(self, ident):
        return self._record_of_kind(ident, 'column_mapping')

    def investigation(self, ident):
        return self._record_of_kind(ident, 'investigation')

    def record_investigation(self, payload, project_id=None):
        pid = project_id or self.project_id
        run = self.get_run(payload['run_id'])
        if run['state'] not in {'succeeded', 'partial'}:
            raise DomainError('RESULT_UNAVAILABLE', 'A completed run is required before saving an investigation.')
        if payload.get('snapshot_id') and payload['snapshot_id'] != run['snapshot_id']:
            raise DomainError('NOT_COMPARABLE', 'Investigation snapshot differs from the selected run.')
        snapshot = self.snapshot(run['snapshot_id'])
        preparation = snapshot.get('manifest', {}).get('flat_preparation')
        item = {'title': payload['title'], 'decision': payload['decision'],
                'actor_label': payload['actor_label'], 'run_id': run['id'],
                'snapshot_id': run['snapshot_id'],
                'preparation_manifest_hash': preparation['manifest_hash'] if preparation else None,
                'baseline_version_id': run['baseline_version_id'],
                'target_version_id': run['target_version_id'],
                'context': run['context'], 'relationship': run['relationship'],
                'review_status': 'recorded_locally', 'project_id': pid,
                **({'target_snapshot_id': run['target_snapshot_id']} if run.get('target_snapshot_id') else {})}
        if run.get('execution_policy') == 'relational_snapshot_v1':
            provenance = run['result']['provenance']
            item.update(execution_policy='relational_snapshot_v1', contract_hash=snapshot['contract_hash'],
                        snapshot_manifest_hash=provenance['snapshot_manifest_hash'],
                        baseline_ir_hash=provenance['baseline_ir_hash'], target_ir_hash=provenance['target_ir_hash'])
        return self.record('investigation', item, project_id=pid)

    def record_dataset_contract(self, payload, project_id=None):
        from packages.metricguard_core.v2_contract import validate_contract

        pid = project_id or self.project_id
        contract = validate_contract(payload)
        with self.engine.begin() as conn:
            conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
            prior = [row['data'] for row in conn.execute(select(records).where(
                records.c.kind == 'dataset_contract', records.c.project_id == pid)).mappings()]
            for item in prior:
                if item['contract_hash'] == contract['contract_hash']:
                    return item
            if len(prior) >= 100:
                raise DomainError('IMPORT_LIMIT', 'This workspace supports at most 100 dataset contracts.')
            item = {**contract, 'id': str(uuid4()), 'created_at': now(), 'project_id': pid,
                    'version_number': 1 + sum(old['name'] == contract['name'] for old in prior)}
            conn.execute(insert(records).values(id=item['id'], project_id=pid,
                kind='dataset_contract', created_at=item['created_at'], data=item))
        return item

    def record_column_mapping(self, contract_id, payload, project_id=None):
        from packages.metricguard_core.v2_contract import validate_mapping

        pid = project_id or self.project_id
        contract_record = self.dataset_contract(contract_id)
        contract = {key: value for key, value in contract_record.items()
                    if key not in {'id', 'created_at', 'version_number', 'project_id'}}
        mapping = validate_mapping(payload, contract)
        with self.engine.begin() as conn:
            conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
            prior = [row['data'] for row in conn.execute(select(records).where(
                records.c.kind == 'column_mapping', records.c.project_id == pid)).mappings()]
            for item in prior:
                if item['mapping_hash'] == mapping['mapping_hash']:
                    return item
            if len(prior) >= 100:
                raise DomainError('IMPORT_LIMIT', 'This workspace supports at most 100 column mappings.')
            item = {**mapping, 'contract_id': contract_id, 'id': str(uuid4()),
                    'created_at': now(), 'project_id': pid, 'version_number': 1 + sum(
                        old['contract_id'] == contract_id for old in prior)}
            conn.execute(insert(records).values(id=item['id'], project_id=pid,
                kind='column_mapping', created_at=item['created_at'], data=item))
        return item

    def _record_definition_conn(self, conn, name, source_label, owner_label, raw_sql, time_column=None,
                                 definition_id=None, source_changed_at=None, effective_from=None,
                                 mapping_id=None, project_id=None):
        from packages.metricguard_core import CoreError, parse_definition
        from packages.metricguard_core.v2_binding import bind_sql
        pid = project_id or self.project_id
        if not raw_sql.strip() or len(raw_sql) > 50000:
            raise DomainError('INVALID_SQL_LENGTH', 'SQL must contain between 1 and 50,000 characters.')
        mapping = self.column_mapping(mapping_id) if mapping_id else None
        bound_sql, bound_time = raw_sql, time_column
        if mapping:
            bound_sql = bind_sql(raw_sql, mapping)
            if time_column:
                inverse = {source.lower(): role for role, source in mapping['columns'].items()}
                if time_column.lower() not in inverse:
                    raise DomainError('UNSUPPORTED_SQL', 'Business date is not in the approved mapping.')
                bound_time = inverse[time_column.lower()]
        try:
            ir = parse_definition(bound_sql, time_column=bound_time)
            support_state, support_reason = ('executable', None) if ir['time_column'] else (
                'needs_context', 'Confirm an order_date or payment_date business date column before comparison.')
        except CoreError as error:
            if mapping:
                raise DomainError('UNSUPPORTED_SQL', error.message)
            ir = None
            support_state = 'needs_context' if error.code.lower() == 'context_required' else 'unsupported'
            support_reason = error.message
        raw_hash = hashlib.sha256(raw_sql.encode()).hexdigest()
        # Lock the project to serialize first imports and version numbering on PostgreSQL.
        conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
        if definition_id:
            definition = conn.execute(select(definitions).where(definitions.c.id == definition_id)).mappings().first()
            if not definition:
                raise DomainError('NOT_FOUND', 'Definition not found.', 404)
            previous = conn.execute(select(versions).where(versions.c.definition_id == definition_id).order_by(versions.c.version_number.desc())).mappings().first()
            previous = previous['data'] if previous else None
            if previous and previous.get('mapping_id') != mapping_id:
                raise DomainError('MAPPING_CHANGED', 'A mapping change requires a new definition identity.')
            previous_source_time = (previous.get('source_time_column') if previous.get('mapping_id')
                                    else previous['time_column']) if previous else None
            if previous and previous['raw_hash'] == raw_hash and previous_source_time == time_column and \
                    previous.get('source_changed_at') == source_changed_at and previous.get('effective_from') == effective_from:
                defn_row = dict(definition)
                defn_row['versions'] = [r['data'] for r in conn.execute(select(versions).where(versions.c.definition_id == definition_id).order_by(versions.c.version_number)).mappings()]
                return defn_row
        else:
            if len(conn.execute(select(definitions.c.id).where(definitions.c.project_id == pid)).all()) >= 100:
                raise DomainError('IMPORT_LIMIT', 'This local project supports at most 100 definitions.')
            definition_id, previous = str(uuid4()), None
            conn.execute(insert(definitions).values(id=definition_id, project_id=pid,
                name=name, source_label=source_label, owner_label=owner_label, created_at=now()))
        ir_hash = ir.get('ir_hash', fingerprint(ir)) if ir else None
        change = 'initial' if not previous else ('unknown' if not ir or not previous['ir'] else (
            'metadata_only' if previous['ir_hash'] == ir_hash and previous['raw_hash'] == raw_hash
            else 'cosmetic' if previous['ir_hash'] == ir_hash else 'structural'))
        # Check if Level-1 normalization was applied
        from packages.metricguard_core.normalization import normalize_level1_cte
        norm_applied, norm_sql, _, norm_meta = normalize_level1_cte(raw_sql)
        normalized_sql = norm_sql if norm_applied else None
        normalization_info = norm_meta if norm_applied else None

        data = dict(id=str(uuid4()), definition_id=definition_id, version_number=previous['version_number'] + 1 if previous else 1,
            raw_sql=raw_sql, bound_sql=bound_sql if mapping else None,
            normalized_sql=normalized_sql, normalization=normalization_info,
            mapping_id=mapping_id, contract_hash=mapping['contract_hash'] if mapping else None,
            mapping_hash=mapping['mapping_hash'] if mapping else None,
            source_time_column=time_column if mapping else None,
            dialect='duckdb', time_column=bound_time, support_state=support_state, support_reason=support_reason,
            raw_hash=raw_hash, ir_hash=ir_hash, ir=ir, observed_at=now(),
            source_changed_at=source_changed_at, effective_from=effective_from, change_kind=change,
            project_id=pid)
        conn.execute(insert(versions).values(id=data['id'], definition_id=definition_id, version_number=data['version_number'], data=data))
        defn_row = dict(conn.execute(select(definitions).where(definitions.c.id == definition_id)).mappings().first())
        defn_row['versions'] = [r['data'] for r in conn.execute(select(versions).where(versions.c.definition_id == definition_id).order_by(versions.c.version_number)).mappings()]
        return defn_row

    def record_definition(self, name, source_label, owner_label, raw_sql, time_column=None,
                          definition_id=None, source_changed_at=None, effective_from=None,
                          mapping_id=None, project_id=None):
        with self.engine.begin() as conn:
            return self._record_definition_conn(conn, name, source_label, owner_label, raw_sql,
                                               time_column=time_column, definition_id=definition_id,
                                               source_changed_at=source_changed_at, effective_from=effective_from,
                                               mapping_id=mapping_id, project_id=project_id)

    def record_snapshot(self, name, manifest, artifact_path, project_id=None):
        pid = project_id or self.project_id
        from packages.metricguard_core.free_tier import check_rows
        check_rows(manifest.get('row_count', 0))
        m_hash = manifest.get('manifest_hash') or manifest.get('database_hash') or fingerprint(manifest)
        with self.engine.begin() as conn:
            existing = conn.execute(select(snapshots).where(snapshots.c.manifest_hash == m_hash, snapshots.c.project_id == pid)).mappings().first()
            if existing:
                return existing['data']
            unit_label = manifest.get('unit_label') or manifest.get('unit')
            cur = manifest.get('currency')
            if not cur and unit_label and str(unit_label).lower() in ('inr', 'usd', 'eur', 'gbp'):
                cur = str(unit_label).upper()
            elif not cur and unit_label and str(unit_label).lower() in ('count', 'units', 'records', 'items'):
                cur = None
            elif not cur and manifest.get('grain') == 'one_row_per_order_id' and manifest.get('scope') == 'synthetic_retail_all_orders':
                cur = 'INR'
            data = dict(id=manifest.get('snapshot_id') or str(uuid4()), name=name, row_count=manifest.get('row_count', 0), manifest_hash=m_hash,
                source_hash=manifest.get('source_hash'), currency=cur,
                contract_hash=manifest.get('contract_hash'), mapping_hash=manifest.get('mapping_hash'),
                timezone=manifest.get('timezone', manifest.get('business_timezone', 'Asia/Kolkata')),
                scope=manifest.get('scope', 'synthetic_retail_all_orders'), grain=manifest.get('grain', 'one_row_per_order_id'),
                cutoff_at=manifest.get('cutoff_at', manifest.get('extraction_cutoff', '2026-09-02')),
                schema=manifest.get('schema', {}), status='validated', created_at=now(), artifact_path=str(artifact_path), manifest=manifest,
                project_id=pid)
            if unit_label:
                data['unit_label'] = unit_label
            if manifest.get('mode') == 'relational_snapshot':
                from packages.metricguard_core.relational_snapshot import validate_manifest
                validate_manifest(manifest, manifest['contract'])
                data.update(execution_policy='relational_snapshot_v1', currency=None, timezone=None, cutoff_at=None)
            elif manifest.get('mode') == 'flat_snapshot' and manifest.get('execution_policy') == 'flat_snapshot_v1':
                # Single-table warehouse extracts: the same policy as an uploaded file, so they can be compared.
                from packages.metricguard_core.flat import validate_contract
                contract = validate_contract(manifest['contract'])
                if contract['contract_hash'] != manifest.get('contract_hash'):
                    raise DomainError('SNAPSHOT_INTEGRITY', 'Collected snapshot contract hash differs from its contract.')
                data.update(execution_policy='flat_snapshot_v1', currency=None)
            conn.execute(insert(snapshots).values(id=data['id'], project_id=pid, manifest_hash=data['manifest_hash'], data=data))
        return data

    @staticmethod
    def _import(row):
        result = {**row['data'], 'id': row['id'], 'project_id': row['project_id'], 'state': row['state'],
                  'attempt': row['attempt'], 'created_at': row['created_at'],
                  'completed_at': row['completed_at'], 'snapshot_id': row['snapshot_id'], 'error': row['error']}
        return result

    def get_import(self, ident):
        return self._import(self._row(snapshot_imports, ident))

    def list_imports(self, project_id=None):
        with self.engine.connect() as conn:
            query = select(snapshot_imports)
            if project_id:
                query = query.where(snapshot_imports.c.project_id == project_id)
            return [self._import(row) for row in conn.execute(query.order_by(
                snapshot_imports.c.created_at.desc()).limit(100)).mappings()]

    def queue_import(self, source_path, source_hash, name, metadata, project_id=None):
        pid = project_id or self.project_id
        ident = str(uuid4())
        with self.engine.begin() as conn:
            conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
            for row in conn.execute(select(snapshot_imports).where(snapshot_imports.c.state.in_(['queued', 'running']), snapshot_imports.c.project_id == pid)).mappings():
                if row['data']['source_hash'] == source_hash and row['data']['metadata'] == metadata:
                    return {**self._import(row), 'reused': True}
            conn.execute(insert(snapshot_imports).values(id=ident, project_id=pid,
                state='queued', attempt=0, created_at=now(), source_path=str(source_path),
                data={'name': name, 'source_hash': source_hash, 'metadata': metadata}))
        return {**self.get_import(ident), 'reused': False}

    def claim_import(self, worker_id, lease_seconds=90):
        moment = time.time()
        with self.engine.begin() as conn:
            available = or_(snapshot_imports.c.state == 'queued', and_(snapshot_imports.c.state == 'running',
                snapshot_imports.c.lease_expires_at < moment))
            candidate = conn.execute(select(snapshot_imports.c.id, snapshot_imports.c.attempt).where(available)
                .order_by(snapshot_imports.c.created_at).limit(1)).first()
            if candidate is None:
                return None
            changed = conn.execute(update(snapshot_imports).where(snapshot_imports.c.id == candidate.id,
                snapshot_imports.c.attempt == candidate.attempt, available).values(state='running',
                attempt=candidate.attempt + 1, lease_owner=worker_id, lease_expires_at=moment + lease_seconds))
            if not changed.rowcount:
                return None
            return dict(conn.execute(select(snapshot_imports).where(snapshot_imports.c.id == candidate.id)).mappings().one())

    def heartbeat_import(self, ident, worker_id, attempt, lease_seconds=90):
        moment = time.time()
        with self.engine.begin() as conn:
            changed = conn.execute(update(snapshot_imports).where(snapshot_imports.c.id == ident,
                snapshot_imports.c.state == 'running', snapshot_imports.c.lease_owner == worker_id,
                snapshot_imports.c.attempt == attempt, snapshot_imports.c.lease_expires_at >= moment)
                .values(lease_expires_at=moment + lease_seconds))
            return bool(changed.rowcount)

    def finish_import(self, ident, worker_id, attempt, manifest=None, artifact_path=None, error=None):
        with self.engine.begin() as conn:
            condition = and_(snapshot_imports.c.id == ident, snapshot_imports.c.state == 'running',
                snapshot_imports.c.lease_owner == worker_id, snapshot_imports.c.attempt == attempt,
                snapshot_imports.c.lease_expires_at >= time.time())
            row = conn.execute(select(snapshot_imports).where(condition).with_for_update()).mappings().first()
            if row is None:
                return False
            snapshot_id = None
            if manifest is not None and error is None:
                import_pid = row['project_id']
                existing = conn.execute(select(snapshots).where(
                    snapshots.c.manifest_hash == manifest['manifest_hash'],
                    snapshots.c.project_id == import_pid
                )).mappings().first()
                if existing:
                    snapshot_id = existing['id']
                else:
                    snapshot_id = str(uuid4())
                    data = dict(id=snapshot_id, name=row['data']['name'], row_count=manifest['row_count'],
                        manifest_hash=manifest['manifest_hash'], source_hash=manifest['source_hash'],
                        contract_hash=manifest.get('contract_hash'), mapping_hash=manifest.get('mapping_hash'),
                        execution_policy=manifest.get('execution_policy', 'retail_v1'),
                        currency=manifest.get('currency'), timezone=manifest.get('timezone'), scope=manifest['scope'],
                        grain=manifest.get('grain'), cutoff_at=manifest.get('cutoff_at'), schema=manifest['schema'],
                        status='validated', created_at=now(), artifact_path=str(artifact_path), manifest=manifest,
                        project_id=import_pid)
                    if manifest.get('unit_label'):
                        data['unit_label'] = manifest['unit_label']
                    conn.execute(insert(snapshots).values(id=snapshot_id, project_id=import_pid,
                        manifest_hash=manifest['manifest_hash'], data=data))
            conn.execute(update(snapshot_imports).where(condition).values(state='failed' if error else 'validated',
                snapshot_id=snapshot_id, error=error, completed_at=now(), lease_expires_at=None))
            return True

    def finish_preview(self, ident, worker_id, attempt, preview=None, error=None):
        with self.engine.begin() as conn:
            condition = and_(snapshot_imports.c.id == ident,
                snapshot_imports.c.state == 'running',
                snapshot_imports.c.lease_owner == worker_id,
                snapshot_imports.c.attempt == attempt,
                snapshot_imports.c.lease_expires_at >= time.time())
            row = conn.execute(select(snapshot_imports).where(condition).with_for_update()).mappings().first()
            if row is None:
                return False
            if row['data'].get('metadata', {}).get('purpose') != 'preview':
                raise DomainError('INVALID_JOB', 'Preview completion was requested for another import.')
            data = {**row['data'], **({'preview': preview} if preview is not None else {})}
            changed = conn.execute(update(snapshot_imports).where(condition).values(
                state='failed' if error else 'previewed', data=data, error=error,
                completed_at=now(), lease_expires_at=None))
            return bool(changed.rowcount)

    @staticmethod
    def _run(row):
        output = dict(row)
        output.update(output.pop('request'))
        output.pop('request_hash', None)
        output.pop('idempotency_key', None)
        return output

    def get_run(self, ident):
        run = self._run(self._row(runs, ident))
        expired = self.evidence_context(ident, run['attempt'], kind='evidence_expired')
        if expired:
            run.update(evidence_state='expired', evidence_expired_at=expired['expired_at'])
        context = self.evidence_context(ident, run['attempt'])
        if context and context.get('status') == 'unavailable':
            run.update(context_status='unavailable', context_unavailable_reason=context['reason'])
        return run

    def queue(self, request, idempotency_key, execution_mode, project_id=None):
        pid = project_id or self.project_id
        digest = fingerprint(request)
        try:
            with self.engine.begin() as conn:
                # Serialize admission with account deletion and other queue requests.
                # SQLite acquires its write lock; PostgreSQL locks this workspace row.
                conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
                existing = conn.execute(select(runs).where(runs.c.idempotency_key == idempotency_key)).mappings().first()
                if existing:
                    if existing['request_hash'] != digest:
                        raise DomainError('IDEMPOTENCY_CONFLICT', 'This idempotency key belongs to a different request.', 409)
                    return self._run(existing)
                from packages.metricguard_core.free_tier import configured_limit, check_rows
                active_limit = configured_limit('METRICGUARD_MAX_ACTIVE_RUNS_PER_WORKSPACE')
                if active_limit and conn.execute(select(func.count()).select_from(runs).where(
                        runs.c.project_id == pid, runs.c.state.in_(['queued', 'running']))).scalar_one() >= active_limit:
                    message = ('One comparison at a time on the free beta. It will start when the current one finishes.'
                               if active_limit == 1 else f'Free beta limit: {active_limit} active comparisons per workspace. Wait for a comparison to finish.')
                    raise DomainError('FREE_TIER_ACTIVE_RUNS', message, 409)
                daily_limit = configured_limit('METRICGUARD_MAX_RUNS_PER_DAY')
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                if daily_limit and conn.execute(select(func.count()).select_from(runs).where(
                        runs.c.project_id == pid, runs.c.created_at >= cutoff)).scalar_one() >= daily_limit:
                    raise DomainError('FREE_TIER_DAILY_RUNS', f'Free beta limit: {daily_limit:,} comparisons per workspace in 24 hours. Try again later.', 429)
                if configured_limit('METRICGUARD_MAX_ROWS'):
                    for sid in {request['snapshot_id'], request.get('target_snapshot_id')} - {None}:
                        snap = conn.execute(select(snapshots.c.data).where(snapshots.c.id == sid)).scalar_one_or_none()
                        if snap:
                            check_rows(snap.get('row_count', snap.get('manifest', {}).get('row_count', 0)))
                ident = str(uuid4())
                conn.execute(insert(runs).values(id=ident, project_id=pid, baseline_version_id=request['baseline_version_id'],
                    target_version_id=request['target_version_id'], snapshot_id=request['snapshot_id'], idempotency_key=idempotency_key,
                    request_hash=digest, request=request, state='queued', execution_mode=execution_mode, attempt=0,
                    cancel_requested=False, created_at=now()))
            return self.get_run(ident)
        except IntegrityError:
            # Concurrent retries race at the unique constraint; read the committed winner.
            with self.engine.connect() as conn:
                existing = conn.execute(select(runs).where(runs.c.idempotency_key == idempotency_key)).mappings().first()
            if existing:
                if existing['request_hash'] != digest:
                    raise DomainError('IDEMPOTENCY_CONFLICT', 'This idempotency key belongs to a different request.', 409)
                return self._run(existing)
            raise DomainError('NOT_FOUND', 'One of the selected versions or snapshot is unavailable.', 404)

    def complete_demo(self, ident, result):
        with self.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.id == ident, runs.c.state == 'queued', runs.c.execution_mode == 'demo').values(
                state='succeeded' if result['explanation_status'] == 'complete_ordered_bridge' else 'partial', result=result, completed_at=now()))
        return self.get_run(ident)

    def claim(self, worker_id, lease_seconds=90):
        moment = time.time()
        with self.engine.begin() as conn:
            available = and_(runs.c.execution_mode == 'isolated', runs.c.cancel_requested.is_(False),
                or_(runs.c.state == 'queued', and_(runs.c.state == 'running', runs.c.lease_expires_at < moment)))
            candidate = conn.execute(select(runs.c.id, runs.c.attempt).where(available).order_by(runs.c.created_at).limit(1)).first()
            if candidate is None:
                return None
            changed = conn.execute(update(runs).where(runs.c.id == candidate.id, available, runs.c.attempt == candidate.attempt).values(
                state='running', attempt=candidate.attempt + 1, lease_owner=worker_id, lease_expires_at=moment + lease_seconds,
                heartbeat_at=moment, started_at=now()))
            if not changed.rowcount:
                return None
        return self.get_run(candidate.id)

    def heartbeat(self, run_id, worker_id, attempt, lease_seconds=90):
        with self.engine.begin() as conn:
            changed = conn.execute(update(runs).where(runs.c.id == run_id, runs.c.state == 'running',
                runs.c.lease_owner == worker_id, runs.c.attempt == attempt, runs.c.lease_expires_at >= time.time()).values(
                    heartbeat_at=time.time(), lease_expires_at=time.time() + lease_seconds))
            return bool(changed.rowcount)

    def finish(self, run_id, worker_id, attempt, result=None, error=None):
        with self.engine.begin() as conn:
            condition = and_(runs.c.id == run_id, runs.c.state == 'running', runs.c.lease_owner == worker_id,
                runs.c.attempt == attempt, runs.c.lease_expires_at >= time.time())
            row = conn.execute(select(runs).where(condition).with_for_update()).mappings().first()
            if row is None:
                return False
            state = 'canceled' if row['cancel_requested'] else ('failed' if error or result is None else (
                'succeeded' if result.get('explanation_status') in {'complete_ordered_bridge', 'complete_eligibility_change', 'complete_case_definition_change', 'complete_join_filter_change',
                                                                       'complete_entity_membership_change', 'complete_ratio_bridge', 'complete_snapshot_change'} else 'partial'))
            changed = conn.execute(update(runs).where(condition).values(state=state, result=result if state in ('succeeded', 'partial') else None,
                error=error, completed_at=now(), lease_expires_at=None))
            return bool(changed.rowcount)

    def cancel(self, ident):
        self.get_run(ident)
        with self.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.id == ident, runs.c.state == 'queued').values(state='canceled', cancel_requested=True, completed_at=now()))
            # Cancellation is terminal even if the worker disappeared. The old
            # lease can no longer finalize; a live supervisor will see its
            # heartbeat rejected and terminate the fixed container promptly.
            conn.execute(update(runs).where(runs.c.id == ident, runs.c.state == 'running').values(
                state='canceled', cancel_requested=True, completed_at=now(), lease_expires_at=None))
        return self.get_run(ident)

    def worker_seen(self, ident):
        with self.engine.begin() as conn:
            if not conn.execute(update(worker_status).where(worker_status.c.id == ident).values(last_seen=time.time())).rowcount:
                conn.execute(insert(worker_status).values(id=ident, last_seen=time.time()))

    def worker_available(self):
        with self.engine.connect() as conn:
            return conn.execute(select(worker_status.c.id).where(worker_status.c.last_seen > time.time() - 20)).first() is not None

    def record(self, kind, data, project_id=None):
        pid = project_id or self.project_id
        value = {**data, 'id': str(uuid4()), 'created_at': now(), 'project_id': pid}
        with self.engine.begin() as conn:
            if kind in ('investigation', 'investigation_draft'):
                conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
                rid = data.get('run_id') or data.get('comparison_run_id')
                if rid and conn.execute(select(records.c.id).where(records.c.kind == 'evidence_expired',
                        records.c.data['run_id'].as_string() == rid)).first():
                    raise DomainError('EVIDENCE_EXPIRED', 'Detailed records expired — rerun to see them', 410)
            conn.execute(insert(records).values(id=value['id'], project_id=pid, kind=kind, created_at=value['created_at'], data=value))
        return value

    def evidence_context(self, run_id: str, attempt: int, kind: str = 'evidence_context') -> dict | None:
        """The record-context (or, with kind='evidence_members', membership) file registered for one run attempt."""
        with self.engine.connect() as conn:
            for row in conn.execute(select(records).where(records.c.kind == kind,
                    records.c.data['run_id'].as_string() == run_id)).mappings():
                data = row['data']
                if data.get('run_id') == run_id and data.get('attempt') == attempt:
                    return data
        return None

    def record_baseline(self, data, project_id=None):
        pid = project_id or self.project_id
        self.version(data['version_id'])
        value = {**data, 'id': str(uuid4()), 'created_at': now(), 'superseded_by': None, 'review_status': 'recorded_locally', 'project_id': pid}
        with self.engine.begin() as conn:
            conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
            for old in conn.execute(select(records).where(records.c.kind == 'baseline', records.c.project_id == pid)).mappings():
                previous = old['data']
                if previous['scope'] == data['scope'] and previous.get('superseded_by') is None:
                    conn.execute(update(records).where(records.c.id == old['id']).values(data={**previous, 'superseded_by': value['id']}))
            conn.execute(insert(records).values(id=value['id'], project_id=pid, kind='baseline', created_at=value['created_at'], data=value))
        return value

    def bootstrap(self, project_id=None):
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            # Definitions and their versions in two queries (not two per definition).
            definition_list = [dict(r) for r in conn.execute(select(definitions).where(definitions.c.project_id == pid).order_by(definitions.c.created_at)).mappings()]
            version_rows = conn.execute(select(versions.c.definition_id, versions.c.data).where(
                versions.c.definition_id.in_([d['id'] for d in definition_list])).order_by(versions.c.version_number)).mappings() if definition_list else []
            by_definition = {}
            for r in version_rows:
                by_definition.setdefault(r['definition_id'], []).append(r['data'])
            for d in definition_list:
                d['versions'] = by_definition.get(d['id'], [])
            snapshot_list = [_with_flat_policy({**r['data'], 'project_id': r['project_id']}) for r in conn.execute(select(snapshots).where(snapshots.c.project_id == pid).order_by(snapshots.c.id)).mappings()]
            run_list = [self._run(r) for r in conn.execute(select(runs).where(runs.c.project_id == pid).order_by(runs.c.created_at.desc()).limit(100)).mappings()]
            all_records = list(conn.execute(select(records).where(records.c.project_id == pid).order_by(records.c.created_at.desc())).mappings())
        expired = {r['data']['run_id']: r['data'] for r in all_records if r['kind'] == 'evidence_expired'}
        unavailable = {r['data']['run_id']: r['data'] for r in all_records
                       if r['kind'] == 'evidence_context' and r['data'].get('status') == 'unavailable'}
        for run in run_list:
            if run['id'] in unavailable:
                run.update(context_status='unavailable', context_unavailable_reason=unavailable[run['id']]['reason'])
            if run['id'] in expired:
                run.update(evidence_state='expired', evidence_expired_at=expired[run['id']]['expired_at'])
        snapshot_import_list = [item for item in self.list_imports(pid) if item['metadata'].get('purpose') != 'preview']
        p_row = self.get_project(pid)
        p_name = p_row['name'] if p_row else ('Retail analytics' if pid == 'local-retail' else f"Workspace {pid}")
        metric_list = self.list_metrics(pid) if hasattr(self, 'list_metrics') else []
        diff_list = self.list_metric_diff_reports(pid) if hasattr(self, 'list_metric_diff_reports') else []
        return dict(project={'id': pid, 'name': p_name}, definitions=definition_list,
            snapshots=snapshot_list, imports=snapshot_import_list, runs=run_list, baselines=[r['data'] for r in all_records if r['kind'] == 'baseline'],
            dependencies=[r['data'] for r in all_records if r['kind'] == 'dependency'], reviews=[r['data'] for r in all_records if r['kind'] == 'review'],
            metadata_imports=[r['data'] for r in all_records if r['kind'] == 'metadata_import'],
            dataset_contracts=[r['data'] for r in all_records if r['kind'] == 'dataset_contract'],
            flat_contracts=[r['data'] for r in all_records if r['kind'] == 'flat_contract'],
            relational_contracts=[r['data'] for r in all_records if r['kind'] == 'relational_contract'],
            column_mappings=[r['data'] for r in all_records if r['kind'] == 'column_mapping'],
            investigations=[r['data'] for r in all_records if r['kind'] == 'investigation'],
            investigation_drafts=[r['data'] for r in all_records if r['kind'] == 'investigation_draft'],
            metrics=metric_list, metric_diffs=diff_list)

    # -----------------------------------------------------------------------
    # V3 Identity, Projects & RBAC
    # -----------------------------------------------------------------------

    def create_user(self, name: str, email: str, api_key: str | None = None) -> tuple[dict, str]:
        raw_key = api_key or f"mg_{uuid4().hex}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        user_id = str(uuid4())
        item = dict(id=user_id, name=name, email=email, api_key_hash=key_hash, is_active=True, created_at=now())
        with self.engine.begin() as conn:
            existing = conn.execute(select(users).where(users.c.email == email)).mappings().first()
            if existing:
                raise DomainError('EMAIL_TAKEN', f"A user with email '{email}' already exists.")
            conn.execute(insert(users).values(**item))
        return {k: v for k, v in item.items() if k != 'api_key_hash'}, raw_key

    def get_user_by_api_key(self, api_key: str) -> dict | None:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        with self.engine.connect() as conn:
            row = conn.execute(select(users).where(users.c.api_key_hash == key_hash, users.c.is_active == True)).mappings().first()
            if row:
                return {k: v for k, v in dict(row).items() if k != 'api_key_hash'}
        return None

    def create_session(self, user_id: str, ttl_seconds: int) -> str:
        import secrets
        import time as _time
        token = secrets.token_urlsafe(32)
        now_ts = _time.time()
        with self.engine.begin() as conn:
            conn.execute(delete(auth_sessions).where(auth_sessions.c.expires_at < now_ts))
            conn.execute(insert(auth_sessions).values(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user_id,
                                                      created_at=now_ts, expires_at=now_ts + ttl_seconds))
        return token

    def get_user_by_session(self, token: str) -> dict | None:
        import time as _time
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.engine.connect() as conn:
            row = conn.execute(select(users).join(auth_sessions, auth_sessions.c.user_id == users.c.id).where(
                auth_sessions.c.token_hash == token_hash, auth_sessions.c.expires_at > _time.time(),
                users.c.is_active == True)).mappings().first()
            return {k: v for k, v in dict(row).items() if k != 'api_key_hash'} if row else None

    def revoke_session(self, token: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(auth_sessions).where(auth_sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest()))

    def start_oauth_state(self, state: str, seconds: int = 600) -> None:
        moment = time.time()
        with self.engine.begin() as conn:
            conn.execute(delete(oauth_states).where(oauth_states.c.expires_at < moment))
            conn.execute(insert(oauth_states).values(nonce_hash=hashlib.sha256(state.encode()).hexdigest(),
                                                   expires_at=moment + seconds))

    def consume_oauth_state(self, state: str) -> bool:
        with self.engine.begin() as conn:
            changed = conn.execute(delete(oauth_states).where(
                oauth_states.c.nonce_hash == hashlib.sha256(state.encode()).hexdigest(),
                oauth_states.c.expires_at > time.time()))
            return changed.rowcount == 1

    def request_access(self, email: str, max_rows: int = 5000) -> None:
        """Record a waitlist email once (repeats only bump a counter); the list is capped."""
        email = email.strip().casefold()
        stamp = now()
        with self.engine.begin() as conn:
            known = conn.execute(update(access_requests).where(access_requests.c.email == email).values(
                last_requested_at=stamp, request_count=access_requests.c.request_count + 1)).rowcount
            if known:
                return
            if conn.execute(select(func.count()).select_from(access_requests)).scalar_one() >= max_rows:
                raise DomainError('ACCESS_REQUESTS_FULL', 'The waitlist is full right now. Please try again later.', 429)
            try:
                with conn.begin_nested():
                    conn.execute(insert(access_requests).values(email=email, created_at=stamp,
                                                                last_requested_at=stamp, request_count=1))
            except IntegrityError:
                pass  # a concurrent request recorded the same email

    def access_request_list(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(access_requests).order_by(access_requests.c.created_at)).mappings()]

    def oauth_sign_in(self, provider: str, subject: str, email: str, name: str,
                      signup_mode: str, invited: set[str]) -> tuple[dict, str]:
        """Atomically link a verified identity and create its personal workspace once."""
        import secrets
        email = email.strip().casefold()
        for attempt in range(3):
            try:
                with self.engine.begin() as conn:
                    identity = conn.execute(select(oauth_identities.c.user_id).where(
                        oauth_identities.c.provider == provider, oauth_identities.c.subject == subject)).scalar_one_or_none()
                    matches = conn.execute(select(users).where(users.c.id == identity) if identity else
                                           select(users).where(func.lower(users.c.email) == email)).mappings().all()
                    if len(matches) > 1 or (matches and not matches[0]['is_active']):
                        raise DomainError('OAUTH_FAILED', 'This account cannot sign in.', 403)
                    if matches:
                        user = dict(matches[0])
                    else:
                        if signup_mode != 'open' and email not in invited:
                            raise DomainError('OAUTH_WAITLIST', 'An invitation is required.', 403)
                        user = {'id': str(uuid4()), 'name': name[:160] or 'Analyst', 'email': email,
                                'api_key_hash': hashlib.sha256(secrets.token_urlsafe(48).encode()).hexdigest(),
                                'is_active': True, 'created_at': now()}
                        conn.execute(insert(users).values(**user))
                    if not identity:
                        conn.execute(insert(oauth_identities).values(provider=provider, subject=subject, user_id=user['id']))
                    pid = 'personal-' + user['id']
                    if not conn.execute(select(projects.c.id).where(projects.c.id == pid)).first():
                        conn.execute(insert(projects).values(id=pid, name=f"{user['name']}'s workspace"))
                        conn.execute(insert(project_members).values(id=str(uuid4()), project_id=pid,
                            user_id=user['id'], role='owner', created_at=now(), revoked_at=None))
                    else:
                        member = conn.execute(select(project_members).where(project_members.c.project_id == pid,
                            project_members.c.user_id == user['id'], project_members.c.role == 'owner',
                            project_members.c.revoked_at.is_(None))).first()
                        if member is None:
                            raise DomainError('OAUTH_FAILED', 'Personal workspace access is unavailable.', 403)
                return {key: value for key, value in user.items() if key != 'api_key_hash'}, pid
            except IntegrityError:
                if attempt == 2:
                    raise DomainError('OAUTH_FAILED', 'Account sign-in conflicted. Start sign-in again.', 409)
        raise AssertionError('unreachable')

    def get_user(self, user_id: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(users).where(users.c.id == user_id)).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"User '{user_id}' not found.", 404)
            return {k: v for k, v in dict(row).items() if k != 'api_key_hash'}

    def deactivate_users_by_email(self, email: str) -> int:
        """Deactivate every key issued to this email; their requests then return 401. Memberships are kept."""
        with self.engine.begin() as conn:
            result = conn.execute(update(users).where(users.c.email == email, users.c.is_active == True).values(is_active=False))
            return result.rowcount or 0

    def create_project(self, name: str, owner_id: str | None = None, project_id: str | None = None) -> dict:
        pid = project_id or str(uuid4())
        with self.engine.begin() as conn:
            existing = conn.execute(select(projects).where(projects.c.id == pid)).first()
            if existing:
                raise DomainError('PROJECT_EXISTS', f"Project '{pid}' already exists.")
            conn.execute(insert(projects).values(id=pid, name=name))
            if owner_id:
                conn.execute(insert(project_members).values(
                    id=str(uuid4()), project_id=pid, user_id=owner_id, role='owner', created_at=now(), revoked_at=None
                ))
        return {'id': pid, 'name': name}

    def get_project(self, project_id: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(projects).where(projects.c.id == project_id)).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"Project '{project_id}' not found.", 404)
            return dict(row)

    def list_projects(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(projects)).mappings().all()]

    def list_projects_for_user(self, user_id: str) -> list[dict]:
        """Active memberships only, each with the caller's role (no other members are disclosed)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(projects.c.id, projects.c.name, project_members.c.role)
                .join(project_members, project_members.c.project_id == projects.c.id)
                .where(project_members.c.user_id == user_id, project_members.c.revoked_at == None)
                .order_by(projects.c.name)
            ).mappings().all()
            return [dict(r) for r in rows]

    def add_project_member(self, project_id: str, user_id: str, role: str) -> dict:
        if role not in ('owner', 'analyst', 'reader'):
            raise DomainError('INVALID_ROLE', "Role must be one of owner, analyst, reader.")
        self.get_user(user_id)
        self.get_project(project_id)
        with self.engine.begin() as conn:
            existing = conn.execute(select(project_members).where(
                project_members.c.project_id == project_id, project_members.c.user_id == user_id
            )).mappings().first()
            if existing:
                conn.execute(update(project_members).where(project_members.c.id == existing['id']).values(
                    role=role, revoked_at=None
                ))
                mid = existing['id']
            else:
                mid = str(uuid4())
                conn.execute(insert(project_members).values(
                    id=mid, project_id=project_id, user_id=user_id, role=role, created_at=now(), revoked_at=None
                ))
        return {'id': mid, 'project_id': project_id, 'user_id': user_id, 'role': role, 'revoked': False}

    def revoke_project_member(self, project_id: str, user_id: str) -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(select(project_members).where(
                project_members.c.project_id == project_id, project_members.c.user_id == user_id,
                project_members.c.revoked_at == None
            )).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', 'Active membership not found.', 404)
            conn.execute(update(project_members).where(project_members.c.id == row['id']).values(revoked_at=now()))
        return {'project_id': project_id, 'user_id': user_id, 'revoked': True}

    def get_project_member(self, project_id: str, user_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(project_members).where(
                project_members.c.project_id == project_id, project_members.c.user_id == user_id
            )).mappings().first()
            return dict(row) if row else None

    def list_project_members(self, project_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(project_members, users.c.name, users.c.email).join(
                users, users.c.id == project_members.c.user_id
            ).where(project_members.c.project_id == project_id)).mappings().all()
            return [dict(r) for r in rows]

    # -----------------------------------------------------------------------
    # V3 Review Requests & Superseding Decisions
    # -----------------------------------------------------------------------

    def create_review_request(self, project_id: str, requester_id: str, payload: dict) -> dict:
        req_id = str(uuid4())
        record = {
            'id': req_id,
            'project_id': project_id,
            'requester_id': requester_id,
            'status': 'pending',
            'rationale': payload['rationale'],
            'definition_id': payload.get('definition_id'),
            'baseline_version_id': payload.get('baseline_version_id'),
            'target_version_id': payload.get('target_version_id'),
            'run_id': payload.get('run_id'),
            'is_intentional_variant': payload.get('is_intentional_variant', False),
            'variant_rationale': payload.get('variant_rationale'),
            'effective_time': payload.get('effective_time'),
            'created_at': now(),
            'updated_at': now(),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(review_requests).values(
                id=req_id, project_id=project_id, requester_id=requester_id,
                status='pending', data=record, created_at=record['created_at'], updated_at=record['updated_at']
            ))
        return record

    def list_review_requests(self, project_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(review_requests).where(
                review_requests.c.project_id == project_id
            ).order_by(review_requests.c.created_at.desc())).mappings().all()
            return [{**r['data'], 'status': r['status'], 'updated_at': r['updated_at']} for r in rows]

    def get_review_request(self, request_id: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(review_requests).where(review_requests.c.id == request_id)).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"Review request '{request_id}' not found.", 404)
            return {**row['data'], 'status': row['status'], 'updated_at': row['updated_at']}

    def record_decision(self, project_id: str, decider_id: str, payload: dict) -> dict:
        decision_id = str(uuid4())
        self.version(payload['approved_version_id'])
        if payload.get('supersedes_version_id'):
            self.version(payload['supersedes_version_id'])
        record = {
            'id': decision_id,
            'project_id': project_id,
            'decider_id': decider_id,
            'review_request_id': payload.get('review_request_id'),
            'approved_version_id': payload['approved_version_id'],
            'supersedes_version_id': payload.get('supersedes_version_id'),
            'decision': payload['decision'],
            'rationale': payload['rationale'],
            'created_at': now(),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(decisions).values(
                id=decision_id, project_id=project_id, decider_id=decider_id,
                data=record, created_at=record['created_at']
            ))
            if payload.get('review_request_id'):
                rr = conn.execute(select(review_requests).where(review_requests.c.id == payload['review_request_id'])).mappings().first()
                if rr:
                    ts = now()
                    updated_data = {**rr['data'], 'status': payload['decision'], 'updated_at': ts}
                    conn.execute(update(review_requests).where(
                        review_requests.c.id == payload['review_request_id']
                    ).values(status=payload['decision'], data=updated_data, updated_at=ts))
        return record

    def list_decisions(self, project_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(decisions).where(
                decisions.c.project_id == project_id
            ).order_by(decisions.c.created_at.desc())).mappings().all()
            return [r['data'] for r in rows]

    # -----------------------------------------------------------------------
    # V3 Monitoring Policies & Alerts
    # -----------------------------------------------------------------------

    def create_monitoring_policy(self, project_id: str, creator_id: str, payload: dict) -> dict:
        policy_id = str(uuid4())
        record = {
            'id': policy_id,
            'project_id': project_id,
            'creator_id': creator_id,
            'name': payload['name'],
            'baseline_version_id': payload['baseline_version_id'],
            'target_version_id': payload['target_version_id'],
            'snapshot_id': payload.get('snapshot_id'),
            'schedule_type': payload.get('schedule_type', 'event_triggered'),
            'absolute_threshold': payload.get('absolute_threshold'),
            'relative_threshold': payload.get('relative_threshold'),
            'suppress_intentional_variants': payload.get('suppress_intentional_variants', True),
            'enabled': payload.get('enabled', True),
            'created_at': now(),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(monitoring_policies).values(
                id=policy_id, project_id=project_id, name=record['name'],
                data=record, created_at=record['created_at']
            ))
        return record

    def get_monitoring_policy(self, policy_id: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(monitoring_policies).where(monitoring_policies.c.id == policy_id)).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"Monitoring policy '{policy_id}' not found.", 404)
            return row['data']

    def list_monitoring_policies(self, project_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(monitoring_policies).where(
                monitoring_policies.c.project_id == project_id
            ).order_by(monitoring_policies.c.created_at.desc())).mappings().all()
            return [r['data'] for r in rows]

    def record_alert(self, project_id: str, policy_id: str, event_hash: str, alert_data: dict) -> tuple[dict, bool]:
        with self.engine.begin() as conn:
            existing = conn.execute(select(alerts).where(
                alerts.c.policy_id == policy_id, alerts.c.event_hash == event_hash
            )).mappings().first()
            if existing:
                return existing['data'], False
            alert_id = str(uuid4())
            record = {
                'id': alert_id,
                'project_id': project_id,
                'policy_id': policy_id,
                'event_hash': event_hash,
                'state': 'unacknowledged',
                'data': alert_data,
                'created_at': now(),
                'acknowledged_at': None,
                'acknowledged_by': None,
                'note': None,
            }
            conn.execute(insert(alerts).values(
                id=alert_id, project_id=project_id, policy_id=policy_id, event_hash=event_hash,
                state='unacknowledged', data=record, created_at=record['created_at'],
                acknowledged_at=None, acknowledged_by=None
            ))
            return record, True

    def get_alert(self, alert_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(alerts).where(alerts.c.id == alert_id)).mappings().first()
            return row['data'] if row else None

    def list_alerts(self, project_id: str, state: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            query = select(alerts).where(alerts.c.project_id == project_id)
            if state:
                query = query.where(alerts.c.state == state)
            rows = conn.execute(query.order_by(alerts.c.created_at.desc())).mappings().all()
            return [r['data'] for r in rows]

    def acknowledge_alert(self, alert_id: str, user_id: str, note: str = '') -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(select(alerts).where(alerts.c.id == alert_id)).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"Alert '{alert_id}' not found.", 404)
            updated_data = {**row['data'], 'state': 'acknowledged', 'acknowledged_at': now(),
                            'acknowledged_by': user_id, 'note': note}
            conn.execute(update(alerts).where(alerts.c.id == alert_id).values(
                state='acknowledged', data=updated_data, acknowledged_at=updated_data['acknowledged_at'],
                acknowledged_by=user_id
            ))
            return updated_data

    # -----------------------------------------------------------------------
    # V3 Quotas and Redacted Operational Audit Logs
    # -----------------------------------------------------------------------

    def set_project_quota(self, project_id: str, quota_data: dict) -> dict:
        self.get_project(project_id)
        with self.engine.begin() as conn:
            existing = conn.execute(select(records).where(
                records.c.project_id == project_id, records.c.kind == 'quota'
            )).mappings().first()
            val = {**quota_data, 'id': str(uuid4()), 'project_id': project_id, 'created_at': now()}
            if existing:
                conn.execute(update(records).where(records.c.id == existing['id']).values(data=val))
            else:
                conn.execute(insert(records).values(id=val['id'], project_id=project_id, kind='quota', created_at=now(), data=val))
        return val

    def get_project_quota(self, project_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(records).where(
                records.c.project_id == project_id, records.c.kind == 'quota'
            )).mappings().first()
            return row['data'] if row else None

    def check_storage_quota(self, project_id: str, new_bytes: int, data_dir: Path) -> None:
        quota = self.get_project_quota(project_id)
        if not quota or not quota.get('max_storage_bytes'):
            return
        max_bytes = quota['max_storage_bytes']
        current_bytes = 0
        with self.engine.connect() as conn:
            snaps = conn.execute(select(snapshots).where(snapshots.c.project_id == project_id)).mappings().all()
            for s in snaps:
                art = Path(s['data'].get('artifact_path', ''))
                if art.is_file():
                    current_bytes += art.stat().st_size
            run_rows = conn.execute(select(runs).where(runs.c.project_id == project_id)).mappings().all()
            ev_dir = data_dir / 'evidence'
            if ev_dir.is_dir():
                for r in run_rows:
                    for ev in ev_dir.glob(f"{r['id']}-*.jsonl"):
                        current_bytes += ev.stat().st_size
        if current_bytes + new_bytes > max_bytes:
            raise DomainError('QUOTA_EXCEEDED', f"Project storage quota exceeded ({current_bytes + new_bytes} > {max_bytes} bytes).", 413)

    @staticmethod
    def _redact_value(v: Any) -> Any:
        if isinstance(v, dict):
            return Store._redact_dict(v)
        if isinstance(v, (list, tuple)):
            return [Store._redact_value(item) for item in v]
        if isinstance(v, str):
            # 1. Bearer token pattern
            v = re.sub(r'(?i)\bbearer\s+[a-zA-Z0-9_\-\.]+', 'Bearer [REDACTED]', v)
            # 2. Connection string password pattern: scheme://user:pass@host
            v = re.sub(r'([a-zA-Z0-9\+\.\-]+://[^:]+:)([^@]+)(@)', r'\1[REDACTED]\3', v)
            # 3. Query params with secrets: key=..., token=..., secret=..., password=...
            v = re.sub(r'(?i)([\?&](?:api_key|apikey|token|access_token|secret|password|pwd|auth|key)=)[^&]+', r'\1[REDACTED]', v)
        return v

    @staticmethod
    def _redact_dict(obj: Any) -> Any:
        if not isinstance(obj, dict):
            return Store._redact_value(obj)
        redacted = {}
        secret_keys = ('api_key', 'apikey', 'token', 'secret', 'password', 'passwd', 'pwd',
                       'authorization', 'auth', 'bearer', 'credential', 'dsn', 'connection_string',
                       'private_key')
        for k, v in obj.items():
            k_norm = str(k).lower().replace('-', '_').replace(' ', '_')
            if any(sec in k_norm for sec in secret_keys):
                redacted[k] = '[REDACTED]'
            else:
                redacted[k] = Store._redact_value(v)
        return redacted

    def record_audit_log(self, project_id: str, actor_id: str, action: str, target_type: str, target_id: str, data: dict) -> dict:
        log_id = str(uuid4())
        safe_data = self._redact_dict(data)
        record = {
            'id': log_id,
            'project_id': project_id,
            'actor_id': actor_id,
            'action': action,
            'target_type': target_type,
            'target_id': target_id,
            'data': safe_data,
            'created_at': now(),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(audit_logs).values(
                id=log_id, project_id=project_id, actor_id=actor_id,
                action=action, target_type=target_type, target_id=target_id,
                data=safe_data, created_at=record['created_at']
            ))
        return record

    def list_audit_logs(self, project_id: str, limit: int = 100) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(audit_logs).where(
                audit_logs.c.project_id == project_id
            ).order_by(audit_logs.c.created_at.desc()).limit(limit)).mappings().all()
            return [dict(r) for r in rows]

    # -----------------------------------------------------------------------
    # V4 Connected SQL: dbt Import Revisions & Resource Tracking
    # -----------------------------------------------------------------------

    def record_dbt_import(self, manifest_content: bytes, run_results_content: bytes | None = None,
                          source_revision: dict | None = None, project_id: str | None = None) -> dict:
        from services.api.metadata_adapters import parse_dbt_project_artifacts
        pid = project_id or self.project_id
        parsed = parse_dbt_project_artifacts(manifest_content, run_results_content, source_revision)
        manifest_hash = parsed['manifest_hash']

        with self.engine.begin() as conn:
            # Check for existing import in this project (idempotent re-import)
            existing_rev = conn.execute(
                select(dbt_import_revisions)
                .where(dbt_import_revisions.c.project_id == pid,
                       dbt_import_revisions.c.manifest_hash == manifest_hash)
            ).mappings().first()

            if existing_rev:
                res_rows = conn.execute(
                    select(dbt_resource_revisions)
                    .where(dbt_resource_revisions.c.import_revision_id == existing_rev['id'])
                    .order_by(dbt_resource_revisions.c.unique_id)
                ).mappings().all()
                return {
                    **dict(existing_rev),
                    'resources': [dict(r) for r in res_rows],
                    'is_reimport': True,
                }

            import_rev_id = f"dbt-rev-{uuid4().hex}"
            created_time = now()
            source_rev_data = source_revision or {}

            conn.execute(insert(dbt_import_revisions).values(
                id=import_rev_id,
                project_id=pid,
                manifest_hash=manifest_hash,
                source_revision=source_rev_data,
                invocation_id=parsed['metadata'].get('invocation_id'),
                dbt_version=parsed['metadata'].get('dbt_version'),
                generated_at=parsed['metadata'].get('generated_at'),
                created_at=created_time,
                data={
                    'project_name': parsed['metadata'].get('project_name'),
                    'schema_version': parsed['metadata'].get('schema_version'),
                    'resource_count': len(parsed['resources']),
                    'limitations': parsed.get('limitations', []),
                }
            ))

            resources_out = []
            for res in parsed['resources']:
                unique_id = res['unique_id']
                resource_type = res['resource_type']
                name = res['name']
                compiled_sql = res['compiled_sql']
                support_state = res['support_state']
                diagnostics = res['diagnostics']
                observed_lineage = res['observed_lineage']
                run_results_status = res.get('run_results_status')

                defn_id = None
                def_version_id = None

                # Definition & Version linking (Correction 1)
                # Link only if compiled SQL is present and support_state is executable or needs_context
                if compiled_sql and support_state in ('executable', 'needs_context'):
                    existing_link = conn.execute(
                        select(dbt_resource_revisions.c.definition_id)
                        .where(dbt_resource_revisions.c.project_id == pid,
                               dbt_resource_revisions.c.unique_id == unique_id,
                               dbt_resource_revisions.c.definition_id.is_not(None))
                    ).first()

                    if existing_link:
                        defn_id = existing_link[0]
                        latest_ver = conn.execute(
                            select(versions)
                            .where(versions.c.definition_id == defn_id)
                            .order_by(versions.c.version_number.desc())
                        ).mappings().first()

                        raw_hash = hashlib.sha256(compiled_sql.encode()).hexdigest()
                        if latest_ver and latest_ver['data'].get('raw_hash') == raw_hash:
                            # SQL unchanged: Reuse existing DefinitionVersion!
                            def_version_id = latest_ver['id']
                        else:
                            # SQL changed: Create new sequential version under existing definition
                            defn = self._record_definition_conn(
                                conn, name=name, source_label='dbt', owner_label='dbt-import',
                                raw_sql=compiled_sql, definition_id=defn_id, project_id=pid
                            )
                            def_version_id = defn['versions'][-1]['id']
                    else:
                        defn = self._record_definition_conn(
                            conn, name=name, source_label='dbt', owner_label='dbt-import',
                            raw_sql=compiled_sql, project_id=pid
                        )
                        defn_id = defn['id']
                        def_version_id = defn['versions'][-1]['id']

                resource_rev_id = f"dbt-res-{uuid4().hex}"
                res_record = {
                    'id': resource_rev_id,
                    'import_revision_id': import_rev_id,
                    'project_id': pid,
                    'unique_id': unique_id,
                    'resource_type': resource_type,
                    'name': name,
                    'definition_id': defn_id,
                    'definition_version_id': def_version_id,
                    'compiled_sql_present': res['compiled_sql_present'],
                    'support_state': support_state,
                    'diagnostics': diagnostics,
                    'observed_lineage': observed_lineage,
                    'run_results_status': run_results_status,
                    'data': {
                        'original_file_path': res.get('original_file_path', ''),
                        'compiled_sql': compiled_sql,
                        'normalized_sql': res.get('normalized_sql'),
                        'normalization': res.get('normalization'),
                        'raw_code': res.get('raw_code'),
                        'meta': res.get('meta', {}),
                        'tags': res.get('tags', []),
                        'materialized': res.get('materialized', 'model'),
                    }
                }
                conn.execute(insert(dbt_resource_revisions).values(**res_record))
                resources_out.append(res_record)

            return {
                'id': import_rev_id,
                'project_id': pid,
                'manifest_hash': manifest_hash,
                'source_revision': source_rev_data,
                'invocation_id': parsed['metadata'].get('invocation_id'),
                'dbt_version': parsed['metadata'].get('dbt_version'),
                'generated_at': parsed['metadata'].get('generated_at'),
                'created_at': created_time,
                'data': {
                    'project_name': parsed['metadata'].get('project_name'),
                    'schema_version': parsed['metadata'].get('schema_version'),
                    'resource_count': len(resources_out),
                    'limitations': parsed.get('limitations', []),
                },
                'resources': resources_out,
                'is_reimport': False,
            }

    def list_dbt_import_revisions(self, project_id: str | None = None) -> list[dict]:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(dbt_import_revisions)
                .where(dbt_import_revisions.c.project_id == pid)
                .order_by(dbt_import_revisions.c.created_at.desc())
            ).mappings().all()
            return [dict(r) for r in rows]

    def get_dbt_import_revision(self, revision_id: str, project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            row = conn.execute(
                select(dbt_import_revisions)
                .where(dbt_import_revisions.c.id == revision_id,
                       dbt_import_revisions.c.project_id == pid)
            ).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"dbt import revision '{revision_id}' not found.", 404)
            res_rows = conn.execute(
                select(dbt_resource_revisions)
                .where(dbt_resource_revisions.c.import_revision_id == revision_id)
                .order_by(dbt_resource_revisions.c.unique_id)
            ).mappings().all()
            return {**dict(row), 'resources': [dict(r) for r in res_rows]}

    def get_dbt_resource_revision(self, resource_rev_id: str, project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            row = conn.execute(
                select(dbt_resource_revisions)
                .where(dbt_resource_revisions.c.id == resource_rev_id,
                       dbt_resource_revisions.c.project_id == pid)
            ).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"dbt resource revision '{resource_rev_id}' not found.", 404)
            return dict(row)

    def get_dbt_resource_history(self, unique_id: str, project_id: str | None = None) -> list[dict]:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(dbt_resource_revisions)
                .where(dbt_resource_revisions.c.unique_id == unique_id,
                       dbt_resource_revisions.c.project_id == pid)
                .order_by(dbt_resource_revisions.c.id.desc())
            ).mappings().all()
            return [dict(r) for r in rows]

    def get_dbt_lineage(self, unique_id: str, import_revision_id: str | None = None, project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            query = select(dbt_resource_revisions).where(
                dbt_resource_revisions.c.unique_id == unique_id,
                dbt_resource_revisions.c.project_id == pid
            )
            if import_revision_id:
                query = query.where(dbt_resource_revisions.c.import_revision_id == import_revision_id)
            row = conn.execute(query.order_by(dbt_resource_revisions.c.id.desc())).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"No resource lineage found for '{unique_id}'.", 404)
            return {
                'unique_id': unique_id,
                'import_revision_id': row['import_revision_id'],
                'observed_lineage': row['observed_lineage'],
            }

    # -----------------------------------------------------------------------
    # V4-02 PostgreSQL Extractions
    # -----------------------------------------------------------------------

    def record_extraction(self, project_id: str, extraction_id: str, selection: dict, attempt: int = 1) -> dict:
        pid = project_id or self.project_id
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(postgres_extractions).where(postgres_extractions.c.id == extraction_id)
            ).mappings().first()

            if existing:
                if existing['project_id'] != pid:
                    raise DomainError('FORBIDDEN', 'Cross-project extraction access denied.', 403)
                if existing['state'] == 'succeeded':
                    raise DomainError('EXTRACTION_ALREADY_SUCCEEDED', 'This extraction has already completed successfully.')
                if existing['cutoff_value'] != selection['cutoff_value']:
                    raise DomainError(
                        'CUTOFF_CHANGED',
                        f"Extraction retry cannot alter cutoff: expected '{existing['cutoff_value']}', got '{selection['cutoff_value']}'."
                    )
                if existing['cutoff_column'] != selection['cutoff_column']:
                    raise DomainError('CUTOFF_CHANGED', 'Extraction retry cannot alter cutoff column.')
                if (existing['schema_name'] != selection['schema_name'] or
                        existing['table_name'] != selection['table_name'] or
                        existing['contract_id'] != selection['contract_id']):
                    raise DomainError('EXTRACTION_MISMATCH', 'Extraction retry cannot alter source target or contract.')

                next_attempt = max(attempt, existing['attempt'] + 1)
                data = dict(existing['data'])
                attempts = data.setdefault('attempts', [])
                attempts.append({
                    'attempt': next_attempt,
                    'started_at': now(),
                    'cutoff_value': selection['cutoff_value'],
                    'status': 'running'
                })

                conn.execute(
                    update(postgres_extractions)
                    .where(postgres_extractions.c.id == extraction_id)
                    .values(
                        attempt=next_attempt,
                        state='running',
                        started_at=now(),
                        error=None,
                        data=data
                    )
                )
                updated = conn.execute(
                    select(postgres_extractions).where(postgres_extractions.c.id == extraction_id)
                ).mappings().first()
                return dict(updated)
            else:
                data = {
                    'attempts': [{
                        'attempt': attempt,
                        'started_at': now(),
                        'cutoff_value': selection['cutoff_value'],
                        'status': 'running'
                    }]
                }
                record = {
                    'id': extraction_id,
                    'project_id': pid,
                    'state': 'running',
                    'attempt': attempt,
                    'cutoff_value': selection['cutoff_value'],
                    'cutoff_column': selection['cutoff_column'],
                    'source_sanitized': selection.get('source_sanitized', ''),
                    'schema_name': selection['schema_name'],
                    'table_name': selection['table_name'],
                    'contract_id': selection['contract_id'],
                    'mapping_id': selection.get('mapping_id'),
                    'snapshot_id': None,
                    'row_count': None,
                    'manifest_hash': None,
                    'error': None,
                    'created_at': now(),
                    'started_at': now(),
                    'completed_at': None,
                    'data': data
                }
                conn.execute(insert(postgres_extractions).values(**record))
                return record

    def update_extraction(
        self,
        extraction_id: str,
        state: str,
        snapshot_id: str | None = None,
        error: dict | None = None,
        row_count: int | None = None,
        manifest_hash: str | None = None
    ) -> dict:
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(postgres_extractions).where(postgres_extractions.c.id == extraction_id)
            ).mappings().first()
            if not existing:
                raise DomainError('NOT_FOUND', f"Extraction '{extraction_id}' not found.", 404)

            data = dict(existing['data'])
            attempts = data.setdefault('attempts', [])
            if attempts:
                attempts[-1]['status'] = state
                attempts[-1]['completed_at'] = now()
                if error:
                    attempts[-1]['error'] = error
                if row_count is not None:
                    attempts[-1]['row_count'] = row_count

            values: dict[str, Any] = {
                'state': state,
                'data': data
            }
            if state in ('succeeded', 'failed'):
                values['completed_at'] = now()
            if snapshot_id is not None:
                values['snapshot_id'] = snapshot_id
            if error is not None:
                values['error'] = error
            if row_count is not None:
                values['row_count'] = row_count
            if manifest_hash is not None:
                values['manifest_hash'] = manifest_hash

            conn.execute(
                update(postgres_extractions)
                .where(postgres_extractions.c.id == extraction_id)
                .values(**values)
            )
            updated = conn.execute(
                select(postgres_extractions).where(postgres_extractions.c.id == extraction_id)
            ).mappings().first()
            return dict(updated)

    def get_extraction(self, extraction_id: str, project_id: str | None = None) -> dict:
        with self.engine.connect() as conn:
            query = select(postgres_extractions).where(postgres_extractions.c.id == extraction_id)
            if project_id is not None:
                query = query.where(postgres_extractions.c.project_id == project_id)
            row = conn.execute(query).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"Extraction '{extraction_id}' not found.", 404)
            return dict(row)

    def list_extractions(self, project_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(postgres_extractions)
                .where(postgres_extractions.c.project_id == project_id)
                .order_by(postgres_extractions.c.created_at.desc())
            ).mappings().all()
            return [dict(r) for r in rows]

    # -----------------------------------------------------------------------
    # V5-01: Stable Metric Identity, Lifecycle Bindings & Change Sets
    # -----------------------------------------------------------------------

    def create_metric(self, display_name: str, importance: str = 'unclassified',
                      metric_id: str | None = None, project_id: str | None = None) -> dict:
        valid_importance = ('critical', 'important', 'standard', 'unclassified')
        if importance not in valid_importance:
            raise DomainError('INVALID_INPUT', f"Importance must be one of {valid_importance}, got '{importance}'.", 400)
        pid = project_id or self.project_id
        mid = metric_id or f"metric-{uuid4().hex}"
        created_time = now()

        with self.engine.begin() as conn:
            proj = conn.execute(select(projects).where(projects.c.id == pid)).first()
            if not proj:
                raise DomainError('NOT_FOUND', f"Project '{pid}' not found.", 404)
            existing = conn.execute(select(metrics).where(metrics.c.id == mid)).first()
            if existing:
                raise DomainError('CONFLICT', f"Metric with id '{mid}' already exists.", 409)

            conn.execute(insert(metrics).values(
                id=mid,
                project_id=pid,
                display_name=display_name,
                importance=importance,
                created_at=created_time,
                data={},
            ))

        return {
            'id': mid,
            'project_id': pid,
            'display_name': display_name,
            'importance': importance,
            'created_at': created_time,
            'data': {},
        }

    def get_metric(self, metric_id: str, project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            row = conn.execute(
                select(metrics)
                .where(metrics.c.id == metric_id, metrics.c.project_id == pid)
            ).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"Metric '{metric_id}' not found in project '{pid}'.", 404)

            active = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.metric_id == metric_id,
                       metric_definition_bindings.c.status == 'active')
            ).mappings().first()

            return {
                **dict(row),
                'active_binding': dict(active) if active else None,
            }

    def list_metrics(self, project_id: str | None = None) -> list[dict]:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(metrics)
                .where(metrics.c.project_id == pid)
                .order_by(metrics.c.created_at.asc())
            ).mappings().all()

            results = []
            for r in rows:
                active = conn.execute(
                    select(metric_definition_bindings)
                    .where(metric_definition_bindings.c.metric_id == r['id'],
                           metric_definition_bindings.c.status == 'active')
                ).mappings().first()
                results.append({
                    **dict(r),
                    'active_binding': dict(active) if active else None,
                })
            return results

    def update_metric(self, metric_id: str, display_name: str | None = None,
                      importance: str | None = None, project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        valid_importance = ('critical', 'important', 'standard', 'unclassified')
        if importance is not None and importance not in valid_importance:
            raise DomainError('INVALID_INPUT', f"Importance must be one of {valid_importance}, got '{importance}'.", 400)

        with self.engine.begin() as conn:
            row = conn.execute(
                select(metrics)
                .where(metrics.c.id == metric_id, metrics.c.project_id == pid)
            ).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', f"Metric '{metric_id}' not found in project '{pid}'.", 404)

            updates = {}
            if display_name is not None:
                updates['display_name'] = display_name
            if importance is not None:
                updates['importance'] = importance

            if updates:
                conn.execute(
                    update(metrics)
                    .where(metrics.c.id == metric_id)
                    .values(**updates)
                )

            updated = conn.execute(
                select(metrics).where(metrics.c.id == metric_id)
            ).mappings().first()

            active = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.metric_id == metric_id,
                       metric_definition_bindings.c.status == 'active')
            ).mappings().first()

            return {
                **dict(updated),
                'active_binding': dict(active) if active else None,
            }

    def attach_external_metric_reference(self, metric_id: str, provider: str,
                                         external_id: str, project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.begin() as conn:
            m = conn.execute(
                select(metrics)
                .where(metrics.c.id == metric_id, metrics.c.project_id == pid)
            ).mappings().first()
            if not m:
                raise DomainError('NOT_FOUND', f"Metric '{metric_id}' not found in project '{pid}'.", 404)

            existing = conn.execute(
                select(external_metric_references)
                .where(external_metric_references.c.project_id == pid,
                       external_metric_references.c.provider == provider,
                       external_metric_references.c.external_id == external_id)
            ).mappings().first()

            if existing:
                if existing['metric_id'] == metric_id:
                    return dict(existing)
                raise DomainError('CONFLICT', f"External reference '{provider}:{external_id}' is already mapped to metric '{existing['metric_id']}' in project '{pid}'.", 409)

            ref_id = f"ext-ref-{uuid4().hex}"
            created_time = now()
            rec = {
                'id': ref_id,
                'project_id': pid,
                'metric_id': metric_id,
                'provider': provider,
                'external_id': external_id,
                'created_at': created_time,
                'data': {},
            }
            conn.execute(insert(external_metric_references).values(**rec))
            return rec

    def list_external_metric_references(self, metric_id: str, project_id: str | None = None) -> list[dict]:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            m = conn.execute(
                select(metrics)
                .where(metrics.c.id == metric_id, metrics.c.project_id == pid)
            ).mappings().first()
            if not m:
                raise DomainError('NOT_FOUND', f"Metric '{metric_id}' not found in project '{pid}'.", 404)

            rows = conn.execute(
                select(external_metric_references)
                .where(external_metric_references.c.project_id == pid,
                       external_metric_references.c.metric_id == metric_id)
                .order_by(external_metric_references.c.created_at.asc())
            ).mappings().all()
            return [dict(r) for r in rows]

    def bind_metric_definition(self, metric_id: str, definition_id: str, status: str = 'candidate',
                               approved_version_id: str | None = None, project_id: str | None = None,
                               actor_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        if status not in ('candidate', 'active', 'retired'):
            raise DomainError('INVALID_INPUT', f"Status must be candidate, active, or retired, got '{status}'.", 400)

        # Active binding must go through promotion so immutable transition evidence is atomically recorded
        if status == 'active':
            return self.promote_metric_definition(
                metric_id=metric_id,
                definition_id=definition_id,
                approved_version_id=approved_version_id,
                project_id=pid,
                actor_id=actor_id
            )['binding']

        with self.engine.begin() as conn:
            m = conn.execute(
                select(metrics)
                .where(metrics.c.id == metric_id, metrics.c.project_id == pid)
            ).mappings().first()
            if not m:
                raise DomainError('NOT_FOUND', f"Metric '{metric_id}' not found in project '{pid}'.", 404)

            d = conn.execute(
                select(definitions)
                .where(definitions.c.id == definition_id)
            ).mappings().first()
            if not d:
                raise DomainError('NOT_FOUND', f"Definition '{definition_id}' not found.", 404)
            if d['project_id'] != pid:
                raise DomainError('CROSS_PROJECT_FORBIDDEN', f"Definition '{definition_id}' belongs to project '{d['project_id']}', not '{pid}'.", 400)

            if approved_version_id:
                v = conn.execute(
                    select(versions)
                    .where(versions.c.id == approved_version_id)
                ).mappings().first()
                if not v:
                    raise DomainError('NOT_FOUND', f"DefinitionVersion '{approved_version_id}' not found.", 404)
                if v['definition_id'] != definition_id:
                    raise DomainError('INVALID_BINDING', f"Version '{approved_version_id}' does not belong to definition '{definition_id}'.", 400)

            existing = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.project_id == pid,
                       metric_definition_bindings.c.metric_id == metric_id,
                       metric_definition_bindings.c.definition_id == definition_id)
            ).mappings().first()

            if existing:
                if existing['status'] == 'active' and status == 'candidate':
                    raise DomainError('INVALID_STATE', 'Definition is already actively bound to this metric. Cannot re-bind as candidate.', 400)
                updates = {'status': status}
                if approved_version_id:
                    updates['approved_version_id'] = approved_version_id
                if status == 'retired':
                    updates['retired_at'] = now()
                conn.execute(
                    update(metric_definition_bindings)
                    .where(metric_definition_bindings.c.id == existing['id'])
                    .values(**updates)
                )
                return {**dict(existing), **updates}

            binding_id = f"bind-{uuid4().hex}"
            created_time = now()
            rec = {
                'id': binding_id,
                'project_id': pid,
                'metric_id': metric_id,
                'definition_id': definition_id,
                'status': status,
                'approved_version_id': approved_version_id,
                'activated_at': None,
                'retired_at': now() if status == 'retired' else None,
                'created_at': created_time,
                'data': {},
            }
            conn.execute(insert(metric_definition_bindings).values(**rec))
            return rec

    def promote_metric_definition(self, metric_id: str, definition_id: str,
                                  approved_version_id: str | None = None,
                                  comparison_run_id: str | None = None,
                                  review_decision_id: str | None = None,
                                  actor_id: str | None = None,
                                  project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.begin() as conn:
            m = conn.execute(
                select(metrics)
                .where(metrics.c.id == metric_id, metrics.c.project_id == pid)
            ).mappings().first()
            if not m:
                raise DomainError('NOT_FOUND', f"Metric '{metric_id}' not found in project '{pid}'.", 404)

            d = conn.execute(
                select(definitions)
                .where(definitions.c.id == definition_id)
            ).mappings().first()
            if not d:
                raise DomainError('NOT_FOUND', f"Definition '{definition_id}' not found.", 404)
            if d['project_id'] != pid:
                raise DomainError('CROSS_PROJECT_FORBIDDEN', f"Definition '{definition_id}' belongs to project '{d['project_id']}', not '{pid}'.", 400)

            if comparison_run_id:
                run_row = conn.execute(select(runs).where(runs.c.id == comparison_run_id, runs.c.project_id == pid)).first()
                if not run_row:
                    raise DomainError('NOT_FOUND', f"Comparison run '{comparison_run_id}' not found in project '{pid}'.", 404)

            if review_decision_id:
                dec_row = conn.execute(select(decisions).where(decisions.c.id == review_decision_id, decisions.c.project_id == pid)).first()
                if not dec_row:
                    raise DomainError('NOT_FOUND', f"Decision '{review_decision_id}' not found in project '{pid}'.", 404)

            # Determine target approved_version_id
            target_version_id = approved_version_id
            if not target_version_id:
                cand = conn.execute(
                    select(metric_definition_bindings)
                    .where(metric_definition_bindings.c.project_id == pid,
                           metric_definition_bindings.c.metric_id == metric_id,
                           metric_definition_bindings.c.definition_id == definition_id)
                ).mappings().first()
                if cand and cand['approved_version_id']:
                    target_version_id = cand['approved_version_id']
                else:
                    latest_ver = conn.execute(
                        select(versions)
                        .where(versions.c.definition_id == definition_id)
                        .order_by(versions.c.version_number.desc())
                    ).mappings().first()
                    if not latest_ver:
                        raise DomainError('INVALID_STATE', f"Definition '{definition_id}' has no versions to approve.", 400)
                    target_version_id = latest_ver['id']

            v = conn.execute(select(versions).where(versions.c.id == target_version_id)).mappings().first()
            if not v:
                raise DomainError('NOT_FOUND', f"Version '{target_version_id}' not found.", 404)
            if v['definition_id'] != definition_id:
                raise DomainError('INVALID_BINDING', f"Version '{target_version_id}' does not belong to definition '{definition_id}'.", 400)

            current_active = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.project_id == pid,
                       metric_definition_bindings.c.metric_id == metric_id,
                       metric_definition_bindings.c.status == 'active')
            ).mappings().first()

            trans_time = now()
            if current_active and current_active['definition_id'] == definition_id:
                prev_ver_id = current_active['approved_version_id']
                conn.execute(
                    update(metric_definition_bindings)
                    .where(metric_definition_bindings.c.id == current_active['id'])
                    .values(approved_version_id=target_version_id)
                )
                updated_binding = {
                    **dict(current_active),
                    'approved_version_id': target_version_id,
                }
                transition_id = f"mst-{uuid4().hex}"
                transition_record = {
                    'id': transition_id,
                    'project_id': pid,
                    'metric_id': metric_id,
                    'transition_kind': 'version_approved',
                    'definition_id': definition_id,
                    'approved_version_id': target_version_id,
                    'previous_definition_id': definition_id,
                    'previous_approved_version_id': prev_ver_id,
                    'comparison_run_id': comparison_run_id,
                    'review_decision_id': review_decision_id,
                    'actor_id': actor_id or 'system',
                    'metadata': {},
                    'created_at': trans_time,
                }
                conn.execute(insert(metric_state_transitions).values(**transition_record))
                return {
                    'binding': updated_binding,
                    'transition': transition_record,
                }

            if current_active:
                prev_defn_id = current_active['definition_id']
                prev_ver_id = current_active['approved_version_id']
                conn.execute(
                    update(metric_definition_bindings)
                    .where(metric_definition_bindings.c.id == current_active['id'])
                    .values(status='retired', retired_at=trans_time)
                )
            else:
                prev_defn_id = None
                prev_ver_id = None

            candidate = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.project_id == pid,
                       metric_definition_bindings.c.metric_id == metric_id,
                       metric_definition_bindings.c.definition_id == definition_id)
            ).mappings().first()

            if candidate:
                conn.execute(
                    update(metric_definition_bindings)
                    .where(metric_definition_bindings.c.id == candidate['id'])
                    .values(
                        status='active',
                        approved_version_id=target_version_id,
                        activated_at=trans_time,
                        retired_at=None,
                    )
                )
                active_binding = {
                    **dict(candidate),
                    'status': 'active',
                    'approved_version_id': target_version_id,
                    'activated_at': trans_time,
                    'retired_at': None,
                }
            else:
                binding_id = f"bind-{uuid4().hex}"
                active_binding = {
                    'id': binding_id,
                    'project_id': pid,
                    'metric_id': metric_id,
                    'definition_id': definition_id,
                    'status': 'active',
                    'approved_version_id': target_version_id,
                    'activated_at': trans_time,
                    'retired_at': None,
                    'created_at': trans_time,
                    'data': {},
                }
                conn.execute(insert(metric_definition_bindings).values(**active_binding))

            transition_id = f"mst-{uuid4().hex}"
            transition_record = {
                'id': transition_id,
                'project_id': pid,
                'metric_id': metric_id,
                'transition_kind': 'definition_promoted',
                'definition_id': definition_id,
                'approved_version_id': target_version_id,
                'previous_definition_id': prev_defn_id,
                'previous_approved_version_id': prev_ver_id,
                'comparison_run_id': comparison_run_id,
                'review_decision_id': review_decision_id,
                'actor_id': actor_id or 'system',
                'metadata': {},
                'created_at': trans_time,
            }
            conn.execute(insert(metric_state_transitions).values(**transition_record))

            return {
                'binding': active_binding,
                'transition': transition_record,
            }

    def approve_metric_version(self, metric_id: str, approved_version_id: str,
                               comparison_run_id: str | None = None,
                               review_decision_id: str | None = None,
                               actor_id: str | None = None,
                               project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.begin() as conn:
            m = conn.execute(
                select(metrics)
                .where(metrics.c.id == metric_id, metrics.c.project_id == pid)
            ).mappings().first()
            if not m:
                raise DomainError('NOT_FOUND', f"Metric '{metric_id}' not found in project '{pid}'.", 404)

            active = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.project_id == pid,
                       metric_definition_bindings.c.metric_id == metric_id,
                       metric_definition_bindings.c.status == 'active')
            ).mappings().first()
            if not active:
                raise DomainError('INVALID_STATE', 'Metric has no active definition binding to approve a version for.', 400)

            v = conn.execute(select(versions).where(versions.c.id == approved_version_id)).mappings().first()
            if not v:
                raise DomainError('NOT_FOUND', f"DefinitionVersion '{approved_version_id}' not found.", 404)
            if v['definition_id'] != active['definition_id']:
                raise DomainError('INVALID_BINDING', f"Approved version '{approved_version_id}' does not belong to active definition '{active['definition_id']}'.", 400)

            if comparison_run_id:
                run_row = conn.execute(select(runs).where(runs.c.id == comparison_run_id, runs.c.project_id == pid)).first()
                if not run_row:
                    raise DomainError('NOT_FOUND', f"Comparison run '{comparison_run_id}' not found in project '{pid}'.", 404)

            if review_decision_id:
                dec_row = conn.execute(select(decisions).where(decisions.c.id == review_decision_id, decisions.c.project_id == pid)).first()
                if not dec_row:
                    raise DomainError('NOT_FOUND', f"Decision '{review_decision_id}' not found in project '{pid}'.", 404)

            trans_time = now()
            prev_ver_id = active['approved_version_id']
            conn.execute(
                update(metric_definition_bindings)
                .where(metric_definition_bindings.c.id == active['id'])
                .values(approved_version_id=approved_version_id)
            )
            updated_binding = {
                **dict(active),
                'approved_version_id': approved_version_id,
            }

            transition_id = f"mst-{uuid4().hex}"
            transition_record = {
                'id': transition_id,
                'project_id': pid,
                'metric_id': metric_id,
                'transition_kind': 'version_approved',
                'definition_id': active['definition_id'],
                'approved_version_id': approved_version_id,
                'previous_definition_id': active['definition_id'],
                'previous_approved_version_id': prev_ver_id,
                'comparison_run_id': comparison_run_id,
                'review_decision_id': review_decision_id,
                'actor_id': actor_id or 'system',
                'metadata': {},
                'created_at': trans_time,
            }
            conn.execute(insert(metric_state_transitions).values(**transition_record))

            return {
                'binding': updated_binding,
                'transition': transition_record,
            }

    def list_metric_definition_bindings(self, metric_id: str, project_id: str | None = None) -> list[dict]:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            m = conn.execute(
                select(metrics)
                .where(metrics.c.id == metric_id, metrics.c.project_id == pid)
            ).mappings().first()
            if not m:
                raise DomainError('NOT_FOUND', f"Metric '{metric_id}' not found in project '{pid}'.", 404)

            rows = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.project_id == pid,
                       metric_definition_bindings.c.metric_id == metric_id)
                .order_by(metric_definition_bindings.c.created_at.asc())
            ).mappings().all()
            return [dict(r) for r in rows]

    def list_metric_state_transitions(self, metric_id: str, project_id: str | None = None) -> list[dict]:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            m = conn.execute(
                select(metrics)
                .where(metrics.c.id == metric_id, metrics.c.project_id == pid)
            ).mappings().first()
            if not m:
                raise DomainError('NOT_FOUND', f"Metric '{metric_id}' not found in project '{pid}'.", 404)

            rows = conn.execute(
                select(metric_state_transitions)
                .where(metric_state_transitions.c.project_id == pid,
                       metric_state_transitions.c.metric_id == metric_id)
                .order_by(metric_state_transitions.c.created_at.desc())
            ).mappings().all()
            return [dict(r) for r in rows]

    def compute_dbt_change_set(self, baseline_revision_id: str, target_revision_id: str,
                               project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            b_rev = conn.execute(
                select(dbt_import_revisions)
                .where(dbt_import_revisions.c.id == baseline_revision_id,
                       dbt_import_revisions.c.project_id == pid)
            ).mappings().first()
            if not b_rev:
                raise DomainError('NOT_FOUND', f"Baseline dbt import revision '{baseline_revision_id}' not found in project '{pid}'.", 404)

            t_rev = conn.execute(
                select(dbt_import_revisions)
                .where(dbt_import_revisions.c.id == target_revision_id,
                       dbt_import_revisions.c.project_id == pid)
            ).mappings().first()
            if not t_rev:
                raise DomainError('NOT_FOUND', f"Target dbt import revision '{target_revision_id}' not found in project '{pid}'.", 404)

            b_res_rows = conn.execute(
                select(dbt_resource_revisions)
                .where(dbt_resource_revisions.c.import_revision_id == baseline_revision_id)
            ).mappings().all()
            baseline_map = {r['unique_id']: dict(r) for r in b_res_rows}

            t_res_rows = conn.execute(
                select(dbt_resource_revisions)
                .where(dbt_resource_revisions.c.import_revision_id == target_revision_id)
            ).mappings().all()
            target_map = {r['unique_id']: dict(r) for r in t_res_rows}

            metrics_rows = conn.execute(
                select(metrics)
                .where(metrics.c.project_id == pid)
            ).mappings().all()
            metrics_by_id = {m['id']: dict(m) for m in metrics_rows}

            ext_refs = conn.execute(
                select(external_metric_references)
                .where(external_metric_references.c.project_id == pid,
                       external_metric_references.c.provider == 'dbt')
            ).mappings().all()
            metric_id_by_external_id = {ref['external_id']: ref['metric_id'] for ref in ext_refs}

            active_bindings = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.project_id == pid,
                       metric_definition_bindings.c.status == 'active')
            ).mappings().all()
            metric_id_by_definition_id = {b['definition_id']: b['metric_id'] for b in active_bindings}

            def _get_metric_for_resource(uid: str, defn_id: str | None) -> dict | None:
                m_id = metric_id_by_external_id.get(uid)
                if not m_id and defn_id:
                    m_id = metric_id_by_definition_id.get(defn_id)
                if m_id and m_id in metrics_by_id:
                    m = metrics_by_id[m_id]
                    return {
                        'id': m['id'],
                        'display_name': m['display_name'],
                        'importance': m['importance'],
                    }
                return None

            def _sql_hash(sql_text: str | None) -> str | None:
                if not sql_text:
                    return None
                return hashlib.sha256(sql_text.encode('utf-8')).hexdigest()

            def _norm_lineage(lin: dict | None) -> dict:
                if not lin or not isinstance(lin, dict):
                    return {'upstream_nodes': [], 'downstream_nodes': []}
                return {
                    'upstream_nodes': sorted(lin.get('upstream_nodes') or []),
                    'downstream_nodes': sorted(lin.get('downstream_nodes') or []),
                }

            all_uids = sorted(set(baseline_map.keys()) | set(target_map.keys()))
            resources = []

            for uid in all_uids:
                b = baseline_map.get(uid)
                t = target_map.get(uid)

                if b is None and t is not None:
                    t_sql = t['data'].get('compiled_sql')
                    t_hash = _sql_hash(t_sql)
                    defn_id = t.get('definition_id')
                    metric_info = _get_metric_for_resource(uid, defn_id)

                    resources.append({
                        'unique_id': uid,
                        'resource_type': t['resource_type'],
                        'name': t['name'],
                        'change_category': 'added',
                        'change_dimensions': ['added'],
                        'summary_label': f"{metric_info['display_name']} definition added" if metric_info else f"{uid} added",
                        'old_definition_id': None,
                        'new_definition_id': defn_id,
                        'old_definition_version_id': None,
                        'new_definition_version_id': t.get('definition_version_id'),
                        'old_compiled_sql_hash': None,
                        'new_compiled_sql_hash': t_hash,
                        'old_support_state': None,
                        'new_support_state': t['support_state'],
                        'old_diagnostics': [],
                        'new_diagnostics': t.get('diagnostics') or [],
                        'diagnostics': t.get('diagnostics') or [],
                        'old_lineage': None,
                        'new_lineage': t['observed_lineage'],
                        'old_metadata': None,
                        'new_metadata': {
                            'meta': t['data'].get('meta', {}),
                            'tags': sorted(t['data'].get('tags', [])),
                            'materialized': t['data'].get('materialized', 'model'),
                            'original_file_path': t['data'].get('original_file_path', ''),
                        },
                        'metric': metric_info,
                    })

                elif b is not None and t is None:
                    b_sql = b['data'].get('compiled_sql')
                    b_hash = _sql_hash(b_sql)
                    defn_id = b.get('definition_id')
                    metric_info = _get_metric_for_resource(uid, defn_id)

                    resources.append({
                        'unique_id': uid,
                        'resource_type': b['resource_type'],
                        'name': b['name'],
                        'change_category': 'removed',
                        'change_dimensions': ['removed'],
                        'summary_label': f"{metric_info['display_name']} definition removed" if metric_info else f"{uid} removed",
                        'old_definition_id': defn_id,
                        'new_definition_id': None,
                        'old_definition_version_id': b.get('definition_version_id'),
                        'new_definition_version_id': None,
                        'old_compiled_sql_hash': b_hash,
                        'new_compiled_sql_hash': None,
                        'old_support_state': b['support_state'],
                        'new_support_state': None,
                        'old_diagnostics': b.get('diagnostics') or [],
                        'new_diagnostics': [],
                        'diagnostics': b.get('diagnostics') or [],
                        'old_lineage': b['observed_lineage'],
                        'new_lineage': None,
                        'old_metadata': {
                            'meta': b['data'].get('meta', {}),
                            'tags': sorted(b['data'].get('tags', [])),
                            'materialized': b['data'].get('materialized', 'model'),
                            'original_file_path': b['data'].get('original_file_path', ''),
                        },
                        'new_metadata': None,
                        'metric': metric_info,
                    })

                else:
                    b_sql = b['data'].get('compiled_sql')
                    t_sql = t['data'].get('compiled_sql')
                    b_hash = _sql_hash(b_sql)
                    t_hash = _sql_hash(t_sql)

                    b_lin_norm = _norm_lineage(b.get('observed_lineage'))
                    t_lin_norm = _norm_lineage(t.get('observed_lineage'))

                    b_meta = {
                        'meta': b['data'].get('meta', {}),
                        'tags': sorted(b['data'].get('tags', [])),
                        'materialized': b['data'].get('materialized', 'model'),
                        'original_file_path': b['data'].get('original_file_path', ''),
                    }
                    t_meta = {
                        'meta': t['data'].get('meta', {}),
                        'tags': sorted(t['data'].get('tags', [])),
                        'materialized': t['data'].get('materialized', 'model'),
                        'original_file_path': t['data'].get('original_file_path', ''),
                    }

                    dimensions = []
                    if b_hash != t_hash:
                        dimensions.append('compiled_sql_changed')
                    if b_lin_norm != t_lin_norm:
                        dimensions.append('lineage_changed')
                    if b_meta != t_meta or b['name'] != t['name']:
                        dimensions.append('metadata_changed')
                    if b['support_state'] != t['support_state']:
                        dimensions.append('support_state_changed')

                    defn_id = t.get('definition_id') or b.get('definition_id')
                    metric_info = _get_metric_for_resource(uid, defn_id)

                    if dimensions:
                        change_category = 'modified'
                        summary_label = f"{metric_info['display_name']} definition changed" if metric_info else f"{uid} changed"
                    else:
                        change_category = 'unchanged'
                        summary_label = f"{metric_info['display_name']} definition unchanged" if metric_info else f"{uid} unchanged"

                    resources.append({
                        'unique_id': uid,
                        'resource_type': t['resource_type'],
                        'name': t['name'],
                        'change_category': change_category,
                        'change_dimensions': sorted(dimensions),
                        'summary_label': summary_label,
                        'old_definition_id': b.get('definition_id'),
                        'new_definition_id': t.get('definition_id'),
                        'old_definition_version_id': b.get('definition_version_id'),
                        'new_definition_version_id': t.get('definition_version_id'),
                        'old_compiled_sql_hash': b_hash,
                        'new_compiled_sql_hash': t_hash,
                        'old_support_state': b['support_state'],
                        'new_support_state': t['support_state'],
                        'old_diagnostics': b.get('diagnostics') or [],
                        'new_diagnostics': t.get('diagnostics') or [],
                        'diagnostics': t.get('diagnostics') or b.get('diagnostics') or [],
                        'old_lineage': b['observed_lineage'],
                        'new_lineage': t['observed_lineage'],
                        'old_metadata': b_meta,
                        'new_metadata': t_meta,
                        'metric': metric_info,
                    })

            counts = {
                'total_resources': len(resources),
                'changed_count': sum(1 for r in resources if r['change_category'] == 'modified'),
                'unchanged_count': sum(1 for r in resources if r['change_category'] == 'unchanged'),
                'added_count': sum(1 for r in resources if r['change_category'] == 'added'),
                'removed_count': sum(1 for r in resources if r['change_category'] == 'removed'),
            }

            return {
                'schema_version': 'metricguard_change_set_v1',
                'project_id': pid,
                'baseline_revision_id': baseline_revision_id,
                'target_revision_id': target_revision_id,
                'baseline_manifest_hash': b_rev['manifest_hash'],
                'target_manifest_hash': t_rev['manifest_hash'],
                'baseline_source_revision': b_rev.get('source_revision', {}),
                'target_source_revision': t_rev.get('source_revision', {}),
                'counts': counts,
                'resources': resources,
            }

    @staticmethod
    def _build_impact_context(manifest: dict, user_context: dict | None, baseline_ir: dict, target_ir: dict) -> dict:
        ctx = dict(user_context or {})
        if 'timezone' not in ctx:
            ctx['timezone'] = manifest.get('timezone') or manifest.get('business_timezone') or 'Asia/Kolkata'
        unit_label = ctx.get('unit_label') or manifest.get('unit_label') or manifest.get('unit')
        if unit_label and str(unit_label).lower() in ('count', 'units', 'records', 'items'):
            ctx['currency'] = None
            ctx['unit_label'] = unit_label
            ctx['unit'] = 'count'
        else:
            if 'currency' not in ctx:
                cur = manifest.get('currency')
                if not cur and unit_label and str(unit_label).lower() in ('inr', 'usd', 'eur', 'gbp'):
                    cur = str(unit_label).upper()
                elif not cur and manifest.get('grain') == 'one_row_per_order_id':
                    cur = 'INR'
                ctx['currency'] = cur
            if 'unit' not in ctx:
                ctx['unit'] = 'currency' if baseline_ir.get('aggregate') == 'sum' else 'count'
        if 'grain' not in ctx:
            ctx['grain'] = manifest.get('grain') or 'one_row_per_order_id'
        if 'scope' not in ctx:
            ctx['scope'] = manifest.get('scope') or 'synthetic_retail_all_orders'
        if 'contract_hash' not in ctx and manifest.get('contract_hash'):
            ctx['contract_hash'] = manifest['contract_hash']
        if 'mapping_hash' not in ctx and manifest.get('mapping_hash'):
            ctx['mapping_hash'] = manifest['mapping_hash']
        if 'manifest_hash' not in ctx and manifest.get('manifest_hash'):
            ctx['manifest_hash'] = manifest['manifest_hash']
        if 'source_hash' not in ctx and manifest.get('source_hash'):
            ctx['source_hash'] = manifest['source_hash']

        if 'unit' not in ctx:
            ctx['unit'] = 'currency' if baseline_ir.get('aggregate') == 'sum' else 'count'

        ir_period = baseline_ir.get('period') or target_ir.get('period')
        if ir_period and isinstance(ir_period, dict) and ir_period.get('start') and ir_period.get('end_exclusive'):
            if 'start' not in ctx or not user_context:
                ctx['start'] = ir_period['start']
            if 'end_exclusive' not in ctx or not user_context:
                ctx['end_exclusive'] = ir_period['end_exclusive']
        else:
            if 'start' not in ctx:
                ctx['start'] = '2026-08-01'
            if 'end_exclusive' not in ctx:
                ctx['end_exclusive'] = '2026-09-01'

        return ctx

    def _run_controlled_comparison(self, pid: str, baseline_version_id: str,
                                   target_version_id: str, snapshot_row: dict,
                                   snapshot_manifest: dict, context: dict | None,
                                   materiality_policy: dict | None) -> dict:
        from packages.metricguard_core.engine import compare, validate_context
        from packages.metricguard_core.common import CoreError
        from services.api.monitoring import evaluate_materiality

        b_ver = self.version(baseline_version_id)
        t_ver = self.version(target_version_id)

        b_sup = b_ver.get('support_state', 'unsupported')
        t_sup = t_ver.get('support_state', 'unsupported')

        if b_sup != 'executable':
            return {
                'execution_status': 'refused',
                'support_state': b_sup,
                'refusal_reason': b_ver.get('support_reason') or f"Baseline definition version is {b_sup}.",
                'diagnostics': b_ver.get('diagnostics') or {},
                'baseline_version_id': baseline_version_id,
                'target_version_id': target_version_id,
                'baseline_value': None,
                'target_value': None,
                'exact_delta': None,
                'absolute_delta': None,
                'relative_change_percent': None,
                'residual': None,
                'run_id': None,
                'materiality': None,
                'evidence_summary': None,
            }

        if t_sup != 'executable':
            return {
                'execution_status': 'refused',
                'support_state': t_sup,
                'refusal_reason': t_ver.get('support_reason') or f"Target definition version is {t_sup}.",
                'diagnostics': t_ver.get('diagnostics') or {},
                'baseline_version_id': baseline_version_id,
                'target_version_id': target_version_id,
                'baseline_value': None,
                'target_value': None,
                'exact_delta': None,
                'absolute_delta': None,
                'relative_change_percent': None,
                'residual': None,
                'run_id': None,
                'materiality': None,
                'evidence_summary': None,
            }

        b_ir = b_ver.get('ir')
        t_ir = t_ver.get('ir')
        if not b_ir or not t_ir:
            return {
                'execution_status': 'refused',
                'support_state': 'metadata_only',
                'refusal_reason': 'Definition IR unavailable for execution.',
                'diagnostics': {},
                'baseline_version_id': baseline_version_id,
                'target_version_id': target_version_id,
                'baseline_value': None,
                'target_value': None,
                'exact_delta': None,
                'absolute_delta': None,
                'relative_change_percent': None,
                'residual': None,
                'run_id': None,
                'materiality': None,
                'evidence_summary': None,
            }

        full_ctx = self._build_impact_context(snapshot_manifest, context, b_ir, t_ir)
        artifact_path = snapshot_row['data']['artifact_path']
        durable.ensure(artifact_path)

        try:
            validate_context(artifact_path, b_ir, t_ir, full_ctx)
        except CoreError as err:
            return {
                'execution_status': 'refused',
                'support_state': err.code,
                'refusal_reason': err.message,
                'diagnostics': {'error_code': err.code},
                'baseline_version_id': baseline_version_id,
                'target_version_id': target_version_id,
                'baseline_value': None,
                'target_value': None,
                'exact_delta': None,
                'absolute_delta': None,
                'relative_change_percent': None,
                'residual': None,
                'run_id': None,
                'materiality': None,
                'evidence_summary': None,
            }

        raw_result = compare(artifact_path, b_ir, t_ir, full_ctx)

        delta_str = raw_result.get('delta_target_minus_baseline')
        base_val_str = raw_result.get('baseline_value')
        target_val_str = raw_result.get('target_value')

        abs_thresh = materiality_policy.get('absolute_threshold') if materiality_policy else None
        rel_thresh = materiality_policy.get('relative_threshold') if materiality_policy else None

        materiality = evaluate_materiality(
            delta_str=delta_str,
            baseline_val_str=base_val_str,
            target_val_str=target_val_str,
            absolute_threshold=abs_thresh,
            relative_threshold=rel_thresh,
            exact_ratio=raw_result.get('ratio'),
        )

        run_id = f"run-impact-{uuid4().hex}"
        request_payload = {
            'baseline_version_id': baseline_version_id,
            'target_version_id': target_version_id,
            'snapshot_id': snapshot_row['id'],
            'context': full_ctx,
            'relationship': 'definition_change',
            'purpose': 'dbt_metric_impact'
        }
        run_digest = fingerprint(request_payload)
        idempotency_key = f"impact-run-{run_id}"

        with self.engine.begin() as conn:
            conn.execute(insert(runs).values(
                id=run_id,
                project_id=pid,
                baseline_version_id=baseline_version_id,
                target_version_id=target_version_id,
                snapshot_id=snapshot_row['id'],
                idempotency_key=idempotency_key,
                request_hash=run_digest,
                state='succeeded' if raw_result.get('explanation_status') == 'complete_ordered_bridge' else 'partial',
                execution_mode='inline_snapshot',
                attempt=1,
                cancel_requested=False,
                created_at=now(),
                started_at=now(),
                completed_at=now(),
                request=request_payload,
                result=raw_result,
                error=None
            ))

        evidence = raw_result.get('evidence', [])
        ev_summary = {
            'total_records': len(evidence),
            'included_in_both': sum(1 for e in evidence if e.get('membership') == 'included_in_both'),
            'baseline_only': sum(1 for e in evidence if e.get('membership') == 'baseline_only'),
            'target_only': sum(1 for e in evidence if e.get('membership') == 'target_only'),
            'included_in_neither': sum(1 for e in evidence if e.get('membership') == 'included_in_neither'),
        }

        return {
            'execution_status': 'executed',
            'support_state': 'executable',
            'refusal_reason': None,
            'diagnostics': None,
            'baseline_version_id': baseline_version_id,
            'target_version_id': target_version_id,
            'baseline_value': base_val_str,
            'target_value': target_val_str,
            'exact_delta': delta_str,
            'absolute_delta': raw_result.get('absolute_gap'),
            'relative_change_percent': raw_result.get('relative_change_percent_display'),
            'residual': raw_result.get('residual'),
            'run_id': run_id,
            'materiality': materiality,
            'evidence_summary': ev_summary,
            'bridge_steps': raw_result.get('steps', []),
            'shapley': raw_result.get('shapley', {}),
        }

    def execute_metric_impact(self, baseline_revision_id: str, target_revision_id: str,
                              snapshot_id: str, context: dict | None = None,
                              materiality_policy: dict | None = None,
                              project_id: str | None = None) -> dict:
        from packages.metricguard_core.snapshot import inspect_snapshot
        from packages.metricguard_core.common import CoreError

        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            b_rev = conn.execute(
                select(dbt_import_revisions)
                .where(dbt_import_revisions.c.id == baseline_revision_id,
                       dbt_import_revisions.c.project_id == pid)
            ).mappings().first()
            if not b_rev:
                other = conn.execute(select(dbt_import_revisions.c.project_id).where(dbt_import_revisions.c.id == baseline_revision_id)).scalar()
                if other:
                    raise DomainError('FORBIDDEN', f"Baseline dbt import revision '{baseline_revision_id}' belongs to project '{other}', not '{pid}'.", 403)
                raise DomainError('NOT_FOUND', f"Baseline dbt import revision '{baseline_revision_id}' not found in project '{pid}'.", 404)

            t_rev = conn.execute(
                select(dbt_import_revisions)
                .where(dbt_import_revisions.c.id == target_revision_id,
                       dbt_import_revisions.c.project_id == pid)
            ).mappings().first()
            if not t_rev:
                other = conn.execute(select(dbt_import_revisions.c.project_id).where(dbt_import_revisions.c.id == target_revision_id)).scalar()
                if other:
                    raise DomainError('FORBIDDEN', f"Target dbt import revision '{target_revision_id}' belongs to project '{other}', not '{pid}'.", 403)
                raise DomainError('NOT_FOUND', f"Target dbt import revision '{target_revision_id}' not found in project '{pid}'.", 404)

            snap_row = conn.execute(
                select(snapshots)
                .where(snapshots.c.id == snapshot_id,
                       snapshots.c.project_id == pid)
            ).mappings().first()
            if not snap_row:
                other = conn.execute(select(snapshots.c.project_id).where(snapshots.c.id == snapshot_id)).scalar()
                if other:
                    raise DomainError('FORBIDDEN', f"Snapshot '{snapshot_id}' belongs to project '{other}', not '{pid}'.", 403)
                raise DomainError('NOT_FOUND', f"Snapshot '{snapshot_id}' not found in project '{pid}'.", 404)

            artifact_path = snap_row['data'].get('artifact_path')
            if not durable.ensure(artifact_path):
                raise DomainError('SNAPSHOT_MISSING', f"Snapshot file for '{snapshot_id}' is missing.", 404)

            try:
                snap_manifest = inspect_snapshot(artifact_path)
            except CoreError as err:
                raise DomainError('SNAPSHOT_CORRUPTED', f"Snapshot '{snapshot_id}' integrity check failed: {err.message}", 400)

            all_bindings_rows = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.project_id == pid)
            ).mappings().all()

            bindings_fingerprint = sorted([
                (b['metric_id'], b['definition_id'], b['status'], b.get('approved_version_id'))
                for b in all_bindings_rows
            ])
            req_hash_data = {
                'pid': pid,
                'baseline_revision_id': baseline_revision_id,
                'target_revision_id': target_revision_id,
                'snapshot_id': snapshot_id,
                'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
                'context': context,
                'materiality_policy': materiality_policy,
                'bindings': bindings_fingerprint,
            }
            req_hash = fingerprint(req_hash_data)

            existing_report = conn.execute(
                select(metric_impact_reports)
                .where(metric_impact_reports.c.project_id == pid,
                       metric_impact_reports.c.request_hash == req_hash)
            ).mappings().first()
            if existing_report:
                return existing_report['data']

            # Change set computation
            change_set = self.compute_dbt_change_set(
                baseline_revision_id=baseline_revision_id,
                target_revision_id=target_revision_id,
                project_id=pid
            )

            metrics_rows = conn.execute(
                select(metrics).where(metrics.c.project_id == pid)
            ).mappings().all()
            metrics_by_id = {m['id']: dict(m) for m in metrics_rows}

            ext_refs = conn.execute(
                select(external_metric_references)
                .where(external_metric_references.c.project_id == pid,
                       external_metric_references.c.provider == 'dbt')
            ).mappings().all()
            metric_id_by_ext_id = {r['external_id']: r['metric_id'] for r in ext_refs}

            bindings_by_metric = {}
            bindings_by_defn = {}
            for b in all_bindings_rows:
                b_dict = dict(b)
                bindings_by_metric.setdefault(b_dict['metric_id'], []).append(b_dict)
                bindings_by_defn.setdefault(b_dict['definition_id'], []).append(b_dict)

        changed_resources = [r for r in change_set['resources'] if r['change_category'] != 'unchanged']
        undeclared_resources = []
        metrics_evaluated = {}
        candidate_metrics_evaluated = {}

        for res in changed_resources:
            uid = res['unique_id']
            old_def_id = res['old_definition_id']
            new_def_id = res['new_definition_id']

            attached_metric_ids = set()
            if uid in metric_id_by_ext_id:
                attached_metric_ids.add(metric_id_by_ext_id[uid])
            for d_id in (old_def_id, new_def_id):
                if d_id and d_id in bindings_by_defn:
                    for b in bindings_by_defn[d_id]:
                        attached_metric_ids.add(b['metric_id'])
            if res.get('metric') and res['metric'].get('id'):
                attached_metric_ids.add(res['metric']['id'])

            if not attached_metric_ids:
                undeclared_resources.append({
                    'unique_id': uid,
                    'resource_type': res['resource_type'],
                    'name': res['name'],
                    'change_category': res['change_category'],
                    'change_dimensions': res['change_dimensions'],
                    'old_definition_version_id': res['old_definition_version_id'],
                    'new_definition_version_id': res['new_definition_version_id'],
                    'support_state': res['new_support_state'] or res['old_support_state'],
                    'reason': 'UNDECLARED_METRIC'
                })
                continue

            for m_id in attached_metric_ids:
                if m_id not in metrics_by_id:
                    continue
                m_info = metrics_by_id[m_id]
                m_bindings = bindings_by_metric.get(m_id, [])
                active_binding = next((b for b in m_bindings if b['status'] == 'active'), None)
                candidate_bindings = [b for b in m_bindings if b['status'] == 'candidate']

                # Check if changed resource maps to candidate definition
                is_candidate = False
                matched_candidate_binding = None
                for cb in candidate_bindings:
                    if cb['definition_id'] in (old_def_id, new_def_id):
                        is_candidate = True
                        matched_candidate_binding = cb
                        break

                if is_candidate and matched_candidate_binding:
                    cand_key = (m_id, matched_candidate_binding['definition_id'])
                    if cand_key in candidate_metrics_evaluated:
                        continue

                    active_base_ver_id = None
                    if active_binding:
                        if active_binding.get('approved_version_id'):
                            active_base_ver_id = active_binding['approved_version_id']
                        else:
                            with self.engine.connect() as conn:
                                latest_active = conn.execute(
                                    select(versions)
                                    .where(versions.c.definition_id == active_binding['definition_id'])
                                    .order_by(versions.c.version_number.desc())
                                ).mappings().first()
                                if latest_active:
                                    active_base_ver_id = latest_active['id']

                    cand_target_ver_id = res['new_definition_version_id']

                    if not active_base_ver_id or not cand_target_ver_id:
                        sup_st = 'unsupported'
                        refusal_reason = 'Active baseline or candidate target version unavailable.'
                        diag_info = {}
                        if not cand_target_ver_id:
                            if res.get('new_support_state') in ('metadata_only', 'needs_context', 'unsupported'):
                                sup_st = res['new_support_state']
                                new_diags = res.get('new_diagnostics') or res.get('diagnostics') or []
                                refusal_reason = new_diags[0] if new_diags else f"Candidate target definition has support state '{sup_st}'."
                                diag_info = {'target': new_diags}
                            else:
                                refusal_reason = 'Candidate target definition version unavailable (resource removed).'
                        elif not active_base_ver_id:
                            refusal_reason = 'Active baseline definition has no approved version or baseline version.'

                        cand_impact = {
                            'execution_status': 'refused',
                            'support_state': sup_st,
                            'refusal_reason': refusal_reason,
                            'diagnostics': diag_info,
                            'baseline_version_id': active_base_ver_id,
                            'target_version_id': cand_target_ver_id,
                            'baseline_value': None,
                            'target_value': None,
                            'exact_delta': None,
                            'absolute_delta': None,
                            'relative_change_percent': None,
                            'residual': None,
                            'run_id': None,
                            'materiality': None,
                            'evidence_summary': None,
                        }
                    else:
                        cand_impact = self._run_controlled_comparison(
                            pid=pid,
                            baseline_version_id=active_base_ver_id,
                            target_version_id=cand_target_ver_id,
                            snapshot_row=snap_row,
                            snapshot_manifest=snap_manifest,
                            context=context,
                            materiality_policy=materiality_policy
                        )

                    candidate_metrics_evaluated[cand_key] = {
                        'metric_id': m_id,
                        'display_name': m_info['display_name'],
                        'importance': m_info['importance'],
                        'comparison_type': 'candidate_vs_active',
                        'active_definition_id': active_binding['definition_id'] if active_binding else None,
                        'candidate_definition_id': matched_candidate_binding['definition_id'],
                        'has_approved_baseline': bool(active_binding and active_binding.get('approved_version_id')),
                        'approved_version_id': active_binding.get('approved_version_id') if active_binding else None,
                        'baseline_revision_version_id': res['old_definition_version_id'],
                        'target_version_id': cand_target_ver_id,
                        'reference_snapshot_id': snapshot_id,
                        'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
                        'comparison_run_id': cand_impact.get('run_id'),
                        'baseline_value': cand_impact.get('baseline_value'),
                        'target_value': cand_impact.get('target_value'),
                        'exact_delta': cand_impact.get('exact_delta'),
                        'absolute_delta': cand_impact.get('absolute_delta'),
                        'relative_change_percent': cand_impact.get('relative_change_percent'),
                        'residual': cand_impact.get('residual'),
                        'materiality': cand_impact.get('materiality'),
                        'support_state': cand_impact.get('support_state'),
                        'execution_status': cand_impact.get('execution_status'),
                        'refusal_reason': cand_impact.get('refusal_reason'),
                        'diagnostics': cand_impact.get('diagnostics'),
                        'primary_impact': cand_impact,
                        'approved_baseline_impact': None,
                        'incremental_impact': cand_impact,
                    }
                    continue

                if m_id in metrics_evaluated:
                    continue

                if not active_binding:
                    metrics_evaluated[m_id] = {
                        'metric_id': m_id,
                        'display_name': m_info['display_name'],
                        'importance': m_info['importance'],
                        'comparison_type': 'active',
                        'active_definition_id': None,
                        'has_approved_baseline': False,
                        'approved_version_id': None,
                        'baseline_revision_version_id': res['old_definition_version_id'],
                        'target_version_id': res['new_definition_version_id'],
                        'reference_snapshot_id': snapshot_id,
                        'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
                        'comparison_run_id': None,
                        'baseline_value': None,
                        'target_value': None,
                        'exact_delta': None,
                        'absolute_delta': None,
                        'relative_change_percent': None,
                        'residual': None,
                        'materiality': None,
                        'support_state': 'unsupported',
                        'execution_status': 'refused',
                        'refusal_reason': 'Metric has no active definition binding.',
                        'diagnostics': {},
                        'primary_impact': None,
                        'approved_baseline_impact': None,
                        'incremental_impact': None,
                    }
                    continue

                active_defn_id = active_binding['definition_id']
                approved_v_id = active_binding.get('approved_version_id')
                base_rev_v_id = res['old_definition_version_id']
                target_v_id = res['new_definition_version_id']

                if not target_v_id:
                    sup_st = 'unsupported'
                    refusal_reason = 'Target definition version unavailable (resource removed).'
                    diag_info = {}
                    if res.get('new_support_state') in ('metadata_only', 'needs_context', 'unsupported'):
                        sup_st = res['new_support_state']
                        new_diags = res.get('new_diagnostics') or res.get('diagnostics') or []
                        refusal_reason = new_diags[0] if new_diags else f"Target definition has support state '{sup_st}'."
                        diag_info = {'target': new_diags}

                    metrics_evaluated[m_id] = {
                        'metric_id': m_id,
                        'display_name': m_info['display_name'],
                        'importance': m_info['importance'],
                        'comparison_type': 'active',
                        'active_definition_id': active_defn_id,
                        'has_approved_baseline': bool(approved_v_id),
                        'approved_version_id': approved_v_id,
                        'baseline_revision_version_id': base_rev_v_id,
                        'target_version_id': None,
                        'reference_snapshot_id': snapshot_id,
                        'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
                        'comparison_run_id': None,
                        'baseline_value': None,
                        'target_value': None,
                        'exact_delta': None,
                        'absolute_delta': None,
                        'relative_change_percent': None,
                        'residual': None,
                        'materiality': None,
                        'support_state': sup_st,
                        'execution_status': 'refused',
                        'refusal_reason': refusal_reason,
                        'diagnostics': diag_info,
                        'primary_impact': None,
                        'approved_baseline_impact': None,
                        'incremental_impact': None,
                    }
                    continue

                if approved_v_id:
                    appr_impact = self._run_controlled_comparison(
                        pid=pid,
                        baseline_version_id=approved_v_id,
                        target_version_id=target_v_id,
                        snapshot_row=snap_row,
                        snapshot_manifest=snap_manifest,
                        context=context,
                        materiality_policy=materiality_policy
                    )

                    if base_rev_v_id and base_rev_v_id != approved_v_id:
                        incr_impact = self._run_controlled_comparison(
                            pid=pid,
                            baseline_version_id=base_rev_v_id,
                            target_version_id=target_v_id,
                            snapshot_row=snap_row,
                            snapshot_manifest=snap_manifest,
                            context=context,
                            materiality_policy=materiality_policy
                        )
                    elif not base_rev_v_id and res.get('old_support_state') in ('metadata_only', 'needs_context', 'unsupported'):
                        old_diags = res.get('old_diagnostics') or res.get('diagnostics') or []
                        incr_impact = {
                            'execution_status': 'refused',
                            'support_state': res['old_support_state'],
                            'refusal_reason': old_diags[0] if old_diags else f"Baseline revision definition has support state '{res['old_support_state']}'.",
                            'diagnostics': {'baseline': old_diags},
                            'baseline_version_id': None,
                            'target_version_id': target_v_id,
                            'baseline_value': None,
                            'target_value': None,
                            'exact_delta': None,
                            'absolute_delta': None,
                            'relative_change_percent': None,
                            'residual': None,
                            'run_id': None,
                            'materiality': None,
                            'evidence_summary': None,
                        }
                    else:
                        incr_impact = appr_impact

                    metrics_evaluated[m_id] = {
                        'metric_id': m_id,
                        'display_name': m_info['display_name'],
                        'importance': m_info['importance'],
                        'comparison_type': 'active',
                        'active_definition_id': active_defn_id,
                        'has_approved_baseline': True,
                        'approved_version_id': approved_v_id,
                        'baseline_revision_version_id': base_rev_v_id,
                        'target_version_id': target_v_id,
                        'reference_snapshot_id': snapshot_id,
                        'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
                        'comparison_run_id': appr_impact.get('run_id'),
                        'baseline_value': appr_impact.get('baseline_value'),
                        'target_value': appr_impact.get('target_value'),
                        'exact_delta': appr_impact.get('exact_delta'),
                        'absolute_delta': appr_impact.get('absolute_delta'),
                        'relative_change_percent': appr_impact.get('relative_change_percent'),
                        'residual': appr_impact.get('residual'),
                        'materiality': appr_impact.get('materiality'),
                        'support_state': appr_impact.get('support_state'),
                        'execution_status': appr_impact.get('execution_status'),
                        'refusal_reason': appr_impact.get('refusal_reason'),
                        'diagnostics': appr_impact.get('diagnostics'),
                        'primary_impact': appr_impact,
                        'approved_baseline_impact': appr_impact,
                        'incremental_impact': incr_impact,
                    }
                else:
                    if not base_rev_v_id:
                        sup_st = 'unsupported'
                        refusal_reason = 'Baseline definition version unavailable (resource added without prior baseline).'
                        diag_info = {}
                        if res.get('old_support_state') in ('metadata_only', 'needs_context', 'unsupported'):
                            sup_st = res['old_support_state']
                            old_diags = res.get('old_diagnostics') or res.get('diagnostics') or []
                            refusal_reason = old_diags[0] if old_diags else f"Baseline definition has support state '{sup_st}'."
                            diag_info = {'baseline': old_diags}

                        metrics_evaluated[m_id] = {
                            'metric_id': m_id,
                            'display_name': m_info['display_name'],
                            'importance': m_info['importance'],
                            'comparison_type': 'active',
                            'active_definition_id': active_defn_id,
                            'has_approved_baseline': False,
                            'approved_version_id': None,
                            'baseline_revision_version_id': None,
                            'target_version_id': target_v_id,
                            'reference_snapshot_id': snapshot_id,
                            'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
                            'comparison_run_id': None,
                            'baseline_value': None,
                            'target_value': None,
                            'exact_delta': None,
                            'absolute_delta': None,
                            'relative_change_percent': None,
                            'residual': None,
                            'materiality': None,
                            'support_state': sup_st,
                            'execution_status': 'refused',
                            'refusal_reason': refusal_reason,
                            'diagnostics': diag_info,
                            'primary_impact': None,
                            'approved_baseline_impact': None,
                            'incremental_impact': None,
                        }
                    else:
                        incr_impact = self._run_controlled_comparison(
                            pid=pid,
                            baseline_version_id=base_rev_v_id,
                            target_version_id=target_v_id,
                            snapshot_row=snap_row,
                            snapshot_manifest=snap_manifest,
                            context=context,
                            materiality_policy=materiality_policy
                        )
                        metrics_evaluated[m_id] = {
                            'metric_id': m_id,
                            'display_name': m_info['display_name'],
                            'importance': m_info['importance'],
                            'comparison_type': 'active',
                            'active_definition_id': active_defn_id,
                            'has_approved_baseline': False,
                            'approved_version_id': None,
                            'baseline_revision_version_id': base_rev_v_id,
                            'target_version_id': target_v_id,
                            'reference_snapshot_id': snapshot_id,
                            'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
                            'comparison_run_id': incr_impact.get('run_id'),
                            'baseline_value': incr_impact.get('baseline_value'),
                            'target_value': incr_impact.get('target_value'),
                            'exact_delta': incr_impact.get('exact_delta'),
                            'absolute_delta': incr_impact.get('absolute_delta'),
                            'relative_change_percent': incr_impact.get('relative_change_percent'),
                            'residual': incr_impact.get('residual'),
                            'materiality': incr_impact.get('materiality'),
                            'support_state': incr_impact.get('support_state'),
                            'execution_status': incr_impact.get('execution_status'),
                            'refusal_reason': incr_impact.get('refusal_reason'),
                            'diagnostics': incr_impact.get('diagnostics'),
                            'primary_impact': incr_impact,
                            'approved_baseline_impact': None,
                            'incremental_impact': incr_impact,
                        }

        metrics_list = list(metrics_evaluated.values())
        candidate_metrics_list = list(candidate_metrics_evaluated.values())
        all_metrics_list = metrics_list + candidate_metrics_list

        counts = {
            'total_metrics_evaluated': len(all_metrics_list),
            'material_impact_count': sum(1 for m in all_metrics_list if m.get('materiality') and m['materiality'].get('is_material')),
            'immaterial_impact_count': sum(1 for m in all_metrics_list if m.get('materiality') and not m['materiality'].get('is_material')),
            'refused_count': sum(1 for m in all_metrics_list if m.get('execution_status') == 'refused'),
            'undeclared_resource_count': len(undeclared_resources),
        }

        impact_id = f"impact-{uuid4().hex}"
        report_data = {
            'schema_version': 'metricguard_metric_impact_v1',
            'id': impact_id,
            'project_id': pid,
            'baseline_revision_id': baseline_revision_id,
            'target_revision_id': target_revision_id,
            'snapshot_id': snapshot_id,
            'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
            'request_hash': req_hash,
            'created_at': now(),
            'counts': counts,
            'metrics': metrics_list,
            'candidate_metrics': candidate_metrics_list,
            'undeclared_resources': undeclared_resources,
        }

        with self.engine.begin() as conn:
            conn.execute(insert(metric_impact_reports).values(
                id=impact_id,
                project_id=pid,
                baseline_revision_id=baseline_revision_id,
                target_revision_id=target_revision_id,
                snapshot_id=snapshot_id,
                request_hash=req_hash,
                created_at=report_data['created_at'],
                data=report_data
            ))

        return report_data

    def get_metric_impact_report(self, impact_id: str, project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            row = conn.execute(
                select(metric_impact_reports)
                .where(metric_impact_reports.c.id == impact_id,
                       metric_impact_reports.c.project_id == pid)
            ).mappings().first()
            if not row:
                other = conn.execute(
                    select(metric_impact_reports.c.project_id)
                    .where(metric_impact_reports.c.id == impact_id)
                ).scalar()
                if other:
                    raise DomainError('FORBIDDEN', f"Metric impact report '{impact_id}' belongs to project '{other}', not '{pid}'.", 403)
                raise DomainError('NOT_FOUND', f"Metric impact report '{impact_id}' not found in project '{pid}'.", 404)
            return row['data']

    def list_metric_impact_reports(self, project_id: str | None = None) -> list[dict]:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(metric_impact_reports)
                .where(metric_impact_reports.c.project_id == pid)
                .order_by(metric_impact_reports.c.created_at.desc())
            ).mappings().all()
            return [r['data'] for r in rows]

    def promote_metric_impact_to_investigation(self, impact_id: str, metric_id: str,
                                               title: str | None = None,
                                               decision: str = 'Promoted from CI metric impact evaluation',
                                               actor_label: str = 'CI / Analyst',
                                               project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        report = self.get_metric_impact_report(impact_id, project_id=pid)

        all_evaluated = (report.get('metrics') or []) + (report.get('candidate_metrics') or [])
        metric_entry = next((m for m in all_evaluated if m['metric_id'] == metric_id), None)
        if not metric_entry:
            raise DomainError('NOT_FOUND', f"Metric '{metric_id}' was not evaluated in impact report '{impact_id}'.", 404)

        if metric_entry.get('execution_status') != 'executed' or not metric_entry.get('comparison_run_id'):
            raise DomainError('RESULT_UNAVAILABLE', f"Metric '{metric_id}' in impact report '{impact_id}' was not successfully executed or has no comparison run.", 400)

        run_id = metric_entry['comparison_run_id']
        snapshot_id = metric_entry['reference_snapshot_id']
        inv_title = title or f"Investigation: {metric_entry['display_name']} impact"

        inv_payload = {
            'run_id': run_id,
            'snapshot_id': snapshot_id,
            'title': inv_title,
            'decision': decision,
            'actor_label': actor_label,
        }
        return self.record_investigation(inv_payload, project_id=pid)

    def execute_metric_diff(self, baseline_revision_id: str, target_revision_id: str,
                            snapshot_id: str, mode: str = 'advisory',
                            context: dict | None = None,
                            materiality_policy: dict | None = None,
                            project_id: str | None = None) -> dict:
        from services.diff_report import (
            compute_diff_coverage,
            aggregate_diff_refusals,
            determine_diff_outcome_and_exit_code,
        )
        from packages.metricguard_core.snapshot import inspect_snapshot
        from packages.metricguard_core.common import CoreError

        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            b_rev = conn.execute(
                select(dbt_import_revisions)
                .where(dbt_import_revisions.c.id == baseline_revision_id,
                       dbt_import_revisions.c.project_id == pid)
            ).mappings().first()
            if not b_rev:
                other = conn.execute(select(dbt_import_revisions.c.project_id).where(dbt_import_revisions.c.id == baseline_revision_id)).scalar()
                if other:
                    raise DomainError('FORBIDDEN', f"Baseline dbt import revision '{baseline_revision_id}' belongs to project '{other}', not '{pid}'.", 403)
                raise DomainError('NOT_FOUND', f"Baseline dbt import revision '{baseline_revision_id}' not found in project '{pid}'.", 404)

            t_rev = conn.execute(
                select(dbt_import_revisions)
                .where(dbt_import_revisions.c.id == target_revision_id,
                       dbt_import_revisions.c.project_id == pid)
            ).mappings().first()
            if not t_rev:
                other = conn.execute(select(dbt_import_revisions.c.project_id).where(dbt_import_revisions.c.id == target_revision_id)).scalar()
                if other:
                    raise DomainError('FORBIDDEN', f"Target dbt import revision '{target_revision_id}' belongs to project '{other}', not '{pid}'.", 403)
                raise DomainError('NOT_FOUND', f"Target dbt import revision '{target_revision_id}' not found in project '{pid}'.", 404)

            snap_row = conn.execute(
                select(snapshots)
                .where(snapshots.c.id == snapshot_id,
                       snapshots.c.project_id == pid)
            ).mappings().first()
            if not snap_row:
                other = conn.execute(select(snapshots.c.project_id).where(snapshots.c.id == snapshot_id)).scalar()
                if other:
                    raise DomainError('FORBIDDEN', f"Snapshot '{snapshot_id}' belongs to project '{other}', not '{pid}'.", 403)
                raise DomainError('NOT_FOUND', f"Snapshot '{snapshot_id}' not found in project '{pid}'.", 404)

            artifact_path = snap_row['data'].get('artifact_path')
            if not durable.ensure(artifact_path):
                raise DomainError('SNAPSHOT_MISSING', f"Snapshot file for '{snapshot_id}' is missing.", 404)

            try:
                snap_manifest = inspect_snapshot(artifact_path)
            except CoreError as err:
                raise DomainError('SNAPSHOT_CORRUPTED', f"Snapshot '{snapshot_id}' integrity check failed: {err.message}", 400)

            all_bindings_rows = conn.execute(
                select(metric_definition_bindings)
                .where(metric_definition_bindings.c.project_id == pid)
            ).mappings().all()

            bindings_fingerprint = sorted([
                (b['metric_id'], b['definition_id'], b['status'], b.get('approved_version_id'))
                for b in all_bindings_rows
            ])

            req_hash_data = {
                'pid': pid,
                'baseline_revision_id': baseline_revision_id,
                'target_revision_id': target_revision_id,
                'snapshot_id': snapshot_id,
                'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
                'mode': mode,
                'context': context,
                'materiality_policy': materiality_policy,
                'bindings': bindings_fingerprint,
            }
            req_hash = fingerprint(req_hash_data)

            existing_diff = conn.execute(
                select(metric_diff_reports)
                .where(metric_diff_reports.c.project_id == pid,
                       metric_diff_reports.c.request_hash == req_hash)
            ).mappings().first()
            if existing_diff:
                return existing_diff['data']

        # 2. Compute/reuse change set (V5-01)
        change_set = self.compute_dbt_change_set(
            baseline_revision_id=baseline_revision_id,
            target_revision_id=target_revision_id,
            project_id=pid
        )
        change_set_hash = fingerprint({
            'baseline_manifest_hash': change_set['baseline_manifest_hash'],
            'target_manifest_hash': change_set['target_manifest_hash'],
            'counts': change_set['counts'],
            'resources_count': len(change_set['resources']),
        })

        # 3. Execute/reuse controlled metric impact (V5-02)
        impact_report = self.execute_metric_impact(
            baseline_revision_id=baseline_revision_id,
            target_revision_id=target_revision_id,
            snapshot_id=snapshot_id,
            context=context,
            materiality_policy=materiality_policy,
            project_id=pid
        )

        # 4. Coverage calculation (Section 3)
        coverage = compute_diff_coverage(change_set=change_set, impact_report=impact_report)

        # 5. Refusal aggregation (Section 4)
        refusals = aggregate_diff_refusals(change_set=change_set, impact_report=impact_report)

        # 6. Revision-level outcome & exit code (Sections 6 & 13)
        material_metrics_count = impact_report['counts']['material_impact_count']
        refused_metrics_count = coverage['declared_metrics']['refused']
        overall_outcome, exit_code = determine_diff_outcome_and_exit_code(
            mode=mode,
            material_metrics_count=material_metrics_count,
            refused_metrics_count=refused_metrics_count,
            operational_failure=False,
        )

        diff_id = f"diff-{uuid4().hex}"
        total_changed = coverage['structural']['changed_resources_count']
        unchanged_count = change_set['counts'].get('unchanged_count', 0)
        declared_affected = coverage['declared_metrics']['total_affected']
        analyzable_count = coverage['declared_metrics']['analyzable']
        refused_count = coverage['declared_metrics']['refused']
        undeclared_count = coverage['structural']['undeclared_changed_resources_count']

        counts = {
            'total_changed_resources': total_changed,
            'unchanged_resources': unchanged_count,
            'total_declared_metrics_affected': declared_affected,
            'successfully_analyzed_metrics': analyzable_count,
            'refused_metrics': refused_count,
            'undeclared_changed_resources': undeclared_count,
            'material_metrics': material_metrics_count,
        }

        # Enhance each metric with investigation link / promotability
        enhanced_metrics = []
        for m in impact_report['metrics']:
            m_copy = dict(m)
            m_copy['impact_id'] = impact_report['id']
            m_copy['investigation_promotable'] = (m.get('execution_status') == 'executed')
            if m.get('comparison_run_id'):
                m_copy['evidence_reference'] = {
                    'run_id': m.get('comparison_run_id'),
                    'evidence_summary': m.get('primary_impact', {}).get('evidence_summary') if m.get('primary_impact') else None,
                }
            else:
                m_copy['evidence_reference'] = None
            enhanced_metrics.append(m_copy)

        diff_data = {
            'schema_version': 'metricguard_metric_diff_v1',
            'id': diff_id,
            'project_id': pid,
            'baseline_revision_id': baseline_revision_id,
            'target_revision_id': target_revision_id,
            'baseline_manifest_hash': b_rev['manifest_hash'],
            'target_manifest_hash': t_rev['manifest_hash'],
            'snapshot_id': snapshot_id,
            'snapshot_hash': snap_manifest.get('manifest_hash') or snap_row['manifest_hash'],
            'source_metadata': {
                'baseline': b_rev.get('source_revision', {}),
                'target': t_rev.get('source_revision', {}),
            },
            'change_set_id': f"cs-{change_set_hash[:16]}",
            'change_set_hash': change_set_hash,
            'impact_report_id': impact_report['id'],
            'mode': mode,
            'overall_outcome': overall_outcome,
            'exit_code': exit_code,
            'counts': counts,
            'coverage': coverage,
            'refusals': refusals,
            'metrics': enhanced_metrics,
            'candidate_metrics': impact_report.get('candidate_metrics', []),
            'undeclared_resources': impact_report.get('undeclared_resources', []),
            'request_hash': req_hash,
            'created_at': now(),
        }

        with self.engine.begin() as conn:
            conn.execute(insert(metric_diff_reports).values(
                id=diff_id,
                project_id=pid,
                baseline_revision_id=baseline_revision_id,
                target_revision_id=target_revision_id,
                snapshot_id=snapshot_id,
                request_hash=req_hash,
                created_at=diff_data['created_at'],
                data=diff_data
            ))

        return diff_data

    def get_metric_diff_report(self, diff_id: str, project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            row = conn.execute(
                select(metric_diff_reports)
                .where(metric_diff_reports.c.id == diff_id,
                       metric_diff_reports.c.project_id == pid)
            ).mappings().first()
            if not row:
                other = conn.execute(
                    select(metric_diff_reports.c.project_id)
                    .where(metric_diff_reports.c.id == diff_id)
                ).scalar()
                if other:
                    raise DomainError('FORBIDDEN', f"Metric diff report '{diff_id}' belongs to project '{other}', not '{pid}'.", 403)
                raise DomainError('NOT_FOUND', f"Metric diff report '{diff_id}' not found in project '{pid}'.", 404)
            return row['data']

    def list_metric_diff_reports(self, project_id: str | None = None) -> list[dict]:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(metric_diff_reports)
                .where(metric_diff_reports.c.project_id == pid)
                .order_by(metric_diff_reports.c.created_at.desc())
            ).mappings().all()
            return [r['data'] for r in rows]

    # -----------------------------------------------------------------------
    # V5.2-05: Durable Server-Backed Investigation Drafts
    # -----------------------------------------------------------------------

    def record_investigation_draft(self, payload: dict, project_id: str | None = None, created_by: str | None = None) -> dict:
        pid = project_id or self.project_id
        self.get_project(pid)

        retry_key = payload.get('idempotency_key')
        if retry_key is not None and (not isinstance(retry_key, str) or not 1 <= len(retry_key) <= 160):
            raise DomainError('INVALID_IDEMPOTENCY_KEY', 'Draft retry key must contain 1 to 160 characters.')
        draft_id = ('draft-' + fingerprint({'project': pid, 'actor': created_by, 'key': retry_key})
                    if retry_key else payload.get('id') or f"draft-{uuid4().hex}")
        if retry_key:
            with self.engine.connect() as conn:
                existing = conn.execute(select(records.c.data).where(records.c.id == draft_id,
                    records.c.project_id == pid, records.c.kind == 'investigation_draft')).scalar_one_or_none()
                if existing is not None:
                    return existing
        run_id = payload.get('comparison_run_id')
        if run_id and payload.get('origin') != 'ci':
            # One open draft per comparison run: a repeated comparison resumes it instead of adding another.
            with self.engine.connect() as conn:
                for existing in conn.execute(select(records.c.data).where(records.c.project_id == pid,
                        records.c.kind == 'investigation_draft').order_by(records.c.created_at)).scalars():
                    if existing.get('comparison_run_id') == run_id and existing.get('origin') != 'ci':
                        return existing
        current_time = now()
        token = uuid4().hex[:12]

        item = {
            'id': draft_id,
            'project_id': pid,
            'title': payload.get('title') or 'Untitled investigation',
            'metric_id': payload.get('metric_id'),
            'baseline_definition_id': payload.get('baseline_definition_id'),
            'baseline_version_id': payload.get('baseline_version_id'),
            'target_definition_id': payload.get('target_definition_id'),
            'target_version_id': payload.get('target_version_id'),
            'snapshot_id': payload.get('snapshot_id'),
            'comparison_run_id': payload.get('comparison_run_id'),
            'status': payload.get('status') or 'draft',
            'current_step': payload.get('current_step') or 'config',
            'notes': payload.get('notes'),
            'rationale': payload.get('rationale'),
            'decision_draft': payload.get('decision_draft'),
            'editor_state': payload.get('editor_state'),
            'version_token': token,
            'created_by': created_by or payload.get('created_by'),
            'created_at': current_time,
            'updated_at': current_time,
        }

        # Preserve additional metadata (e.g. CI origin, hashes, refusal diagnostics)
        for k, v in payload.items():
            if k not in item:
                item[k] = v

        try:
            with self.engine.begin() as conn:
                conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
                conn.execute(insert(records).values(
                    id=draft_id,
                    project_id=pid,
                    kind='investigation_draft',
                    created_at=current_time,
                    data=item,
                ))
        except IntegrityError:
            if not retry_key:
                raise
            return self.get_investigation_draft(draft_id, project_id=pid)
        return item

    def get_investigation_draft(self, draft_id: str, project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            row = conn.execute(
                select(records)
                .where(records.c.id == draft_id,
                       records.c.kind == 'investigation_draft',
                       records.c.project_id == pid)
            ).mappings().first()
            if not row:
                other = conn.execute(
                    select(records.c.project_id)
                    .where(records.c.id == draft_id, records.c.kind == 'investigation_draft')
                ).scalar()
                if other:
                    raise DomainError('FORBIDDEN', f"Investigation draft '{draft_id}' belongs to project '{other}', not '{pid}'.", 403)
                raise DomainError('NOT_FOUND', f"Investigation draft '{draft_id}' not found in project '{pid}'.", 404)
            return row['data']

    def list_investigation_drafts(self, project_id: str | None = None) -> list[dict]:
        pid = project_id or self.project_id
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(records)
                .where(records.c.project_id == pid,
                       records.c.kind == 'investigation_draft')
                .order_by(records.c.created_at.desc())
            ).mappings().all()
            drafts = [r['data'] for r in rows]
            drafts.sort(key=lambda d: d.get('updated_at', d.get('created_at', '')), reverse=True)
            return drafts

    def update_investigation_draft(self, draft_id: str, updates: dict,
                                   expected_version_token: str | None = None,
                                   project_id: str | None = None) -> dict:
        pid = project_id or self.project_id
        existing = self.get_investigation_draft(draft_id, project_id=pid)

        if expected_version_token and existing.get('version_token') != expected_version_token:
            raise DomainError('STALE_DRAFT', 'This investigation was updated elsewhere. Reload the latest version.', 409)

        current_time = now()
        new_token = uuid4().hex[:12]

        nullable_fields = {'metric_id', 'baseline_definition_id', 'baseline_version_id',
                           'target_definition_id', 'target_version_id', 'snapshot_id',
                           'comparison_run_id', 'current_step', 'notes', 'rationale',
                           'decision_draft', 'editor_state'}
        clean_updates = {k: v for k, v in updates.items()
                         if k not in {'id', 'project_id', 'created_at', 'version_token'}
                         and (v is not None or k in nullable_fields)}
        merged = {
            **existing,
            **clean_updates,
            'id': draft_id,
            'project_id': pid,
            'version_token': new_token,
            'updated_at': current_time,
        }

        with self.engine.begin() as conn:
            conn.execute(update(projects).where(projects.c.id == pid).values(name=projects.c.name))
            statement = update(records).where(
                records.c.id == draft_id,
                records.c.kind == 'investigation_draft',
                records.c.project_id == pid)
            if expected_version_token:
                statement = statement.where(
                    records.c.data['version_token'].as_string() == expected_version_token)
            receipt = conn.execute(statement.values(data=merged))
            if receipt.rowcount != 1:
                raise DomainError('STALE_DRAFT', 'This investigation was updated elsewhere. Reload the latest version.', 409)
        return merged

    def delete_investigation_draft(self, draft_id: str, project_id: str | None = None) -> bool:
        pid = project_id or self.project_id
        self.get_investigation_draft(draft_id, project_id=pid)
        with self.engine.begin() as conn:
            conn.execute(
                delete(records)
                .where(records.c.id == draft_id,
                       records.c.kind == 'investigation_draft',
                       records.c.project_id == pid)
            )
        return True

    def resolve_ci_investigation_draft(
        self,
        project_id: str,
        ci_run_id: str,
        metric_id: str,
        baseline_version_id: str | None = None,
        target_version_id: str | None = None,
        snapshot_id: str | None = None,
        comparison_run_id: str | None = None,
        status: str = 'draft',
        refusal_reason: str | None = None,
        diagnostics: dict | None = None,
        ci_provenance: dict | None = None,
        title: str | None = None,
    ) -> dict:
        """
        V6.4: Deterministically resolve or create a durable investigation draft for a CI metric finding.
        Guarantees zero recomputation by directly binding the existing comparison_run_id and snapshot.
        Guarantees idempotency: repeated CI runs or clicks resolve to the existing draft.
        Preserves human investigations: never overwrites or resets status if already completed.
        """
        pid = project_id or self.project_id
        self.get_project(pid)

        # 1. Deterministic draft ID
        if comparison_run_id:
            seed = f"{pid}:{metric_id}:{comparison_run_id}"
            draft_id = f"draft-ci-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"
        else:
            seed = f"{pid}:{metric_id}:{ci_run_id}"
            draft_id = f"draft-ci-refusal-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"

        with self.engine.connect() as conn:
            # Check by exact deterministic draft ID
            row = conn.execute(
                select(records)
                .where(records.c.id == draft_id,
                       records.c.kind == 'investigation_draft',
                       records.c.project_id == pid)
            ).mappings().first()
            if row:
                return row['data']

            # If comparison_run_id exists, check if any existing draft already points to it
            if comparison_run_id:
                existing_rows = conn.execute(
                    select(records)
                    .where(records.c.project_id == pid,
                           records.c.kind == 'investigation_draft')
                ).mappings().all()
                for er in existing_rows:
                    data = er['data']
                    if data.get('comparison_run_id') == comparison_run_id and data.get('metric_id') == metric_id:
                        return data

        # Resolve baseline and target definition IDs if available
        baseline_def_id = None
        if baseline_version_id:
            try:
                b_ver = self.version(baseline_version_id)
                baseline_def_id = b_ver.get('definition_id')
            except Exception:
                pass

        target_def_id = None
        if target_version_id:
            try:
                t_ver = self.version(target_version_id)
                target_def_id = t_ver.get('definition_id')
            except Exception:
                pass

        # Resolve metric display name for a clean title
        default_title = f"CI Investigation: {metric_id} ({ci_run_id[:8]})"
        try:
            m_rec = self.get_metric(metric_id, project_id=pid)
            m_name = m_rec.get('display_name') or metric_id
            if status == 'refused' or refusal_reason:
                default_title = f"CI Refusal: {m_name} ({ci_run_id[:8]})"
            else:
                default_title = f"CI Investigation: {m_name} ({ci_run_id[:8]})"
        except Exception:
            pass

        # Create new draft item
        payload = {
            'id': draft_id,
            'title': title or default_title,
            'metric_id': metric_id,
            'baseline_definition_id': baseline_def_id,
            'baseline_version_id': baseline_version_id,
            'target_definition_id': target_def_id,
            'target_version_id': target_version_id,
            'snapshot_id': snapshot_id,
            'comparison_run_id': comparison_run_id,
            'status': status,
            'current_step': 'evidence' if comparison_run_id else 'config',
            'origin': 'ci',
            'ci_run_id': ci_run_id,
            'ci_provenance': ci_provenance or {},
            'refusal_reason': refusal_reason,
            'diagnostics': diagnostics or {},
            'created_by': 'metricguard-ci',
        }

        try:
            return self.record_investigation_draft(payload, project_id=pid, created_by='metricguard-ci')
        except Exception:
            return self.get_investigation_draft(draft_id, project_id=pid)

