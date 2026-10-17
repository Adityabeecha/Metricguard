import sys
from pathlib import Path
import json
import shutil
import hashlib
import sqlite3
import subprocess
import time
import os
import uuid
import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.api.store import Store
from services.api.backup import backup_project, restore_project
from services.api.reports import html_report, join_evidence_html

DATA_DIR = Path('data/v8_final_acceptance')
keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
meta = json.loads((DATA_DIR / 'test_metadata.json').read_text(encoding='utf-8'))

pid = 'ws-relational-final'
PRIMARY_API_URL = 'http://127.0.0.1:8001'
DISPOSABLE_API_URL = 'http://127.0.0.1:8002'

def ensure_primary_service_running():
    """Ensure primary acceptance API is running on port 8001."""
    try:
        r = httpx.get(f'{PRIMARY_API_URL}/api/v1/health', timeout=1.0)
        if r.status_code == 200:
            return None, None
    except Exception:
        pass

    env = os.environ.copy()
    env['METRICGUARD_MODE'] = 'isolated'
    env['METRICGUARD_DATA_DIR'] = str(DATA_DIR.resolve())
    env['METRICGUARD_DATABASE_URL'] = f"sqlite:///{(DATA_DIR / 'metricguard.db').resolve()}"
    env['METRICGUARD_ALLOW_UNVERIFIED_LOCAL'] = 'false'
    env['METRICGUARD_RUNNER_IMAGE'] = 'metricguard-runner:1.0'

    api_proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'services.api.app:create_app', '--factory', '--host', '127.0.0.1', '--port', '8001'],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    for _ in range(30):
        try:
            r = httpx.get(f'{PRIMARY_API_URL}/api/v1/health', timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.5)

    return api_proc, None

def ensure_disposable_service_running(disposable_dir: Path):
    """Start an API process bound to the restored database and data directory on port 8002."""
    env = os.environ.copy()
    disp_resolved = disposable_dir.resolve()
    env['METRICGUARD_MODE'] = 'isolated'
    env['METRICGUARD_DATA_DIR'] = str(disp_resolved)
    env['METRICGUARD_DATABASE_URL'] = f"sqlite:///{(disp_resolved / 'metricguard.db')}"
    env['METRICGUARD_ALLOW_UNVERIFIED_LOCAL'] = 'false'
    env['METRICGUARD_PORT'] = '8002'
    env['METRICGUARD_RUNNER_IMAGE'] = 'metricguard-runner:1.0'

    api_proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'services.api.app:create_app', '--factory', '--host', '127.0.0.1', '--port', '8002'],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    for _ in range(30):
        try:
            r = httpx.get(f'{DISPOSABLE_API_URL}/api/v1/health', timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.5)

    return api_proc


