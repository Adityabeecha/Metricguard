"""Versioned mapping of a declared flat extract to the supported orders roles."""

import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import tempfile

import duckdb

from .common import CoreError, DATES, NUMERIC, SCHEMA, digest, file_hash
from .snapshot import MAX_BYTES, NULLABLE, manifest_path


NULL_MARKER = '__METRICGUARD_NULL_7c56b3e4__'
MAX_ROWS = 1_000_000
MAX_PREVIEW_BYTES = 64 * 1024
MAX_SAMPLE_CHARS = 256


def validate_contract(value: dict) -> dict:
    """Freeze the supported semantic roles and business context as a hash."""
    required = {'version', 'name', 'currency', 'timezone', 'scope', 'grain',
                'cutoff_at', 'null_policy', 'row_key', 'entity_key',
                'decimal_scale', 'schema'}
    if not isinstance(value, dict) or set(value) not in (required, required | {'contract_hash'}):
        raise CoreError('invalid_contract', 'Contract fields do not match the supported v2 policy')
    payload = {key: value[key] for key in required}
    if (payload['version'] != 2 or payload['schema'] != SCHEMA or
            payload['row_key'] != 'order_id' or payload['entity_key'] != 'customer_id' or
            payload['decimal_scale'] != 2 or
            payload['null_policy'] not in {'blank_nullable', 'literal_NULL', 'native_null'}):
        raise CoreError('invalid_contract', 'Unsupported v2 contract semantics')
    if any(not isinstance(payload[key], str) or not payload[key].strip() or
           len(payload[key]) > limit for key, limit in (
           ('name', 160), ('currency', 8), ('timezone', 64),
           ('scope', 200), ('grain', 100))):
        raise CoreError('invalid_contract', 'Contract metadata is missing or too long')
    if not re.fullmatch('[A-Z]{3}', payload['currency']):
        raise CoreError('invalid_contract', 'Currency must be an explicit ISO-style code')
    try:
        if len(payload['cutoff_at']) != 10:
            raise ValueError()
        date.fromisoformat(payload['cutoff_at'])
    except (TypeError, ValueError):
        raise CoreError('invalid_contract', 'A YYYY-MM-DD extraction cutoff is required')
    contract_hash = digest(payload)
    if 'contract_hash' in value and value['contract_hash'] != contract_hash:
        raise CoreError('invalid_contract', 'Contract hash does not match its contents')
    return {**payload, 'contract_hash': contract_hash}


def validate_mapping(value: dict, contract: dict) -> dict:
    """Require one explicitly approved source column for each analytical role."""
    contract = validate_contract(contract)
    if not isinstance(value, dict) or set(value) not in ({'columns'}, {'columns', 'mapping_hash', 'contract_hash'}):
        raise CoreError('invalid_mapping', 'Mapping fields are invalid')
    columns = value['columns']
    if (not isinstance(columns, dict) or set(columns) != set(SCHEMA) or
            any(not isinstance(source, str) or not source or len(source) > 128 or
                any(ord(char) < 32 for char in source) for source in columns.values()) or
            len(set(columns.values())) != len(columns)):
        raise CoreError('invalid_mapping', 'Every analytical role needs one distinct source column')
    payload = {'columns': columns, 'contract_hash': contract['contract_hash']}
    mapping_hash = digest(payload)
    if (value.get('mapping_hash', mapping_hash) != mapping_hash or
            value.get('contract_hash', contract['contract_hash']) != contract['contract_hash']):
        raise CoreError('invalid_mapping', 'Mapping hash or contract differs')
    return {**payload, 'mapping_hash': mapping_hash}


