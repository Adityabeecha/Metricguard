"""Bounded two-CSV ingestion and grain-preservation proof for relational v1."""

import csv
import io
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .free_tier import check_rows, check_upload_size
from .common import CoreError, digest, file_hash
from .numeric import ScaledInteger
from .snapshot import manifest_path
from .relational import (EXECUTION_POLICY, MODE, POLICY, MAX_BYTES, MAX_ROWS,
                         fail, quote, schemas, validate_contract)

NULL_MARKER = '__METRICGUARD_RELATIONAL_NULL_54ce__'
AMOUNT = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')


def amount(value, contract):
    if value is None:
        return None
    m = contract['measure']
    if not isinstance(value, str) or len(value) > 128 or not AMOUNT.fullmatch(value):
        fail('invalid_amount', 'Amounts must be exact ASCII decimal strings without exponents, whitespace or separators.')
    parsed = ScaledInteger.parse(value)
    if parsed.scale > m['scale']:
        fail('scale_mismatch', f'Amount has more than the declared {m["scale"]} decimal places; rounding is refused.')
    coeff = parsed.coeff * 10 ** (m['scale'] - parsed.scale)
    if abs(coeff) >= 10 ** m['precision']:
        fail('invalid_amount', 'Amount exceeds the declared decimal precision.')
    return ScaledInteger(coeff, m['scale']).to_fixed_string()


def join_sql(contract):
    c = validate_contract(contract)
    return (f'FROM {quote(c["fact"]["table"])} f LEFT JOIN {quote(c["dimension"]["table"])} d ON '
            f'f.{quote(c["relationship"]["fact_foreign_key"])} = d.{quote(c["dimension"]["key"])}')


def prove_grain(con, contract):
    """Prove at most one joined dimension row per fact; report orphan keys."""
    c = validate_contract(contract)
    counts = {}
    for role in ('fact', 'dimension'):
        table, key = quote(c[role]['table']), quote(c[role]['key'])
        total, distinct, missing = con.execute(
            f'SELECT COUNT(*), COUNT(DISTINCT {key}), COUNT(*) FILTER (WHERE {key} IS NULL OR {key} = \'\') FROM {table}'
        ).fetchone()
        check_rows(total)
        if not 0 <= total <= MAX_ROWS or distinct != total or missing:
            fail('invalid_keys', f'{role} key must be unique, non-NULL and non-empty across the complete table.')
        counts[role] = total
    fk, dk = quote(c['relationship']['fact_foreign_key']), quote(c['dimension']['key'])
    total, matched, missing, null_fk = con.execute(
        f'SELECT COUNT(*), COUNT(d.{dk}), COUNT(*) FILTER (WHERE d.{dk} IS NULL), '
        f'COUNT(*) FILTER (WHERE f.{fk} IS NULL) {join_sql(c)}'
    ).fetchone()
    if total != counts['fact']:
        fail('join_fanout', 'Join multiplied fact rows; this relationship cannot execute.')
    return {'fact_rows': counts['fact'], 'dimension_rows': counts['dimension'],
            'fact_key_unique': True, 'dimension_key_unique': True,
            'left_join_rows': total, 'matched_fact_rows': matched,
            'unmatched_fact_rows': missing, 'null_foreign_key_rows': null_fk,
            'missing_nonnull_foreign_key_rows': missing - null_fk,
            'max_dimension_matches_per_fact': 1 if matched else 0,
            'fanout_rows': 0, 'grain_preserved': True}