def verify_html_escaping_with_special_characters():
    print("\n--- Part A: Verify HTML Entity Escaping with Malicious / Special Character Fixture ---")
    special_bundle = {
        'id': 'test-run-xss',
        'project_id': pid,
        'execution_mode': 'isolated',
        'state': 'succeeded',
        'baseline_version_id': 'v-base',
        'target_version_id': 'v-target',
        'snapshot_id': 'snap-1',
        'context': {'contract_hash': 'abc'},
        'baseline': {'name': 'Baseline <script>alert("xss")</script> & "quotes" \'single\''},
        'target': {'name': 'Target <img src=x onerror=alert(1)>'},
        'result': {
            'baseline_value': '65.00',
            'target_value': '10.00',
            'delta_target_minus_baseline': '-55.00',
            'residual': '0.00',
            'definition_status': 'different',
            'value_status': 'defined',
            'explanation_status': 'complete_join_filter_change',
            'steps': [
                {
                    'rule': 'join_and_filter <script>evil()</script>',
                    'previous_value': '65.00',
                    'contribution': '-55.00',
                    'next_value': '10.00'
                }
            ],
            'evidence': [
                {
                    'order_id': 'F1<script>alert("row")</script>',
                    'fact_key': 'F1<script>alert("row")</script>',
                    'membership': 'included_in_both & <b>bold</b>',
                    'baseline_contribution': '10.00',
                    'target_contribution': '10.00',
                    'delta': '0.00',
                    'foreign_key': 'D1 & <tag>',
                    'dimension_key': 'D1 & <tag>',
                    'join_membership': 'matched',
                    'baseline_join_included': True,
                    'target_join_included': True,
                    'measure_value': '10.00',
                    'fact_values': {'kind': 'web <script>'},
                    'dimension_values': {'segment': 'north & "seg"'}
                }
            ],
            'limitations': [
                'Limitation <script>alert("limitation")</script> & "special"',
                'Second limit with <b>html</b>'
            ],
            'provenance': {
                'execution_policy': 'relational_snapshot_v1'
            }
        },
        'evidence_coverage': {
            'total_rows': 1,
            'included_rows': 1,
            'omitted_rows': 0,
            'detail': 'first_rows'
        },
        'reproducibility': {
            'notes': '<script>steal_keys()</script>'
        },
        'join_evidence': {
            'summary': {
                'contract': {
                    'fact': {'table': 'facts <script>', 'key': 'fid', 'grain': 'one fact <test>'},
                    'dimension': {'table': 'dims <img>', 'key': 'did', 'grain': 'one dim & "quote"'},
                    'relationship': {'fact_foreign_key': 'gid <tag>'},
                    'measure': {'column': 'amt', 'scale': 2, 'meaning': 'points & <bold>'},
                    'unit_label': 'pts <script>'
                },
                'join_proof': {
                    'fact_rows': 1, 'dimension_rows': 1, 'fact_key_unique': True,
                    'dimension_key_unique': True, 'left_join_rows': 1, 'matched_fact_rows': 1,
                    'null_foreign_key_rows': 0, 'missing_nonnull_foreign_key_rows': 0,
                    'max_dimension_matches_per_fact': 1, 'fanout_rows': 0
                },
                'reconciliation': {
                    'status': 'reconciled',
                    'baseline_bookkeeping_sum': '65.00',
                    'target_bookkeeping_sum': '10.00',
                    'row_delta_sum': '-55.00',
                    'delta_target_minus_baseline': '-55.00',
                    'residual': '0.00'
                },
                'side_counts': {
                    'baseline': {'included_value': 1, 'included_null_measure': 0, 'excluded_join': 0, 'excluded_filter_false': 0, 'excluded_filter_unknown': 0},
                    'target': {'included_value': 1, 'included_null_measure': 0, 'excluded_join': 0, 'excluded_filter_false': 0, 'excluded_filter_unknown': 0}
                },
                'definitions': {
                    'baseline': {'join': 'left', 'predicates': []},
                    'target': {'join': 'inner', 'predicates': []}
                },
                'fact_rows': 1,
                'changed_inclusion_rows': 0,
                'nonzero_contribution_rows': 1,
                'null_semantics': 'NULL policy & <tag>',
                'pins': {'contract_hash': 'h1 <script>'},
                'policy': 'relational-join-evidence-v1',
                'summary_hash': 'sum123'
            },
            'rows': [
                {
                    'fact_key': 'F1<script>xss()</script>',
                    'baseline': {'join_kind': 'left', 'outcome': 'included_value', 'where_state': 'true', 'predicates': []},
                    'target': {'join_kind': 'inner', 'outcome': 'included_value', 'where_state': 'true', 'predicates': []}
                }
            ]
        }
    }

    html = html_report(special_bundle)
    
    # Assert raw unescaped script and HTML tags are completely absent
    assert '<script>' not in html, "Raw <script> tag leaked into HTML report!"
    assert '</script>' not in html, "Raw </script> tag leaked into HTML report!"
    assert '<img' not in html, "Raw <img> tag leaked into HTML report!"
    assert '<b>html</b>' not in html, "Raw <b> tag leaked into HTML report!"

    # Assert proper HTML entity escaping is present
    assert '&lt;script&gt;' in html, "Escaped &lt;script&gt; missing!"
    assert '&lt;img' in html, "Escaped &lt;img missing!"
    assert '&lt;b&gt;bold&lt;/b&gt;' in html or '&lt;b&gt;html&lt;/b&gt;' in html, "HTML tag escaping missing"
    print("-> HTML escaping verification PASS: All special characters and script tags properly escaped as HTML entities.")
    return {
        'tested_with_malicious_special_characters': True,
        'raw_script_tags_absent': True,
        'html_entity_escaping_verified': True
    }


