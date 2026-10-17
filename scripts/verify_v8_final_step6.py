import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import json
import tarfile
import io
import httpx

DATA_DIR = Path('data/v8_final_acceptance')
keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
meta = json.loads((DATA_DIR / 'test_metadata.json').read_text(encoding='utf-8'))

pid = 'ws-relational-final'
iso_pid = 'ws-isolation-final'
API_URL = 'http://127.0.0.1:8001'

c_owner = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['owner']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)
c_analyst = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['analyst']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)
c_reader = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['reader']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)
c_outsider = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['outsider']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)

contract_id = meta['contract_id']
base_def_id = meta['base_def_id']
base_ver_id = meta['base_ver_id']
target_def_id = meta['target_def_id']
target_ver_id = meta['target_ver_id']

def main():
    print("=================================================================")
    print("STEP 6: Boundaries & Governance Regressions")
    print("=================================================================")

    # 1. Negative & Boundary Checks
    print("\n--- 1. Boundary & Refusal Checks ---")
    
    # 1a. Refuse excess scale (> precision)
    r_bad_scale = c_analyst.post('/api/v1/relational/contracts', json={
        "version": 1,
        "mode": "relational_snapshot",
        "policy": "relational-snapshot-v1",
        "name": "Excess Scale Contract",
        "fact": {"table": "fact_items", "key": "fact_id", "grain": "fact_id", "categories": []},
        "dimension": {"table": "dim_groups", "key": "group_id", "grain": "group_id", "categories": []},
        "relationship": {"fact_foreign_key": "group_id", "dimension_key": "group_id", "cardinality": "many_to_one", "missing_dimension_policy": "preserve_and_report"},
        "measure": {"column": "amount", "precision": 5, "scale": 10},  # scale > precision!
        "measure_meaning": "Excess scale test",
        "unit_label": "points",
        "values_comparable": True,
        "scope": "entire_supplied_snapshot",
        "null_policy": "blank_nullable"
    })
    assert r_bad_scale.status_code in (400, 422), f"Expected 400/422 for excess scale, got {r_bad_scale.status_code}"
    print("-> 1a PASS: Refused excess scale (scale > precision).")

    # 1b. Refuse unsupported SQL: arbitrary join (e.g. RIGHT JOIN)
    r_bad_join = c_analyst.post('/api/v1/relational/definitions', json={
        "contract_id": contract_id,
        "name": "Unsupported RIGHT JOIN",
        "raw_sql": "SELECT SUM(f.amount) FROM fact_items f RIGHT JOIN dim_groups d ON f.group_id = d.group_id"
    })
    assert r_bad_join.status_code in (400, 422), f"Expected 400/422 for RIGHT JOIN, got {r_bad_join.status_code}"
    print("-> 1b PASS: Refused unsupported RIGHT JOIN.")

    # 1c. Refuse unsupported SQL: arbitrary aggregate (e.g. AVG or COUNT)
    r_bad_agg = c_analyst.post('/api/v1/relational/definitions', json={
        "contract_id": contract_id,
        "name": "Unsupported AVG",
        "raw_sql": "SELECT AVG(f.amount) FROM fact_items f LEFT JOIN dim_groups d ON f.group_id = d.group_id"
    })
    assert r_bad_agg.status_code in (400, 422), f"Expected 400/422 for AVG aggregate, got {r_bad_agg.status_code}"
    print("-> 1c PASS: Refused unsupported AVG aggregate.")

    # 1d. Refuse cross-workspace access
    r_cross = c_outsider.get(f'/api/v1/definitions/{base_def_id}')
    assert r_cross.status_code == 403, f"Expected 403 for cross-workspace outsider, got {r_cross.status_code}"
    print("-> 1d PASS: Refused cross-workspace access for outsider (HTTP 403).")

    # 1e. Refuse unauthorized role mutation (reader attempting definition creation)
    r_reader_mut = c_reader.post('/api/v1/relational/definitions', json={
        "contract_id": contract_id,
        "name": "Reader Definition",
        "raw_sql": "SELECT SUM(f.amount) FROM fact_items f LEFT JOIN dim_groups d ON f.group_id = d.group_id"
    })
    assert r_reader_mut.status_code == 403, f"Expected 403 for reader mutation, got {r_reader_mut.status_code}"
    print("-> 1e PASS: Refused reader mutation (HTTP 403).")

    # 2. Governed Relational Contract Lifecycle & Invariance
    print("\n--- 2. Governed Relational Contract Lifecycle & Invariance ---")
    
    # 2a. Register a new Metric in ws-relational-final
    r_metric = c_owner.post(f'/api/v1/projects/{pid}/metrics', json={
        "display_name": "Final Governed Metric",
        "importance": "important"
    })
    assert r_metric.status_code == 201, f"Failed to create metric: {r_metric.text}"
    metric_data = r_metric.json()
    mid = metric_data['id']
    print(f"Created metric: {mid}")

    # 2b. Bind definition to metric & approve baseline version
    r_bind = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{mid}/bindings', json={
        "definition_id": base_def_id,
        "status": "active",
        "approved_version_id": base_ver_id
    })
    assert r_bind.status_code == 201, f"Binding create failed: {r_bind.text}"
    
    r_app_ver = c_owner.post(f'/api/v1/projects/{pid}/metrics/{mid}/approve-version', json={
        "approved_version_id": base_ver_id
    })
    assert r_app_ver.status_code in (200, 201), f"Approve version failed: {r_app_ver.text}"
    print(f"Bound and approved baseline definition version {base_ver_id}")

    # 2c. Get current contract head token
    r_head = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/contracts')
    assert r_head.status_code == 200
    head_data = r_head.json()
    token_v1 = head_data.get('version_token')

    # 2d. Analyst proposes Schema-2 relational expectations
    contract_hash = meta['contract_hash']
    exp_rev = {
        "schema_version": 2,
        "owner_label": "Billing Operations Team",
        "meaning": "Standard point items billed per group segment",
        "unit_label": "points",
        "fact": {
            "table": "fact_items",
            "key": "fact_id",
            "grain": "one row per item"
        },
        "dimension": {
            "table": "dim_groups",
            "key": "group_id",
            "grain": "one row per group"
        },
        "relationship": {
            "fact_foreign_key": "group_id",
            "dimension_key": "group_id",
            "cardinality": "many_to_one",
            "allowed_join_kinds": ["left"],
            "missing_dimension_policy": "preserve_and_report"
        },
        "expected_fields": [
            {"table": "fact_items", "column": "kind", "type": "string"},
            {"table": "dim_groups", "column": "segment", "type": "string"}
        ],
        "source_expectations": {
            "mode": "relational_snapshot",
            "execution_policy": "relational_snapshot_v1",
            "source_label": None,
            "contract_hash": contract_hash
        },
        "materiality_policy": {
            "absolute_threshold": "0.01",
            "relative_threshold": "0.001"
        }
    }
    r_prop = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{mid}/contracts', json={
        "expectations": exp_rev,
        "expected_version_token": token_v1,
        "expected_approved_version_id": base_ver_id
    })
    assert r_prop.status_code in (200, 201), f"Failed to propose contract: {r_prop.text}"
    prop_data = r_prop.json()
    rev_id = prop_data['id']
    print(f"Contract revision proposed: {rev_id}")

    # 2e. Owner approves contract revision
    head_after_prop = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/contracts').json()
    r_app = c_owner.post(f'/api/v1/projects/{pid}/metrics/{mid}/contracts/{rev_id}/approve', json={
        "expected_version_token": head_after_prop['version_token'],
        "reason": "Approved final v8 acceptance governed contract"
    })
    assert r_app.status_code in (200, 201), f"Failed to approve contract: {r_app.text}"
    print("Contract revision approved by Owner.")

    # 2f. Run metadata check and save report
    snapshot_id = meta['snapshot_id']
    r_check = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{mid}/contracts/checks', json={
        "snapshot_id": snapshot_id,
        "definition_version_id": base_ver_id
    })
    assert r_check.status_code in (200, 201), f"Failed to run contract check: {r_check.text}"
    check_report = r_check.json()
    orig_check_id = check_report['id']
    orig_report_hash = check_report['report_hash']
    print(f"Saved metadata check {orig_check_id} with report hash: {orig_report_hash}")

    # 2g. Promote target definition version as NEW baseline
    r_promote_new = c_owner.post(f'/api/v1/projects/{pid}/metrics/{mid}/promote', json={
        "definition_id": target_def_id,
        "approved_version_id": target_ver_id
    })
    assert r_promote_new.status_code == 200, f"Failed to promote new baseline: {r_promote_new.text}"
    print(f"Promoted NEW baseline definition {target_def_id} version {target_ver_id}")

    # Verify state transition
    r_trans = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/transitions')
    assert r_trans.status_code == 200
    transitions = r_trans.json().get('items', [])
    latest_trans = transitions[0] if transitions else {}
    assert latest_trans.get('previous_approved_version_id') == base_ver_id
    assert latest_trans.get('approved_version_id') == target_ver_id
    print("-> State transition verified: baseline successfully updated.")

    # 2h. Bit-for-bit check reopen across baseline change
    r_reopen_check = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/contracts/checks/{orig_check_id}')
    assert r_reopen_check.status_code == 200, f"Failed to reopen check: {r_reopen_check.text}"
    reopened_check = r_reopen_check.json()
    assert reopened_check['id'] == orig_check_id
    assert reopened_check['report_hash'] == orig_report_hash, f"Report hash mismatch! {reopened_check['report_hash']} != {orig_report_hash}"
    assert reopened_check['definition_version_id'] == base_ver_id
    # Assert bit-for-bit identical content
    assert json.dumps(reopened_check, sort_keys=True) == json.dumps(check_report, sort_keys=True)
    print(f"-> 2h PASS: Bit-for-bit check reopen confirmed! Hash: {orig_report_hash}")

    # 2i. Verify STALE_METRIC_BASELINE (409)
    r_head_v2 = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/contracts').json()
    token_v2 = r_head_v2.get('version_token')
    r_stale_base = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{mid}/contracts', json={
        "expectations": exp_rev,
        "expected_version_token": token_v2,
        "expected_approved_version_id": base_ver_id  # Old baseline!
    })
    assert r_stale_base.status_code == 409
    assert r_stale_base.json()['error']['code'] == 'STALE_METRIC_BASELINE'
    print("-> 2i PASS: STALE_METRIC_BASELINE (HTTP 409) returned on stale baseline expectation.")

    # 2j. Verify STALE_METRIC_CONTRACT (409)
    r_stale_token = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{mid}/contracts', json={
        "expectations": exp_rev,
        "expected_version_token": "stale-token-deadbeef",  # Invalid/old token!
        "expected_approved_version_id": target_ver_id
    })
    assert r_stale_token.status_code == 409
    assert r_stale_token.json()['error']['code'] == 'STALE_METRIC_CONTRACT'
    print("-> 2j PASS: STALE_METRIC_CONTRACT (HTTP 409) returned on stale head token.")

    # 3. Backup and Restore
    print("\n--- 3. Project Backup and Integrity Verification ---")
    r_backup = c_owner.post(f'/api/v1/projects/{pid}/backup')
    assert r_backup.status_code == 200, f"Failed to create backup: {r_backup.text}"
    backup_bytes = r_backup.content
    assert len(backup_bytes) > 0
    print(f"Backup archive created: {len(backup_bytes)} bytes.")

    # Inspect tarball contents
    with tarfile.open(fileobj=io.BytesIO(backup_bytes), mode='r:gz') as tar:
        names = tar.getnames()
        print(f"Archive files count: {len(names)}")
        # Verify manifest.json exists
        assert 'manifest.json' in names or any('manifest' in n for n in names)
        # Verify records / data exists
        assert any('records' in n or 'snapshots' in n or 'evidence' in n for n in names)
    print("-> 3 PASS: Project backup verified with valid tar.gz contents.")

    # Write Step 6 Receipt
    receipt_step6 = {
        "gate": "BOUNDARIES_AND_GOVERNANCE_REGRESSIONS_V8_3",
        "status": "PASS",
        "boundary_checks": {
            "refuse_excess_scale": "PASS",
            "refuse_unsupported_right_join": "PASS",
            "refuse_unsupported_avg_aggregate": "PASS",
            "refuse_cross_workspace_outsider": "PASS",
            "refuse_reader_mutation": "PASS"
        },
        "governed_contract_lifecycle": {
            "metric_id": mid,
            "baseline_definition_version": base_ver_id,
            "target_definition_version": target_ver_id,
            "proposal_approved": True,
            "metadata_check_id": orig_check_id,
            "metadata_report_hash": orig_report_hash,
            "baseline_promoted_to_target": True,
            "bit_for_bit_reopen_identical": True,
            "stale_metric_baseline_409": True,
            "stale_metric_contract_409": True
        },
        "backup_and_restore": {
            "backup_downloaded": True,
            "archive_byte_size": len(backup_bytes),
            "tar_structure_verified": True
        }
    }

    r5_path = DATA_DIR / 'receipts' / 'receipt_05_boundaries_and_governance.json'
    r5_path.write_text(json.dumps(receipt_step6, indent=2), encoding='utf-8')
    print(f"\nWrote Step 6 receipt to {r5_path}")

if __name__ == '__main__':
    main()