def ingest_relational(source, destination, payload):
    if isinstance(payload, dict) and payload.get('version') in (2, 3):
        from .relational_star import ingest
        return ingest(source, destination, payload)
    c = validate_contract(payload)
    source, destination = Path(source), Path(destination)
    if destination.exists() or manifest_path(destination).exists():
        fail('immutable_snapshot', 'Snapshot destination already exists.')
    check_rows(0)
    check_upload_size(source.stat().st_size)
    if source.stat().st_size > MAX_BYTES + 4096:
        fail('file_too_large', 'Relational upload exceeds 25 MiB plus bundle metadata.')
    source_hash = file_hash(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hashes, row_counts = {}, {}
    try:
        with zipfile.ZipFile(source) as bundle, tempfile.TemporaryDirectory(prefix='relational-', dir=destination.parent) as folder:
            entries = bundle.infolist()
            check_upload_size(sum(entry.file_size for entry in entries))
            if (len(entries) != 2 or {e.filename for e in entries} != {'fact.csv', 'dimension.csv'} or
                    any(e.flag_bits & 1 or e.file_size > MAX_BYTES for e in entries) or
                    sum(e.file_size for e in entries) > MAX_BYTES):
                fail('invalid_bundle', 'The internal bundle requires exactly two bounded, unencrypted CSV sources.')
            import hashlib
            for role in ('fact', 'dimension'):
                entry = bundle.getinfo(role + '.csv')
                # Bounded read, independent of the archive's claimed byte size.
                with bundle.open(entry) as stream:
                    content = stream.read(MAX_BYTES + 1)
                if len(content) != entry.file_size or not content:
                    fail('invalid_bundle', 'CSV byte counts are inconsistent or empty.')
                source_hashes[role] = hashlib.sha256(content).hexdigest()
                schema = schemas(c)[c[role]['table']]
                columns = list(schema)
                try:
                    reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig'), newline=''))
                    if (reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)) or
                            len(reader.fieldnames) > 64 or any(len(n) > 128 for n in reader.fieldnames) or
                            not set(columns).issubset(reader.fieldnames)):
                        fail('invalid_schema', f'{role} CSV must contain every registered column and distinct headings (max 64).')
                    token = '' if c['null_policy'] == 'blank_nullable' else 'NULL'
                    keys, count = set(), 0
                    with (Path(folder) / (role + '.csv')).open('w', encoding='utf-8', newline='') as output:
                        writer = csv.writer(output); writer.writerow(columns)
                        for line, raw in enumerate(reader, 2):
                            check_rows(count + 1)
                            if count >= MAX_ROWS:
                                fail('too_many_rows', f'{role} CSV exceeds {MAX_ROWS} rows.')
                            if None in raw or any(v is None for v in raw.values()):
                                fail('invalid_row', f'{role} CSV has an inconsistent field count at line {line}.')
                            values = {name: None if raw[name] == token else raw[name] for name in columns}
                            for name, value in values.items():
                                if value is not None and (len(value) > 256 or '\x00' in value or value == NULL_MARKER):
                                    fail('invalid_row', f'{role}.{name} exceeds 256 characters or contains a reserved value at line {line}.')
                            key = values[c[role]['key']]
                            if not key or key in keys:
                                fail('invalid_keys', f'{role} key is missing or duplicated at line {line}; no deduplication is performed.')
                            keys.add(key)
                            if role == 'fact':
                                values[c['measure']['column']] = amount(values[c['measure']['column']], c)
                                foreign_key = values[c['relationship']['fact_foreign_key']]
                                if foreign_key == '':
                                    fail('invalid_keys', f'An empty foreign key must use the declared NULL token at line {line}.')
                            writer.writerow(NULL_MARKER if values[n] is None else values[n] for n in columns)
                            count += 1
                    row_counts[role] = count
                except (UnicodeError, csv.Error) as error:
                    raise CoreError('invalid_csv', f'{role} source is not a supported UTF-8 CSV.') from error
            with duckdb.connect(str(destination), config={'threads': '1', 'memory_limit': '256MB'}) as con:
                for role in ('fact', 'dimension'):
                    schema = schemas(c)[c[role]['table']]
                    con.execute(f'CREATE TABLE {quote(c[role]["table"])} (' + ', '.join(quote(n) + ' ' + t for n, t in schema.items()) + ')')
                    con.execute(f"COPY {quote(c[role]['table'])} FROM ? (FORMAT CSV, HEADER TRUE, NULLSTR '{NULL_MARKER}')", [str(Path(folder) / (role + '.csv'))])
                proof = prove_grain(con, c)
                if proof['fact_rows'] != row_counts['fact'] or proof['dimension_rows'] != row_counts['dimension']:
                    fail('source_changed', 'Loaded table counts differ from the validated sources.')
            if file_hash(source) != source_hash:
                fail('source_changed', 'Source bundle changed during import.')
            table_schemas = schemas(c)
            manifest = {'mode': MODE, 'policy': POLICY, 'execution_policy': EXECUTION_POLICY,
                        'contract_version': 1, 'contract': c, 'contract_hash': c['contract_hash'],
                        'source_hash': source_hash, 'source_hashes': source_hashes, 'database_hash': file_hash(destination),
                        'schema': {table + '.' + col: kind for table, cols in table_schemas.items() for col, kind in cols.items()},
                        'table_schemas': table_schemas, 'primary_key': c['fact']['key'], 'row_count': proof['fact_rows'],
                        'join_proof': proof, 'scope': c['scope'], 'grain': c['fact']['grain'],
                        'unit_label': c['unit_label'], 'measure_meaning': c['measure_meaning'],
                        'null_policy': c['null_policy'], 'values_comparable': True,
                        'currency': None, 'timezone': None, 'cutoff_at': None,
                        'ingestion_version': POLICY, 'created_at': datetime.now(timezone.utc).isoformat(),
                        'transformations': ['Only registered fields copied; exact measure validation; no deduplication, key coercion or amount imputation.'],
                        'null_semantics': 'NULL fact foreign keys never match; LEFT retains unmatched facts with NULL dimension fields; INNER excludes them. NULL measures are ignored by SUM; empty/all-NULL SUM is NULL.'}
            manifest['manifest_hash'] = digest(manifest)
            manifest_path(destination).write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
            return manifest
    except zipfile.BadZipFile as error:
        destination.unlink(missing_ok=True); manifest_path(destination).unlink(missing_ok=True)
        raise CoreError('invalid_bundle', 'Internal source bundle could not be read.') from error
    except Exception:
        destination.unlink(missing_ok=True); manifest_path(destination).unlink(missing_ok=True)
        raise


