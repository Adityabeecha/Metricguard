"""V8.2 metric-governance-v2 checks for relational_snapshot_v1 implementations.

Metadata only: frozen manifests, registered contracts and sealed definition IR
are validated with the existing core validators. No analytical SQL is run, no
source is recollected and no investigation is recomputed.
"""
from __future__ import annotations

from sqlalchemy import select

from packages.metricguard_core.common import CoreError
from packages.metricguard_core.relational import (EXECUTION_POLICY, MODE, schemas, validate_contract,
                                                  validate_ir)
from packages.metricguard_core.relational_snapshot import validate_manifest

POLICY_V2 = 'metric-governance-v2'
SCHEMA_V2 = 2
_UNKNOWN_UNITS = ('unknown', 'unknown currency', 'unknown_currency', 'unspecified')
_LOGICAL = {'VARCHAR': 'string', 'TEXT': 'string', 'BOOLEAN': 'boolean', 'DATE': 'date', 'TIMESTAMP': 'timestamp'}


def is_relational_version(version):
    return (version.get('execution_policy') == EXECUTION_POLICY or
            (version.get('ir') or {}).get('mode') == MODE)


def registered_contract(conn, records, version, pid, error):
    """Load and validate the relational contract a definition version was registered against."""
    ident = version.get('contract_id')
    row = conn.execute(select(records.c.data).where(records.c.id == ident, records.c.kind == 'relational_contract',
                                                    records.c.project_id == pid)).scalar_one_or_none() if ident else None
    if not row:
        raise error('DEFINITION_INTEGRITY', 'The relational definition is not bound to a registered contract in this workspace.')
    try:
        contract = validate_contract({k: v for k, v in row.items()
                                      if k not in {'id', 'created_at', 'project_id', 'version_number'}})
    except CoreError as exc:
        raise error('DEFINITION_INTEGRITY', f'Registered relational contract failed validation: {exc}') from exc
    if contract['contract_hash'] != version.get('contract_hash'):
        raise error('DEFINITION_INTEGRITY', 'Definition contract hash differs from its registered relational contract.')
    return contract


def validate_definition(version, contract, error):
    ir = version.get('ir')
    try:
        validate_ir(ir, contract)
    except CoreError as exc:
        raise error('DEFINITION_INTEGRITY', f'Relational definition IR failed validation: {exc}') from exc
    if version.get('ir_hash') != ir['ir_hash']:
        raise error('DEFINITION_INTEGRITY', 'Definition IR hash differs from its sealed IR.')
    return ir


def validate_proposal(expectations, contract, error):
    """Reject unregistered or non-categorical qualified references against the approved contract."""
    table_schemas = schemas(contract)
    roles = {contract['fact']['table']: 'fact', contract['dimension']['table']: 'dimension'}
    problems = []
    for role in ('fact', 'dimension'):
        name = expectations[role]['table']
        if name not in roles:
            problems.append(f'{role} table {name} is not registered in the approved relational contract')
    if not problems:
        fact, dim = expectations['fact']['table'], expectations['dimension']['table']
        for table, column, label in ((fact, expectations['fact']['key'], 'fact key'),
                                     (fact, expectations['relationship']['fact_foreign_key'], 'fact foreign key'),
                                     (dim, expectations['dimension']['key'], 'dimension key')):
            if column not in table_schemas[table]:
                problems.append(f'{label} {table}.{column} is not a registered column')
        for field in expectations['expected_fields']:
            table, column = field['table'], field['column']
            if column not in contract[roles[table]]['categories']:
                problems.append(f'{table}.{column} is not a registered categorical field')
    if problems:
        raise error('METRIC_CONTRACT_INVALID', 'Relational governance references are invalid: ' + '; '.join(problems) + '.')


def _unknown(value):
    return not value or str(value).strip().lower() in _UNKNOWN_UNITS