def _validated_row(raw: dict, mapping: dict, contract: dict, ids: set) -> list:
    result = []
    for role in SCHEMA:
        value = raw[mapping['columns'][role]]
        null_token = '' if contract['null_policy'] == 'blank_nullable' else 'NULL'
        is_null = value == null_token and role in NULLABLE
        if is_null:
            result.append(NULL_MARKER)
            continue
        if value == NULL_MARKER:
            raise CoreError('invalid_row', 'Source contains the reserved NULL marker')
        if role in DATES:
            if len(value) != 10:
                raise CoreError('invalid_row', f'{role} requires YYYY-MM-DD')
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise CoreError('invalid_row', f'{role} has an invalid date') from exc
        elif role in NUMERIC:
            if not re.fullmatch(r'-?\d+(\.\d{1,2})?', value):
                raise CoreError('invalid_row', f'{role} requires a fixed-scale decimal')
            try:
                amount = Decimal(value)
            except InvalidOperation as exc:
                raise CoreError('invalid_row', f'{role} has an invalid decimal') from exc
            if not amount.is_finite() or abs(amount) >= Decimal('1e16'):
                raise CoreError('invalid_row', f'{role} exceeds DECIMAL(18,2)')
        elif not value and role not in NULLABLE:
            raise CoreError('invalid_row', f'{role} is required')
        if role == 'currency' and value != contract['currency']:
            raise CoreError('invalid_row', 'Mixed or incompatible currency')
        if role == 'order_id':
            if value in ids:
                raise CoreError('invalid_row', 'Duplicate row key')
            ids.add(value)
        result.append(value)
    return result


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _validated_parquet_row(row: tuple, contract: dict, ids: set) -> None:
    values = dict(zip(SCHEMA, row))
    for role, value in values.items():
        if value is None:
            if role not in NULLABLE:
                raise CoreError('invalid_row', f'{role} is required')
            continue
        if role in NUMERIC and (not value.is_finite() or abs(value) >= Decimal('1e16')):
            raise CoreError('invalid_row', f'{role} exceeds DECIMAL(18,2)')
        if role not in NUMERIC | DATES and value == '' and role not in NULLABLE:
            raise CoreError('invalid_row', f'{role} is required')
    if values['currency'] != contract['currency']:
        raise CoreError('invalid_row', 'Mixed or incompatible currency')
    if values['order_id'] in ids:
        raise CoreError('invalid_row', 'Duplicate row key')
    ids.add(values['order_id'])


def _load_mapped_csv(source: Path, destination: Path, contract: dict, mapping: dict, source_hash: str) -> int:
    with tempfile.TemporaryDirectory(prefix='mapped-', dir=destination.parent) as folder:
        canonical = Path(folder) / 'canonical.csv'
        ids = set()
        count = 0
        try:
            with source.open(encoding='utf-8-sig', newline='') as stream, canonical.open('w', encoding='utf-8', newline='') as output:
                reader = csv.DictReader(stream)
                if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames) or \
                        not set(mapping['columns'].values()).issubset(reader.fieldnames):
                    raise CoreError('invalid_schema', 'Mapped source columns are missing or duplicated')
                writer = csv.writer(output)
                writer.writerow(SCHEMA)
                for raw in reader:
                    if count >= MAX_ROWS:
                        raise CoreError('too_many_rows', 'Snapshot exceeds one million rows')
                    if None in raw or any(item is None for item in raw.values()):
                        raise CoreError('invalid_row', 'Source row has the wrong field count')
                    writer.writerow(_validated_row(raw, mapping, contract, ids))
                    count += 1
        except (UnicodeError, csv.Error) as exc:
            raise CoreError('invalid_csv', 'Source CSV could not be decoded or parsed') from exc
        if file_hash(source) != source_hash:
            raise CoreError('source_changed', 'Source changed during validation')
        with duckdb.connect(str(destination), config={'enable_external_access': 'true'}) as con:
            con.execute('CREATE TABLE orders (' + ', '.join(_quoted(name) + ' ' + kind for name, kind in SCHEMA.items()) + ')')
            con.execute("COPY orders FROM ? (FORMAT CSV, HEADER TRUE, NULLSTR '__METRICGUARD_NULL_7c56b3e4__')", [str(canonical)])
            if con.execute('SELECT COUNT(*) FROM orders').fetchone()[0] != count:
                raise CoreError('source_changed', 'Canonical row count changed during ingestion')
    return count


