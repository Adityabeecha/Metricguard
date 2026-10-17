"""Versioned whole-snapshot SUM comparison for explicitly declared flat extracts."""

import csv
import hashlib
import json
import re
import tempfile
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from .common import CoreError, digest, file_hash
from .flat_boolean import PREDICATE_POLICY, lower_boolean, validate_boolean, compile_boolean
from .numeric import ScaledInteger
from .snapshot import MAX_BYTES, manifest_path

POLICY = 'flat-snapshot-v1'
MEASURE_POLICY_V6 = 'flat-subtraction-v1'
MEASURE_POLICY_V7 = 'flat-case-v1'
MAX_FLAT_ROWS = 100_000
NULL_MARKER = '__METRICGUARD_FLAT_NULL_82f3c47a__'
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}\Z')
DECIMAL = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
ALLOWED_DIMENSION_TYPES = {'string', 'boolean', 'date', 'timestamp'}

FIELDS = {'version', 'mode', 'name', 'row_key', 'measure', 'precision', 'scale',
          'categories', 'measure_meaning', 'unit_label', 'values_comparable',
          'scope', 'null_policy'}
FIELDS_V4 = FIELDS | {'dimension_types'}
FIELDS_V6 = {'version', 'mode', 'name', 'row_key', 'decimal_columns', 'measure_expression',
             'measure_meaning', 'unit_label', 'scale', 'categories', 'dimension_types', 'values_comparable',
             'scope', 'null_policy', 'measure_policy', 'predicate_policy'}


def _error(code, message):
    raise CoreError(code, message)


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
TIMESTAMP_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\Z")
PHYSICAL_TYPES = {'string': 'VARCHAR', 'boolean': 'BOOLEAN', 'date': 'DATE', 'timestamp': 'TIMESTAMP'}


def _canonical_type(value, kind):
    if kind == 'string':
        return isinstance(value, str) and len(value) <= 256
    if kind == 'boolean':
        return value in ('true', 'false')
    pattern = DATE_PATTERN if kind == 'date' else TIMESTAMP_PATTERN
    if not isinstance(value, str) or not pattern.fullmatch(value):
        return False
    try:
        (date if kind == 'date' else datetime).fromisoformat(value)
        return True
    except ValueError:
        return False


def infer_dimension_type(values):
    """Infer from all non-NULL values under the blank_nullable CSV policy.

    Timestamps are timezone-free ISO local datetimes with at most microseconds.
    No trimming, case folding, offset conversion, or missing-value imputation.
    """
    values = [v for v in values if v is not None and v != '']
    for kind in ('boolean', 'date', 'timestamp'):
        if values and all(_canonical_type(v, kind) for v in values):
            return kind
    return 'string'


def flat_schema(contract):
    if contract['version'] == 6:
        schema = {contract['row_key']: 'VARCHAR'}
        for col in contract['decimal_columns']:
            schema[col['name']] = f"DECIMAL({col['precision']},{col['scale']})"
        for name in contract['categories']:
            schema[name] = PHYSICAL_TYPES[contract['dimension_types'][name]]
        return schema
    return {contract['row_key']: 'VARCHAR',
            contract['measure']: f"DECIMAL({contract['precision']},{contract['scale']})",
            **{name: PHYSICAL_TYPES[contract['dimension_types'][name]] if contract['version'] >= 4 else 'VARCHAR'
               for name in contract['categories']}}


def infer_dimension_types(sample_rows, categories):
    """Infer dimension types for given categories from list of row dictionaries."""
    result = {}
    for cat in categories:
        values = [row.get(cat) for row in sample_rows]
        result[cat] = infer_dimension_type(values)
    return result


def validate_contract(payload):
    optional = {'contract_hash', 'execution_policy', 'id', 'created_at', 'version_number', 'project_id',
                'nullable_categories'}
    if not isinstance(payload, dict):
        _error('invalid_contract', 'Flat contract payload must be a dictionary')
    ver = payload.get('version')
    if ver == 7 and type(ver) is not int:
        _error('invalid_contract', 'FlatContract v7 version must be an integer')
    if ver == 3:
        req_fields = FIELDS
        if 'dimension_types' in payload:
            _error('invalid_contract', 'Flat contract v3 does not support dimension_types')
    elif ver in (4, 5, 7):
        req_fields = FIELDS_V4 | ({'predicate_policy'} if ver in (5, 7) else set())
        if ver == 7:
            req_fields |= {'measure_policy'}
    elif ver == 6:
        req_fields = FIELDS_V6
    else:
        _error('invalid_contract', 'Flat contract version, mode, or scope is unsupported')

    if not req_fields.issubset(payload) or set(payload) - req_fields - optional:
        _error('invalid_contract', 'Flat contract fields are incomplete or unsupported')

    if ver < 5 and 'predicate_policy' in payload:
        _error('invalid_contract', 'Boolean tree policy requires a new FlatContract v5 snapshot')
    if ver in (5, 6, 7) and payload.get('predicate_policy') != PREDICATE_POLICY:
        _error('invalid_contract', f'FlatContract v{ver} requires predicate_policy flat-boolean-v1')
    if ver < 6:
        if 'measure_policy' in payload:
            _error('invalid_contract', 'Measure subtraction policy requires FlatContract v6')
        if 'measure_expression' in payload:
            _error('invalid_contract', 'Measure expressions require FlatContract v6')
        if 'decimal_columns' in payload:
            _error('invalid_contract', 'decimal_columns requires FlatContract v6')
    if ver == 6:
        if payload.get('measure_policy') != MEASURE_POLICY_V6:
            _error('invalid_contract', 'FlatContract v6 requires measure_policy flat-subtraction-v1')
        if 'measure' in payload:
            _error('invalid_contract', 'FlatContract v6 does not allow legacy measure field; specify decimal_columns and measure_expression')
        if 'precision' in payload:
            _error('invalid_contract', 'FlatContract v6 does not allow legacy precision field; specify precision within decimal_columns')

    if ver == 7 and payload.get('measure_policy') != MEASURE_POLICY_V7:
        _error('invalid_contract', 'FlatContract v7 requires measure_policy flat-case-v1')

    value = {key: payload[key] for key in req_fields}
    if value['mode'] != 'flat_snapshot' or value['scope'] != 'entire_supplied_snapshot':
        _error('invalid_contract', 'Flat contract version, mode, or scope is unsupported')
    for key in ('name', 'measure_meaning', 'unit_label'):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 200:
            _error('invalid_contract', f'{key} must be declared')
    if value['values_comparable'] is not True:
        _error('invalid_contract', 'Comparable measure values must be confirmed')

    if ver != 6:
        if type(value['precision']) is not int or type(value['scale']) is not int or not 1 <= value['precision'] <= 38 or not 0 <= value['scale'] <= min(value['precision'], 18):
            _error('invalid_contract', 'Decimal precision and scale are invalid')
    else:
        if type(value['scale']) is not int or not 0 <= value['scale'] <= 18:
            _error('invalid_contract', 'Decimal scale is invalid')
        decs = value['decimal_columns']
        if not isinstance(decs, list) or len(decs) != 2:
            _error('invalid_contract', 'FlatContract v6 requires exactly two decimal_columns')
        for col in decs:
            if not isinstance(col, dict) or set(col) != {'name', 'precision', 'scale', 'unit_label'}:
                _error('invalid_contract', 'decimal_columns entry must specify name, precision, scale, and unit_label')
            if not isinstance(col['name'], str) or not IDENT.fullmatch(col['name']) or col['name'] != col['name'].lower():
                _error('invalid_contract', 'decimal column name must be a plain lowercase identifier')
            if type(col['precision']) is not int or not 1 <= col['precision'] <= 38:
                _error('invalid_contract', 'decimal column precision must be an integer between 1 and 38')
            if type(col['scale']) is not int or not 0 <= col['scale'] <= min(col['precision'], 18):
                _error('invalid_contract', 'decimal column scale is invalid')
        c0, c1 = decs[0], decs[1]
        if c0['name'] == c1['name']:
            _error('invalid_contract', 'decimal_columns must have distinct names')
        if c0['scale'] != c1['scale'] or c0['scale'] != value['scale']:
            _error('scale_mismatch', 'Operands must have identical declared scale matching contract scale')
        if c0['unit_label'] != c1['unit_label'] or c0['unit_label'] != value['unit_label']:
            _error('invalid_contract', 'Operands must have compatible unit labels matching contract unit_label')
        p_req = max(c0['precision'], c1['precision']) + 1
        if p_req > 38:
            _error('unsupported_precision', 'Result precision exceeds maximum supported decimal precision 38')
        me = value['measure_expression']
        if not isinstance(me, dict) or set(me) != {'op', 'minuend', 'subtrahend'}:
            _error('invalid_contract', 'measure_expression must specify op, minuend, and subtrahend')
        if me['op'] != 'sub':
            _error('invalid_contract', 'measure_expression op must be sub')
        if not isinstance(me['minuend'], str) or not isinstance(me['subtrahend'], str):
            _error('invalid_contract', 'measure_expression operands must be strings')
        if me['minuend'] == me['subtrahend']:
            _error('identical_operands', 'Identical operands in subtraction are not supported')
        dec_names = {c0['name'], c1['name']}
        if {me['minuend'], me['subtrahend']} != dec_names:
            _error('invalid_contract', 'measure_expression operands must match registered decimal_columns')

    if not isinstance(value['null_policy'], str) or value['null_policy'] not in {'blank_nullable', 'literal_NULL', 'native_null'}:
        _error('invalid_contract', 'NULL policy is unsupported')
    categories = value['categories']
    names = [value['row_key']] + ([c0['name'], c1['name']] if ver == 6 else [value['measure']])
    if not isinstance(categories, list) or len(categories) > 8:
        _error('invalid_contract', 'Register at most 8 categorical fields')
    names += categories
    if any(not isinstance(name, str) or not IDENT.fullmatch(name) or name != name.lower() for name in names) or len(set(names)) != len(names):
        _error('invalid_contract', 'Column names must be distinct plain identifiers')

    if ver >= 4:
        dim_types = value['dimension_types']
        if not isinstance(dim_types, dict):
            _error('invalid_contract', 'dimension_types must be a dictionary')
        if set(dim_types.keys()) != set(categories):
            _error('invalid_contract', 'dimension_types keys must match categories exactly')
        for cat, dt in dim_types.items():
            if not isinstance(dt, str) or dt not in ALLOWED_DIMENSION_TYPES:
                _error('invalid_contract', f'Unsupported dimension type: {dt} for {cat}')
        if payload.get('nullable_categories') is not None:
            nc = payload['nullable_categories']
            if not isinstance(nc, list) or any(c not in categories for c in nc):
                _error('invalid_contract', 'nullable_categories must be a subset of categories')
            value['nullable_categories'] = sorted(list(set(nc)))

    result = {**value, 'contract_hash': digest(value), 'execution_policy': 'flat_snapshot_v1'}
    if payload.get('contract_hash', result['contract_hash']) != result['contract_hash']:
        _error('invalid_contract', 'Contract hash does not match its contents')
    if payload.get('execution_policy', result['execution_policy']) != result['execution_policy']:
        _error('invalid_contract', 'Execution policy differs from flat contract')
    return result


