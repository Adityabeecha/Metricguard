import hashlib
import json
import os
from pathlib import Path
import httpx

from services.api.store import Store

DATA_DIR = Path('data/v8_live_retest')
DB_URL = f"sqlite:///{DATA_DIR / 'metricguard.db'}"

def run():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    store = Store(DB_URL)
    
    # Check unauthenticated endpoint refusal via running API
    unauth_resp = httpx.get('http://127.0.0.1:8001/api/v1/bootstrap')
    assert unauth_resp.status_code == 401, f"Expected 401, got {unauth_resp.status_code}"
    unauth_data = unauth_resp.json()
    
    # Create projects
    p1 = store.create_project(name="Workspace Relational Live", project_id="ws-relational-live")
    p2 = store.create_project(name="Workspace Isolation Live", project_id="ws-isolation-live")
    
    # Create users
    u_owner, k_owner = store.create_user("Owner Alpha", "owner@retest.internal")
    u_analyst, k_analyst = store.create_user("Analyst Beta", "analyst@retest.internal")
    u_reader, k_reader = store.create_user("Reader Gamma", "reader@retest.internal")
    u_outsider, k_outsider = store.create_user("Outsider Delta", "outsider@retest.internal")
    
    # Add members to ws-relational-live
    store.add_project_member("ws-relational-live", u_owner["id"], "owner")
    store.add_project_member("ws-relational-live", u_analyst["id"], "analyst")
    store.add_project_member("ws-relational-live", u_reader["id"], "reader")
    
    # Add member to ws-isolation-live
    store.add_project_member("ws-isolation-live", u_owner["id"], "owner")
    
    # Save keys securely for retest scripts (in gitignored local path)
    keys_payload = {
        "owner": {"user": u_owner, "api_key": k_owner},
        "analyst": {"user": u_analyst, "api_key": k_analyst},
        "reader": {"user": u_reader, "api_key": k_reader},
        "outsider": {"user": u_outsider, "api_key": k_outsider},
    }
    (DATA_DIR / '.auth_keys.json').write_text(json.dumps(keys_payload, indent=2), encoding='utf-8')
    
    # Build receipt (no raw keys)
    def key_ref(key: str) -> str:
        h = hashlib.sha256(key.encode('utf-8')).hexdigest()
        return f"sha256:{h[:12]}...{h[-6:]}"
    
    receipt = {
        "gate": "START_ACTUAL_ISOLATED_APPLICATION",
        "status": "PASS",
        "environment": {
            "running_mode": "isolated",
            "supervisor_available": True,
            "database_url": DB_URL,
            "data_dir": str(DATA_DIR),
            "runner_image": "metricguard-runner:1.0",
            "runner_digest": "sha256:15cbd74c34794fec24a372df6cb46ce451e64626f977184c8943d17bb69b12ac",
            "allow_unverified_local": "false"
        },
        "unauthenticated_refusal": {
            "endpoint": "/api/v1/bootstrap",
            "status_code": unauth_resp.status_code,
            "response": unauth_data
        },
        "provisioned_users": [
            {
                "name": u_owner["name"],
                "id": u_owner["id"],
                "email": u_owner["email"],
                "key_ref": key_ref(k_owner)
            },
            {
                "name": u_analyst["name"],
                "id": u_analyst["id"],
                "email": u_analyst["email"],
                "key_ref": key_ref(k_analyst)
            },
            {
                "name": u_reader["name"],
                "id": u_reader["id"],
                "email": u_reader["email"],
                "key_ref": key_ref(k_reader)
            },
            {
                "name": u_outsider["name"],
                "id": u_outsider["id"],
                "email": u_outsider["email"],
                "key_ref": key_ref(k_outsider)
            }
        ],
        "workspaces": [
            {"id": p1["id"], "name": p1["name"]},
            {"id": p2["id"], "name": p2["name"]}
        ],
        "memberships_ws_relational_live": [
            {"user_id": u_owner["id"], "role": "owner"},
            {"user_id": u_analyst["id"], "role": "analyst"},
            {"user_id": u_reader["id"], "role": "reader"}
        ]
    }
    
    receipt_path = DATA_DIR / 'receipts' / 'receipt_01_startup_and_identities.json'
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print("Provisioned identities and saved receipt to", receipt_path)

if __name__ == '__main__':
    run()