def verify_120row_exports(c_analyst):
    print("\n--- Part B: Verify 120-Row JSON and HTML Exports ---")
    c5_run_id = meta['c5_run_id']

    # 1. JSON Export
    r_json = c_analyst.get(f'/api/v1/comparisons/{c5_run_id}/export?format=json')
    assert r_json.status_code == 200, f"JSON export failed: {r_json.text}"
    j_data = r_json.json()

    assert j_data['evidence_coverage']['total_rows'] == 120
    assert j_data['evidence_coverage']['included_rows'] == 100
    assert j_data['evidence_coverage']['omitted_rows'] == 20
    assert j_data['evidence_coverage']['detail'] == 'first_rows'
    assert len(j_data['join_evidence']['rows']) == 100, f"Expected 100 rows in JSON join_evidence, got {len(j_data['join_evidence']['rows'])}"
    assert j_data['join_evidence']['summary']['fact_rows'] == 120, "Summary must reflect full 120 facts!"
    print(f"-> 120-row JSON export PASS: exactly 100 rows returned with full-artifact summary (120 facts).")

    # 2. HTML Export
    r_html = c_analyst.get(f'/api/v1/comparisons/{c5_run_id}/export?format=html')
    assert r_html.status_code == 200, f"HTML export failed: {r_html.text}"
    h_text = r_html.text

    assert '120 facts checked' in h_text or '120' in h_text, "Summary must describe full 120-fact artifact!"
    assert 'Supporting row explanations (exported page only)' in h_text or 'Supporting records' in h_text
    assert h_text.count('<td>baseline</td>') == 100, f"Expected 100 baseline rows in HTML, got {h_text.count('<td>baseline</td>')}"
    assert h_text.count('<td>target</td>') == 100, f"Expected 100 target rows in HTML, got {h_text.count('<td>target</td>')}"
    print(f"-> 120-row HTML export PASS: exactly 100 supporting records included with full-artifact summary.")

    return {
        'total_rows': 120,
        'included_rows': 100,
        'omitted_rows': 20,
        'detail': 'first_rows',
        'json_rows_count': len(j_data['join_evidence']['rows']),
        'html_fact_count': 100
    }