def relational_checks(revision, metric, version, definition, snapshot, binding, definition_contract, error):
    """Return (checks, observed, assurance). Raises structured refusals on integrity failures."""
    expected = revision['expectations']
    source = expected['source_expectations']
    relationship = expected['relationship']
    pin = revision['approved_definition']
    manifest = snapshot.get('manifest') or {}
    checks = []

    def check(category, field, wanted, observed, *, status=None, unknown=False, note=''):
        if status is None:
            status = 'unverified' if unknown or observed is None else 'match' if wanted == observed else 'mismatch'
        checks.append({'category': category, 'field': field, 'expected': wanted, 'observed': observed,
                       'status': status, 'note': note})

    # Frozen relational snapshot: validate with the core validator and pin record identity.
    contract = None
    snapshot_relational = snapshot.get('execution_policy') == EXECUTION_POLICY or manifest.get('mode') == MODE
    if snapshot_relational:
        try:
            contract = validate_manifest(manifest, manifest.get('contract'))
        except (CoreError, TypeError, KeyError, AttributeError, ValueError) as exc:
            raise error('SNAPSHOT_INTEGRITY', f'Frozen relational manifest failed validation: {exc}') from exc
        if (snapshot.get('manifest_hash') != manifest.get('manifest_hash') or
                snapshot.get('contract_hash') != contract['contract_hash'] or
                snapshot.get('execution_policy') != EXECUTION_POLICY):
            raise error('SNAPSHOT_INTEGRITY', 'Snapshot record identity does not match its frozen relational manifest.')
    ir = validate_definition(version, definition_contract, error) if definition_contract is not None else None

    # Identity and pinned baseline.
    identity = revision['metric_identity']
    check('identity', 'display_name', identity['display_name'], metric['display_name'])
    check('identity', 'importance', identity['importance'], metric['importance'])
    check('identity', 'approved_baseline', pin['version_id'], binding['approved_version_id'])
    check('identity', 'definition_revision', pin['version_id'], version['id'])
    check('identity', 'definition_raw_hash', pin['raw_hash'], version.get('raw_hash'))
    check('identity', 'definition_ir_hash', pin['ir_hash'], version.get('ir_hash'))
    check('identity', 'definition_support', 'executable', version.get('support_state'))

    # Source mode and execution policy compatibility.
    snapshot_mode = manifest.get('mode') or 'retail'
    version_mode = (version.get('ir') or {}).get('mode')
    version_mode = version_mode if version_mode in ('flat_snapshot', MODE) else 'retail'
    check('policy', 'source_mode', source['mode'], snapshot_mode)
    check('policy', 'snapshot_execution_policy', source['execution_policy'],
          snapshot.get('execution_policy') or manifest.get('execution_policy'),
          note='Snapshots without a registered execution policy remain unverified.')
    check('policy', 'definition_mode', source['mode'], version_mode)
    check('policy', 'definition_execution_policy', source['execution_policy'], version.get('execution_policy'))
    if source['source_label'] is not None:
        check('policy', 'source_label', source['source_label'], definition['source_label'])
    snapshot_hash = contract['contract_hash'] if contract else (manifest.get('contract_hash') or snapshot.get('contract_hash'))
    if source['contract_hash'] is not None:
        check('policy', 'source_contract_hash', source['contract_hash'], snapshot_hash)
    if pin.get('contract_hash') is not None:
        check('policy', 'definition_dataset_contract', pin['contract_hash'], snapshot_hash,
              note='The approved implementation was registered against this dataset contract.')
    check('policy', 'selected_definition_contract', snapshot_hash, version.get('contract_hash'),
          note='The selected definition IR was validated against its own registered contract; a matching hash binds it to this frozen snapshot.')

    missing = 'Not evaluated: the selected snapshot is not a relational_snapshot_v1 snapshot.'

    # Declared relational structure versus the frozen contract.
    def structural(field, wanted, path):
        if contract is None:
            check('structure', field, wanted, None, note=missing)
            return
        value = contract
        for part in path:
            value = value[part]
        check('structure', field, wanted, value)

    structural('fact.table', expected['fact']['table'], ('fact', 'table'))
    structural('dimension.table', expected['dimension']['table'], ('dimension', 'table'))
    structural('fact.key', expected['fact']['key'], ('fact', 'key'))
    structural('dimension.key', expected['dimension']['key'], ('dimension', 'key'))
    structural('relationship.fact_foreign_key', relationship['fact_foreign_key'], ('relationship', 'fact_foreign_key'))
    structural('relationship.dimension_key', relationship['dimension_key'], ('relationship', 'dimension_key'))
    structural('relationship.cardinality', relationship['cardinality'], ('relationship', 'cardinality'))
    structural('relationship.missing_dimension_policy', relationship['missing_dimension_policy'],
               ('relationship', 'missing_dimension_policy'))
    if ir is None:
        check('structure', 'relationship.join_kind', relationship['allowed_join_kinds'], None,
              note='Not evaluated: the selected definition is not a relational implementation.')
    else:
        check('structure', 'relationship.join_kind', relationship['allowed_join_kinds'], ir['join'],
              status='match' if ir['join'] in relationship['allowed_join_kinds'] else 'mismatch',
              note='The selected definition join kind must be one of the allowed join kinds.')

    for item in expected['expected_fields']:
        name = f"field.{item['table']}.{item['column']}"
        if contract is None:
            check('structure', name, item['type'], None, note=missing)
            continue
        table_schemas = schemas(contract)
        roles = {contract['fact']['table']: 'fact', contract['dimension']['table']: 'dimension'}
        if item['table'] not in roles:
            check('structure', name, item['type'], None, status='mismatch',
                  note='Qualified table is not registered in the frozen relational contract.')
            continue
        physical = table_schemas[item['table']].get(item['column'])
        elsewhere = [t for t, cols in table_schemas.items() if t != item['table'] and item['column'] in cols]
        if physical is None:
            hint = f' A column with this name exists only in {elsewhere[0]}; qualification is not inferred.' if elsewhere else ''
            check('structure', name, item['type'], None, status='mismatch',
                  note='Expected field is absent from the registered table.' + hint)
        elif item['column'] not in contract[roles[item['table']]]['categories']:
            check('structure', name, item['type'], None, status='mismatch',
                  note='Registered column is a key or measure, not a categorical field.')
        else:
            logical = 'decimal' if str(physical).upper().startswith('DECIMAL(') else _LOGICAL.get(str(physical).upper())
            check('structure', name, item['type'], logical)

    # Declarations: text agreement with the frozen registration, not proof of meaning or grain.
    if contract is None:
        observed_unit = manifest.get('unit_label') or snapshot.get('unit_label') or snapshot.get('currency')
    else:
        observed_unit = contract['unit_label']
    check('declaration', 'unit_label', expected['unit_label'], observed_unit,
          unknown=_unknown(observed_unit) or _unknown(expected['unit_label']),
          note='Declared labels are compared without currency inference or conversion; unknown currency remains unverified.')
    for role in ('fact', 'dimension'):
        check('declaration', f'{role}.grain', expected[role]['grain'], contract[role]['grain'] if contract else None,
              note='Matching declared grain text is metadata agreement, not proof of business grain.' if contract else missing)

    # Observed structural proof recorded when the snapshot was frozen.
    proof = manifest.get('join_proof') if contract is not None else None
    if proof is None:
        for field in ('fact_key_unique', 'dimension_key_unique', 'row_preservation', 'fanout_rows',
                      'max_dimension_matches_per_fact', 'unmatched_fact_rows'):
            check('join_proof', f'join_proof.{field}', None, None, note=missing)
    else:
        proof_note = 'Observed on the frozen snapshot at import; it does not establish source authenticity.'
        check('join_proof', 'join_proof.fact_key_unique', True, proof['fact_key_unique'], note=proof_note)
        check('join_proof', 'join_proof.dimension_key_unique', True, proof['dimension_key_unique'], note=proof_note)
        check('join_proof', 'join_proof.row_preservation', proof['fact_rows'], proof['left_join_rows'],
              note='LEFT join row count equals fact row count, so facts were not multiplied.')
        check('join_proof', 'join_proof.fanout_rows', 0, proof['fanout_rows'], note=proof_note)
        check('join_proof', 'join_proof.max_dimension_matches_per_fact', '≤ 1', proof['max_dimension_matches_per_fact'],
              status='match' if proof['max_dimension_matches_per_fact'] <= 1 else 'mismatch', note=proof_note)
        unmatched = {'unmatched_fact_rows': proof['unmatched_fact_rows'],
                     'null_foreign_key_rows': proof['null_foreign_key_rows'],
                     'missing_nonnull_foreign_key_rows': proof['missing_nonnull_foreign_key_rows']}
        join_note = ''
        if ir is not None and proof['unmatched_fact_rows']:
            join_note = (f" The selected {ir['join'].upper()} join "
                         f"{'retains' if ir['join'] == 'left' else 'excludes'} {proof['unmatched_fact_rows']} unmatched facts.")
        reported = contract['relationship']['missing_dimension_policy'] == 'preserve_and_report'
        check('join_proof', 'join_proof.unmatched_fact_rows', 'preserve_and_report', unmatched,
              status='match' if reported else 'mismatch',
              note='Unmatched facts are preserved and reported, never imputed.' + join_note)

    observed = {'snapshot_contract_hash': snapshot_hash,
                'definition_contract_hash': version.get('contract_hash'),
                'definition_join_kind': ir['join'] if ir else None,
                'relationship': contract['relationship'] if contract else None,
                'fact': {k: contract['fact'][k] for k in ('table', 'key', 'grain')} if contract else None,
                'dimension': {k: contract['dimension'][k] for k in ('table', 'key', 'grain')} if contract else None,
                'join_proof': proof}
    assurance = {
        'declared_business_semantics': {
            'status': 'declared_not_proven',
            'note': 'Owner, meaning, unit and grain are human declarations; agreement with registered metadata does not prove business meaning or grain.'},
        'structural_join_proof': {
            'status': ('observed_preserved' if proof and proof['grain_preserved'] and proof['fanout_rows'] == 0
                       else 'not_evaluated' if proof is None else 'observed_violation'),
            'note': 'Frozen key-uniqueness and row-count proof for this snapshot only.'},
        'source_authenticity': {
            'status': 'not_independently_verified',
            'source_hashes': manifest.get('source_hashes') if contract else None,
            'note': 'Source hashes identify the supplied bytes; metadata agreement does not prove the extracts are authentic or complete.'},
    }
    return checks, observed, assurance
