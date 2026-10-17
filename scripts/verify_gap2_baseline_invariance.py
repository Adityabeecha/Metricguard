import json
from pathlib import Path
import httpx

DATA_DIR = Path('data/v8_live_retest')
keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
pid = 'ws-relational-live'
mid = 'metric-e8cb92dbef384193ae56cad1f887fdff'
orig_check_id = 'mcc-71d977f7b857463d8aee0ef551a53fda'
expected_orig_hash = 'dfe6cf15d9afd046b5775106a044215cdfe513429fc73bc8a7c91342139d2484'

API_URL = 'http://127.0.0.1:8001'

c_owner = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['owner']['api_key']}", 'X-Project-Id': pid}, timeout=30.0)
c_analyst = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['analyst']['api_key']}", 'X-Project-Id': pid}, timeout=30.0)

def main():
    print("=== Step 1: Save canonical content of original report ===")
    r_orig = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/contracts/checks/{orig_check_id}')
    assert r_orig.status_code == 200, f"Failed to get original check: {r_orig.text}"
    canonical_orig = r_orig.json()
    assert canonical_orig['report_hash'] == expected_orig_hash, f"Hash mismatch: {canonical_orig['report_hash']} != {expected_orig_hash}"
    print(f"Original check {orig_check_id} retrieved with hash: {expected_orig_hash}")
    
    print("\n=== Step 2: Inspect current baseline and active definition ===")
    m_info = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}').json()
    prev_baseline_id = m_info['active_binding']['approved_version_id']
    active_def_id = m_info['active_binding']['definition_id']
    print(f"Metric active definition: {active_def_id}")
    print(f"Current approved baseline version: {prev_baseline_id}")
    
    def_info = c_analyst.get(f'/api/v1/definitions/{active_def_id}').json()
    contract_id = def_info['versions'][0]['contract_id']
    print(f"Underlying relational contract ID: {contract_id}")
    
    print("\n=== Step 3: Register a genuinely different implementation version ===")
    new_def_payload = {
        "contract_id": contract_id,
        "name": "Baseline Points SUM (V2 Distinct North Implementation)",
        "raw_sql": "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE d.segment = 'north'"
    }
    r_new_def = c_analyst.post('/api/v1/relational/definitions', json=new_def_payload)
    assert r_new_def.status_code == 201, f"Failed to create new definition: {r_new_def.text}"
    new_def_data = r_new_def.json()
    new_def_id = new_def_data['id']
    new_baseline_id = new_def_data['versions'][-1]['id']
    new_ver_number = new_def_data['versions'][-1]['version_number']
    assert new_baseline_id != prev_baseline_id, "New version ID is not genuinely different!"
    print(f"Created New Definition {new_def_id}: Version ID {new_baseline_id} (IR hash: {new_def_data['versions'][-1].get('ir_hash')})")
    
    print("\n=== Step 4: Approve the genuinely different version as new implementation baseline ===")
    r_app_base = c_owner.post(f'/api/v1/projects/{pid}/metrics/{mid}/promote', json={
        "definition_id": new_def_id,
        "approved_version_id": new_baseline_id
    })
    assert r_app_base.status_code == 200, f"Failed to approve/promote new baseline version: {r_app_base.text}"
    app_base_result = r_app_base.json()
    print("Promoted definition and approved new baseline version successfully.")
    
    # Check transitions
    r_trans = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/transitions')
    assert r_trans.status_code == 200
    transitions = r_trans.json().get('items', [])
    latest_trans = transitions[0] if transitions else {}
    print(f"State transition recorded: {latest_trans.get('id')} - {latest_trans.get('transition_kind')}")
    print(f"  previous_definition_id: {latest_trans.get('previous_definition_id')}")
    print(f"  definition_id: {latest_trans.get('definition_id')}")
    print(f"  previous_approved_version_id: {latest_trans.get('previous_approved_version_id')}")
    print(f"  approved_version_id: {latest_trans.get('approved_version_id')}")
    assert latest_trans.get('previous_approved_version_id') == prev_baseline_id
    assert latest_trans.get('approved_version_id') == new_baseline_id
    
    print("\n=== Step 5: Distinguish STALE_METRIC_BASELINE vs STALE_METRIC_CONTRACT ===")
    head_cur = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/contracts').json()
    cur_token = head_cur.get('version_token')
    active_expectations = head_cur['active_contract']['expectations']
    
    # 5a. Stale Baseline Conflict (expecting old baseline prev_baseline_id)
    r_stale_base = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{mid}/contracts', json={
        "expectations": active_expectations,
        "expected_version_token": cur_token,
        "expected_approved_version_id": prev_baseline_id
    })
    print(f"Stale baseline proposal response: HTTP {r_stale_base.status_code}, Code: {r_stale_base.json().get('error', {}).get('code')}")
    assert r_stale_base.status_code == 409
    assert r_stale_base.json()['error']['code'] == 'STALE_METRIC_BASELINE'
    print("-> STALE_METRIC_BASELINE correctly returned for stale baseline.")
    
    # 5b. Stale Head Conflict (expecting new baseline, but stale token)
    r_stale_contract = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{mid}/contracts', json={
        "expectations": active_expectations,
        "expected_version_token": "stale-version-token-deadbeef",
        "expected_approved_version_id": new_baseline_id
    })
    print(f"Stale contract proposal response: HTTP {r_stale_contract.status_code}, Code: {r_stale_contract.json().get('error', {}).get('code')}")
    assert r_stale_contract.status_code == 409
    assert r_stale_contract.json()['error']['code'] == 'STALE_METRIC_CONTRACT'
    print("-> STALE_METRIC_CONTRACT correctly returned for stale contract head token.")
    
    print("\n=== Step 6: Propose and approve a later governance revision with generated ID ===")
    # Propose new revision under new baseline
    exp_rev = {
        **active_expectations,
        "meaning": "Governed points under north segment baseline (baseline-change invariance test)",
        "materiality_policy": {"absolute_threshold": "0.10", "relative_threshold": "0.01"}
    }
    r_prop = c_analyst.post(f'/api/v1/projects/{pid}/metrics/{mid}/contracts', json={
        "expectations": exp_rev,
        "expected_version_token": cur_token,
        "expected_approved_version_id": new_baseline_id
    })
    assert r_prop.status_code == 201, f"Failed to propose revision: {r_prop.text}"
    prop_data = r_prop.json()
    new_rev_id = prop_data['id']
    new_rev_num = prop_data['version_number']
    print(f"Proposed Revision {new_rev_num}: ID {new_rev_id}")
    
    # Owner approves the revision
    head_after_prop = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/contracts').json()
    r_app = c_owner.post(f'/api/v1/projects/{pid}/metrics/{mid}/contracts/{new_rev_id}/approve', json={
        "expected_version_token": head_after_prop['version_token'],
        "reason": "Synthetic engineering rationale: Approved revision under new baseline to verify report invariance."
    })
    assert r_app.status_code in (200, 201), f"Failed to approve revision: {r_app.text}"
    print(f"Owner approved Revision {new_rev_num} ({new_rev_id}).")
    
    print("\n=== Step 7: Reopen original check and verify hash and canonical invariance ===")
    r_reopen = c_analyst.get(f'/api/v1/projects/{pid}/metrics/{mid}/contracts/checks/{orig_check_id}')
    assert r_reopen.status_code == 200, f"Failed to reopen original check: {r_reopen.text}"
    canonical_after = r_reopen.json()
    
    print(f"Reopened report hash: {canonical_after['report_hash']}")
    assert canonical_after['report_hash'] == expected_orig_hash, "Report hash changed after baseline change!"
    assert canonical_after == canonical_orig, "Canonical report payload changed after baseline change!"
    print("Canonical content is bit-for-bit identical to the original report!")
    
    print("\n=== Step 8: Write receipt ===")
    receipt = {
        "gate": "REAL_BASELINE_CHANGE_INVARIANCE",
        "status": "PASS",
        "metric_id": mid,
        "original_check": {
            "check_id": orig_check_id,
            "report_hash": expected_orig_hash,
            "pinned_baseline_version_id": prev_baseline_id,
            "reopened_identical": True
        },
        "baseline_transition": {
            "previous_definition_id": active_def_id,
            "new_definition_id": new_def_id,
            "previous_baseline_version_id": prev_baseline_id,
            "new_baseline_version_id": new_baseline_id,
            "new_version_number": new_ver_number,
            "transition_kind": latest_trans.get('transition_kind'),
            "transition_id": latest_trans.get('id')
        },
        "conflict_distinction": {
            "stale_baseline": {
                "attempted_approved_version_id": prev_baseline_id,
                "status_code": r_stale_base.status_code,
                "error_code": r_stale_base.json()['error']['code'],
                "message": r_stale_base.json()['error']['message']
            },
            "stale_head": {
                "attempted_token": "stale-version-token-deadbeef",
                "status_code": r_stale_contract.status_code,
                "error_code": r_stale_contract.json()['error']['code'],
                "message": r_stale_contract.json()['error']['message']
            }
        },
        "subsequent_governance_progression": {
            "generated_revision_id": new_rev_id,
            "version_number": new_rev_num,
            "pinned_baseline_version_id": new_baseline_id,
            "approved_by": "Owner Alpha",
            "approval_reason": "Synthetic engineering rationale: Approved revision under new baseline to verify report invariance.",
            "status": "active"
        },
        "verification_result": {
            "canonical_payload_identical": True,
            "report_hash_unchanged": True,
            "hash": expected_orig_hash
        }
    }
    
    receipt_file = DATA_DIR / 'receipts' / 'receipt_baseline_change_invariance_corrected.json'
    receipt_file.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f"Wrote receipt to {receipt_file}")

if __name__ == '__main__':
    main()