def validate_manifest(manifest, contract, source_hash=None):
    if isinstance(contract, dict) and contract.get('version') in (2, 3):
        from .relational_star import validate_manifest as validate_star_manifest
        return validate_star_manifest(manifest, contract, source_hash)
    c = validate_contract(contract)
    ts = schemas(c)
    expected = {'mode': MODE, 'policy': POLICY, 'execution_policy': EXECUTION_POLICY,
                'contract_version': 1, 'contract': c, 'contract_hash': c['contract_hash'],
                'table_schemas': ts, 'schema': {t + '.' + n: typ for t, cols in ts.items() for n, typ in cols.items()},
                'primary_key': c['fact']['key'], 'scope': c['scope'], 'grain': c['fact']['grain'],
                'unit_label': c['unit_label'], 'measure_meaning': c['measure_meaning'],
                'null_policy': c['null_policy'], 'values_comparable': True,
                'currency': None, 'timezone': None, 'cutoff_at': None}
    if (not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in expected.items()) or
            type(manifest.get('contract_version')) is not int or manifest.get('values_comparable') is not True or
            manifest.get('manifest_hash') != digest({k: v for k, v in manifest.items() if k != 'manifest_hash'}) or
            (source_hash is not None and manifest.get('source_hash') != source_hash)):
        fail('snapshot_integrity', 'Frozen relational snapshot manifest does not match the approved contract or source.')
    proof = manifest.get('join_proof')
    from .relational import HASH
    if (not isinstance(manifest.get('source_hashes'), dict) or set(manifest['source_hashes']) != {'fact', 'dimension'} or
            any(not isinstance(v, str) or not HASH.fullmatch(v) for v in manifest['source_hashes'].values()) or
            any(not isinstance(manifest.get(k), str) or not HASH.fullmatch(manifest[k]) for k in ('source_hash', 'database_hash'))):
        fail('snapshot_integrity', 'Source lineage hashes are missing or invalid.')
    proof_fields = {'fact_rows', 'dimension_rows', 'left_join_rows', 'matched_fact_rows', 'unmatched_fact_rows',
                    'null_foreign_key_rows', 'missing_nonnull_foreign_key_rows', 'max_dimension_matches_per_fact', 'fanout_rows',
                    'fact_key_unique', 'dimension_key_unique', 'grain_preserved'}
    if not isinstance(proof, dict) or set(proof) != proof_fields:
        fail('snapshot_integrity', 'The frozen grain proof is incomplete.')
    for key in proof_fields - {'fact_key_unique', 'dimension_key_unique', 'grain_preserved'}:
        if type(proof[key]) is not int or not 0 <= proof[key] <= MAX_ROWS:
            fail('snapshot_integrity', 'The frozen grain proof has invalid counts.')
    if (any(proof[k] is not True for k in ('fact_key_unique', 'dimension_key_unique', 'grain_preserved')) or
            proof['left_join_rows'] != proof['fact_rows'] or proof['fanout_rows'] != 0 or
            proof['matched_fact_rows'] + proof['unmatched_fact_rows'] != proof['fact_rows'] or
            proof['null_foreign_key_rows'] + proof['missing_nonnull_foreign_key_rows'] != proof['unmatched_fact_rows'] or
            proof['max_dimension_matches_per_fact'] != (1 if proof['matched_fact_rows'] else 0) or
            type(manifest.get('row_count')) is not int or manifest['row_count'] != proof['fact_rows']):
        fail('snapshot_integrity', 'The frozen grain proof does not reconcile.')
    return c


def open_snapshot(database):
    database = Path(database)
    try:
        manifest = json.loads(manifest_path(database).read_text(encoding='utf-8'))
        c = validate_manifest(manifest, manifest['contract'])
        if file_hash(database) != manifest['database_hash']:
            fail('snapshot_integrity', 'Relational snapshot bytes changed.')
        return c, manifest
    except (OSError, ValueError, KeyError) as error:
        if isinstance(error, CoreError):
            raise
        raise CoreError('snapshot_missing', 'Relational snapshot or sidecar is unavailable.') from error


def verify_database(con, contract, manifest):
    if isinstance(contract, dict) and contract.get('version') in (2, 3):
        from .relational_star import verify_database as verify_star
        return verify_star(con, contract, manifest)
    for table, columns in schemas(contract).items():
        actual = {r[0]: r[1] for r in con.execute('DESCRIBE ' + quote(table)).fetchall()}
        if actual != columns:
            fail('snapshot_integrity', 'Relational table schema differs from the frozen contract.')
    if prove_grain(con, contract) != manifest['join_proof']:
        fail('snapshot_integrity', 'Current table keys or join cardinality differ from the frozen proof.')
