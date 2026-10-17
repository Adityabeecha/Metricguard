"""Validated fixed-schema CSV ingestion; no inferred types or hidden cleanup."""
import csv
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import duckdb
from .common import CoreError, SCHEMA, NUMERIC, DATES, ALL_COLUMNS, digest, file_hash

DEFAULT_METADATA={'currency':'INR','timezone':'Asia/Kolkata','scope':'synthetic_retail_all_orders','grain':'one_row_per_order_id','cutoff_at':'2026-09-02','null_policy':'blank_nullable'}
NULLABLE={'customer_id','payment_date','status','refund_amount'}
MAX_BYTES=25*1024*1024

def manifest_path(database): return Path(str(database)+'.manifest.json')

def ingest_csv(source, destination, metadata=None):
    source,destination=Path(source),Path(destination)
    meta={**DEFAULT_METADATA,**(metadata or {})}
    if destination.exists() or manifest_path(destination).exists(): raise CoreError('immutable_snapshot','Snapshot destination already exists')
    if meta['currency']!='INR' or meta['timezone']!='Asia/Kolkata' or not meta['scope'] or meta['grain']!='one_row_per_order_id': raise CoreError('invalid_metadata','v1 requires INR, Asia/Kolkata, declared scope, and unique order grain')
    if meta['null_policy'] not in {'blank_nullable','literal_NULL'}: raise CoreError('invalid_metadata','Unknown explicit NULL policy')
    try: date.fromisoformat(meta['cutoff_at'][:10])
    except (ValueError,TypeError): raise CoreError('invalid_metadata','Valid extraction cutoff required')
    if source.stat().st_size>MAX_BYTES: raise CoreError('file_too_large','CSV exceeds 25 MiB')
    source_hash=file_hash(source)
    row_count=0; ids=set()
    try:
        with source.open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream)
            if reader.fieldnames!=list(SCHEMA): raise CoreError('invalid_schema','CSV must have the exact ordered schema: '+', '.join(SCHEMA))
            for number,raw in enumerate(reader,2):
                if row_count>=1000000: raise CoreError('too_many_rows','Snapshot exceeds 1000000 rows')
                if None in raw or any(v is None for v in raw.values()): raise CoreError('invalid_row',f'Wrong field count at line {number}')
                row=[]
                for column,value in raw.items():
                    is_null=value==('' if meta['null_policy']=='blank_nullable' else 'NULL') and column in NULLABLE
                    if is_null: row.append(None); continue
                    if column in DATES:
                        if len(value)!=10: raise ValueError('Date must use YYYY-MM-DD')
                        value=date.fromisoformat(value)
                    elif column in NUMERIC:
                        import re
                        if not re.fullmatch(r'-?\d+(\.\d{1,2})?',value): raise ValueError('Amounts require decimal syntax with at most two places')
                        value=Decimal(value)
                        if not value.is_finite() or abs(value)>=Decimal('1e16'): raise ValueError('Amount exceeds DECIMAL(18,2)')
                    elif value=='' and column not in NULLABLE: raise ValueError('Required string is empty')
                    if column=='currency' and value!=meta['currency']: raise ValueError('Mixed or incompatible currency')
                    if column=='order_id':
                        if value in ids: raise ValueError('Duplicate order_id')
                        ids.add(value)
                    row.append(value)
                row_count+=1
    except CoreError: raise
    except (ValueError,InvalidOperation,UnicodeError,csv.Error) as e: raise CoreError('invalid_csv',f'CSV validation failed: {e}') from e
    if file_hash(source)!=source_hash: raise CoreError('source_changed','CSV changed while it was being validated')
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        # The source has been fully validated above. Only this fixed, parameterized
        # ingestion COPY may read it; comparison connections disable external access.
        with duckdb.connect(str(destination),config={'enable_external_access':'true'}) as con:
            con.execute('BEGIN TRANSACTION')
            con.execute('CREATE TABLE orders ('+', '.join('"'+name+'" '+kind for name,kind in SCHEMA.items())+')')
            null_literal="''" if meta['null_policy']=='blank_nullable' else "'NULL'"
            con.execute(f'COPY orders FROM ? (FORMAT CSV, HEADER TRUE, NULLSTR {null_literal})',
                        [str(source.resolve())])
            if con.execute('SELECT COUNT(*) FROM orders').fetchone()[0]!=row_count:
                raise CoreError('source_changed','CSV row count changed during bulk loading')
            con.execute('COMMIT')
        if file_hash(source)!=source_hash: raise CoreError('source_changed','CSV changed during bulk loading')
        manifest={**meta,'row_count':row_count,'schema':SCHEMA,'primary_key':'order_id','source_hash':source_hash,'database_hash':file_hash(destination),'ingestion_version':'csv-v1','created_at':datetime.now(timezone.utc).isoformat(),'transformations':['Declared nullable fields map empty tokens to SQL NULL' if meta['null_policy']=='blank_nullable' else 'Declared nullable fields map literal NULL tokens to SQL NULL']}
        manifest['manifest_hash']=digest(manifest)
        manifest_path(destination).write_text(json.dumps(manifest,sort_keys=True,indent=2),encoding='utf-8')
        return manifest
    except Exception:
        if destination.exists(): destination.unlink()
        raise

def inspect_snapshot(database):
    database=Path(database)
    try:
        manifest=json.loads(manifest_path(database).read_text(encoding='utf-8'))
        expected=manifest.pop('manifest_hash')
        if digest(manifest)!=expected or file_hash(database)!=manifest['database_hash']: raise CoreError('snapshot_integrity','Snapshot bytes or manifest changed')
        manifest['manifest_hash']=expected
        if manifest['schema'] not in (SCHEMA, ALL_COLUMNS): raise CoreError('snapshot_integrity','Snapshot schema differs from registered schema')
        return manifest
    except CoreError: raise
    except (OSError,ValueError,KeyError) as e: raise CoreError('snapshot_missing','Snapshot or valid manifest is unavailable') from e