def _amount(value, contract, row_number):
    if value is None:
        return None
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        _error('invalid_row', f'{contract["measure"]} requires an exact decimal at row {row_number}')
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise CoreError('invalid_row', f'Invalid decimal at row {row_number}') from exc
    digits = number.as_tuple().digits
    fractional = max(-number.as_tuple().exponent, 0)
    integer = max(len(digits) + number.as_tuple().exponent, 0)
    if fractional > contract['scale'] or integer > contract['precision'] - contract['scale']:
        _error('invalid_row', f'{contract["measure"]} exceeds declared DECIMAL({contract["precision"]},{contract["scale"]}) at row {row_number}')
    return format(number, 'f')


def _text_value(value, field, row_number):
    if value is not None and (not isinstance(value, str) or len(value) > 256):
        _error('invalid_row', f'{field} must be text of at most 256 characters at row {row_number}')
    return value


def _validate_flat_cell(value, field, logical_type, row_number):
    if value is None:
        return None
    if not _canonical_type(value, logical_type):
        _error('invalid_row', f'{field} requires canonical {logical_type} at row {row_number}')
    return value


def validate_preparation(raw: bytes, source_hash: str):
    if not raw or len(raw) > 16 * 1024:
        _error('invalid_preparation', 'Preparation manifest must be 1–16384 bytes')
    try:
        value = json.loads(raw.decode('utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError) as exc:
        raise CoreError('invalid_preparation', 'Preparation manifest must be valid UTF-8 JSON') from exc
    required = {'version', 'classification', 'flat_sha256', 'lineage_file', 'lineage_sha256',
                'source_hashes', 'script', 'script_version', 'transformation'}
    if not isinstance(value, dict) or not required <= value.keys() or value['version'] != 1 or value['classification'] != 'external_preparation':
        _error('invalid_preparation', 'Preparation manifest version or required fields are invalid')
    if value['flat_sha256'] != source_hash:
        _error('invalid_preparation', 'Prepared CSV hash does not match the uploaded file')
    lineage_file = value['lineage_file']
    if not isinstance(lineage_file, str) or not 1 <= len(lineage_file) <= 160 or Path(lineage_file).name != lineage_file or '/' in lineage_file or '\\' in lineage_file:
        _error('invalid_preparation', 'Lineage artifact name must be a plain filename')
    if not isinstance(value['lineage_sha256'], str) or not SHA256.fullmatch(value['lineage_sha256']):
        _error('invalid_preparation', 'Lineage SHA-256 is invalid')
    hashes = value['source_hashes']
    if not isinstance(hashes, dict) or not 1 <= len(hashes) <= 16 or any(
            not isinstance(k, str) or not IDENT.fullmatch(k) or
            not isinstance(v, str) or not SHA256.fullmatch(v) for k, v in hashes.items()):
        _error('invalid_preparation', 'Original source hashes are invalid')
    for field in ('script', 'script_version', 'transformation'):
        if not isinstance(value[field], str) or not 1 <= len(value[field]) <= 1000:
            _error('invalid_preparation', f'{field} must be bounded text')
    return {'manifest': value, 'manifest_hash': hashlib.sha256(raw).hexdigest()}


def ingest_flat(source, destination, contract, preparation=None):
    contract = validate_contract(contract)
    ver = contract['version']
    dim_types = contract.get('dimension_types', {})
    source, destination = Path(source), Path(destination)
    if destination.exists() or manifest_path(destination).exists():
        _error('immutable_snapshot', 'Snapshot destination already exists')
    if source.stat().st_size > MAX_BYTES:
        _error('file_too_large', 'Extract exceeds 25 MiB')
    source_hash = file_hash(source)
    if ver == 6:
        col_a, col_b = contract['decimal_columns'][0]['name'], contract['decimal_columns'][1]['name']
        columns = [contract['row_key'], col_a, col_b, *contract['categories']]
    else:
        columns = [contract['row_key'], contract['measure'], *contract['categories']]
    rows, keys = [], set()

    if source.suffix.lower() == '.csv':
        if contract['null_policy'] == 'native_null':
            _error('invalid_contract', 'CSV requires an explicit NULL token policy')
        try:
            with source.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames) or not set(columns).issubset(reader.fieldnames):
                    _error('invalid_schema', 'Source is missing a registered column or has duplicate headings')
                token = '' if contract['null_policy'] == 'blank_nullable' else 'NULL'
                for index, raw in enumerate(reader, 2):
                    if len(rows) >= MAX_FLAT_ROWS: _error('too_many_rows', f'Flat snapshot exceeds {MAX_FLAT_ROWS} rows')
                    if None in raw or any(v is None for v in raw.values()): _error('invalid_row', f'Wrong field count at line {index}')
                    key = _text_value(raw[contract['row_key']], contract['row_key'], index)
                    if not key or key == token or key in keys: _error('invalid_row', f'Missing or duplicate row key at line {index}')
                    keys.add(key)
                    if ver == 6:
                        va, vb = raw[col_a], raw[col_b]
                        c0, c1 = contract['decimal_columns'][0], contract['decimal_columns'][1]
                        amt_a = _amount(None if va == token else va, {**contract, 'precision': c0['precision'], 'scale': c0['scale'], 'measure': col_a}, index)
                        amt_b = _amount(None if vb == token else vb, {**contract, 'precision': c1['precision'], 'scale': c1['scale'], 'measure': col_b}, index)
                        cat_vals = [
                            _validate_flat_cell(None if raw[name] == token else raw[name], name, dim_types[name], index)
                            for name in contract['categories']
                        ]
                        row = (key, amt_a, amt_b, *cat_vals)
                    else:
                        amount = raw[contract['measure']]
                        amount = _amount(None if amount == token else amount, contract, index)
                        if ver >= 4:
                            cat_vals = [
                                _validate_flat_cell(None if raw[name] == token else raw[name], name, dim_types[name], index)
                                for name in contract['categories']
                            ]
                        else:
                            cat_vals = [
                                _text_value(None if raw[name] == token else raw[name], name, index)
                                for name in contract['categories']
                            ]
                        row = (key, amount, *cat_vals)
                    if NULL_MARKER in row: _error('invalid_row', 'Source contains reserved NULL marker')
                    rows.append(row)
        except (UnicodeError, csv.Error) as exc:
            raise CoreError('invalid_csv', 'Source CSV could not be parsed') from exc
    elif source.suffix.lower() == '.parquet':
        if contract['null_policy'] != 'native_null': _error('invalid_contract', 'Parquet requires native NULL policy')
        with duckdb.connect(':memory:') as con:
            schema_raw = {r[0]: r[1] for r in con.execute('DESCRIBE SELECT * FROM read_parquet(?)', [str(source.resolve())]).fetchall()}
            if ver == 6:
                TYPE_MAP = {'string': 'VARCHAR', 'boolean': 'BOOLEAN', 'date': 'DATE', 'timestamp': 'TIMESTAMP'}
                if schema_raw.get(contract['row_key']) != 'VARCHAR':
                    _error('invalid_schema', 'Registered Parquet fields require VARCHAR key')
                for c in contract['decimal_columns']:
                    exp_dec = f"DECIMAL({c['precision']},{c['scale']})"
                    if schema_raw.get(c['name']) != exp_dec:
                        _error('invalid_schema', f"Registered Parquet field {c['name']} requires {exp_dec}")
                for name in contract['categories']:
                    exp_type = TYPE_MAP[dim_types[name]]
                    act_type = schema_raw.get(name)
                    if act_type != exp_type:
                        _error('invalid_schema', f'Registered Parquet field {name} requires {exp_type}, got {act_type}')
                selected = ', '.join(_quote(name) for name in columns)
                cursor = con.execute(f'SELECT {selected} FROM read_parquet(?)', [str(source.resolve())])
                c0, c1 = contract['decimal_columns'][0], contract['decimal_columns'][1]
                while batch := cursor.fetchmany(10000):
                    for raw in batch:
                        if len(rows) >= MAX_FLAT_ROWS: _error('too_many_rows', f'Flat snapshot exceeds {MAX_FLAT_ROWS} rows')
                        key = _text_value(raw[0], contract['row_key'], len(rows) + 1)
                        if not key or key in keys: _error('invalid_row', 'Missing or duplicate row key')
                        keys.add(key)
                        amt_a = _amount(None if raw[1] is None else format(raw[1], 'f'), {**contract, 'precision': c0['precision'], 'scale': c0['scale'], 'measure': col_a}, len(rows) + 1)
                        amt_b = _amount(None if raw[2] is None else format(raw[2], 'f'), {**contract, 'precision': c1['precision'], 'scale': c1['scale'], 'measure': col_b}, len(rows) + 1)
                        cat_vals = []
                        for name, value in zip(contract['categories'], raw[3:]):
                            dt = dim_types[name]
                            if value is None:
                                norm_val = None
                            elif dt == 'boolean':
                                norm_val = 'true' if value else 'false'
                            elif dt in {'date', 'timestamp'} and hasattr(value, 'isoformat'):
                                norm_val = value.isoformat()
                            else:
                                norm_val = str(value)
                            cat_vals.append(_validate_flat_cell(norm_val, name, dt, len(rows) + 1))
                        row = (key, amt_a, amt_b, *cat_vals)
                        if NULL_MARKER in row: _error('invalid_row', 'Source contains reserved NULL marker')
                        rows.append(row)
            else:
                expected = 'DECIMAL(' + str(contract['precision']) + ',' + str(contract['scale']) + ')'
                if ver >= 4:
                    TYPE_MAP = {'string': 'VARCHAR', 'boolean': 'BOOLEAN', 'date': 'DATE', 'timestamp': 'TIMESTAMP'}
                    if schema_raw.get(contract['row_key']) != 'VARCHAR' or schema_raw.get(contract['measure']) != expected:
                        _error('invalid_schema', f'Registered Parquet fields require VARCHAR key and {expected} measure')
                    for name in contract['categories']:
                        exp_type = TYPE_MAP[dim_types[name]]
                        act_type = schema_raw.get(name)
                        if act_type != exp_type:
                            _error('invalid_schema', f'Registered Parquet field {name} requires {exp_type}, got {act_type}')
                else:
                    if any(schema_raw.get(name) != 'VARCHAR' for name in [contract['row_key'], *contract['categories']]) or schema_raw.get(contract['measure']) != expected:
                        _error('invalid_schema', f'Registered Parquet fields require VARCHAR keys/categories and {expected} measure')
                selected = ', '.join(_quote(name) for name in columns)
                cursor = con.execute(f'SELECT {selected} FROM read_parquet(?)', [str(source.resolve())])
                while batch := cursor.fetchmany(10000):
                    for raw in batch:
                        if len(rows) >= MAX_FLAT_ROWS: _error('too_many_rows', f'Flat snapshot exceeds {MAX_FLAT_ROWS} rows')
                        key = _text_value(raw[0], contract['row_key'], len(rows) + 1)
                        if not key or key in keys: _error('invalid_row', 'Missing or duplicate row key')
                        keys.add(key)
                        amount = _amount(None if raw[1] is None else format(raw[1], 'f'), contract, len(rows) + 1)
                        if NULL_MARKER in raw: _error('invalid_row', 'Source contains reserved NULL marker')
                        if ver >= 4:
                            cat_vals = []
                            for name, value in zip(contract['categories'], raw[2:]):
                                dt = dim_types[name]
                                if value is None:
                                    norm_val = None
                                elif dt == 'boolean':
                                    norm_val = 'true' if value else 'false'
                                elif dt in {'date', 'timestamp'} and hasattr(value, 'isoformat'):
                                    norm_val = value.isoformat()
                                else:
                                    norm_val = str(value)
                                cat_vals.append(_validate_flat_cell(norm_val, name, dt, len(rows) + 1))
                        else:
                            cat_vals = [
                                _text_value(value, name, len(rows) + 1)
                                for name, value in zip(contract['categories'], raw[2:])
                            ]
                        rows.append((key, amount, *cat_vals))
    else:
        _error('invalid_schema', 'Only CSV and Parquet extracts are supported')
    if file_hash(source) != source_hash: _error('source_changed', 'Source changed during validation')
    destination.parent.mkdir(parents=True, exist_ok=True)

    schema = flat_schema(contract)
    if ver >= 4 and 'nullable_categories' in contract:
        cat_offset = 3 if ver == 6 else 2
        for index, row in enumerate(rows, 2):
            for name, value in zip(contract['categories'], row[cat_offset:]):
                if value is None and name not in contract['nullable_categories']:
                    _error('invalid_row', f'{name} is not nullable at row {index}')

    try:
        with tempfile.TemporaryDirectory(prefix='flat-', dir=destination.parent) as folder:
            canonical = Path(folder) / 'validated.csv'
            with canonical.open('w', encoding='utf-8', newline='') as output:
                writer = csv.writer(output)
                writer.writerow(columns)
                writer.writerows(tuple(NULL_MARKER if value is None else value for value in row) for row in rows)
            with duckdb.connect(str(destination)) as con:
                con.execute('CREATE TABLE snapshot (' + ', '.join(_quote(name) + ' ' + kind for name, kind in schema.items()) + ')')
                con.execute(f"COPY snapshot FROM ? (FORMAT CSV, HEADER TRUE, NULLSTR '{NULL_MARKER}')", [str(canonical)])
                if con.execute('SELECT COUNT(*) FROM snapshot').fetchone()[0] != len(rows):
                    _error('source_changed', 'Validated row count differs after loading')
        if file_hash(source) != source_hash: _error('source_changed', 'Source changed during loading')
        manifest = {'mode': 'flat_snapshot', 'policy': POLICY, 'execution_policy': 'flat_snapshot_v1', 'contract_version': ver,
                    'contract_hash': contract['contract_hash'], 'contract': contract,
                    'row_count': len(rows), 'schema': schema, 'primary_key': contract['row_key'],
                    'source_hash': source_hash, 'database_hash': file_hash(destination),
                    'ingestion_version': POLICY, 'scope': contract['scope'],
                    'unit_label': contract['unit_label'], 'measure_meaning': contract['measure_meaning'],
                    'values_comparable': True, 'null_policy': contract['null_policy'],
                    'currency': None, 'timezone': None, 'cutoff_at': None,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'transformations': ['Registered fields copied without amount imputation; missing measure is SQL NULL']}
        if preparation is not None:
            if not isinstance(preparation, dict) or preparation.get('manifest', {}).get('flat_sha256') != source_hash:
                _error('invalid_preparation', 'Preparation does not match this source')
            manifest['flat_preparation'] = preparation
        manifest['manifest_hash'] = digest(manifest)
        manifest_path(destination).write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
        return manifest
    except Exception:
        destination.unlink(missing_ok=True)
        manifest_path(destination).unlink(missing_ok=True)
        raise


def _identifier(node):
    if not isinstance(node, exp.Identifier) or node.args.get('quoted'):
        _error('unsupported_sql', 'Only unquoted registered identifiers are supported')
    return node.this.lower()


def _validate_typed_predicate(p, contract):
    """The runner independently validates the full typed IR, not just its hash."""
    kind, op, values = p['type'], p['op'], p['values']
    allowed = {'eq', 'neq', 'in', 'is_null', 'is_not_null'}
    if kind in {'date', 'timestamp', 'decimal'}:
        allowed |= {'gt', 'gte', 'lt', 'lte', 'between'}
    if kind == 'boolean':
        allowed |= {'eq_bool', 'is_true', 'is_false', 'is_not_true', 'is_not_false', 'bare_bool', 'not_bool'}
    if op not in allowed:
        _error('invalid_ir', f'Unsupported operator {op} for type {kind}')
    if op == 'in' and not values:
        _error('invalid_ir', 'IN requires nonempty literal values')
    for value in values:
        if kind == 'decimal':
            try:
                if contract.get('version') == 6:
                    col_spec = next((c for c in contract.get('decimal_columns', []) if c['name'] == p['field']), None)
                    if col_spec:
                        _amount(value, {**contract, 'precision': col_spec['precision'], 'scale': col_spec['scale'], 'measure': p['field']}, 'predicate')
                    else:
                        _amount(value, contract, 'predicate')
                else:
                    _amount(value, contract, 'predicate')
            except CoreError as exc:
                raise CoreError('invalid_ir', 'Decimal literal exceeds frozen measure type') from exc
        elif not _canonical_type(value, kind):
            _error('invalid_ir', f'Invalid canonical {kind} literal')
    if op == 'between':
        bounds = ([Decimal(v) for v in values] if kind == 'decimal' else
                  [datetime.fromisoformat(v) for v in values] if kind == 'timestamp' else values)
        if len(bounds) != 2 or bounds[0] > bounds[1]:
            _error('invalid_ir', 'INVALID_RANGE_BOUNDS: Lower bound exceeds upper bound in BETWEEN')


def _typed_predicates(root, contract):
    if contract.get('version') == 6:
        types = {**contract['dimension_types'], **{c['name']: 'decimal' for c in contract['decimal_columns']}}
    else:
        types = {**contract['dimension_types'], contract['measure']: 'decimal'}
    result = []

    def literal(node, kind):
        if isinstance(node, exp.Column):
            _error('unsupported_sql', 'Cross-column comparisons are not supported')
        if isinstance(node, exp.Boolean):
            if kind != 'boolean':
                _error('unsupported_sql', 'Boolean comparison requires boolean column')
            return 'true' if node.this else 'false'
        if kind in {'date', 'timestamp'}:
            expected = exp.DataType.Type.DATE if kind == 'date' else exp.DataType.Type.TIMESTAMPNTZ
            if not (type(node) is exp.Cast and node.to.this == expected
                    and isinstance(node.this, exp.Literal) and node.this.is_string
                    and not any(v for k, v in node.args.items() if k not in {'this', 'to'})
                    and not any(v for k, v in node.to.args.items() if k != 'this')):
                _error('unsupported_sql', f'Implicit string-to-{kind} comparison is not allowed; use a typed {kind.upper()} literal')
            value = str(node.this.this)
            if not _canonical_type(value, kind):
                _error('unsupported_sql', f'Invalid {kind.upper()} literal')
            return value
        if kind == 'boolean':
            _error('unsupported_sql', 'Implicit string-to-boolean comparison is not allowed; use TRUE/FALSE')
        negative = isinstance(node, exp.Neg)
        value_node = node.this if negative else node
        if not isinstance(value_node, exp.Literal) or value_node.is_string != (kind == 'string') or (negative and kind != 'decimal'):
            _error('unsupported_sql', f'{kind} predicate requires matching literal type')
        value = ('-' if negative else '') + str(value_node.this)
        if len(value) > 256:
            _error('unsupported_sql', 'Literal exceeds 256 characters')
        return value

    def lower(node):
        if isinstance(node, exp.Paren):
            return lower(node.this)
        if isinstance(node, exp.And):
            lower(node.this); lower(node.expression)
            return
        if len(result) >= 8:
            _error('unsupported_sql', 'At most eight predicates are supported')
        negated = isinstance(node, exp.Not)
        inner = node.this if negated else node
        col = inner if isinstance(inner, exp.Column) else inner.this
        if not isinstance(col, exp.Column) or any(v for k, v in col.args.items() if k != 'this'):
            _error('unsupported_sql', 'Predicate requires an unqualified registered column')
        field = _identifier(col.this)
        if field not in types:
            _error('unsupported_sql', f'Unregistered predicate field {field}')
        kind = types[field]
        values = []
        if isinstance(inner, exp.Column):
            if kind != 'boolean':
                _error('unsupported_sql', 'Bare boolean predicate requires boolean column')
            op = 'not_bool' if negated else 'bare_bool'
        elif isinstance(inner, exp.Is):
            if isinstance(inner.expression, exp.Null):
                op = 'is_not_null' if negated else 'is_null'
            elif isinstance(inner.expression, exp.Boolean) and kind == 'boolean':
                op = ('is_not_' if negated else 'is_') + ('true' if inner.expression.this else 'false')
            else:
                _error('unsupported_sql', 'IS TRUE/FALSE requires boolean column')
        elif negated:
            _error('unsupported_sql', 'Only NOT boolean and IS NOT predicates are supported')
        elif type(inner) in {exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE}:
            op = {exp.EQ: 'eq', exp.NEQ: 'neq', exp.GT: 'gt', exp.GTE: 'gte', exp.LT: 'lt', exp.LTE: 'lte'}[type(inner)]
            values = [literal(inner.expression, kind)]
            if kind == 'boolean' and op in {'eq', 'neq'}:
                if op == 'neq':
                    values = ['false' if values[0] == 'true' else 'true']
                op = 'eq_bool'
        elif isinstance(inner, exp.In):
            if any(v for k, v in inner.args.items() if k not in {'this', 'expressions'}) or not 1 <= len(inner.expressions) <= 32:
                _error('unsupported_sql', 'IN requires 1–32 literal items, without subqueries')
            op, values = 'in', [literal(v, kind) for v in inner.expressions]
        elif isinstance(inner, exp.Between):
            if any(v for k, v in inner.args.items() if k not in {'this', 'low', 'high'}):
                _error('unsupported_sql', 'Unsupported BETWEEN options')
            op, values = 'between', [literal(inner.args.get(k), kind) for k in ('low', 'high')]
        else:
            _error('unsupported_sql', 'Unsupported predicate expression')
        predicate = {'field': field, 'op': op, 'values': values, 'type': kind}
        try:
            _validate_typed_predicate(predicate, contract)
        except CoreError as exc:
            raise CoreError('unsupported_sql', str(exc)) from exc
        result.append(predicate)

    lower(root)
    return result


def parse_flat(sql, contract):
    contract = validate_contract(contract)
    ver = contract['version']
    dim_types = contract.get('dimension_types', {})
    if not isinstance(sql, str) or not 1 <= len(sql.strip()) <= 50000: _error('unsupported_sql', 'SQL must contain one SELECT')
    try:
        if ver == 7:
            # SQLGlot discards unary + while parsing; do not let arithmetic CASE
            # branches such as THEN +amount silently become a plain column.
            from sqlglot.tokens import TokenType
            if any(token.token_type == TokenType.PLUS for token in sqlglot.tokenize(sql, read='duckdb')):
                _error('unsupported_sql', 'Arithmetic is not supported by the CASE policy')
        statements = sqlglot.parse(sql, read='duckdb')
    except CoreError:
        raise
    except Exception as exc:
        raise CoreError('unsupported_sql', 'SQL could not be parsed') from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select): _error('unsupported_sql', 'Exactly one SELECT is required')
    tree = statements[0]
    if ver == 7:
        from .flat_case import validate_case_ast
        validate_case_ast(tree)
    if any(value is not None and value is not False and value != [] for key, value in tree.args.items()
           if key not in {'expressions', 'from_', 'where'}) or len(list(tree.walk())) > (250 if ver == 7 else 200) or len(tree.expressions) != 1:
        _error('unsupported_sql', 'Only one SUM over the whole snapshot is supported')
    source = tree.args.get('from_')
    if not isinstance(source, exp.From) or not isinstance(source.this, exp.Table) or any(value is not None and value is not False and value != [] for key, value in source.this.args.items() if key != 'this') or _identifier(source.this.this) != 'snapshot':
        _error('unsupported_sql', 'FROM snapshot is required without joins or aliases')
    aggregate = tree.expressions[0]
    if isinstance(aggregate, exp.Alias):
        if ver == 7:
            _error('unsupported_sql', 'Aliases are not supported by the CASE policy')
        _identifier(aggregate.args['alias']); aggregate = aggregate.this
    if not isinstance(aggregate, exp.Sum) or any(value is not None and value is not False and value != [] for key, value in aggregate.args.items() if key != 'this'):
        _error('unsupported_sql', 'Only one SUM over the whole snapshot is supported')

    if ver == 7:
        from .flat_case import parse_case
        return parse_case(aggregate.this, tree.args.get('where'), contract)

    if ver == 6:
        if not isinstance(aggregate.this, exp.Sub):
            if isinstance(aggregate.this, exp.Column):
                _error('unsupported_sql', 'Direct column SUM is not supported under FlatContract v6; use SUM(A - B)')
            _error('unsupported_sql', 'Only SUM of subtraction of two registered decimal columns is supported under FlatContract v6')
        sub = aggregate.this
        if any(value is not None and value is not False and value != [] for key, value in sub.args.items() if key not in {'this', 'expression'}):
            _error('unsupported_sql', 'Unsupported subtraction options')
        left, right = sub.this, sub.expression
        if not isinstance(left, exp.Column) or any(value is not None and value is not False and value != [] for key, value in left.args.items() if key != 'this') or \
           not isinstance(right, exp.Column) or any(value is not None and value is not False and value != [] for key, value in right.args.items() if key != 'this'):
            _error('unsupported_sql', 'Subtraction operands must be plain column identifiers')
        minuend = _identifier(left.this)
        subtrahend = _identifier(right.this)
        if minuend == subtrahend:
            _error('identical_operands', 'Identical operands in subtraction are not supported')
        dec_names = {c['name'] for c in contract['decimal_columns']}
        if minuend not in dec_names or subtrahend not in dec_names:
            _error('unsupported_sql', f'Operands must be registered decimal columns: {dec_names}')
        if minuend != contract['measure_expression']['minuend'] or subtrahend != contract['measure_expression']['subtrahend']:
            _error('different_measure_expressions', f"Expression must match contract measure expression: {contract['measure_expression']['minuend']} - {contract['measure_expression']['subtrahend']}")
    else:
        if isinstance(aggregate.this, exp.Sub):
            _error('unsupported_sql', 'Measure expressions require a FlatContract v6 snapshot')
        if not isinstance(aggregate.this, exp.Column) or any(value is not None and value is not False and value != [] for key, value in aggregate.this.args.items() if key != 'this') or _identifier(aggregate.this.this) != contract['measure']:
            _error('unsupported_sql', 'Only SUM of the registered measure is supported')

    where = tree.args.get('where')
    if ver == 6:
        ir = {'mode': 'flat_snapshot', 'policy': POLICY, 'contract_version': 6,
              'contract_hash': contract['contract_hash'],
              'measure_policy': MEASURE_POLICY_V6, 'predicate_policy': PREDICATE_POLICY,
              'table': 'snapshot', 'aggregate': 'sum',
              'measure_expression': {'op': 'sub', 'minuend': minuend, 'subtrahend': subtrahend},
              'predicate_tree': lower_boolean(where.this if where else None, lambda node: _typed_predicates(node, contract)[0])}
        validate_boolean(ir['predicate_tree'], lambda p: _check_flat_predicates([p], contract))
        return {**ir, 'ir_hash': digest(ir)}
    elif ver == 5:
        ir = {'mode': 'flat_snapshot', 'policy': POLICY, 'contract_hash': contract['contract_hash'],
              'table': 'snapshot', 'aggregate': 'sum', 'measure': contract['measure'],
              'predicate_policy': PREDICATE_POLICY,
              'predicate_tree': lower_boolean(where.this if where else None, lambda node: _typed_predicates(node, contract)[0])}
        validate_boolean(ir['predicate_tree'], lambda p: _check_flat_predicates([p], contract))
        return {**ir, 'ir_hash': digest(ir)}
    else:
        predicates = []
        categories_lower = {name.lower() for name in contract['categories']}

        def lower(node):
            if isinstance(node, exp.Paren):
                return lower(node.this)
            if isinstance(node, exp.And):
                lower(node.this)
                lower(node.expression)
                return
            if len(predicates) >= 8:
                _error('unsupported_sql', 'At most eight categorical predicates are supported')
            col_node = None
            op = None
            values = []
            if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
                if not isinstance(node.this, exp.Column) or node.this.args.get('table'):
                    _error('unsupported_sql', f'Unsupported predicate structure on {type(node).__name__}')
                col_node = node.this
                field = _identifier(col_node.this)
                if field not in categories_lower:
                    _error('unsupported_sql', f'Unregistered categorical field {field}')
                rhs = node.expression
                if isinstance(rhs, exp.Column):
                    _error('unsupported_sql', 'Cross-column comparisons are not supported')
                if isinstance(node, exp.EQ) and isinstance(rhs, exp.Boolean):
                    op = 'eq_bool'
                    values = ['true' if rhs.this else 'false']
                else:
                    op_map = {exp.EQ: 'eq', exp.NEQ: 'neq', exp.GT: 'gt', exp.GTE: 'gte', exp.LT: 'lt', exp.LTE: 'lte'}
                    op = op_map[type(node)]
                    if not isinstance(rhs, exp.Literal) or len(str(rhs.this)) > 256:
                        _error('unsupported_sql', 'Predicates require bounded literal constants')
                    values = [str(rhs.this)]
            elif isinstance(node, exp.In):
                if not isinstance(node.this, exp.Column) or node.this.args.get('table'):
                    _error('unsupported_sql', 'IN predicate requires plain column')
                col_node = node.this
                field = _identifier(col_node.this)
                if field not in categories_lower:
                    _error('unsupported_sql', f'Unregistered categorical field {field}')
                op = 'in'
                raw_vals = list(node.expressions)
                if not raw_vals or len(raw_vals) > 32:
                    _error('unsupported_sql', 'IN predicate requires 1 to 32 items')
                if any((not isinstance(v, exp.Literal) or len(str(v.this)) > 256 for v in raw_vals)):
                    _error('unsupported_sql', 'IN predicate requires at most 32 bounded literals')
                values = [str(v.this) for v in raw_vals]
            elif isinstance(node, exp.Between):
                if not isinstance(node.this, exp.Column) or node.this.args.get('table'):
                    _error('unsupported_sql', 'BETWEEN requires plain column')
                col_node = node.this
                field = _identifier(col_node.this)
                if field not in categories_lower:
                    _error('unsupported_sql', f'Unregistered categorical field {field}')
                low, high = (node.args.get('low'), node.args.get('high'))
                if not isinstance(low, exp.Literal) or not isinstance(high, exp.Literal):
                    _error('unsupported_sql', 'BETWEEN requires literal bounds')
                low_str, high_str = (str(low.this), str(high.this))
                try:
                    if Decimal(low_str) > Decimal(high_str):
                        _error('unsupported_sql', 'INVALID_RANGE_BOUNDS: Lower bound exceeds upper bound in BETWEEN')
                except InvalidOperation:
                    if low_str > high_str:
                        _error('unsupported_sql', 'INVALID_RANGE_BOUNDS: Lower bound exceeds upper bound in BETWEEN')
                op = 'between'
                values = [low_str, high_str]
            elif isinstance(node, exp.Is):
                if not isinstance(node.this, exp.Column) or node.this.args.get('table'):
                    _error('unsupported_sql', 'IS predicate requires plain column')
                col_node = node.this
                field = _identifier(col_node.this)
                if field not in categories_lower:
                    _error('unsupported_sql', f'Unregistered categorical field {field}')
                if isinstance(node.expression, exp.Null):
                    op = 'is_null'
                elif isinstance(node.expression, exp.Boolean):
                    op = 'is_true' if node.expression.this else 'is_false'
                else:
                    _error('unsupported_sql', 'Only IS NULL and IS TRUE/FALSE are supported')
                values = []
            elif isinstance(node, exp.Not):
                if isinstance(node.this, exp.Is):
                    if not isinstance(node.this.this, exp.Column) or node.this.this.args.get('table'):
                        _error('unsupported_sql', 'IS NOT predicate requires plain column')
                    col_node = node.this.this
                    field = _identifier(col_node.this)
                    if field not in categories_lower:
                        _error('unsupported_sql', f'Unregistered categorical field {field}')
                    if isinstance(node.this.expression, exp.Null):
                        op = 'is_not_null'
                    elif isinstance(node.this.expression, exp.Boolean):
                        op = 'is_not_true' if node.this.expression.this else 'is_not_false'
                    else:
                        _error('unsupported_sql', 'Only IS NOT NULL and IS NOT TRUE/FALSE are supported')
                    values = []
                elif isinstance(node.this, exp.Column):
                    col_node = node.this
                    field = _identifier(col_node.this)
                    if field not in categories_lower:
                        _error('unsupported_sql', f'Unregistered categorical field {field}')
                    op = 'not_bool'
                    values = []
                else:
                    _error('unsupported_sql', 'Unsupported NOT predicate')
            elif isinstance(node, exp.Column):
                col_node = node
                field = _identifier(col_node.this)
                if field not in categories_lower:
                    _error('unsupported_sql', f'Unregistered categorical field {field}')
                op = 'bare_bool'
                values = []
            else:
                _error('unsupported_sql', f'Unsupported predicate: {type(node).__name__}')
            pred_dict = {'field': field, 'op': op, 'values': values}
            predicates.append(pred_dict)

        if where:
            if ver == 4:
                predicates = _typed_predicates(where.this, contract)
            else:
                lower(where.this)
        ir = {'mode': 'flat_snapshot', 'policy': POLICY, 'contract_hash': contract['contract_hash'],
              'table': 'snapshot', 'aggregate': 'sum', 'measure': contract['measure'], 'predicates': predicates}
        return {**ir, 'ir_hash': digest(ir)}


