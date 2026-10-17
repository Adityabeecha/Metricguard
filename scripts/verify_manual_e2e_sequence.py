import urllib.request
import json
import time
import sys

BASE_URL = 'http://localhost:5173/api/v1'

def req_json(url, method='GET', data=None, headers=None):
    hdrs = {'Content-Type': 'application/json'}
    if headers:
        hdrs.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def main():
    print("=== STEP 1-4: VERIFY DATA SOURCE IN IDIOTMETRICS ===")
    boot_idiot = req_json(f"{BASE_URL}/bootstrap", headers={'X-Project-Id': 'idiotmetrics'})
    assert boot_idiot['project']['id'] == 'idiotmetrics'
    print("Project confirmed: idiotmetrics")

    snaps = [s for s in boot_idiot['snapshots'] if s['name'] == '01_sales_multi_region']
    assert len(snaps) > 0, "Snapshot 01_sales_multi_region not found in idiotmetrics!"
    snap = snaps[0]
    print(f"Snapshot confirmed: {snap['name']} ({snap['id']})")
    assert snap.get('contract_hash'), "Snapshot missing contract_hash"

    contracts = [c for c in boot_idiot.get('flat_contracts', []) if c['contract_hash'] == snap['contract_hash']]
    assert len(contracts) > 0, "Flat contract not found for snapshot"
    contract = contracts[0]
    print(f"Contract confirmed: {contract['name']} ({contract['id']})")

    print("\n=== STEP 5-7: RUN BASELINE AND TARGET COMPARISON IN IDIOTMETRICS ===")
    baseline_sql = "SELECT SUM(revenue)\nFROM snapshot\nWHERE status = 'completed'"
    target_sql = "SELECT SUM(revenue)\nFROM snapshot\nWHERE status = 'completed'\n  AND channel = 'Online'"

    b_def = req_json(f"{BASE_URL}/flat/definitions", method='POST', headers={'X-Project-Id': 'idiotmetrics'}, data={
        'contract_id': contract['id'],
        'raw_sql': baseline_sql,
        'name': 'Sales Completed Total'
    })
    print(f"Baseline definition registered: {b_def['id']}")

    t_def = req_json(f"{BASE_URL}/flat/definitions", method='POST', headers={'X-Project-Id': 'idiotmetrics'}, data={
        'contract_id': contract['id'],
        'raw_sql': target_sql,
        'name': 'Sales Completed Online'
    })
    print(f"Target definition registered: {t_def['id']}")

    comp_payload = {
        'baseline_version_id': b_def['versions'][0]['id'],
        'target_version_id': t_def['versions'][0]['id'],
        'snapshot_id': snap['id'],
        'context': {
            'contract_hash': snap['contract_hash'],
            'unit_label': snap.get('currency') or contract.get('unit_label') or 'INR',
            'scope': 'entire_supplied_snapshot',
            'mode': 'flat_snapshot',
            'values_comparable': True,
        },
        'relationship': 'intentional_variant'
    }

    run_res = req_json(
        f"{BASE_URL}/flat/comparisons",
        method='POST',
        headers={'X-Project-Id': 'idiotmetrics', 'Idempotency-Key': f"e2e-{time.time()}"},
        data=comp_payload
    )
    run_id = run_res['id']
    print(f"Comparison run queued: {run_id}")

    print("Waiting for isolated execution...")
    run_data = None
    for _ in range(40):
        time.sleep(1)
        run_data = req_json(f"{BASE_URL}/comparisons/{run_id}", headers={'X-Project-Id': 'idiotmetrics'})
        if run_data['state'] in ('succeeded', 'completed', 'failed'):
            break

    assert run_data['state'] in ('succeeded', 'completed'), f"Run failed: {run_data.get('error')}"
    res = run_data['result']
    b_val = float(res['baseline_value'])
    t_val = float(res['target_value'])
    delta = float(res['delta_target_minus_baseline'])

    print(f"STEP 8: RECONCILIATION RESULT:")
    print(f"  Baseline: {b_val}")
    print(f"  Target:   {t_val}")
    print(f"  Delta:    {delta}")

    assert b_val == 497700.0, f"Expected Baseline 497700, got {b_val}"
    assert t_val == 283700.0, f"Expected Target 283700, got {t_val}"
    assert delta == -214000.0, f"Expected Delta -214000, got {delta}"
    print("PASS: Baseline = 497700, Target = 283700, Delta = -214000")

    print("\n=== STEP 9-12: SAVE INVESTIGATION & DRAFT IN IDIOTMETRICS ===")
    draft = req_json(
        f"{BASE_URL}/projects/idiotmetrics/investigation-drafts",
        method='POST',
        headers={'X-Project-Id': 'idiotmetrics'},
        data={
            'title': 'Sales Channel Variance Investigation',
            'baseline_version_id': b_def['versions'][0]['id'],
            'target_version_id': t_def['versions'][0]['id'],
            'snapshot_id': snap['id'],
            'comparison_run_id': run_id,
            'status': 'completed',
            'notes': 'Online channel represents 283700 of 497700 total revenue.',
        }
    )
    draft_id = draft['id']
    print(f"Investigation draft saved: {draft_id}")

    # Record investigation
    inv = req_json(
        f"{BASE_URL}/investigations",
        method='POST',
        headers={'X-Project-Id': 'idiotmetrics'},
        data={
            'title': 'Sales Channel Variance Investigation',
            'decision': 'approved',
            'actor_label': 'Analyst',
            'run_id': run_id,
            'snapshot_id': snap['id'],
        }
    )
    inv_id = inv['id']
    print(f"Investigation recorded: {inv_id}")

    # Re-fetch investigations list
    inv_list = req_json(f"{BASE_URL}/investigations", headers={'X-Project-Id': 'idiotmetrics'})
    assert any(item['id'] == inv_id for item in inv_list['items']), "Investigation not in list"
    print("Investigation successfully listed in idiotmetrics")

    print("\n=== STEP 13-14: SWITCH TO LOCAL-RETAIL & CONFIRM ZERO LEAKAGE ===")
    boot_retail = req_json(f"{BASE_URL}/bootstrap", headers={'X-Project-Id': 'local-retail'})
    assert boot_retail['project']['id'] == 'local-retail'

    # Check snapshots: 01_sales_multi_region must NOT be in local-retail
    retail_snap_names = [s['name'] for s in boot_retail['snapshots']]
    assert '01_sales_multi_region' not in retail_snap_names, f"LEAK: 01_sales_multi_region appeared in local-retail!"
    print("PASS: 01_sales_multi_region is NOT in local-retail snapshots")

    # Check investigations: inv_id must NOT be in local-retail
    retail_inv_list = req_json(f"{BASE_URL}/investigations", headers={'X-Project-Id': 'local-retail'})
    assert not any(item['id'] == inv_id for item in retail_inv_list['items']), f"LEAK: Investigation {inv_id} appeared in local-retail!"
    print("PASS: Investigation is NOT in local-retail investigations")

    # Direct access to idiotmetrics draft from local-retail must be 403 Forbidden
    try:
        req = urllib.request.Request(
            f"{BASE_URL}/projects/local-retail/investigation-drafts/{draft_id}",
            headers={'X-Project-Id': 'local-retail'}
        )
        with urllib.request.urlopen(req) as resp:
            pass
        assert False, "Should have failed with 403 or 404"
    except urllib.error.HTTPError as err:
        assert err.code in (403, 404), f"Unexpected status: {err.code}"
        print(f"PASS: Cross-workspace access to draft correctly denied ({err.code})")

    print("\n=== STEP 15-16: SWITCH BACK TO IDIOTMETRICS & CONFIRM AVAILABILITY ===")
    boot_idiot_again = req_json(f"{BASE_URL}/bootstrap", headers={'X-Project-Id': 'idiotmetrics'})
    assert boot_idiot_again['project']['id'] == 'idiotmetrics'
    assert any(s['name'] == '01_sales_multi_region' for s in boot_idiot_again['snapshots'])
    assert any(item['id'] == inv_id for item in req_json(f"{BASE_URL}/investigations", headers={'X-Project-Id': 'idiotmetrics'})['items'])

    # Verify draft retrieval
    draft_fetched = req_json(
        f"{BASE_URL}/projects/idiotmetrics/investigation-drafts/{draft_id}",
        headers={'X-Project-Id': 'idiotmetrics'}
    )
    assert draft_fetched['id'] == draft_id
    assert draft_fetched['comparison_run_id'] == run_id
    print("PASS: Everything remains fully available and isolated in idiotmetrics!")
    print("\n=======================================================")
    print("ALL 16 WORKSPACE ISOLATION & E2E CHECKS PASSED PERFECTLY")
    print("=======================================================")

if __name__ == '__main__':
    main()
