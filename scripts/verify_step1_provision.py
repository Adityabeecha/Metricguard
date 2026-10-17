"""Step 1: Environment verification and synthetic provisioning.

Validates the isolated running environment and provisions synthetic identities and workspaces.
Outputs: data/v8_acceptance/receipts/receipt_01_startup_and_identities.json
"""
import hashlib
import json
import os
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS_DIR = ROOT / 'data/v8_acceptance/receipts'
RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
API_BASE = 'http://127.0.0.1:8001/api/v1'

def redact_key(key: str) -> str:
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
    return f"sha256:{digest[:12]}...{digest[-6:]}"

def main():
    # 1. Health check
    res_health = httpx.get(f'{API_BASE}/health')
    assert res_health.status_code == 200, f"Health check failed: {res_health.text}"
    health_data = res_health.json()
    assert health_data.get('mode') == 'isolated', f"Expected isolated mode, got {health_data}"
    assert health_data.get('runner_available') is True, f"Supervisor not available: {health_data}"

    # 2. Check unauthenticated bootstrap is refused
    res_unauth = httpx.get(f'{API_BASE}/bootstrap')
    assert res_unauth.status_code == 401, f"Expected 401 unauth bootstrap, got {res_unauth.status_code}"

    # 3. Create synthetic users
    users = {}
    tokens = {}
    for name, email in [
        ('Owner Alpha', 'owner@acceptance.internal'),
        ('Analyst Beta', 'analyst@acceptance.internal'),
        ('Reader Gamma', 'reader@acceptance.internal'),
        ('Outsider Delta', 'outsider@acceptance.internal'),
    ]:
        res = httpx.post(f'{API_BASE}/users', json={'name': name, 'email': email})
        assert res.status_code == 201, f"Failed to create user {name}: {res.text}"
        data = res.json()
        users[name] = data['user']
        tokens[name] = data['api_key']

    # 4. Create primary workspace as Owner
    owner_headers = {'X-API-Key': tokens['Owner Alpha']}
    res_ws1 = httpx.post(f'{API_BASE}/projects', json={'name': 'Workspace Relational 8', 'id': 'ws-relational-8'}, headers=owner_headers)
    assert res_ws1.status_code == 201, f"Failed to create ws-relational-8: {res_ws1.text}"
    ws1 = res_ws1.json()

    # 5. Create second isolated workspace as Owner
    res_ws2 = httpx.post(f'{API_BASE}/projects', json={'name': 'Workspace Isolation 2', 'id': 'ws-isolation-2'}, headers=owner_headers)
    assert res_ws2.status_code == 201, f"Failed to create ws-isolation-2: {res_ws2.text}"
    ws2 = res_ws2.json()

    # 6. Add members to primary workspace
    res_mem1 = httpx.post(
        f"{API_BASE}/projects/ws-relational-8/members",
        json={'user_id': users['Analyst Beta']['id'], 'role': 'analyst'},
        headers=owner_headers
    )
    assert res_mem1.status_code == 201, f"Failed to add Analyst to ws-relational-8: {res_mem1.text}"

    res_mem2 = httpx.post(
        f"{API_BASE}/projects/ws-relational-8/members",
        json={'user_id': users['Reader Gamma']['id'], 'role': 'reader'},
        headers=owner_headers
    )
    assert res_mem2.status_code == 201, f"Failed to add Reader to ws-relational-8: {res_mem2.text}"

    # Verify membership list
    res_members = httpx.get(f"{API_BASE}/projects/ws-relational-8/members", headers=owner_headers)
    assert res_members.status_code == 200
    members = res_members.json()

    # Save credentials securely to a local file for subsequent test steps (ignored/not committed)
    creds_path = ROOT / 'data/v8_acceptance/.tokens.json'
    creds_path.write_text(json.dumps(tokens, indent=2), encoding='utf-8')

    receipt = {
        'gate': 'START_ACTUAL_ISOLATED_APPLICATION',
        'status': 'PASS',
        'environment': {
            'running_mode': health_data.get('mode'),
            'supervisor_available': health_data.get('runner_available'),
            'database_url': os.environ.get('METRICGUARD_DATABASE_URL', 'sqlite:///data/v8_acceptance/metricguard.db'),
            'data_dir': os.environ.get('METRICGUARD_DATA_DIR', 'data/v8_acceptance'),
            'runner_image': os.environ.get('METRICGUARD_RUNNER_IMAGE', 'metricguard-runner:1.0'),
            'allow_unverified_local': 'false',
        },
        'unauthenticated_refusal': {
            'endpoint': '/api/v1/bootstrap',
            'status_code': res_unauth.status_code,
            'response': res_unauth.json(),
        },
        'provisioned_users': [
            {'name': name, 'id': users[name]['id'], 'email': users[name]['email'], 'key_ref': redact_key(tokens[name])}
            for name in users
        ],
        'workspaces': [
            {'id': ws1['id'], 'name': ws1['name'], 'owner_id': ws1.get('owner_id')},
            {'id': ws2['id'], 'name': ws2['name'], 'owner_id': ws2.get('owner_id')},
        ],
        'memberships_ws_relational_8': [
            {'user_id': m.get('user_id'), 'role': m.get('role'), 'revoked_at': m.get('revoked_at')}
            for m in members
        ],
    }

    out_file = RECEIPTS_DIR / 'receipt_01_startup_and_identities.json'
    out_file.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f"Receipt written to {out_file}")
    print(json.dumps(receipt, indent=2))

if __name__ == '__main__':
    main()
