import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import json
import shutil
import httpx

DATA_DIR = Path('data/v8_final_acceptance')
keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
meta = json.loads((DATA_DIR / 'test_metadata.json').read_text(encoding='utf-8'))

pid = 'ws-relational-final'
API_URL = 'http://127.0.0.1:8001'

c_analyst = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['analyst']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)

def main():
    run_id = meta['run_id']
    c5_run_id = meta['c5_run_id']
    inv_id = meta['investigation_id']
    snapshot_id = meta['snapshot_id']
    
    print("=================================================================")
    print("STEP 5A: JSON & HTML Exports Verification")
    print("=================================================================")
    
    # 1. JSON Export for 5-row run
    r_export_json_5 = c_analyst.get(f'/api/v1/comparisons/{run_id}/export?format=json')
    assert r_export_json_5.status_code == 200, f"Export JSON failed: {r_export_json_5.text}"
    export_json_5 = r_export_json_5.json()
    assert 'join_proof' in export_json_5['result'], "JSON export missing join_proof"
    assert 'join_evidence' in export_json_5, "JSON export missing join_evidence"
    assert export_json_5['join_evidence']['summary']['policy'] == 'relational-join-evidence-v1'
    assert len(export_json_5['join_evidence']['rows']) == 5
    assert export_json_5['evidence_coverage']['total_rows'] == 5
    assert export_json_5['evidence_coverage']['included_rows'] == 5
    assert export_json_5['evidence_coverage']['omitted_rows'] == 0
    print("-> JSON export for 5-row run PASS: join_proof, join_evidence summary and rows present.")
    
    # 2. JSON Export for 120-row run (caps at 100 rows, coverage metadata)
    r_export_json_120 = c_analyst.get(f'/api/v1/comparisons/{c5_run_id}/export?format=json')
    assert r_export_json_120.status_code == 200
    export_json_120 = r_export_json_120.json()
    assert export_json_120['evidence_coverage']['total_rows'] == 120
    assert export_json_120['evidence_coverage']['included_rows'] == 100
    assert export_json_120['evidence_coverage']['omitted_rows'] == 20
    assert export_json_120['evidence_coverage']['detail'] == 'first_rows'
    assert len(export_json_120['join_evidence']['rows']) == 100
    print("-> JSON export for 120-row run PASS: capped at 100 rows with exact coverage metadata.")
    
    # 3. HTML Export for 5-row run
    r_export_html = c_analyst.get(f'/api/v1/comparisons/{run_id}/export?format=html')
    assert r_export_html.status_code == 200
    html_content = r_export_html.text
    assert 'html' in html_content.lower()
    assert 'join_proof' in html_content.lower() or 'grain_preserved' in html_content.lower() or 'join details' in html_content.lower()
    # Check escaping of special chars and HTML safety
    assert '<script>' not in html_content
    print("-> HTML export for 5-row run PASS: HTML report formatted cleanly.")
    
    print("\n=================================================================")
    print("STEP 5B: Snapshot Unavailability Replay Test")
    print("=================================================================")
    # Find snapshot artifact on disk
    r_snap = c_analyst.get(f'/api/v1/snapshots/{snapshot_id}')
    assert r_snap.status_code == 200
    # In sqlite, get snapshot artifact path
    import sqlite3
    conn = sqlite3.connect(DATA_DIR / 'metricguard.db')
    row = conn.execute("SELECT data FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    conn.close()
    snap_record = json.loads(row[0])
    artifact_path = Path(snap_record['artifact_path'])
    assert artifact_path.is_file(), f"Snapshot artifact not found at {artifact_path}"
    
    # Temporarily move snapshot file
    backup_snap_path = artifact_path.with_suffix('.bak')
    shutil.move(artifact_path, backup_snap_path)
    try:
        # Verify GET /join-evidence STILL WORKS
        r_je = c_analyst.get(f'/api/v1/comparisons/{run_id}/join-evidence?offset=0&limit=10')
        assert r_je.status_code == 200, f"join-evidence should still work even if snapshot file is missing: {r_je.text}"
        print("-> Join evidence readable when snapshot file is missing (PASS)")
        
        # Verify POST /replays FAILS with SNAPSHOT_MISSING HTTP 400
        r_rep = c_analyst.post(f'/api/v1/investigations/{inv_id}/replays', headers={'Idempotency-Key': f"replay-missing-{run_id}"})
        assert r_rep.status_code in (400, 422), f"Expected 400/422 SNAPSHOT_MISSING, got {r_rep.status_code}: {r_rep.text}"
        err = r_rep.json()['error']
        assert err['code'] == 'SNAPSHOT_MISSING'
        assert err['message'] == 'Stored results remain readable, but this snapshot is unavailable for replay.'
        print(f"-> Replay refused with SNAPSHOT_MISSING: '{err['message']}' (PASS)")
    finally:
        # Restore snapshot file
        shutil.move(backup_snap_path, artifact_path)
        
    # Verify replay succeeds after restoration
    r_rep_ok = c_analyst.post(f'/api/v1/investigations/{inv_id}/replays', headers={'Idempotency-Key': f"replay-restored-{run_id}"})
    assert r_rep_ok.status_code == 202
    print("-> Replay succeeds after snapshot file restored (PASS)")

if __name__ == '__main__':
    main()
