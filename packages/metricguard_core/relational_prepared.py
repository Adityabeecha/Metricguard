"""Pinned, internal relational-to-flat preparation, preserving proved fact grain.

User SQL is never executed here. Only registered identifiers enter generated SQL.
The prepared snapshot is explicitly a flat-policy artifact, with frozen relational
lineage. INNER joins determine the prepared population; LEFT retains unmatched facts.
"""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import duckdb

from .free_tier import check_rows
from .common import digest, file_hash
from .flat import ingest_flat, validate_contract as validate_flat
from .relational import fail, quote
from .relational_snapshot import open_snapshot, verify_database
from .snapshot import manifest_path

POLICY = 'relational-flat-preparation-v1'


def specification(contract, selected_columns, joins, name):
    if contract['version'] not in (2, 3):
        fail('unsupported_contract', 'Preparation requires a star contract v2 or v3.')
    roles = {'fact': contract['fact'], **{d['role']: d for d in contract['dimensions']}}
    if not isinstance(joins, dict) or set(joins) - (set(roles) - {'fact'}) or any(v not in ('left', 'inner') for v in joins.values()):
        fail('invalid_preparation', 'Choose LEFT or INNER for declared lookup roles only.')
    if not isinstance(selected_columns, list) or len(selected_columns) > 8 or len(set(selected_columns)) != len(selected_columns):
        fail('invalid_preparation', 'Select at most eight distinct categorical fields.')
    checked = []
    for field in sorted(selected_columns):
        if not isinstance(field, str) or field.count('.') != 1:
            fail('invalid_preparation', 'Fields must use role.column names.')
        role, column = field.split('.')
        if role not in roles or (role != 'fact' and role not in joins):
            fail('invalid_preparation', 'Selected fields must belong to fact or a selected lookup.')
        spec = roles[role]
        allowed = {spec['key'], *spec['categories']}
        if role == 'fact':
            allowed |= {d['fact_foreign_key'] for d in contract['dimensions']}
        if column not in allowed:
            fail('invalid_preparation', 'Only registered keys and categorical fields may be selected.')
        checked.append((field, role, column))
    # Prepared columns keep their source names, so analysts filter on `segment`, not `field_2`.
    # A name shared by two tables (or one of the fixed output columns) is prefixed with its role.
    counts = {}
    for _, _, column in checked:
        counts[column] = counts.get(column, 0) + 1
    mapping = {}
    for field, role, column in checked:
        name = column if counts[column] == 1 and column not in ('fact_key', 'amount') else f'{role}_{column}'
        if name in mapping.values() or name in ('fact_key', 'amount'):
            name = f'{role}_{column}_{len(mapping) + 1}'
        mapping[field] = name
    m = contract['measure']
    flat = validate_flat({'version': 5, 'mode': 'flat_snapshot', 'name': name,
        'row_key': 'fact_key', 'measure': 'amount', 'precision': m['precision'], 'scale': m['scale'],
        'categories': list(mapping.values()), 'dimension_types': {n: 'string' for n in mapping.values()},
        'nullable_categories': list(mapping.values()), 'predicate_policy': 'flat-boolean-v1',
        'measure_meaning': contract['measure_meaning'][:200], 'unit_label': contract['unit_label'],
        'values_comparable': True, 'scope': contract['scope'], 'null_policy': 'native_null'})
    return flat, mapping