def _load_mapped_parquet(source: Path, destination: Path, contract: dict, mapping: dict, source_hash: str) -> int:
    source_name = str(source.resolve())
    selected = ', '.join(_quoted(mapping['columns'][role]) for role in SCHEMA)
    try:
        with duckdb.connect(':memory:', config={'enable_external_access': 'true'}) as con:
            description = con.execute('DESCRIBE SELECT * FROM read_parquet(?)', [source_name]).fetchall()
            source_types = {column[0]: column[1] for column in description}
            if any(source_types.get(mapping['columns'][role]) != kind for role, kind in SCHEMA.items()):
                raise CoreError('invalid_schema', 'Mapped Parquet columns are missing or have incompatible types')
            count = con.execute('SELECT COUNT(*) FROM read_parquet(?)', [source_name]).fetchone()[0]
            if count > MAX_ROWS:
                raise CoreError('too_many_rows', 'Snapshot exceeds one million rows')
            ids = set()
            cursor = con.execute(f'SELECT {selected} FROM read_parquet(?)', [source_name])
            validated = 0
            while rows := cursor.fetchmany(10000):
                for row in rows:
                    _validated_parquet_row(row, contract, ids)
                    validated += 1
            if validated != count:
                raise CoreError('source_changed', 'Parquet row count changed during validation')
        if file_hash(source) != source_hash:
            raise CoreError('source_changed', 'Source changed during validation')
        with duckdb.connect(str(destination), config={'enable_external_access': 'true'}) as con:
            con.execute('CREATE TABLE orders (' + ', '.join(_quoted(name) + ' ' + kind for name, kind in SCHEMA.items()) + ')')
            con.execute(f'INSERT INTO orders SELECT {selected} FROM read_parquet(?)', [source_name])
            if con.execute('SELECT COUNT(*) FROM orders').fetchone()[0] != count:
                raise CoreError('source_changed', 'Parquet row count changed during ingestion')
        return count
    except CoreError:
        raise
    except duckdb.Error as exc:
        raise CoreError('invalid_parquet', 'Mapped Parquet could not be read or loaded') from exc