def _check_flat_predicates(predicates, contract):
    if not isinstance(predicates, list) or len(predicates) > 8: _error('invalid_ir', 'Flat predicates are invalid')
    allowed_ops = {'eq', 'neq', 'in', 'gt', 'gte', 'lt', 'lte', 'between', 'is_null', 'is_not_null', 'is_true', 'is_not_true', 'is_false', 'is_not_false', 'eq_bool', 'bare_bool', 'not_bool'}
    ver = contract['version']
    if ver == 6:
        allowed_fields = contract['categories'] + [c['name'] for c in contract['decimal_columns']]
        types_dict = {**contract['dimension_types'], **{c['name']: 'decimal' for c in contract['decimal_columns']}}
    elif ver >= 4:
        allowed_fields = contract['categories'] + [contract['measure']]
        types_dict = {**contract['dimension_types'], contract['measure']: 'decimal'}
    else:
        allowed_fields = contract['categories']
        types_dict = {}

    for p in predicates:
        expected_keys = {'field', 'op', 'values', 'type'} if ver >= 4 else {'field', 'op', 'values'}
        if not isinstance(p, dict) or set(p) != expected_keys or not isinstance(p['field'], str) or not isinstance(p['op'], str) or p['field'] not in allowed_fields or p['op'] not in allowed_ops or not isinstance(p['values'], list) or len(p['values']) > 32 or any(not isinstance(v, str) or len(v) > 256 for v in p['values']):
            _error('invalid_ir', 'Flat predicate is invalid')
        if ver >= 4 and p['type'] != types_dict.get(p['field']):
            _error('invalid_ir', 'Predicate type does not match contract dimension type')
        if ver == 7 and (p['op'] in {'is_not_null', 'is_not_true', 'is_not_false', 'not_bool'}
                         or (p['type'] == 'boolean' and p['op'] in {'eq', 'neq'})):
            _error('invalid_ir', 'CASE predicates must use canonical NOT nodes and boolean equality leaves')
        if ver >= 4:
            _validate_typed_predicate(p, contract)
        if p['op'] in {'eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'eq_bool'} and len(p['values']) != 1:
            _error('invalid_ir', 'Flat predicate requires exactly one value')
        if p['op'] == 'between' and len(p['values']) != 2:
            _error('invalid_ir', 'BETWEEN requires two values')
        if p['op'] in {'is_null', 'is_not_null', 'is_true', 'is_not_true', 'is_false', 'is_not_false', 'bare_bool', 'not_bool'} and len(p['values']) != 0:
            _error('invalid_ir', 'Unary predicate requires zero values')


def _check_ir(ir, contract):
    ver = contract['version']
    if ver == 7:
        from .flat_case import check_case_ir
        return check_case_ir(ir, contract)
    if ver == 6:
        fields = {'mode', 'policy', 'contract_version', 'contract_hash', 'measure_policy', 'predicate_policy',
                  'table', 'aggregate', 'measure_expression', 'predicate_tree', 'ir_hash'}
        if not isinstance(ir, dict) or set(ir) != fields:
            _error('invalid_ir', 'Flat IR shape is invalid for FlatContract v6')
        if (ir['mode'] != 'flat_snapshot' or ir['policy'] != POLICY or ir['contract_version'] != 6 or
                ir['contract_hash'] != contract['contract_hash'] or ir['table'] != 'snapshot' or
                ir['aggregate'] != 'sum' or
                ir['measure_policy'] != MEASURE_POLICY_V6 or ir['predicate_policy'] != PREDICATE_POLICY):
            _error('invalid_ir', 'Flat IR policy or contract differs')
        me = ir['measure_expression']
        if not isinstance(me, dict) or set(me) != {'op', 'minuend', 'subtrahend'} or me['op'] != 'sub':
            _error('invalid_ir', 'Flat IR measure_expression is invalid')
        if not isinstance(me['minuend'], str) or not isinstance(me['subtrahend'], str):
            _error('invalid_ir', 'Flat IR measure_expression operands must be strings')
        if me['minuend'] == me['subtrahend']:
            _error('invalid_ir', 'Minuend and subtrahend must be distinct columns')
        dec_names = {c['name'] for c in contract['decimal_columns']}
        if me['minuend'] not in dec_names or me['subtrahend'] not in dec_names:
            _error('invalid_ir', 'measure_expression operands must match registered decimal columns')
        if me != contract['measure_expression']:
            _error('invalid_ir', 'measure_expression does not match frozen contract')
        validate_boolean(ir['predicate_tree'], lambda p: _check_flat_predicates([p], contract))
        if digest({k: v for k, v in ir.items() if k != 'ir_hash'}) != ir['ir_hash']:
            _error('invalid_ir', 'Flat IR hash differs')
        return ir

    fields = {'mode', 'policy', 'contract_hash', 'table', 'aggregate', 'measure', 'ir_hash'}
    fields |= {'predicate_policy', 'predicate_tree'} if ver == 5 else {'predicates'}
    if not isinstance(ir, dict) or set(ir) != fields:
        _error('invalid_ir', 'Flat IR shape is invalid for the frozen contract version')
    if ir['mode'] != 'flat_snapshot' or ir['policy'] != POLICY or ir['contract_hash'] != contract['contract_hash'] or ir['table'] != 'snapshot' or ir['aggregate'] != 'sum' or ir['measure'] != contract['measure']:
        _error('invalid_ir', 'Flat IR policy or contract differs')
    if ver == 5:
        if ir['predicate_policy'] != PREDICATE_POLICY:
            _error('invalid_ir', 'Boolean predicate policy differs from frozen contract')
        validate_boolean(ir['predicate_tree'], lambda p: _check_flat_predicates([p], contract))
    else:
        _check_flat_predicates(ir['predicates'], contract)
    if digest({k:v for k,v in ir.items() if k != 'ir_hash'}) != ir['ir_hash']:
        _error('invalid_ir', 'Flat IR hash differs')
    return ir


def _predicates_sql(ir, contract=None):
    clauses, params = [], []
    for p in ir['predicates']:
        op = p['op']
        f = _quote(p['field'])
        col_type = p.get('type')
        if not col_type and contract and contract.get('version', 3) >= 4:
            col_type = contract.get('dimension_types', {}).get(p['field'])

        is_date = (col_type == 'date')
        if col_type in {'date', 'timestamp', 'decimal', 'boolean'} and op in {'eq', 'neq', 'in', 'gt', 'gte', 'lt', 'lte', 'between'}:
            if col_type == 'decimal':
                if contract and contract.get('version') == 6:
                    col_spec = next((c for c in contract.get('decimal_columns', []) if c['name'] == p['field']), None)
                    cast_type = f"DECIMAL({col_spec['precision']},{col_spec['scale']})" if col_spec else f"DECIMAL({contract['precision']},{contract['scale']})"
                else:
                    cast_type = f"DECIMAL({contract['precision']},{contract['scale']})"
            else:
                cast_type = PHYSICAL_TYPES[col_type]
            placeholder = f'CAST(? AS {cast_type})'
            if op == 'in':
                clauses.append(f + ' IN (' + ','.join(placeholder for _ in p['values']) + ')')
            elif op == 'between':
                clauses.append(f + ' BETWEEN ' + placeholder + ' AND ' + placeholder)
            else:
                sym = {'eq': '=', 'neq': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op]
                clauses.append(f + ' ' + sym + ' ' + placeholder)
            params.extend(p['values'])
            continue

        if op == 'eq':
            clauses.append(f + (' = CAST(? AS DATE)' if is_date else ' = ?'))
            params.append(p['values'][0])
        elif op == 'neq':
            clauses.append(f + (' <> CAST(? AS DATE)' if is_date else ' <> ?'))
            params.append(p['values'][0])
        elif op == 'in':
            if is_date:
                clauses.append(f + ' IN (' + ','.join('CAST(? AS DATE)' for _ in p['values']) + ')')
            else:
                clauses.append(f + ' IN (' + ','.join('?' for _ in p['values']) + ')')
            params += p['values']
        elif op in {'gt', 'gte', 'lt', 'lte'}:
            sym = {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op]
            clauses.append(f + ' ' + sym + (' CAST(? AS DATE)' if is_date else ' ?'))
            params.append(p['values'][0])
        elif op == 'between':
            if is_date:
                clauses.append(f + ' BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)')
            else:
                clauses.append(f + ' BETWEEN ? AND ?')
            params.extend(p['values'])
        elif op == 'is_null':
            clauses.append(f + ' IS NULL')
        elif op == 'is_not_null':
            clauses.append(f + ' IS NOT NULL')
        elif op == 'eq_bool':
            if contract and contract['version'] == 7:
                clauses.append(f + ' = CAST(? AS BOOLEAN)')
                params.append(p['values'][0])
            else:
                clauses.append(f + (' = TRUE' if p['values'][0] == 'true' else ' = FALSE'))
        elif op == 'is_true':
            clauses.append(f + ' IS TRUE')
        elif op == 'is_not_true':
            clauses.append(f + ' IS NOT TRUE')
        elif op == 'is_false':
            clauses.append(f + ' IS FALSE')
        elif op == 'is_not_false':
            clauses.append(f + ' IS NOT FALSE')
        elif op == 'bare_bool':
            clauses.append(f + ' = TRUE')
        elif op == 'not_bool':
            clauses.append(f + ' = FALSE')
    predicate = ' AND '.join(clauses) if clauses else 'TRUE'
    return predicate, params


def format_expression(contract):
    """Generate display string for measure or measure_expression."""
    if contract.get('version') == 6 and contract.get('measure_expression'):
        me = contract['measure_expression']
        return f"{me['minuend']} - {me['subtrahend']}"
    return contract.get('measure', 'order_amount')


def _query(ir, key, measure=None, contract=None):
    if contract and contract['version'] == 7:
        from .flat_case import case_query
        return case_query(ir, contract)
    if contract and contract['version'] == 6:
        predicate, params = compile_boolean(ir['predicate_tree'], lambda p: _predicates_sql({'predicates': [p]}, contract))
        me = ir['measure_expression']
        expr = f'({_quote(me["minuend"])} - {_quote(me["subtrahend"])})'
        return f'SELECT {_quote(key)}, {predicate} AS included, {expr} AS measure_val, {_quote(me["minuend"])} AS op_a, {_quote(me["subtrahend"])} AS op_b FROM snapshot ORDER BY {_quote(key)}', params
    if contract and contract['version'] == 5:
        predicate, params = compile_boolean(ir['predicate_tree'], lambda p: _predicates_sql({'predicates': [p]}, contract))
    else:
        predicate, params = _predicates_sql(ir, contract)
    return f'SELECT {_quote(key)}, {predicate} AS included, {_quote(measure)} FROM snapshot ORDER BY {_quote(key)}', params


def compare_flat(database, baseline_ir, target_ir, context):
    database = Path(database)
    try:
        manifest = json.loads(manifest_path(database).read_text(encoding='utf-8'))
        expected = manifest.pop('manifest_hash')
        if digest(manifest) != expected or file_hash(database) != manifest['database_hash'] or manifest.get('mode') != 'flat_snapshot' or manifest.get('policy') != POLICY:
            _error('snapshot_integrity', 'Flat snapshot or manifest changed')
        manifest['manifest_hash'] = expected
    except (OSError, ValueError, KeyError) as exc:
        if isinstance(exc, CoreError): raise
        raise CoreError('snapshot_missing', 'Flat snapshot or manifest is unavailable') from exc
    contract = validate_contract(manifest['contract'])
    baseline_ir, target_ir = _check_ir(baseline_ir, contract), _check_ir(target_ir, contract)
    if contract['version'] == 6:
        if baseline_ir['measure_expression'] != target_ir['measure_expression']:
            _error('different_measure_expressions', 'Both definitions must evaluate the identical declared measure expression')
    if not isinstance(context, dict) or any(context.get(key) != value for key, value in {'mode':'flat_snapshot','scope':contract['scope'],'contract_hash':contract['contract_hash'],'unit_label':contract['unit_label'],'values_comparable':True}.items()):
        _error('not_comparable', 'Whole-snapshot comparison context differs from contract')
    for key in ('source_hash', 'manifest_hash'):
        if context.get(key) and context[key] != manifest[key]: _error('snapshot_integrity', f'{key} differs from frozen snapshot')
    started = time.monotonic()
    with localcontext() as dc:
        dc.prec = 100
        with duckdb.connect(str(database), read_only=True, config={'enable_external_access':'false','autoinstall_known_extensions':'false','autoload_known_extensions':'false','memory_limit':'512MB','threads':'1'}) as con:
            con.execute('SET lock_configuration = true')
            endpoints = []
            query_hashes = []
            for ir in (baseline_ir, target_ir):
                sql, params = _query(ir, contract['row_key'], contract.get('measure'), contract=contract)
                try:
                    rows = con.execute(sql, params).fetchall()
                except duckdb.Error as exc:
                    if 'overflow' in str(exc).lower():
                        _error('arithmetic_overflow', f'Row or aggregate arithmetic overflow: {exc}')
                    raise
                if contract['version'] == 7:
                    from .flat_case import case_endpoint_rows
                    included, terms = case_endpoint_rows(rows, contract)
                elif contract['version'] == 6:
                    me = ir['measure_expression']
                    included = {}
                    terms = []
                    for r in rows:
                        key, flag, amount, op_a, op_b = r[0], r[1], r[2], r[3], r[4]
                        is_inc = (flag is True)
                        is_null = (flag is True and amount is None)
                        contrib = Decimal(amount) if flag is True and amount is not None else Decimal(0)
                        op_dict = {
                            me['minuend']: None if op_a is None else format(op_a, 'f'),
                            me['subtrahend']: None if op_b is None else format(op_b, 'f')
                        }
                        expr_val = None if amount is None else format(amount, 'f')
                        included[key] = (is_inc, contrib, is_null, op_dict, expr_val)
                        if flag is True and amount is not None:
                            terms.append(Decimal(amount))
                else:
                    included = {key:(flag is True, Decimal(amount) if flag is True and amount is not None else Decimal(0), flag is True and amount is None) for key,flag,amount in rows}
                    terms = [Decimal(amount) for _, flag, amount in rows if flag is True and amount is not None]
                if contract['version'] == 7:
                    # Accumulate completely before the endpoint capacity check:
                    # intermediate cancellation and wider endpoint gaps are valid.
                    endpoint_val = sum(terms, ScaledInteger.zero(contract['scale'])) if terms else None
                    if endpoint_val is not None:
                        scaled_endpoint = abs(endpoint_val.coeff) * 10 ** contract['scale']
                        if scaled_endpoint > (10 ** 38 - 1) * 10 ** endpoint_val.scale:
                            _error('arithmetic_overflow', f'Endpoint aggregate exceeds DECIMAL(38, {contract["scale"]}) capacity')
                else:
                    # Preserve historical v3–v6 arithmetic/serialization byte-for-byte.
                    endpoint_val = sum(terms, Decimal(0)) if terms else None
                    if contract['version'] == 6 and endpoint_val is not None:
                        scaled_endpoint = abs(endpoint_val * (Decimal(10) ** contract['scale']))
                        if scaled_endpoint > (Decimal(10) ** 38 - 1):
                            _error('arithmetic_overflow', f'Endpoint aggregate exceeds DECIMAL(38, {contract["scale"]}) capacity')
                endpoints.append((endpoint_val, included))
                query_hashes.append(digest({'sql':sql,'params':params}))
        (av, ar), (bv, br) = endpoints
        gap = bv-av if av is not None and bv is not None else None
        evidence = []
        for key in sorted(ar.keys() | br.keys()):
            if contract['version'] == 7:
                from .flat_case import case_evidence_row
                evidence.append(case_evidence_row(key, ar[key], br[key]))
            elif contract['version'] == 6:
                default_ops = {me['minuend']: None, me['subtrahend']: None}
                ai, ac, an, a_ops, a_expr = ar.get(key, (False, Decimal(0), False, default_ops, None))
                bi, bc, bn, b_ops, b_expr = br.get(key, (False, Decimal(0), False, default_ops, None))
                mem = 'included_in_both' if ai and bi else 'baseline_only' if ai else 'target_only' if bi else 'included_in_neither'
                evidence.append({
                    'order_id': key, 'baseline_included': ai, 'target_included': bi,
                    'baseline_contribution': format(ac, 'f'), 'target_contribution': format(bc, 'f'),
                    'delta': format(bc - ac, 'f'), 'baseline_null_term': an, 'target_null_term': bn,
                    'membership': mem,
                    'baseline_operands': a_ops, 'target_operands': b_ops,
                    'baseline_expression_value': a_expr, 'target_expression_value': b_expr
                })
            else:
                ai, ac, an = ar.get(key, (False, Decimal(0), False))
                bi, bc, bn = br.get(key, (False, Decimal(0), False))
                mem = 'included_in_both' if ai and bi else 'baseline_only' if ai else 'target_only' if bi else 'included_in_neither'
                evidence.append({'order_id':key,'baseline_included':ai,'target_included':bi,
                                 'baseline_contribution':format(ac,'f'),'target_contribution':format(bc,'f'),
                                 'delta':format(bc-ac,'f'),'baseline_null_term':an,'target_null_term':bn,
                                 'membership':mem})
        if gap is not None:
            evidence_sum = (sum((ScaledInteger.parse(row['delta']) for row in evidence), ScaledInteger.zero())
                            if contract['version'] == 7 else
                            sum((Decimal(row['delta']) for row in evidence), Decimal(0)))
            if evidence_sum != gap:
                _error('reconciliation_failure', 'Flat evidence does not reconcile')
        number = lambda value: (None if value is None else value.to_fixed_string()
                                if isinstance(value, ScaledInteger) else format(value, 'f'))
        filter_key = 'predicate_tree' if contract['version'] in (5, 6, 7) else 'predicates'
        step = {'rule':'eligibility_predicate','baseline':baseline_ir[filter_key],'target':target_ir[filter_key],
                'changed':baseline_ir[filter_key] != target_ir[filter_key],
                'position':1,'previous_value':number(av),'next_value':number(bv),
                'value_before':number(av),'value_after':number(bv),'contribution':number(gap)}
        if contract['version'] == 7:
            step.update(rule='case_definition_change',
                        baseline={'where': baseline_ir['predicate_tree'], 'case': baseline_ir['measure_expression']},
                        target={'where': target_ir['predicate_tree'], 'case': target_ir['measure_expression']},
                        changed=baseline_ir != target_ir)
        return {'baseline_value':number(av),'target_value':number(bv),'delta_target_minus_baseline':number(gap),
                'absolute_gap':number(abs(gap)) if gap is not None else None,'relative_change_percent_display':None,
                'residual':'0' if gap is not None else None,'currency':None,'unit_label':contract['unit_label'],'measure_meaning':contract['measure_meaning'],
                'numeric_tolerance':'0','method':'flat_case_change' if contract['version'] == 7 else 'flat_filter_change','definition_status':'same_supported_representation' if baseline_ir == target_ir else 'different',
                'value_status':'defined' if gap is not None else 'undefined','explanation_status':('complete_case_definition_change' if contract['version'] == 7 else 'complete_eligibility_change') if gap is not None else 'unavailable',
                'steps':[step] if gap is not None else [],'shapley':None,'evidence':evidence,
                'definition_differences':[step],
                'provenance':{'execution_policy':POLICY,'baseline_ir_hash':baseline_ir['ir_hash'],'target_ir_hash':target_ir['ir_hash'],
                              'snapshot_manifest_hash':manifest['manifest_hash'],'database_hash':manifest['database_hash'],
                              'source_hash':manifest['source_hash'],'contract_hash':contract['contract_hash'],
                              'parser_version':sqlglot.__version__,'duckdb_version':duckdb.__version__,
                              'engine_version':POLICY,
                              **({'predicate_policy': PREDICATE_POLICY, 'contract_version': 5} if contract['version'] == 5 else {}),
                              **({'measure_policy': MEASURE_POLICY_V6, 'predicate_policy': PREDICATE_POLICY, 'contract_version': 6} if contract['version'] == 6 else {}),
                              **({'measure_policy': MEASURE_POLICY_V7, 'predicate_policy': PREDICATE_POLICY, 'contract_version': 7} if contract['version'] == 7 else {}),
                              'context':context,'baseline_query_hash':query_hashes[0],
                              'target_query_hash':query_hashes[1],'query_count':2,'elapsed_ms':round((time.monotonic()-started)*1000),
                              'evidence_unit':'row_key'},
                'limitations':['Observed equality applies only to this frozen whole snapshot.',
                               'Unknown currency remains unknown; no verified financial loss is claimed.'] +
                               (['CASE evidence checks consistency with both frozen definitions and typed row inputs; source authenticity relies on the isolated runner and snapshot/artifact integrity checks.',
                                 'The bridge describes one whole-definition contribution change, not per-predicate causal attribution.'] if contract['version'] == 7 else []) +
                               (['An endpoint SUM is NULL because no non-NULL terms matched; the gap is undefined.'] if gap is None else [])}