def prepare(database, destination, *, selected_columns, joins, name):
    database, destination = Path(database), Path(destination)
    contract, source_manifest = open_snapshot(database)
    flat, mapping = specification(contract, selected_columns, joins, name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lineage_path = destination.with_suffix('.lineage.jsonl')
    if destination.exists() or manifest_path(destination).exists() or lineage_path.exists():
        fail('immutable_snapshot', 'Preparation destination already exists.')
    dims = {d['role']: d for d in contract['dimensions']}
    aliases = {'fact': 'f', **{role: 'd' + str(i) for i, role in enumerate(sorted(joins))}}
    column = lambda role, name: aliases[role] + '.' + quote(name)
    fields = [column('fact', contract['fact']['key']) + ' AS fact_key',
              column('fact', contract['measure']['column']) + ' AS amount']
    fields += [column(*field.split('.')) + ' AS ' + quote(output) for field, output in mapping.items()]
    from_sql = ' FROM ' + quote(contract['fact']['table']) + ' f'
    for role in sorted(joins):
        d = dims[role]
        from_sql += (' LEFT JOIN ' + quote(d['table']) + ' ' + aliases[role] + ' ON ' +
                     column('fact', d['fact_foreign_key']) + ' = ' + column(role, d['key']))
    where = [column(role, dims[role]['key']) + ' IS NOT NULL' for role, kind in joins.items() if kind == 'inner']
    condition = ' AND '.join(where) if where else 'TRUE'
    lineage_fields = [column('fact', contract['fact']['key'])]
    for role in sorted(joins):
        lineage_fields += [column('fact', dims[role]['fact_foreign_key']), column(role, dims[role]['key'])]
    lineage_fields.append('(' + condition + ')')
    try:
        with tempfile.TemporaryDirectory(prefix='relational-prepared-', dir=destination.parent) as folder:
            prepared = Path(folder) / 'snapshot.parquet'
            with duckdb.connect(str(database), read_only=True, config={'threads': '1', 'memory_limit': '256MB',
                    'autoinstall_known_extensions': 'false', 'autoload_known_extensions': 'false'}) as con:
                verify_database(con, contract, source_manifest)
                check_rows(con.execute('SELECT COUNT(*)' + from_sql + ' WHERE ' + condition).fetchone()[0])
                # All projections and predicates above derive solely from checked registered identifiers.
                con.execute('COPY (SELECT ' + ', '.join(fields) + from_sql + ' WHERE ' + condition +
                            ') TO ? (FORMAT PARQUET)', [str(prepared)])
                cursor = con.execute('SELECT ' + ', '.join(lineage_fields) + from_sql + ' ORDER BY ' + lineage_fields[0])
                with lineage_path.open('x', encoding='utf-8', newline='\n') as output:
                    while rows := cursor.fetchmany(1000):
                        for row in rows:
                            lookups = {}
                            for i, role in enumerate(sorted(joins)):
                                fk, key = row[1 + 2*i:3 + 2*i]
                                lookups[role] = {'foreign_key': fk, 'dimension_key': key, 'matched': key is not None,
                                    'outcome': 'matched' if key is not None else 'null_foreign_key' if fk is None else 'missing_dimension'}
                            output.write(json.dumps({'fact_key': row[0], 'lookups': lookups, 'prepared_included': row[-1]},
                                                    sort_keys=True, separators=(',', ':')) + '\n')
            if file_hash(database) != source_manifest['database_hash']:
                fail('snapshot_integrity', 'Relational source changed during preparation.')
            lineage = {'policy': POLICY, 'source_manifest_hash': source_manifest['manifest_hash'],
                'source_database_hash': source_manifest['database_hash'], 'source_contract_hash': contract['contract_hash'],
                'source_hashes': source_manifest['source_hashes'], 'join_proof': source_manifest['join_proof'],
                'joins': dict(sorted(joins.items())), 'column_mapping': mapping,
                'lineage_file': lineage_path.name, 'lineage_sha256': file_hash(lineage_path),
                'scope_note': 'Prepared population uses the declared joins; comparisons cannot change those joins.'}
            lineage['lineage_hash'] = digest(lineage)
            manifest = ingest_flat(prepared, destination, flat)
            manifest['relational_preparation'] = lineage
            manifest['manifest_hash'] = digest({k: v for k, v in manifest.items() if k != 'manifest_hash'})
            manifest_path(destination).write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
            return flat, manifest
    except Exception:
        destination.unlink(missing_ok=True)
        manifest_path(destination).unlink(missing_ok=True)
        lineage_path.unlink(missing_ok=True)
        raise
