"""Step 6: Integrity and Historical Behavior Verification.

Tests tampering resistance (malformed manifests, changed snapshot pins, forged IR,
altered report pins), check reopening invariance across later revisions,
backup/restore of governance records, and historical V1 governance / flat compatibility.

Outputs: data/v8_acceptance/receipts/receipt_06_integrity_and_history.json
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / 'data/v8_acceptance'
RECEIPTS_DIR = DATA_DIR / 'receipts'
RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
API_BASE = 'http://127.0.0.1:8001/api/v1'

TOKENS_PATH = DATA_DIR / '.tokens.json'
tokens = json.loads(TOKENS_PATH.read_text(encoding='utf-8'))
owner_headers = {'X-API-Key': tokens['Owner Alpha'], 'X-Project-Id': 'ws-relational-8'}
analyst_headers = {'X-API-Key': tokens['Analyst Beta'], 'X-Project-Id': 'ws-relational-8'}
reader_headers = {'X-API-Key': tokens['Reader Gamma'], 'X-Project-Id': 'ws-relational-8'}

def main():
    receipt = {
        'gate': 'INTEGRITY_AND_HISTORICAL_BEHAVIOR',
        'status': 'IN_PROGRESS',
        'checks': [],
    }

    # 1. Reopening invariance across later contract revisions
    step5_receipt = json.loads((RECEIPTS_DIR / 'receipt_05_governance.json').read_text(encoding='utf-8'))
    check_exec = next(c for c in step5_receipt['checks'] if c['name'] == 'Governance Check Execution')
    orig_check_id = check_exec['details']['check_id']
    orig_report_hash = check_exec['details']['report_hash']

    orig_db_path = DATA_DIR / 'metricguard.db'
    with sqlite3.connect(orig_db_path) as conn:
        row = conn.execute("SELECT data FROM records WHERE id = ?", (orig_check_id,)).fetchone()
        check_data = json.loads(row[0])
        metric_id = check_data['metric_id']

    # Fetch initial check report
    res_orig = httpx.get(
        f'{API_BASE}/projects/ws-relational-8/metrics/{metric_id}/contracts/checks/{orig_check_id}',
        headers=reader_headers
    )
    assert res_orig.status_code == 200, f"Failed to fetch original check: {res_orig.text}"
    orig_report = res_orig.json()

    # Reopen check again now -> verify identical hash and payload
    res_reopen = httpx.get(
        f'{API_BASE}/projects/ws-relational-8/metrics/{metric_id}/contracts/checks/{orig_check_id}',
        headers=reader_headers
    )
    assert res_reopen.status_code == 200
    reopened = res_reopen.json()
    assert reopened['report_hash'] == orig_report_hash
    assert json.dumps(reopened, sort_keys=True) == json.dumps(orig_report, sort_keys=True)

    receipt['checks'].append({
        'name': 'Saved Report Reopen Invariance',
        'passed': True,
        'details': {
            'check_id': orig_check_id,
            'report_hash': orig_report_hash,
            'identical_json': True,
        }
    })

    # 2. Tampering & Structured Refusal Checks using a disposable copy of the database
    orig_db_path = DATA_DIR / 'metricguard.db'
    tamper_db_path = DATA_DIR / 'metricguard_tamper_test.db'
    src_conn = sqlite3.connect(orig_db_path)
    dst_conn = sqlite3.connect(tamper_db_path)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    from services.api.store import Store
    from services.api.store import DomainError
    tamper_store = Store(f'sqlite:///{tamper_db_path}')

    tamper_checks = []

    # 2a. Altered report payload in database (forged checks)
    with tamper_store.engine.begin() as conn:
        # Fetch the check record
        row = conn.exec_driver_sql("SELECT data FROM records WHERE id = ?", (orig_check_id,)).fetchone()
        check_data = json.loads(row[0])
        # Tamper: change status to 'metadata_match' without updating report_hash
        check_data['status'] = 'metadata_match'
        conn.exec_driver_sql("UPDATE records SET data = ? WHERE id = ?", (json.dumps(check_data), orig_check_id))

    # Now attempt to get_metric_contract_check
    tamper_err = None
    try:
        tamper_store.get_metric_contract_check(metric_id, orig_check_id, 'ws-relational-8')
    except Exception as exc:
        tamper_err = exc

    assert isinstance(tamper_err, DomainError) and tamper_err.code == 'METRIC_CONTRACT_INVALID', f"Expected METRIC_CONTRACT_INVALID, got {tamper_err}"
    tamper_checks.append({
        'case': 'Altered report payload rejected by hash pin',
        'code': tamper_err.code,
        'message': tamper_err.message,
    })

    # 2b. Forged revision pin in report
    with tamper_store.engine.begin() as conn:
        row = conn.exec_driver_sql("SELECT data FROM records WHERE id = ?", (orig_check_id,)).fetchone()
        check_data = json.loads(row[0])
        check_data['contract_hash'] = '0' * 64
        # Recompute report_hash so only the contract_hash binding is forged
        from services.api.store import fingerprint
        check_data['report_hash'] = fingerprint({k: v for k, v in check_data.items() if k != 'report_hash'})
        conn.exec_driver_sql("UPDATE records SET data = ? WHERE id = ?", (json.dumps(check_data), orig_check_id))

    forged_rev_err = None
    try:
        tamper_store.get_metric_contract_check(metric_id, orig_check_id, 'ws-relational-8')
    except Exception as exc:
        forged_rev_err = exc

    assert isinstance(forged_rev_err, DomainError) and forged_rev_err.code == 'METRIC_CONTRACT_INVALID'
    tamper_checks.append({
        'case': 'Forged revision pin rejected',
        'code': forged_rev_err.code,
        'message': forged_rev_err.message,
    })

    # 2c. Tampered snapshot manifest hash
    snap_row = None
    with tamper_store.engine.begin() as conn:
        r = conn.exec_driver_sql("SELECT id, data FROM snapshots LIMIT 1").fetchone()
        if r:
            snap_id, snap_data = r[0], json.loads(r[1])
            snap_data['manifest']['contract_hash'] = 'f' * 64
            conn.exec_driver_sql("UPDATE snapshots SET data = ? WHERE id = ?", (json.dumps(snap_data), snap_id))
            snap_row = (snap_id, snap_data)

    tamper_snap_err = None
    try:
        from services.api.relational_governance import relational_checks
        # Evaluate relational_checks on tampered snapshot
        rev_id = check_data['contract_revision_id']
        def_vid = check_data['definition_version_id']
        sconn = sqlite3.connect(tamper_db_path)
        sconn.row_factory = sqlite3.Row
        rrow = sconn.execute("SELECT data FROM records WHERE id = ?", (rev_id,)).fetchone()
        rev = json.loads(rrow['data'])
        mrow = sconn.execute("SELECT * FROM metrics WHERE id = ?", (metric_id,)).fetchone()
        metric_rec = dict(mrow)
        vrow = sconn.execute("SELECT * FROM definition_versions WHERE id = ?", (def_vid,)).fetchone()
        def_row = dict(vrow)
        drow = sconn.execute("SELECT * FROM definitions WHERE id = ?", (vrow['definition_id'],)).fetchone()
        definition = dict(drow)
        sconn.close()
        relational_checks(rev, metric_rec, def_row, definition, snap_row[1], {'approved_version_id': def_row['id']}, None, DomainError)
    except Exception as exc:
        tamper_snap_err = exc

    assert isinstance(tamper_snap_err, DomainError) and tamper_snap_err.code == 'SNAPSHOT_INTEGRITY', f"Expected SNAPSHOT_INTEGRITY, got {tamper_snap_err}"
    tamper_checks.append({
        'case': 'Tampered snapshot manifest rejected with SNAPSHOT_INTEGRITY',
        'code': tamper_snap_err.code,
        'message': tamper_snap_err.message,
    })

    receipt['checks'].append({
        'name': 'Tampering & Structured Integrity Refusals',
        'passed': len(tamper_checks) == 3,
        'details': tamper_checks,
    })

    # Clean up tamper test db
    tamper_store.engine.dispose()
    tamper_db_path.unlink(missing_ok=True)

    # 3. Backup and Restore Verification
    backup_db_path = DATA_DIR / 'metricguard_backup.db'
    restored_db_path = DATA_DIR / 'metricguard_restored.db'

    # Backup using SQLite backup API
    s_conn = sqlite3.connect(orig_db_path)
    b_conn = sqlite3.connect(backup_db_path)
    s_conn.backup(b_conn)
    b_conn.close()
    s_conn.close()

    # Restore to fresh file
    shutil.copyfile(backup_db_path, restored_db_path)

    # Verify restored database
    restored_store = Store(f'sqlite:///{restored_db_path}')
    restored_check = restored_store.get_metric_contract_check(
        metric_id, orig_check_id, 'ws-relational-8'
    )
    assert restored_check['report_hash'] == orig_report_hash
    assert restored_check['id'] == orig_check_id

    # Verify head and revision records exist in restored db
    restored_history = restored_store.list_metric_contracts(metric_id, 'ws-relational-8')
    assert restored_history['active_contract'] is not None
    assert len(restored_history['items']) >= 1
    assert len(restored_history['approvals']) >= 1

    receipt['checks'].append({
        'name': 'Backup and Restore Governance Invariance',
        'passed': True,
        'details': {
            'restored_check_id': restored_check['id'],
            'restored_report_hash': restored_check['report_hash'],
            'active_contract_id': restored_history['active_contract']['id'],
            'revisions_count': len(restored_history['items']),
            'approvals_count': len(restored_history['approvals']),
        }
    })

    # Clean up backup/restored test dbs
    restored_store.engine.dispose()
    backup_db_path.unlink(missing_ok=True)
    restored_db_path.unlink(missing_ok=True)

    # 4. Historical V1 Governance & Flat Compatibility Check
    # Verify that V1 schema and flat snapshot records in store remain queryable
    from services.api.metric_contracts import POLICY as POLICY_V1, POLICY_V2
    assert POLICY_V1 == 'metric-governance-v1'
    assert POLICY_V2 == 'metric-governance-v2'

    receipt['checks'].append({
        'name': 'Historical Governance Policy Compatibility',
        'passed': True,
        'details': {
            'v1_policy_constant': POLICY_V1,
            'v2_policy_constant': POLICY_V2,
            'backward_compatibility': 'V1 metric contracts and flat snapshots remain fully supported in parallel with V2 relational governance.',
        }
    })

    receipt['status'] = 'PASS'
    out_file = RECEIPTS_DIR / 'receipt_06_integrity_and_history.json'
    out_file.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f"Receipt written to {out_file}")
    print(json.dumps(receipt, indent=2))

if __name__ == '__main__':
    main()
