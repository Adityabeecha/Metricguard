"""Explicit v4 → v5 opt-in over the exact frozen typed table; never re-infer it."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .common import CoreError, digest, file_hash
from .flat import MAX_FLAT_BYTES, MAX_FLAT_ROWS, flat_schema, validate_contract
from .flat_boolean import PREDICATE_POLICY
from .free_tier import check_rows
from .snapshot import manifest_path


def upgrade_contract(contract):
    if contract.get('version') != 4:
        raise CoreError('invalid_contract', 'Only a typed version 4 dataset can enable averages and ratios this way.')
    original = validate_contract(contract)
    original.pop('contract_hash', None)
    return validate_contract({**original, 'version': 5, 'predicate_policy': PREDICATE_POLICY})


def upgrade_frozen(source, destination, metadata):
    """Called inside the runner. Copy verified bytes, then seal a new contract identity.

    v4 and v5 have identical physical types and NULL meanings. Copying the database
    preserves all values exactly, including booleans, microseconds and SQL NULLs.
    """
    source, destination = Path(source), Path(destination)
    sidecar = manifest_path(source)
    if destination.exists() or manifest_path(destination).exists():
        raise CoreError('invalid_metadata', 'The new frozen dataset already exists.')
    if source.stat().st_size > MAX_FLAT_BYTES or sidecar.stat().st_size > 1024 * 1024:
        raise CoreError('file_too_large', 'The frozen dataset exceeds the supported upgrade size.')
    old = json.loads(sidecar.read_text(encoding='utf-8'))
    lineage = metadata['frozen_upgrade']
    old_body = {k: v for k, v in old.items() if k != 'manifest_hash'}
    contract = upgrade_contract(old['contract'])
    if (digest(old_body) != old.get('manifest_hash') or
            old.get('manifest_hash') != lineage.get('manifest_hash') or
            old.get('database_hash') != lineage.get('database_hash') or
            old.get('source_hash') != lineage.get('source_hash') or
            old.get('contract_hash') != lineage.get('contract_hash') or
            old.get('contract_hash') != old['contract'].get('contract_hash') or
            old.get('contract_version') != 4 or old.get('execution_policy') != 'flat_snapshot_v1' or
            file_hash(source) != old.get('database_hash') or
            validate_contract(metadata['flat_contract']) != contract or
            old.get('schema') != flat_schema(contract)):
        raise CoreError('invalid_metadata', 'The frozen dataset or its registered meaning changed. Upgrade refused.')
    with duckdb.connect(str(source), read_only=True, config={'threads': '1', 'memory_limit': '512MB'}) as con:
        physical = {r[0]: r[1] for r in con.execute('DESCRIBE snapshot').fetchall()}
        count = con.execute('SELECT COUNT(*) FROM snapshot').fetchone()[0]
    if physical != old['schema'] or count != old['row_count'] or not 0 <= count <= MAX_FLAT_ROWS:
        raise CoreError('invalid_schema', 'The frozen table does not match its pinned schema and row count.')
    check_rows(count)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, destination)
        if file_hash(destination) != old['database_hash']:
            raise CoreError('source_changed', 'The frozen dataset changed during upgrade.')
        result = {**old, 'contract_version': 5, 'contract_hash': contract['contract_hash'],
                  'contract': contract, 'source_hash': old['database_hash'],
                  'frozen_upgrade': lineage, 'created_at': datetime.now(timezone.utc).isoformat(),
                  'transformations': ['Typed version 4 frozen table copied byte-for-byte; averages and ratios explicitly enabled.']}
        # The original preparation applies to the original source, whose identity remains in lineage.
        # Keep its receipt under a provenance key instead of claiming it prepared this new source.
        if 'flat_preparation' in result:
            result['original_flat_preparation'] = result.pop('flat_preparation')
        result.pop('manifest_hash', None)
        result['manifest_hash'] = digest(result)
        manifest_path(destination).write_text(json.dumps(result, sort_keys=True, indent=2), encoding='utf-8')
        return result
    except Exception:
        destination.unlink(missing_ok=True)
        manifest_path(destination).unlink(missing_ok=True)
        raise
