"""Fixed-schema Parquet ingestion with the same immutable snapshot contract as CSV."""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from .free_tier import check_rows, check_upload_size
from .common import CoreError, DATES, NUMERIC, SCHEMA, digest, file_hash
from .snapshot import DEFAULT_METADATA, MAX_BYTES, NULLABLE, manifest_path


def ingest_parquet(source, destination, metadata=None):
    source, destination = Path(source), Path(destination)
    meta = {**DEFAULT_METADATA, 'null_policy': 'native_null', **(metadata or {})}
    if destination.exists() or manifest_path(destination).exists():
        raise CoreError('immutable_snapshot', 'Snapshot destination already exists')
    if meta['currency'] != 'INR' or meta['timezone'] != 'Asia/Kolkata' or not meta['scope'] or meta['grain'] != 'one_row_per_order_id':
        raise CoreError('invalid_metadata', 'v1 requires INR, Asia/Kolkata, declared scope, and unique order grain')
    if meta['null_policy'] != 'native_null':
        raise CoreError('invalid_metadata', 'Parquet requires native NULL values')
    try:
        date.fromisoformat(meta['cutoff_at'][:10])
    except (ValueError, TypeError):
        raise CoreError('invalid_metadata', 'Valid extraction cutoff required')
    check_rows(0)
    check_upload_size(source.stat().st_size)
    if source.stat().st_size > MAX_BYTES:
        raise CoreError('file_too_large', 'Parquet exceeds 25 MiB')
    source_hash = file_hash(source)
    row_count, ids = 0, set()
    source_name = str(source.resolve())
    try:
        with duckdb.connect(':memory:', config={'enable_external_access': 'true'}) as con:
            columns = con.execute('DESCRIBE SELECT * FROM read_parquet(?)', [source_name]).fetchall()
            if [(item[0], item[1]) for item in columns] != list(SCHEMA.items()):
                raise CoreError('invalid_schema', 'Parquet must have the exact ordered orders schema and types')
            check_rows(con.execute('SELECT COUNT(*) FROM read_parquet(?)', [source_name]).fetchone()[0])
            cursor = con.execute('SELECT * FROM read_parquet(?)', [source_name])
            while rows := cursor.fetchmany(10000):
                for row in rows:
                    check_rows(row_count + 1, exact=False)
                    if row_count >= 1000000:
                        raise CoreError('too_many_rows', 'Snapshot exceeds 1000000 rows')
                    values = dict(zip(SCHEMA, row))
                    for column, value in values.items():
                        if value is None:
                            if column not in NULLABLE:
                                raise CoreError('invalid_row', f'Required {column} is NULL at row {row_count + 1}')
                            continue
                        if column in NUMERIC and (not value.is_finite() or abs(value) >= Decimal('1e16')):
                            raise CoreError('invalid_row', f'Amount exceeds DECIMAL(18,2) at row {row_count + 1}')
                        if column not in NUMERIC | DATES and value == '' and column not in NULLABLE:
                            raise CoreError('invalid_row', f'Required {column} is empty at row {row_count + 1}')
                    if values['currency'] != meta['currency']:
                        raise CoreError('invalid_row', f'Mixed or incompatible currency at row {row_count + 1}')
                    if values['order_id'] in ids:
                        raise CoreError('invalid_row', f'Duplicate order_id at row {row_count + 1}')
                    ids.add(values['order_id'])
                    row_count += 1
    except CoreError:
        raise
    except duckdb.Error as error:
        raise CoreError('invalid_parquet', f'Parquet validation failed: {error}') from error
    if file_hash(source) != source_hash:
        raise CoreError('source_changed', 'Parquet changed while it was being validated')
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with duckdb.connect(str(destination), config={'enable_external_access': 'true'}) as con:
            con.execute('BEGIN TRANSACTION')
            con.execute('CREATE TABLE orders (' + ', '.join('"' + name + '" ' + kind for name, kind in SCHEMA.items()) + ')')
            con.execute('INSERT INTO orders SELECT * FROM read_parquet(?)', [source_name])
            if con.execute('SELECT COUNT(*) FROM orders').fetchone()[0] != row_count:
                raise CoreError('source_changed', 'Parquet row count changed during loading')
            con.execute('COMMIT')
        if file_hash(source) != source_hash:
            raise CoreError('source_changed', 'Parquet changed during loading')
        manifest = {**meta, 'row_count': row_count, 'schema': SCHEMA,
                    'primary_key': 'order_id', 'source_hash': source_hash,
                    'database_hash': file_hash(destination), 'ingestion_version': 'parquet-v1',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'transformations': ['Native Parquet NULL values preserved without token conversion']}
        manifest['manifest_hash'] = digest(manifest)
        manifest_path(destination).write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
        return manifest
    except duckdb.Error as error:
        destination.unlink(missing_ok=True)
        raise CoreError('invalid_parquet', f'Parquet loading failed: {error}') from error
    except Exception:
        destination.unlink(missing_ok=True)
        raise
