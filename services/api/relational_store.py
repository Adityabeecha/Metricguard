"""Relational policy records on the existing version/job/backup infrastructure."""

import hashlib
from uuid import uuid4

from sqlalchemy import insert, select


class RelationalStoreMixin:
    def relational_contract(self, ident):
        return self._record_of_kind(ident, 'relational_contract')

    def record_relational_contract(self, payload, project_id=None):
        from packages.metricguard_core.relational import validate_contract
        from .store import records, now, DomainError
        pid = project_id or self.project_id
        contract = validate_contract(payload)
        with self.engine.begin() as conn:
            prior = [r['data'] for r in conn.execute(select(records).where(
                records.c.kind == 'relational_contract', records.c.project_id == pid)).mappings()]
            for item in prior:
                if item['contract_hash'] == contract['contract_hash']:
                    return item
            if len(prior) >= 100:
                raise DomainError('IMPORT_LIMIT', 'Workspace supports at most 100 relational contracts.')
            item = {**contract, 'id': str(uuid4()), 'created_at': now(), 'project_id': pid,
                    'version_number': 1 + sum(p['name'] == contract['name'] for p in prior)}
            conn.execute(insert(records).values(id=item['id'], project_id=pid, kind='relational_contract', created_at=item['created_at'], data=item))
        return item

    def record_relational_definition(self, name, raw_sql, contract_id, project_id=None):
        from packages.metricguard_core.relational import parse_relational, EXECUTION_POLICY
        from .store import definitions, versions, now, DomainError
        pid = project_id or self.project_id
        record = self.relational_contract(contract_id)
        if record['project_id'] != pid:
            raise DomainError('FORBIDDEN', 'Contract belongs to a different workspace.', 403)
        contract = {k: v for k, v in record.items() if k not in {'id', 'created_at', 'project_id', 'version_number'}}
        ir = parse_relational(raw_sql, contract)
        with self.engine.begin() as conn:
            existing = self._same_snapshot_definition(conn, pid, contract_id, EXECUTION_POLICY, raw_sql)
            if existing:
                return self._definition_on(conn, existing)
            if len(conn.execute(select(definitions.c.id).where(definitions.c.project_id == pid)).all()) >= 100:
                raise DomainError('IMPORT_LIMIT', 'Workspace supports at most 100 definitions.')
            did, vid = str(uuid4()), str(uuid4())
            conn.execute(insert(definitions).values(id=did, project_id=pid, name=name,
                source_label='Relational snapshot SQL', owner_label='Local analyst', created_at=now()))
            version = {'id': vid, 'definition_id': did, 'version_number': 1, 'raw_sql': raw_sql,
                       'bound_sql': None, 'mapping_id': None, 'mapping_hash': None,
                       'contract_id': contract_id, 'contract_hash': contract['contract_hash'],
                       'execution_policy': EXECUTION_POLICY, 'support_state': 'executable', 'support_reason': None,
                       'raw_hash': hashlib.sha256(raw_sql.encode()).hexdigest(), 'ir': ir, 'ir_hash': ir['ir_hash'],
                       'observed_at': now(), 'source_changed_at': None, 'effective_from': None, 'change_kind': 'initial', 'project_id': pid}
            conn.execute(insert(versions).values(id=vid, definition_id=did, version_number=1, data=version))
        return self.definition(did)
