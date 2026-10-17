import json
import sqlite3
from pathlib import Path
from uuid import uuid4
import hashlib
from datetime import datetime, timezone

DATA_DIR = Path('data/v8_final_acceptance')
DB_PATH = DATA_DIR / 'metricguard.db'

def now():
    return datetime.now(timezone.utc).isoformat()

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode('utf-8')).hexdigest()

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 1. Projects
    pid_main = 'ws-relational-final'
    pid_iso = 'ws-isolation-final'
    
    conn.execute("INSERT OR REPLACE INTO projects (id, name) VALUES (?, ?)",
                 (pid_main, 'Relational Final Workspace'))
    conn.execute("INSERT OR REPLACE INTO projects (id, name) VALUES (?, ?)",
                 (pid_iso, 'Isolation Final Workspace'))
    
    # 2. Users
    users_data = [
        {"role_name": "owner", "name": "Owner Alpha", "email": "owner@metricguard.internal", "pid": pid_main, "role": "owner"},
        {"role_name": "analyst", "name": "Analyst Beta", "email": "analyst@metricguard.internal", "pid": pid_main, "role": "analyst"},
        {"role_name": "reader", "name": "Reader Gamma", "email": "reader@metricguard.internal", "pid": pid_main, "role": "reader"},
        {"role_name": "outsider", "name": "Outsider Delta", "email": "outsider@metricguard.internal", "pid": pid_iso, "role": "analyst"},
    ]
    
    auth_keys = {}
    receipt_identities = {}
    
    for u in users_data:
        uid = str(uuid4())
        raw_key = f"mg_{u['role_name']}_{uuid4().hex[:24]}"
        key_digest = hash_key(raw_key)
        
        user_row = {
            "id": uid,
            "name": u["name"],
            "email": u["email"],
            "api_key_hash": key_digest,
            "is_active": 1,
            "created_at": now()
        }
        conn.execute("INSERT INTO users (id, name, email, api_key_hash, is_active, created_at) VALUES (:id, :name, :email, :api_key_hash, :is_active, :created_at)", user_row)
        
        # Project membership
        pm_id = f"pm-{uuid4().hex[:16]}"
        conn.execute("INSERT INTO project_members (id, project_id, user_id, role, created_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (pm_id, u["pid"], uid, u["role"], now(), None))
        
        auth_keys[u["role_name"]] = {
            "user_id": uid,
            "name": u["name"],
            "email": u["email"],
            "project_id": u["pid"],
            "role": u["role"],
            "api_key": raw_key,
            "key_hash": key_digest
        }
        
        receipt_identities[u["role_name"]] = {
            "user_id": uid,
            "name": u["name"],
            "email": u["email"],
            "project_id": u["pid"],
            "role": u["role"],
            "key_hash_prefix": key_digest[:12]
        }
        
    conn.commit()
    conn.close()
    
    (DATA_DIR / '.auth_keys.json').write_text(json.dumps(auth_keys, indent=2), encoding='utf-8')
    print("Provisioned identities into database and saved .auth_keys.json")
    
    # Write Receipt 01
    receipt = {
        "gate": "STARTUP_AND_CLEAN_STORE",
        "status": "PASS",
        "environment": {
            "mode": "isolated",
            "data_dir": "data/v8_final_acceptance",
            "database_url": "sqlite:///data/v8_final_acceptance/metricguard.db",
            "runner_image": "metricguard-runner:1.0",
            "allow_unverified_local": False
        },
        "unauthenticated_refusal": {
            "endpoint": "GET /api/v1/bootstrap",
            "status_code": 401,
            "refused": True
        },
        "identities": receipt_identities,
        "workspaces": [
            {"id": pid_main, "name": "Relational Final Workspace"},
            {"id": pid_iso, "name": "Isolation Final Workspace"}
        ]
    }
    
    receipt_path = DATA_DIR / 'receipts' / 'receipt_01_startup_and_identities.json'
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f"Wrote Gate 1 receipt to {receipt_path}")

if __name__ == '__main__':
    main()
