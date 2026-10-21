"""Trusted supervisor-side PostgreSQL source collector for MetricGuard V4.

Connects to a PostgreSQL source, extracts a declared bounded selection inside
a single READ ONLY REPEATABLE READ transaction, streams and validates rows against
the selected MetricGuard contract, stages a DuckDB snapshot, verifies hashes,
creates an integrity-verified manifest, and atomically publishes it to the Store.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import duckdb
import psycopg
from psycopg import sql

from packages.metricguard_core.free_tier import FreeTierLimit, check_rows, check_upload_size
from packages.metricguard_core.common import (
    CoreError, DATES, NUMERIC, SCHEMA, digest, file_hash
)
from packages.metricguard_core.snapshot import manifest_path, NULLABLE
from packages.metricguard_core.v2_contract import (
    validate_contract as validate_v2_contract,
    validate_mapping as validate_v2_mapping,
)
from packages.metricguard_core.flat import (
    validate_contract as validate_flat_contract,
    flat_schema,
    MAX_FLAT_ROWS,
)

logger = logging.getLogger(__name__)

IDENTIFIER_REGEX = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]{0,127}\Z')
MAX_RETAIL_ROWS = 1_000_000



STAGING_NULL = '__METRICGUARD_COLLECTOR_NULL_7d1e__'


def _staged_cell(value, row_number):
    """One validated value as CSV text for the staging COPY (typed by the DuckDB table)."""
    if value is None:
        return STAGING_NULL
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, 'f')
    text = str(value)
    if text == STAGING_NULL:
        raise CollectorError('INVALID_ROW', f'Row {row_number} contains a reserved value')
    return text


class CollectorError(Exception):
    """Sanitized collector exception guaranteeing zero credential exposure."""
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code
        self.message = redact_credentials(str(message))
        self.status = status
        super().__init__(self.message)


def redact_credentials(text: str) -> str:
    """Mask passwords in connection strings, DSNs, and arbitrary text."""
    if not isinstance(text, str):
        return text
    # 1. Greedy URI password: postgresql://user:password@host[:port]
    text = re.sub(r'([a-zA-Z0-9\+\.\-]+://[^:\s]+:)(.*)(@[^/@\s]+(?::\d+)?(?:/.*)?)$', r'\1***\3', text)
    # 2. Standard URI password without tricky chars
    text = re.sub(r'([a-zA-Z0-9\+\.\-]+://[^:\s]+:)([^@\s]+)(@)', r'\1***\3', text)
    # 3. DSN parameter password=xyz or password='xyz'
    text = re.sub(r'(?i)(password\s*=\s*[\'"]?)([^\s\'"]+)([\'"]?)', r'\g<1>***\g<3>', text)
    # 4. Bearer tokens or secret headers
    text = re.sub(r'(?i)\bbearer\s+[a-zA-Z0-9_\-\.]+', 'Bearer ***', text)
    return text


def sanitize_source_uri(uri: str) -> str:
    """Return a display-safe connection URI with passwords completely stripped."""
    if not uri:
        return ""
    if "://" in uri:
        m = re.match(r'^([a-zA-Z0-9\+\.\-]+://)([^:@\s]+)(?::(.*))?@([^/@\s]+(?::\d+)?(?:/.*)?)$', uri)
        if m:
            scheme, user, pwd, rest = m.groups()
            masked = ':***' if pwd is not None else ''
            return f"{scheme}{user}{masked}@{rest}"
    return redact_credentials(uri)


def validate_identifier(name: str, label: str = 'identifier') -> str:
    """Strictly validate schema, table, and column names against safe identifier syntax."""
    if not isinstance(name, str) or not IDENTIFIER_REGEX.fullmatch(name):
        raise CollectorError('INVALID_IDENTIFIER', f"Invalid {label}: '{redact_credentials(str(name))}'")
    return name


def validate_cutoff_value(val: str) -> str:
    """Ensure logical cutoff is a strictly formatted ISO date or timestamp."""
    if not isinstance(val, str) or not val.strip():
        raise CollectorError('INVALID_CUTOFF', 'Cutoff value must be a non-empty date or timestamp string.')
    clean = val.strip()
    try:
        if len(clean) == 10:
            date.fromisoformat(clean)
            return clean
        datetime.fromisoformat(clean.replace('Z', '+00:00'))
        return clean
    except Exception as exc:
        raise CollectorError('INVALID_CUTOFF', f"Invalid cutoff format '{redact_credentials(clean)}'. Expected YYYY-MM-DD or ISO timestamp.") from exc


@dataclass
class PostgresSourceSelection:
    source_uri: str
    table_name: str
    cutoff_column: str
    cutoff_value: str
    schema_name: str = 'public'
    columns: list[str] | dict[str, str] | None = None
    contract_id: str | None = None
    contract: dict | None = None
    mapping_id: str | None = None
    mapping: dict | None = None
    extraction_id: str | None = None
    attempt: int = 1
    snapshot_name: str | None = None
    row_limit: int = MAX_RETAIL_ROWS
    chunk_size: int = 1000
    # Optional quick look: take only the first N rows (by row key) into a separate, clearly labelled snapshot.
    sample_rows: int | None = None


class PostgresCollector:
    """Trusted supervisor collector extracting bounded PostgreSQL data to DuckDB."""

    def __init__(self, store=None, data_dir=None, chunk_size: int = 1000):
        self.store = store
        self.data_dir = Path(data_dir or './data').resolve()
        self.chunk_size = chunk_size

    def collect(self, selection: PostgresSourceSelection, project_id: str | None = None) -> dict:
        """Execute full extraction workflow with staging, validation, and atomic publication."""
        pid = project_id or getattr(self.store, 'project_id', 'local-retail')
        extraction_id = selection.extraction_id or f"ext-{uuid4().hex}"
        attempt = selection.attempt or 1

        # 1. Validate identifiers and cutoff
        validate_identifier(selection.schema_name, 'schema')
        validate_identifier(selection.table_name, 'table')
        validate_identifier(selection.cutoff_column, 'cutoff column')
        cutoff_val = validate_cutoff_value(selection.cutoff_value)

        # 2. Resolve contract and mapping
        contract_info = self._resolve_contract_and_mapping(selection, pid)
        is_flat = contract_info['is_flat']
        contract = contract_info['contract']
        mapping = contract_info.get('mapping')

        # 3. Check or record extraction in Store
        if self.store is not None and hasattr(self.store, 'record_extraction'):
            self.store.record_extraction(
                project_id=pid,
                extraction_id=extraction_id,
                selection={
                    'cutoff_value': cutoff_val,
                    'cutoff_column': selection.cutoff_column,
                    'schema_name': selection.schema_name,
                    'table_name': selection.table_name,
                    'contract_id': selection.contract_id or contract_info.get('contract_id', 'unknown'),
                    'mapping_id': selection.mapping_id,
                    'source_sanitized': sanitize_source_uri(selection.source_uri),
                },
                attempt=attempt
            )

        # 4. Prepare directories and atomic staging paths
        snapshots_dir = self.data_dir / 'snapshots'
        staging_dir = self.data_dir / 'staging'
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        snapshot_id = f"snap-{uuid4().hex}"
        final_db = snapshots_dir / f"{snapshot_id}.duckdb"
        final_manifest = snapshots_dir / f"{snapshot_id}.duckdb.manifest.json"

        staging_db = staging_dir / f".staging-{extraction_id}-{attempt}-{uuid4().hex}.duckdb"
        staging_manifest = staging_db.with_name(staging_db.name + '.manifest.json')

        try:
            # 5. Connect to PostgreSQL with strict READ ONLY REPEATABLE READ
            conn = self._connect_readonly(selection.source_uri)
            try:
                # 6. Stream, validate, and write rows to staging DuckDB
                row_count = self._stream_and_stage(
                    conn=conn,
                    selection=selection,
                    cutoff_val=cutoff_val,
                    contract_info=contract_info,
                    staging_db=staging_db,
                )
            finally:
                conn.rollback()
                conn.close()

            # 7. Final integrity validation and database hashing
            if not staging_db.is_file() or staging_db.stat().st_size == 0:
                raise CollectorError('SNAPSHOT_CORRUPTED', 'Staging DuckDB snapshot file is missing or empty.')
            db_hash = file_hash(staging_db)

            # 8. Build manifest
            manifest = self._build_manifest(
                snapshot_id=snapshot_id,
                selection=selection,
                cutoff_val=cutoff_val,
                contract_info=contract_info,
                row_count=row_count,
                database_hash=db_hash,
            )

            # 9. Write staging manifest
            staging_manifest.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')

            # 10. Atomic publication
            if final_db.exists() or final_manifest.exists():
                raise CollectorError('IMMUTABLE_SNAPSHOT_EXISTS', 'Destination snapshot already exists.')
            os.replace(staging_db, final_db)
            try:
                os.replace(staging_manifest, final_manifest)
            except Exception:
                final_db.unlink(missing_ok=True)
                raise

            # 11. Register with Store
            if self.store is not None:
                try:
                    if hasattr(self.store, 'check_storage_quota'):
                        self.store.check_storage_quota(pid, final_db.stat().st_size, self.data_dir)
                    snapshot_rec = self.store.record_snapshot(
                        name=manifest['name'],
                        manifest=manifest,
                        artifact_path=str(final_db),
                        project_id=pid
                    )
                    if hasattr(self.store, 'update_extraction'):
                        self.store.update_extraction(
                            extraction_id=extraction_id,
                            state='succeeded',
                            snapshot_id=snapshot_rec['id'],
                            manifest_hash=manifest['manifest_hash'],
                            row_count=row_count
                        )
                except Exception as exc:
                    final_db.unlink(missing_ok=True)
                    final_manifest.unlink(missing_ok=True)
                    if hasattr(self.store, 'update_extraction'):
                        self.store.update_extraction(
                            extraction_id=extraction_id,
                            state='failed',
                            error={'code': 'STORE_REGISTRATION_FAILED', 'message': redact_credentials(str(exc))}
                        )
                    raise CollectorError('STORE_REGISTRATION_FAILED', f"Failed to register snapshot with Store: {redact_credentials(str(exc))}") from None

            logger.info("Successfully extracted %d rows from PostgreSQL to %s", row_count, final_db.name)
            return {
                'snapshot_id': snapshot_id,
                'extraction_id': extraction_id,
                'attempt': attempt,
                'database_hash': db_hash,
                'manifest_hash': manifest['manifest_hash'],
                'row_count': row_count,
                'artifact_path': str(final_db),
                'manifest': manifest,
            }

        except Exception as exc:
            # Clean up staging and incomplete final files immediately
            staging_db.unlink(missing_ok=True)
            staging_manifest.unlink(missing_ok=True)
            final_db.unlink(missing_ok=True)
            final_manifest.unlink(missing_ok=True)

            code = exc.code if isinstance(exc, (CollectorError, FreeTierLimit)) else 'EXTRACTION_FAILED'
            msg = exc.message if isinstance(exc, (CollectorError, FreeTierLimit)) else redact_credentials(str(exc))

            if self.store is not None and hasattr(self.store, 'update_extraction'):
                self.store.update_extraction(
                    extraction_id=extraction_id,
                    state='failed',
                    error={'code': code, 'message': msg}
                )

            if isinstance(exc, (CollectorError, FreeTierLimit)):
                raise
            raise CollectorError(code, msg) from None

    def _connect_readonly(self, uri: str) -> psycopg.Connection:
        """Establish connection with REPEATABLE READ READ ONLY transaction isolation."""
        try:
            from services.supervisor.warehouse_inspect import guarded_conninfo
            conn = psycopg.connect(guarded_conninfo(uri), autocommit=False)
            conn.read_only = True
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            return conn
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError('CONNECTION_FAILED', f"Failed to connect to PostgreSQL source: {redact_credentials(str(exc))}") from None

    def _resolve_contract_and_mapping(self, selection: PostgresSourceSelection, pid: str) -> dict:
        """Resolve and validate dataset contract and optional column mapping."""
        contract = selection.contract
        mapping = selection.mapping

        if contract is None and selection.contract_id and self.store is not None:
            try:
                contract = self.store.dataset_contract(selection.contract_id)
            except Exception:
                try:
                    contract = self.store.flat_contract(selection.contract_id)
                except Exception:
                    raise CollectorError('INVALID_CONTRACT', f"Contract '{selection.contract_id}' not found.")

        if contract is None:
            raise CollectorError('INVALID_CONTRACT', 'An explicit dataset contract is required for collection.')

        is_flat = contract.get('mode') == 'flat_snapshot' or contract.get('version') == 3
        if is_flat:
            clean_contract = {k: v for k, v in contract.items() if k not in {'id', 'created_at', 'version_number', 'project_id'}}
            validated_contract = validate_flat_contract(clean_contract)
            if validated_contract['version'] in (6, 7):
                raise CollectorError('UNSUPPORTED_CONTRACT', 'Warehouse extraction supports datasets with plain amount columns (not subtraction or CASE datasets).')
            return {
                'is_flat': True,
                'contract': validated_contract,
                'contract_hash': validated_contract['contract_hash'],
                'primary_key': validated_contract['row_key'],
                'contract_id': selection.contract_id,
            }
        else:
            clean_contract = {k: v for k, v in contract.items() if k not in {'id', 'created_at', 'version_number', 'project_id'}}
            validated_contract = validate_v2_contract(clean_contract)
            validated_mapping = None
            if mapping is None and selection.mapping_id and self.store is not None:
                try:
                    mapping = self.store.column_mapping(selection.mapping_id)
                except Exception:
                    raise CollectorError('INVALID_MAPPING', f"Column mapping '{selection.mapping_id}' not found.")
            if mapping is not None:
                clean_mapping = {k: v for k, v in mapping.items() if k not in {'id', 'created_at', 'version_number', 'project_id', 'contract_id'}}
                validated_mapping = validate_v2_mapping(clean_mapping, validated_contract)

            return {
                'is_flat': False,
                'contract': validated_contract,
                'contract_hash': validated_contract['contract_hash'],
                'mapping': validated_mapping,
                'mapping_hash': validated_mapping['mapping_hash'] if validated_mapping else None,
                'primary_key': 'order_id',
                'contract_id': selection.contract_id,
                'mapping_id': selection.mapping_id,
            }

    def _stream_and_stage(
        self,
        conn: psycopg.Connection,
        selection: PostgresSourceSelection,
        cutoff_val: str,
        contract_info: dict,
        staging_db: Path,
    ) -> int:
        """Stream chunks from PostgreSQL server-side cursor, validate, and write to DuckDB."""
        is_flat = contract_info['is_flat']
        contract = contract_info['contract']
        mapping = contract_info.get('mapping')

        # Determine select projection
        if is_flat:
            if contract.get('version') in (6, 8):
                columns_to_select = [contract['row_key'], *[c['name'] for c in contract['decimal_columns']], *contract['categories']]
            else:
                columns_to_select = [contract['row_key'], contract['measure'], *contract['categories']]
            for col in columns_to_select:
                validate_identifier(col, 'flat column')
            order_by = contract['row_key']
        else:
            if mapping:
                columns_to_select = [mapping['columns'][role] for role in SCHEMA]
            elif selection.columns and isinstance(selection.columns, dict):
                columns_to_select = [selection.columns[role] for role in SCHEMA]
            elif selection.columns and isinstance(selection.columns, list):
                if len(selection.columns) != len(SCHEMA):
                    raise CollectorError('INVALID_COLUMNS', f"Expected {len(SCHEMA)} columns matching orders schema.")
                columns_to_select = selection.columns
            else:
                columns_to_select = list(SCHEMA)

            for col in columns_to_select:
                validate_identifier(col, 'source column')
            order_by = columns_to_select[0]

        # Build parameterized query using psycopg.sql
        sample = selection.sample_rows if is_flat and selection.sample_rows else None
        if sample is not None and not 1 <= sample <= MAX_FLAT_ROWS:
            raise CollectorError('INVALID_SAMPLE', f'A sample must be between 1 and {MAX_FLAT_ROWS} rows.')
        query = sql.SQL(
            "SELECT {cols} FROM {schema}.{table} WHERE {cutoff} <= %s ORDER BY {order}" + (" LIMIT %s" if sample else "")
        ).format(
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns_to_select),
            schema=sql.Identifier(selection.schema_name),
            table=sql.Identifier(selection.table_name),
            cutoff=sql.Identifier(selection.cutoff_column),
            order=sql.Identifier(order_by),
        )

        cursor_name = f"mg_cur_{uuid4().hex}"
        seen_keys: set[str] = set()
        check_rows(0)
        row_count = 0
        max_rows = MAX_FLAT_ROWS if is_flat else selection.row_limit

        # Validated rows are staged in a CSV and loaded with one COPY: row-by-row INSERTs
        # (executemany) take milliseconds per row, i.e. minutes for a large extract.
        staging_csv = staging_db.with_name(staging_db.name + '.rows.csv')
        with duckdb.connect(str(staging_db)) as ddb, staging_csv.open('w', encoding='utf-8', newline='') as staged:
            writer = csv.writer(staged)
            ddb.execute("BEGIN TRANSACTION")
            if is_flat:
                # Same typed physical schema as a validated file upload (dates, booleans, timestamps).
                schema = flat_schema(contract)
                ddb.execute('CREATE TABLE snapshot (' + ', '.join(f'"{name}" {kind}' for name, kind in schema.items()) + ')')
            else:
                col_defs = ', '.join(f'"{name}" {kind}' for name, kind in SCHEMA.items())
                ddb.execute(f'CREATE TABLE orders ({col_defs})')

            try:
                with conn.cursor(name=cursor_name) as cursor:
                    cursor.itersize = selection.chunk_size
                    cursor.execute(query, [cutoff_val, sample] if sample else [cutoff_val])

                    while True:
                        rows = cursor.fetchmany(selection.chunk_size)
                        if not rows:
                            break

                        validated_batch = []
                        for raw_row in rows:
                            check_rows(row_count + 1, exact=False)
                            row_count += 1
                            if row_count > max_rows:
                                raise CollectorError('TOO_MANY_ROWS', f"Extraction exceeds maximum limit of {max_rows} rows.")

                            if is_flat:
                                val_row = self._validate_flat_row(raw_row, contract, seen_keys, row_count)
                            else:
                                val_row = self._validate_retail_row(raw_row, contract, seen_keys, row_count)

                            validated_batch.append(val_row)

                        writer.writerows([_staged_cell(value, row_count) for value in row] for row in validated_batch)
                        check_upload_size(staged.tell())

                # Load every validated row at once, then verify the count in DuckDB.
                table_name = 'snapshot' if is_flat else 'orders'
                staged.close()
                ddb.execute(f"COPY {table_name} FROM ? (FORMAT CSV, HEADER FALSE, NULLSTR '{STAGING_NULL}')", [str(staging_csv)])
                loaded_count = ddb.execute(f'SELECT COUNT(*) FROM {table_name}').fetchone()[0]
                if loaded_count != row_count:
                    raise CollectorError('ROW_COUNT_MISMATCH', f"DuckDB row count ({loaded_count}) differs from validated row count ({row_count}).")

                ddb.execute("COMMIT")
            except Exception:
                ddb.execute("ROLLBACK")
                raise
            finally:
                staged.close()
                staging_csv.unlink(missing_ok=True)

        return row_count

    def _validate_retail_row(self, raw_tuple: tuple, contract: dict, seen_keys: set, row_number: int) -> tuple:
        """Validate row against Retail V2 schema roles and types."""
        row_dict = dict(zip(SCHEMA, raw_tuple))

        order_id = row_dict['order_id']
        if not order_id or not isinstance(order_id, str):
            raise CollectorError('INVALID_ROW', f"Missing or invalid order_id at row {row_number}")
        if len(order_id) > 128:
            raise CollectorError('INVALID_ROW', f"order_id exceeds 128 chars at row {row_number}")
        if order_id in seen_keys:
            raise CollectorError('DUPLICATE_KEY', f"Duplicate order_id '{order_id}' at row {row_number}")
        seen_keys.add(order_id)

        customer_id = row_dict['customer_id']
        if customer_id is not None:
            customer_id = str(customer_id)
            if len(customer_id) > 128:
                raise CollectorError('INVALID_ROW', f"customer_id exceeds 128 chars at row {row_number}")

        order_date = row_dict['order_date']
        if order_date is None:
            raise CollectorError('INVALID_ROW', f"order_date is required at row {row_number}")
        if isinstance(order_date, (date, datetime)):
            order_date_str = order_date.strftime('%Y-%m-%d')
        else:
            order_date_str = str(order_date)[:10]
            try:
                date.fromisoformat(order_date_str)
            except ValueError:
                raise CollectorError('INVALID_ROW', f"Invalid order_date at row {row_number}")

        payment_date = row_dict['payment_date']
        payment_date_str = None
        if payment_date is not None:
            if isinstance(payment_date, (date, datetime)):
                payment_date_str = payment_date.strftime('%Y-%m-%d')
            else:
                payment_date_str = str(payment_date)[:10]
                try:
                    date.fromisoformat(payment_date_str)
                except ValueError:
                    raise CollectorError('INVALID_ROW', f"Invalid payment_date at row {row_number}")

        status = row_dict['status']
        if status is not None:
            status = str(status)
            if len(status) > 128:
                raise CollectorError('INVALID_ROW', f"status exceeds 128 chars at row {row_number}")

        def parse_decimal(val, col_name, nullable=False):
            if val is None:
                if nullable:
                    return None
                raise CollectorError('INVALID_ROW', f"{col_name} is required at row {row_number}")
            try:
                dec = Decimal(str(val))
            except (InvalidOperation, TypeError):
                raise CollectorError('INVALID_ROW', f"Invalid decimal for {col_name} at row {row_number}")
            if not dec.is_finite() or abs(dec) >= Decimal('1e16'):
                raise CollectorError('INVALID_ROW', f"{col_name} exceeds DECIMAL(18,2) at row {row_number}")
            # Ensure at most 2 decimal places
            exp = dec.as_tuple().exponent
            if exp < -2:
                raise CollectorError('INVALID_ROW', f"{col_name} exceeds 2 decimal places at row {row_number}")
            return dec

        payment_status = row_dict['payment_status']
        if not payment_status:
            raise CollectorError('INVALID_ROW', f"payment_status is required at row {row_number}")
        payment_status = str(payment_status)
        if len(payment_status) > 128:
            raise CollectorError('INVALID_ROW', f"payment_status exceeds 128 chars at row {row_number}")

        order_amount = parse_decimal(row_dict['order_amount'], 'order_amount', nullable=False)
        refund_amount = parse_decimal(row_dict['refund_amount'], 'refund_amount', nullable=True)

        currency = row_dict['currency']
        if not currency or str(currency) != contract['currency']:
            raise CollectorError('INVALID_ROW', f"Incompatible currency '{currency}' at row {row_number}; expected '{contract['currency']}'")

        return (order_id, customer_id, order_date_str, payment_date_str, status,
                payment_status, order_amount, refund_amount, str(currency))

    def _validate_flat_row(self, raw_tuple: tuple, contract: dict, seen_keys: set, row_number: int) -> tuple:
        """Validate row against Flat V3 schema roles and precision/scale bounds."""
        key = raw_tuple[0]
        if not key or not isinstance(key, str):
            raise CollectorError('INVALID_ROW', f"Missing or invalid row_key at row {row_number}")
        if len(key) > 256:
            raise CollectorError('INVALID_ROW', f"row_key exceeds 256 chars at row {row_number}")
        if key in seen_keys:
            raise CollectorError('DUPLICATE_KEY', f"Duplicate row_key '{key}' at row {row_number}")
        seen_keys.add(key)

        specs = (contract['decimal_columns'] if contract.get('version') in (6, 8) else
                 [{'name': contract['measure'], 'precision': contract['precision'], 'scale': contract['scale']}])
        amounts = []
        for offset, spec in enumerate(specs, start=1):
            measure_val = raw_tuple[offset]
            dec_amount = None
            if measure_val is not None:
                try:
                    dec = Decimal(str(measure_val))
                except (InvalidOperation, TypeError):
                    raise CollectorError('INVALID_ROW', f"Invalid decimal {spec['name']} at row {row_number}")
                if not dec.is_finite():
                    raise CollectorError('INVALID_ROW', f"Non-finite {spec['name']} at row {row_number}")
                digits = dec.as_tuple().digits
                fractional = max(-dec.as_tuple().exponent, 0)
                integer = max(len(digits) + dec.as_tuple().exponent, 0)
                if fractional > spec['scale'] or integer > spec['precision'] - spec['scale']:
                    raise CollectorError('INVALID_ROW', f"{spec['name']} exceeds declared DECIMAL({spec['precision']},{spec['scale']}) at row {row_number}")
                dec_amount = dec
            amounts.append(dec_amount)

        categories = []
        types = contract.get('dimension_types') or {}
        nullable = set(contract.get('nullable_categories') or contract['categories'])
        for i, cat_name in enumerate(contract['categories'], start=1 + len(specs)):
            val = raw_tuple[i]
            kind = types.get(cat_name, 'string') if contract['version'] >= 4 else 'string'
            if val is None:
                if contract['version'] >= 4 and cat_name not in nullable:
                    raise CollectorError('INVALID_ROW', f"'{cat_name}' is blank at row {row_number} but was not declared nullable")
                categories.append(None)
            elif kind == 'boolean':
                if not isinstance(val, bool):
                    raise CollectorError('INVALID_ROW', f"'{cat_name}' must be a boolean at row {row_number}")
                categories.append(val)
            elif kind == 'date':
                if not isinstance(val, date) or isinstance(val, datetime):
                    raise CollectorError('INVALID_ROW', f"'{cat_name}' must be a date at row {row_number}")
                categories.append(val)
            elif kind == 'timestamp':
                if not isinstance(val, datetime) or val.tzinfo is not None:
                    raise CollectorError('INVALID_ROW', f"'{cat_name}' must be a timestamp without time zone at row {row_number}")
                categories.append(val)
            else:
                val_str = str(val)
                if len(val_str) > 256:
                    raise CollectorError('INVALID_ROW', f"Category '{cat_name}' exceeds 256 chars at row {row_number}")
                categories.append(val_str)

        return (key, *amounts, *categories)

    def _build_manifest(
        self,
        snapshot_id: str,
        selection: PostgresSourceSelection,
        cutoff_val: str,
        contract_info: dict,
        row_count: int,
        database_hash: str,
    ) -> dict:
        """Build integrity-verified manifest binding the snapshot to source, cutoff, and contract."""
        is_flat = contract_info['is_flat']
        contract = contract_info['contract']
        mapping = contract_info.get('mapping')
        sanitized_source = sanitize_source_uri(selection.source_uri)

        source_hash = digest({
            'source_type': 'postgresql',
            'sanitized_source': sanitized_source,
            'schema': selection.schema_name,
            'table': selection.table_name,
            'cutoff_column': selection.cutoff_column,
            'cutoff_value': cutoff_val,
            'extraction_id': selection.extraction_id,
        })

        manifest = {
            'snapshot_id': snapshot_id,
            'name': (selection.snapshot_name or f"PostgreSQL {selection.table_name} snapshot") + (f" (sample of {selection.sample_rows} rows)" if selection.sample_rows else ''),
            'source_type': 'postgresql',
            'sanitized_source': sanitized_source,
            'schema': selection.schema_name,
            'table': selection.table_name,
            'selected_columns': selection.columns or (list(contract['categories']) if is_flat else list(SCHEMA)),
            'contract_hash': contract_info['contract_hash'],
            'cutoff_column': selection.cutoff_column,
            'cutoff_at': cutoff_val,
            'extraction_cutoff': cutoff_val,
            'extraction_id': selection.extraction_id,
            'attempt': selection.attempt or 1,
            'row_count': row_count,
            'consistency_policy': 'REPEATABLE READ READ ONLY',
            'database_hash': database_hash,
            'manifest_version': 'postgresql-v4',
            'collector_version': '4.0.0',
            'primary_key': contract_info['primary_key'],
            'source_hash': source_hash,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'transformations': [
                'Bounded read-only PostgreSQL extraction',
                'Transaction isolation REPEATABLE READ READ ONLY',
                'Streamed contract validation and row bounds',
                'Frozen DuckDB snapshot'
            ],
        }

        if contract_info.get('mapping_hash'):
            manifest['mapping_hash'] = contract_info['mapping_hash']
        if selection.contract_id:
            manifest['contract_id'] = selection.contract_id
        if selection.mapping_id:
            manifest['mapping_id'] = selection.mapping_id

        if is_flat:
            manifest['mode'] = 'flat_snapshot'
            manifest['policy'] = 'flat-snapshot-v1'
            manifest['execution_policy'] = 'flat_snapshot_v1'
            manifest['contract_version'] = contract['version']
            manifest['contract'] = contract
            manifest['scope'] = contract['scope']
            manifest['unit_label'] = contract['unit_label']
            manifest['measure_meaning'] = contract['measure_meaning']
            manifest['values_comparable'] = True
            manifest['null_policy'] = contract['null_policy']
            manifest['currency'] = None
            manifest['timezone'] = None
            manifest['schema'] = flat_schema(contract)
            if selection.sample_rows:
                manifest['sample_rows'] = selection.sample_rows
                manifest['transformations'] = [*manifest['transformations'], f'Sample: first {selection.sample_rows} rows by row key']
        else:
            manifest['currency'] = contract.get('currency', 'INR')
            manifest['timezone'] = contract.get('timezone', 'Asia/Kolkata')
            manifest['scope'] = contract.get('scope', 'synthetic_retail_all_orders')
            manifest['grain'] = contract.get('grain', 'one_row_per_order_id')
            manifest['null_policy'] = contract.get('null_policy', 'blank_nullable')
            manifest['schema'] = SCHEMA

        manifest['manifest_hash'] = digest(manifest)
        return manifest
