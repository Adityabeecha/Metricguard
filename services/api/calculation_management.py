"""Conservative saved-calculation labels and reference-aware deletion."""
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from .item_management import _clean_name
from .store import DomainError, definitions, versions, metadata, records, runs


def _contains(value, ids):
    if isinstance(value, dict):
        return any(_contains(v, ids) for v in value.values())
    if isinstance(value, list):
        return any(_contains(v, ids) for v in value)
    return isinstance(value, str) and value in ids


def _usage(conn, definition):
    ident, pid = definition['id'], definition['project_id']
    version_ids = set(conn.execute(select(versions.c.id).where(versions.c.definition_id == ident)).scalars())
    used = {}
    def add(kind, rid, name):
        used[(kind, rid)] = {'kind': kind, 'id': rid, 'name': name or rid}
    # Foreign keys are authoritative, including historical approvals and imported metadata.
    for table in metadata.sorted_tables:
        if table is versions:
            continue
        refs = []
        for column in table.c:
            for fk in column.foreign_keys:
                if fk.target_fullname == 'definitions.id':
                    refs.append(column == ident)
                elif fk.target_fullname == 'definition_versions.id' and version_ids:
                    refs.append(column.in_(version_ids))
        if not refs:
            continue
        from sqlalchemy import or_
        query = select(table).where(or_(*refs))
        for row in conn.execute(query).mappings():
            kind = 'run' if table is runs else 'metric' if table.name.startswith('metric_') else 'reference'
            name = row.get('name') or row.get('display_name') or row['id']
            if kind == 'metric' and row.get('metric_id'):
                metric = metadata.tables['metrics']
                name = conn.execute(select(metric.c.display_name).where(metric.c.id == row['metric_id'])).scalar() or name
            reference_id = (row.get('metric_id') or row['id']) if kind == 'metric' else row['id']
            add(kind, reference_id, name)
    referenced_runs = {u['id'] for u in used.values() if u['kind'] == 'run'}
    # Saved cases and drafts pin versions in JSON rather than database foreign keys.
    for row in conn.execute(select(records).where(records.c.project_id == pid)).mappings():
        if _contains(row['data'], {ident, *version_ids, *referenced_runs}):
            kind = 'case' if row['kind'] == 'investigation' else 'reference'
            add(kind, row['id'], row['data'].get('title') or row['kind'])
    return {'definition_id': ident, 'used_by': list(used.values()),
            'retained': any(u['kind'] in ('case', 'metric') for u in used.values()), 'can_delete': not used}


def calculation_usage(store, project_id):
    with store.engine.connect() as conn:
        return {'items': [_usage(conn, row) for row in conn.execute(
            select(definitions).where(definitions.c.project_id == project_id)).mappings()]}


def rename_calculation(store, ident, name):
    name = _clean_name(name, 'calculation')
    with store.engine.begin() as conn:
        if not conn.execute(update(definitions).where(definitions.c.id == ident).values(name=name)).rowcount:
            raise DomainError('NOT_FOUND', 'Calculation not found.', 404)
    return store.definition(ident)


def delete_calculation(store, ident):
    try:
        with store.engine.begin() as conn:
            row = conn.execute(select(definitions).where(definitions.c.id == ident).with_for_update()).mappings().first()
            if not row:
                raise DomainError('NOT_FOUND', 'Calculation not found.', 404)
            usage = _usage(conn, row)
            if usage['used_by']:
                names = '; '.join(f"{u['kind']}: {u['name']}" for u in usage['used_by'][:10])
                raise DomainError('CALCULATION_IN_USE', f'This calculation is used by {names}. It cannot be deleted.', 409)
            conn.execute(delete(versions).where(versions.c.definition_id == ident))
            conn.execute(delete(definitions).where(definitions.c.id == ident))
    except IntegrityError as exc:
        raise DomainError('CALCULATION_IN_USE', 'A comparison or metric now uses this calculation. Refresh the list before trying again.', 409) from exc
    return {'deleted': True, 'id': ident}
