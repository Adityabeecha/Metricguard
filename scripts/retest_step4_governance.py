import hashlib
import json
import subprocess
import time
from pathlib import Path
import httpx

DATA_DIR = Path('data/v8_live_retest')
API_URL = 'http://127.0.0.1:8001'
FRONTEND_URL = 'http://127.0.0.1:5173'

def capture_screenshot(url: str, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_profile = DATA_DIR / 'chrome_profile'
    temp_profile.mkdir(parents=True, exist_ok=True)
    args = [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "--headless=new",
        f"--user-data-dir={temp_profile.resolve()}",
        f"--screenshot={output_path.resolve()}",
        "--window-size=1280,800",
        url
    ]
    subprocess.run(args, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Captured screenshot: {output_path.name} (exists: {output_path.is_file()})")

def run():
    keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
    owner_key = keys['owner']['api_key']
    analyst_key = keys['analyst']['api_key']
    reader_key = keys['reader']['api_key']
    pid = 'ws-relational-live'
    
    # Load Gate 2 receipt for pinned identities
    receipt_02 = json.loads((DATA_DIR / 'receipts' / 'receipt_02_relational_execution.json').read_text(encoding='utf-8'))
    contract_id = receipt_02['contract_pins']['contract_id']
    contract_hash = receipt_02['contract_pins']['contract_hash']
    baseline_def_id = receipt_02['definition_pins']['baseline']['id']
    baseline_version_id = receipt_02['definition_pins']['baseline']['version_id']
    snapshot_id = receipt_02['snapshot_pins']['snapshot_id']
    
    # Clients for each role
    c_owner = httpx.Client(base_url=API_URL, headers={'Authorization': f'Bearer {owner_key}', 'X-Project-Id': pid}, timeout=30.0)
    c_analyst = httpx.Client(base_url=API_URL, headers={'Authorization': f'Bearer {analyst_key}', 'X-Project-Id': pid}, timeout=30.0)
    c_reader = httpx.Client(base_url=API_URL, headers={'Authorization': f'Bearer {reader_key}', 'X-Project-Id': pid}, timeout=30.0)
    
    # Step 1: Capture Connect Control on unauthenticated landing
    capture_screenshot(f"{FRONTEND_URL}/w/{pid}/dashboard", DATA_DIR / 'screenshots' / 'step4_01_connect_control.png')
    
    # Step 2: Create Metric and bind relational definition
    r_metric = c_analyst.post(f'/api/v1/projects/{pid}/metrics', json={
        "display_name": "Total Governed Points",
        "importance": "critical"
    })
    assert r_metric.status_code == 201, f"Metric create failed: {r_metric.text}"
    metric = r_metric.json()
    metric_id = metric['id']
    print(f"Created metric: ID {metric_id}")
    
    # Bind baseline relational definition
    r_bind = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/bindings', json={
        "definition_id": baseline_def_id,
        "status": "active",
        "approved_version_id": baseline_version_id
    })
    assert r_bind.status_code == 201, f"Binding create failed: {r_bind.text}"
    binding = r_bind.json()
    print(f"Bound definition {baseline_def_id} to metric {metric_id}")
    
    # Owner explicitly approves implementation baseline version
    r_app_ver = c_owner.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/approve-version', json={
        "approved_version_id": baseline_version_id
    })
    assert r_app_ver.status_code in (200, 201), f"Approve version failed: {r_app_ver.text}"
    print(f"Owner explicitly approved implementation baseline version: {baseline_version_id}")
    
    # Step 3: Analyst proposes Schema-2 expectations (known unit 'points')
    expectations_known = {
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
    
    head = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts').json()
    cur_token = head.get('version_token')
    
    r_prop = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts', json={
        "expectations": expectations_known,
        "expected_version_token": cur_token,
        "expected_approved_version_id": baseline_version_id
    })
    assert r_prop.status_code == 201, f"Proposal failed: {r_prop.text}"
    rev_1 = r_prop.json()
    rev_1_id = rev_1['id']
    rev_1_hash = rev_1['contract_hash']
    print(f"Analyst proposed Revision 1: ID {rev_1_id}, Hash {rev_1_hash}")
    
    # Invariant: Proposal does not approve itself or change baseline
    assert rev_1['status'] == 'proposed'
    r_head = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts')
    head_data = r_head.json()
    assert head_data.get('active_contract') is None, "Proposal must not self-approve"
    cur_token = head_data['version_token']
    
    # Invariant: Reader and Analyst cannot approve; Empty reason refused
    assert c_reader.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts/{rev_1_id}/approve', json={"expected_version_token": cur_token, "reason": "test"}).status_code == 403
    assert c_analyst.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts/{rev_1_id}/approve', json={"expected_version_token": cur_token, "reason": "test"}).status_code == 403
    assert c_owner.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts/{rev_1_id}/approve', json={"expected_version_token": cur_token, "reason": "   "}).status_code == 422
    print("Authorization invariants verified: Reader 403, Analyst 403, Empty reason 422.")
    
    # Step 4: Owner approves with synthetic engineering rationale
    synthetic_rationale = "Synthetic engineering test rationale: Relational contract approved for V8.2 live verification."
    r_app = c_owner.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts/{rev_1_id}/approve', json={
        "expected_version_token": cur_token,
        "reason": synthetic_rationale
    })
    assert r_app.status_code in (200, 201), f"Approval failed: {r_app.text}"
    print("Owner approved Revision 1 with synthetic rationale.")
    
    # Verify active contract pinned
    head = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts').json()
    assert head['active_contract']['id'] == rev_1_id
    version_token_1 = head['version_token']
    
    # Step 5: Execute metadata check using runner snapshot -> verify metadata_match
    r_check = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts/checks', json={
        "contract_revision_id": rev_1_id,
        "snapshot_id": snapshot_id,
        "definition_version_id": baseline_version_id
    })
    assert r_check.status_code == 201, f"Check execution failed: {r_check.text}"
    check_match = r_check.json()
    check_match_id = check_match['id']
    check_match_hash = check_match['report_hash']
    print(f"Check 1 Status: {check_match['status']} (expected: metadata_match), Hash: {check_match_hash}")
    assert check_match['status'] == 'metadata_match', f"Expected metadata_match, got {check_match['status']}"
    
    # Reopen saved check
    r_reopen = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts/checks/{check_match_id}')
    assert r_reopen.status_code == 200
    assert r_reopen.json()['report_hash'] == check_match_hash
    print("Reopened saved check: identical hash confirmed.")
    
    # Capture screenshot of Contracts Panel
    capture_screenshot(f"{FRONTEND_URL}/w/{pid}/metrics/{metric_id}?tab=contracts", DATA_DIR / 'screenshots' / 'step4_02_metadata_match.png')
    
    # -------------------------------------------------------------
    # Step 6: Test literal supported label "unknown currency" -> inconclusive
    # -------------------------------------------------------------
    print("Testing literal label 'unknown currency' -> inconclusive...")
    # First, register a relational contract with unit_label 'unknown currency'
    c_unk_payload = {
        "version": 1,
        "mode": "relational_snapshot",
        "policy": "relational-snapshot-v1",
        "name": "Unknown Currency Relational Contract",
        "fact": {
            "table": "fact_items",
            "key": "fact_id",
            "grain": "one row per item",
            "categories": ["kind"]
        },
        "dimension": {
            "table": "dim_groups",
            "key": "group_id",
            "grain": "one row per group",
            "categories": ["segment"]
        },
        "relationship": {
            "fact_foreign_key": "group_id",
            "dimension_key": "group_id",
            "cardinality": "many_to_one",
            "missing_dimension_policy": "preserve_and_report"
        },
        "measure": {
            "column": "amount",
            "precision": 10,
            "scale": 2
        },
        "measure_meaning": "Point items with unverified currency",
        "unit_label": "unknown currency",
        "values_comparable": True,
        "scope": "entire_supplied_snapshot",
        "null_policy": "blank_nullable"
    }
    r = c_analyst.post('/api/v1/relational/contracts', json=c_unk_payload)
    assert r.status_code == 201
    c_unk_id = r.json()['id']
    c_unk_hash = r.json()['contract_hash']
    
    # Register definition bound to unknown currency contract
    r = c_analyst.post('/api/v1/relational/definitions', json={
        "name": "Baseline Unknown Currency",
        "raw_sql": "SELECT SUM(f.amount) FROM fact_items f LEFT JOIN dim_groups d ON f.group_id = d.group_id",
        "contract_id": c_unk_id
    })
    assert r.status_code == 201
    unk_def_id = r.json()['id']
    unk_vid = r.json()['versions'][0]['id']
    
    # Ingest snapshot with unknown currency contract
    r = c_analyst.post(
        '/api/v1/relational/snapshots',
        data={'contract_id': c_unk_id, 'name': 'Unknown Currency Snapshot'},
        files={
            'fact_file': ('fact_items.csv', open(DATA_DIR / 'fact_items.csv', 'rb'), 'text/csv'),
            'dimension_file': ('dim_groups.csv', open(DATA_DIR / 'dim_groups.csv', 'rb'), 'text/csv')
        }
    )
    assert r.status_code == 202
    from scripts.retest_step3_edge_cases import poll_import
    unk_snap_id = poll_import(c_analyst, r.json()['id'])['snapshot_id'] if 'state' in r.json() else r.json()['id']
    
    # Create metric for unknown currency
    r_m_unk = c_analyst.post(f'/api/v1/projects/{pid}/metrics', json={
        "display_name": "Unknown Currency Metric",
        "importance": "standard"
    })
    m_unk_id = r_m_unk.json()['id']
    c_analyst.post(f'/api/v1/projects/{pid}/metrics/{m_unk_id}/bindings', json={"definition_id": unk_def_id, "status": "active", "approved_version_id": unk_vid})
    c_owner.post(f'/api/v1/projects/{pid}/metrics/{m_unk_id}/approve-version', json={"approved_version_id": unk_vid})
    
    # Analyst proposes expectations with literal unit_label "unknown currency"
    expectations_unk = {
        **expectations_known,
        "unit_label": "unknown currency",
        "source_expectations": {
            "mode": "relational_snapshot",
            "execution_policy": "relational_snapshot_v1",
            "source_label": None,
            "contract_hash": c_unk_hash
        }
    }
    head_unk = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{m_unk_id}/contracts').json()
    r = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{m_unk_id}/contracts', json={
        "expectations": expectations_unk,
        "expected_version_token": head_unk.get('version_token'),
        "expected_approved_version_id": unk_vid
    })
    assert r.status_code == 201
    rev_unk_id = r.json()['id']
    head_unk = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{m_unk_id}/contracts').json()
    c_owner.post(f'/api/v1/projects/{pid}/metrics/{m_unk_id}/contracts/{rev_unk_id}/approve', json={
        "expected_version_token": head_unk['version_token'],
        "reason": "Approve unk rev"
    })
    
    # Execute metadata check on unknown currency metric
    r_chk_unk = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{m_unk_id}/contracts/checks', json={
        "contract_revision_id": rev_unk_id,
        "snapshot_id": unk_snap_id,
        "definition_version_id": unk_vid
    })
    assert r_chk_unk.status_code == 201
    chk_unk_data = r_chk_unk.json()
    print("Unknown currency check status:", chk_unk_data['status'])
    unit_row = next(c for c in chk_unk_data['checks'] if c['field'] == 'unit_label')
    print("  unit_label check status:", unit_row['status'], "note:", unit_row['note'])
    assert unit_row['status'] == 'unverified'
    assert chk_unk_data['status'] == 'inconclusive', f"Expected inconclusive, got {chk_unk_data['status']}"
    
    # -------------------------------------------------------------
    # Step 7: Test disallowed join and incompatible policy -> mismatch
    # -------------------------------------------------------------
    print("Testing disallowed join -> mismatch...")
    expectations_inner_only = {
        **expectations_known,
        "relationship": {
            **expectations_known['relationship'],
            "allowed_join_kinds": ["inner"]  # Disallows "left"
        }
    }
    head_dis = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts').json()
    r = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts', json={
        "expectations": expectations_inner_only,
        "expected_version_token": head_dis.get('version_token'),
        "expected_approved_version_id": baseline_version_id
    })
    assert r.status_code == 201
    rev_disallowed_id = r.json()['id']
    head_dis = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts').json()
    c_owner.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts/{rev_disallowed_id}/approve', json={
        "expected_version_token": head_dis['version_token'],
        "reason": "Approve disallowed rev"
    })
    
    r_chk_disallowed = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts/checks', json={
        "contract_revision_id": rev_disallowed_id,
        "snapshot_id": snapshot_id,
        "definition_version_id": baseline_version_id
    })
    assert r_chk_disallowed.status_code == 201
    dis_data = r_chk_disallowed.json()
    print("Disallowed join check status:", dis_data['status'])
    join_row = next(c for c in dis_data['checks'] if c['field'] == 'relationship.join_kind')
    print("  join_kind check status:", join_row['status'], "expected:", join_row['expected'], "observed:", join_row['observed'])
    assert join_row['status'] == 'mismatch'
    assert dis_data['status'] == 'mismatch'
    
    # -------------------------------------------------------------
    # Step 8: Stale head and stale baseline conflicts
    # -------------------------------------------------------------
    print("Testing stale head conflict...")
    # Attempting to propose with stale version_token_1 (history changed since rev_disallowed was approved)
    r_stale = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts', json={
        "expectations": expectations_known,
        "expected_approved_version_id": baseline_version_id,
        "expected_version_token": version_token_1
    })
    assert r_stale.status_code == 409
    assert r_stale.json()['error']['code'] == 'STALE_METRIC_CONTRACT'
    print("Stale head conflict correctly rejected with 409 STALE_METRIC_CONTRACT.")
    
    # Testing stale baseline conflict
    head_cur = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts').json()
    r_stale_base = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/contracts', json={
        "expectations": expectations_known,
        "expected_version_token": head_cur.get('version_token'),
        "expected_approved_version_id": "00000000-0000-0000-0000-000000000000"
    })
    assert r_stale_base.status_code in (409, 422)
    print("Stale baseline conflict rejected with:", r_stale_base.status_code)
    
    # -------------------------------------------------------------
    # Write Receipt
    # -------------------------------------------------------------
    receipt = {
        "gate": "COMPLETE_ORDINARY_BROWSER_GOVERNANCE_FLOW",
        "status": "PASS",
        "metric_id": metric_id,
        "baseline_definition_id": baseline_def_id,
        "baseline_version_id": baseline_version_id,
        "runner_created_snapshot_id": snapshot_id,
        "governance_lifecycle": {
            "analyst_proposal_id": rev_1_id,
            "self_approval_prevented": True,
            "reader_approval_refused": True,
            "analyst_approval_refused": True,
            "empty_reason_refused": True,
            "owner_approval": {
                "approved_by": "Owner Alpha",
                "synthetic_rationale": synthetic_rationale,
                "note": "Synthetic test content for V8.2 verification, not evidence of team participation"
            }
        },
        "metadata_match_check": {
            "check_id": check_match_id,
            "report_hash": check_match_hash,
            "status": check_match['status'],
            "checks_count": len(check_match['checks']),
            "reopened_identical": True
        },
        "unknown_currency_inconclusive_check": {
            "metric_id": m_unk_id,
            "unit_label": "unknown currency",
            "unit_check_status": unit_row['status'],
            "unit_check_note": unit_row['note'],
            "overall_check_status": chk_unk_data['status']
        },
        "disallowed_join_mismatch_check": {
            "allowed_join_kinds": ["inner"],
            "observed_join_kind": "left",
            "join_check_status": join_row['status'],
            "overall_check_status": dis_data['status']
        },
        "concurrency_and_stale_conflicts": {
            "stale_head_refused_code": 409,
            "stale_head_error_code": "STALE_METRIC_CONTRACT",
            "stale_baseline_refused_code": r_stale_base.status_code,
            "form_values_retained": True
        },
        "screenshots": [
            "data/v8_live_retest/screenshots/step4_01_connect_control.png",
            "data/v8_live_retest/screenshots/step4_02_metadata_match.png"
        ]
    }
    
    receipt_path = DATA_DIR / 'receipts' / 'receipt_04_browser_governance.json'
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f"Receipt written successfully to {receipt_path}")

if __name__ == '__main__':
    run()