def ingest_mapped(source, destination, contract, mapping) -> dict:
    """Validate a mapped CSV or Parquet and publish one immutable snapshot."""
    source, destination = Path(source), Path(destination)
    contract = validate_contract(contract)
    mapping = validate_mapping(mapping, contract)
    if destination.exists() or manifest_path(destination).exists():
        raise CoreError('immutable_snapshot', 'Snapshot destination already exists')
    source_format = source.suffix.lower()
    if source_format not in {'.csv', '.parquet'}:
        raise CoreError('unsupported_format', 'Mapped ingestion requires CSV or Parquet')
    if source_format == '.csv' and contract['null_policy'] == 'native_null':
        raise CoreError('invalid_contract', 'CSV requires a declared text NULL token')
    if source_format == '.parquet' and contract['null_policy'] != 'native_null':
        raise CoreError('invalid_contract', 'Parquet requires native NULL values')
    if source.stat().st_size > MAX_BYTES:
        raise CoreError('file_too_large', 'Source exceeds 25 MiB')
    source_hash = file_hash(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = (_load_mapped_csv(source, destination, contract, mapping, source_hash)
                 if source_format == '.csv' else
                 _load_mapped_parquet(source, destination, contract, mapping, source_hash))
        if file_hash(source) != source_hash:
            raise CoreError('source_changed', 'Source changed during ingestion')
        manifest = {'currency': contract['currency'], 'timezone': contract['timezone'],
                    'scope': contract['scope'], 'grain': contract['grain'],
                    'cutoff_at': contract['cutoff_at'], 'null_policy': contract['null_policy'],
                    'row_count': count, 'schema': SCHEMA, 'primary_key': 'order_id',
                    'source_hash': source_hash, 'database_hash': file_hash(destination),
                    'contract_hash': contract['contract_hash'],
                    'mapping_hash': mapping['mapping_hash'],
                    'ingestion_version': 'mapped-csv-v2' if source_format == '.csv' else 'mapped-parquet-v2',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'transformations': ['Explicit one-to-one source column mapping to canonical analytical roles'] +
                    (['Native Parquet NULL values preserved'] if source_format == '.parquet' else [])}
        manifest['manifest_hash'] = digest(manifest)
        manifest_path(destination).write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
        return manifest
    except Exception:
        destination.unlink(missing_ok=True)
        manifest_path(destination).unlink(missing_ok=True)
        raise


def _preview_value(value):
    if value is None:
        return None
    rendered = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    if len(rendered) > MAX_SAMPLE_CHARS:
        return rendered[:MAX_SAMPLE_CHARS - 1] + '…'
    return rendered


def _csv_observed_type(value: str) -> str:
    if value == '':
        return 'NULL'
    if len(value) == 10:
        try:
            date.fromisoformat(value)
            return 'DATE'
        except ValueError:
            pass
    if re.fullmatch(r'-?\d+(\.\d{1,2})?', value):
        try:
            if abs(Decimal(value)) < Decimal('1e16'):
                return 'DECIMAL(18,2)'
        except InvalidOperation:
            pass
    return 'VARCHAR'


def _bounded_preview(headers: list[str], declared: dict, rows: list[dict], source_hash: str,
                     *, csv_source: bool) -> dict:
    observed = {}
    for header in headers:
        types = {(_csv_observed_type(row[header]) if csv_source else declared[header])
                 for row in rows if row[header] is not None and row[header] != ''}
        observed[header] = next(iter(types)) if len(types) == 1 else ('NULL' if not types else 'MIXED')
    preview = {'headers': headers, 'declared_types': declared,
               'observed_types': observed,
               'sample_rows': [{name: _preview_value(row[name]) for name in headers} for row in rows],
               'source_hash': source_hash}
    def output_bytes() -> int:
        return len(json.dumps(preview, ensure_ascii=True, allow_nan=False).encode('utf-8'))

    while output_bytes() > MAX_PREVIEW_BYTES and preview['sample_rows']:
        preview['sample_rows'].pop()
    if output_bytes() > MAX_PREVIEW_BYTES:
        raise CoreError('preview_too_large', 'Source headers exceed the preview limit')
    return preview


def preview_source(source: Path) -> dict:
    """Inspect a bounded CSV or Parquet source in the worker, before mapping approval."""
    source = Path(source)
    source_format = source.suffix.lower()
    if source_format not in {'.csv', '.parquet'}:
        raise CoreError('unsupported_format', 'Preview requires CSV or Parquet')
    if source.stat().st_size > MAX_BYTES:
        raise CoreError('file_too_large', 'Source exceeds 25 MiB')
    source_hash = file_hash(source)
    if source_format == '.csv':
        try:
            with source.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                headers = reader.fieldnames
                if not headers or len(headers) != len(set(headers)) or any(not header for header in headers):
                    raise CoreError('invalid_schema', 'CSV headers are missing or duplicated')
                rows = []
                for count, row in enumerate(reader, 1):
                    if count > MAX_ROWS:
                        raise CoreError('too_many_rows', 'Source exceeds one million rows')
                    if None in row or any(value is None for value in row.values()):
                        raise CoreError('invalid_row', 'Source row has the wrong field count')
                    if len(rows) < 5:
                        rows.append(row)
        except (UnicodeError, csv.Error) as exc:
            raise CoreError('invalid_csv', 'Source CSV could not be decoded or parsed') from exc
        declared = dict.fromkeys(headers)
    else:
        try:
            with duckdb.connect(':memory:', config={'enable_external_access': 'true'}) as con:
                source_name = str(source.resolve())
                description = con.execute('DESCRIBE SELECT * FROM read_parquet(?)', [source_name]).fetchall()
                headers = [column[0] for column in description]
                if not headers or len(headers) != len(set(headers)):
                    raise CoreError('invalid_schema', 'Parquet headers are missing or duplicated')
                declared = {column[0]: column[1] for column in description}
                _bounded_preview(headers, declared, [], source_hash, csv_source=False)
                if con.execute('SELECT COUNT(*) FROM read_parquet(?)', [source_name]).fetchone()[0] > MAX_ROWS:
                    raise CoreError('too_many_rows', 'Source exceeds one million rows')
                projection = ', '.join(
                    f'LEFT(CAST({_quoted(name)} AS VARCHAR), {MAX_SAMPLE_CHARS + 1}) AS {_quoted(name)}'
                    for name in headers)
                rows = [dict(zip(headers, row)) for row in con.execute(
                    f'SELECT {projection} FROM read_parquet(?) LIMIT 5', [source_name]).fetchall()]
        except CoreError:
            raise
        except duckdb.Error as exc:
            raise CoreError('invalid_parquet', 'Parquet preview could not be read') from exc
    if file_hash(source) != source_hash:
        raise CoreError('source_changed', 'Source changed during preview')
    return _bounded_preview(headers, declared, rows, source_hash, csv_source=source_format == '.csv')