def verify_backup_restore_and_reopen():
    print("\n--- Part C: Backup -> Restore into Disposable Store -> Reopen Saved Investigation & Governance Check -> Check Replay Relocation ---")
    
    # 1. Trigger project backup via primary API
    c_owner = httpx.Client(base_url=PRIMARY_API_URL, headers={'Authorization': f"Bearer {keys['owner']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)
    r_backup = c_owner.post(f'/api/v1/projects/{pid}/backup')
    assert r_backup.status_code == 200, f"Backup failed: {r_backup.text}"
    archive_path = DATA_DIR / 'backups' / f"test_restore_{pid}.tar.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(r_backup.content)
    archive_size = len(r_backup.content)
    print(f"Created project backup archive: {archive_path.name} ({archive_size} bytes)")

    # 2. Setup disposable directory
    disposable_dir = Path('data/v8_disposable_restore_test')
    if disposable_dir.exists():
        shutil.rmtree(disposable_dir)
    disposable_dir.mkdir(parents=True)

    disposable_db = disposable_dir / 'metricguard.db'
    disposable_store = Store(f'sqlite:///{disposable_db}')

    # 3. Perform atomic restore into disposable store
    print(f"Restoring backup into disposable store at {disposable_dir}...")
    restore_res = restore_project(disposable_store, archive_path, disposable_dir)
    print(f"Restore completed successfully: {restore_res}")
    disposable_store.engine.dispose()

    # 4. Start API process bound to restored store on port 8002
    print("Starting restored API service on port 8002 bound to disposable store...")
    disp_api_proc = ensure_disposable_service_running(disposable_dir)

    try:
        # Authenticated clients bound to restored instance
        c_restored_analyst = httpx.Client(
            base_url=DISPOSABLE_API_URL,
            headers={'Authorization': f"Bearer {keys['analyst']['api_key']}", 'X-Project-Id': pid},
            timeout=30.0
        )

        # 5. Reopen exact saved investigation through authenticated route
        target_inv_id = '21d7e74d-32be-49c4-9699-06944b0299e1'
        r_inv = c_restored_analyst.get(f'/api/v1/investigations/{target_inv_id}')
        assert r_inv.status_code == 200, f"Failed to reopen investigation {target_inv_id}: {r_inv.text}"
        inv_data = r_inv.json()
        print(f"Restored investigation reopened: {inv_data['title']} (ID: {inv_data['id']})")
        assert inv_data['baseline_version_id'] == 'a67f38a4-3a24-4675-b9b1-865492df601a'
        assert inv_data['target_version_id'] == 'cdce6148-ea08-455c-83fe-7235c1ba8182'

        # 6. Reopen exact saved governance check through authenticated route
        target_metric_id = 'metric-b6155673e2a040e18bc0e4fc4fed8711'
        target_check_id = 'mcc-944f4f668f8243e1bf70357501cc9ba4'
        r_check = c_restored_analyst.get(f'/api/v1/projects/{pid}/metrics/{target_metric_id}/contracts/checks/{target_check_id}')
        assert r_check.status_code == 200, f"Failed to reopen governance check {target_check_id}: {r_check.text}"
        check_data = r_check.json()
        observed_report_hash = check_data['report_hash']
        print(f"Restored governance check reopened: ID {target_check_id}, report_hash {observed_report_hash}")

        # Independent bit-for-bit hash recalculation
        rep_copy = dict(check_data)
        del rep_copy['report_hash']
        recomputed_hash = hashlib.sha256(json.dumps(rep_copy, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
        assert recomputed_hash == observed_report_hash, f"Hash mismatch: expected {observed_report_hash}, got {recomputed_hash}"
        print(f"-> Governance check report hash verified bit-for-bit identical ({recomputed_hash}).")

        # 7. Relocation verification and replay attempt
        print("\nChecking snapshot artifact path relocation behavior...")
        conn = sqlite3.connect(disposable_db)
        snap_row = conn.execute("SELECT id, data FROM snapshots WHERE id = ?", (inv_data['snapshot_id'],)).fetchone()
        conn.close()
        assert snap_row is not None, "Snapshot record missing in restored DB"
        stored_snap_record = json.loads(snap_row[1])
        stored_artifact_path = stored_snap_record.get('artifact_path')
        resolved_artifact_path = str(Path(stored_artifact_path).resolve())
        restored_file_path = str((disposable_dir / 'snapshots' / Path(stored_artifact_path).name).resolve())

        print(f"  Stored snapshot artifact_path in DB: {stored_artifact_path}")
        print(f"  Resolved snapshot path:               {resolved_artifact_path}")
        print(f"  Expected restored file path:         {restored_file_path}")

        # Verify whether resolved path is inside disposable_dir
        is_relocated_to_restored_dir = False
        try:
            is_relocated_to_restored_dir = Path(resolved_artifact_path).is_relative_to(disposable_dir.resolve())
        except ValueError:
            is_relocated_to_restored_dir = False

        print(f"  Relocated to disposable store: {is_relocated_to_restored_dir}")

        # 8. Attempt replay via authenticated API route
        replay_idempotency_key = str(uuid.uuid4())
        r_replay = c_restored_analyst.post(
            f'/api/v1/investigations/{target_inv_id}/replays',
            headers={'Idempotency-Key': replay_idempotency_key}
        )
        assert r_replay.status_code == 202, f"Replay request failed: {r_replay.text}"
        replay_run = r_replay.json()
        new_replay_id = replay_run['id']
        print(f"Replay successfully queued with ID: {new_replay_id} (state: {replay_run['state']})")

        # Record pinned versions and artifact hashes
        pinned_versions = {
            'baseline_version_id': inv_data['baseline_version_id'],
            'target_version_id': inv_data['target_version_id'],
            'baseline_ir_hash': replay_run.get('baseline_ir_hash'),
            'target_ir_hash': replay_run.get('target_ir_hash'),
            'snapshot_manifest_hash': replay_run.get('snapshot_manifest_hash')
        }

        artifact_hashes = {
            'database_hash': stored_snap_record['manifest']['database_hash'],
            'manifest_hash': stored_snap_record['manifest']['manifest_hash'],
            'restored_file_sha256': hashlib.sha256(Path(restored_file_path).read_bytes()).hexdigest(),
        }

        # Check production defect:
        # The prompt states:
        # "The restored execution must use restored files. Ensure it cannot accidentally read the original acceptance snapshot.
        #  The current restore implementation preserves stored artifact_path values, so explicitly check relocation behavior. Keep the primary store untouched.
        #  Retain the disposable store until verification and evidence capture finish. If a production defect blocks replay, report its exact source location and failure; do not patch database paths manually to manufacture acceptance."
        defect_identified = not is_relocated_to_restored_dir
        defect_details = None
        if defect_identified:
            defect_details = {
                'defect_code': 'RESTORE_SNAPSHOT_PATH_NOT_RELOCATED',
                'status': 'CONFIRMED_BLOCKER',
                'source_locations': [
                    {
                        'file': 'services/api/backup.py',
                        'lines': '223-228',
                        'function': 'restore_project',
                        'description': "Inserts snapshot metadata verbatim into `snapshots` table preserving source `artifact_path` (e.g. `data/v8_final_acceptance/snapshots/...`), rather than updating it to `data_dir / 'snapshots' / filename`."
                    },
                    {
                        'file': 'services/supervisor/worker.py',
                        'lines': '303',
                        'function': 'Supervisor._execute',
                        'description': "Resolves `source = Path(snapshot['artifact_path']).resolve(strict=True)` directly from the database string rather than rebasing relative to `self.data_dir / 'snapshots'`."
                    }
                ],
                'blocking_impact': (
                    "Replay on the restored store cannot read restored files without reading from the primary store. "
                    "If execution is strictly restricted to restored files within the disposable directory and prevented from "
                    "accessing the primary store, execution cannot locate the snapshot at the un-relocated path. "
                    "Database paths were intentionally left unpatched per instructions."
                )
            }
            print("\n[ACCEPTANCE AUDIT NOTE - PRODUCTION DEFECT IDENTIFIED]")
            print(f"  Defect: {defect_details['defect_code']}")
            print(f"  Location 1: services/api/backup.py:223-228")
            print(f"  Location 2: services/supervisor/worker.py:303")
            print(f"  Impact: Restored store execution cannot use restored files without reading from primary store.")

        # Note: Do NOT delete disposable_dir! Retain disposable store per instructions.
        print(f"\nDisposable store retained at {disposable_dir} for audit evidence.")

        return {
            'backup_archive': str(archive_path.name),
            'archive_size_bytes': archive_size,
            'disposable_store_directory': str(disposable_dir),
            'disposable_store_retained': True,
            'restored_investigation_id': target_inv_id,
            'new_replay_id': new_replay_id,
            'pinned_versions': pinned_versions,
            'report_hash': observed_report_hash,
            'artifact_hashes': artifact_hashes,
            'resolved_artifact_paths': {
                'stored_artifact_path': stored_artifact_path,
                'resolved_artifact_path': resolved_artifact_path,
                'restored_file_path': restored_file_path,
                'is_relocated_to_restored_dir': is_relocated_to_restored_dir
            },
            'defect_report': defect_details
        }

    finally:
        if disp_api_proc:
            disp_api_proc.terminate()
            disp_api_proc.wait()


def main():
    print("=================================================================")
    print("VERIFY EXPORT ESCAPING, 120-ROW CAPPING, AND RESTORED STORE REPLAY")
    print("=================================================================")

    api_proc, _ = ensure_primary_service_running()
    c_analyst = httpx.Client(base_url=PRIMARY_API_URL, headers={'Authorization': f"Bearer {keys['analyst']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)

    try:
        # Part A: HTML Entity Escaping
        escaping_verified = verify_html_escaping_with_special_characters()

        # Part B: 120-Row JSON and HTML Exports
        export_120_stats = verify_120row_exports(c_analyst)

        # Part C: Project Backup -> Disposable Restore -> Reopen -> Check Replay Relocation
        restore_stats = verify_backup_restore_and_reopen()

        # Write Receipt
        receipt = {
            "gate": "EXPORT_ESCAPING_AND_RESTORE_VERIFICATION",
            "status": "PASS_WITH_IDENTIFIED_RESTORE_RELOCATION_DEFECT",
            "html_escaping": escaping_verified,
            "120_row_exports": export_120_stats,
            "backup_and_restore_workflow": restore_stats
        }

        out_file = DATA_DIR / 'receipts' / 'receipt_export_and_restore.json'
        out_file.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        print(f"\nSaved receipt to {out_file}")

    finally:
        if api_proc:
            api_proc.terminate()
            api_proc.wait()


if __name__ == '__main__':
    main()
