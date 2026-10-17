import httpx, json
from pathlib import Path

DATA_DIR = Path('data/v8_live_retest')
keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
pid = 'ws-relational-live'
mid = 'metric-e8cb92dbef384193ae56cad1f887fdff'

c = httpx.Client(base_url='http://127.0.0.1:8001', headers={'Authorization': f"Bearer {keys['owner']['api_key']}", 'X-Project-Id': pid})

r_metric = c.get(f'/api/v1/projects/{pid}/metrics/{mid}')
m = r_metric.json()
print("Metric active binding:", m.get('active_binding'))

r_contracts = c.get(f'/api/v1/projects/{pid}/metrics/{mid}/contracts')
ch = r_contracts.json()
print("Contract version_token:", ch.get('version_token'))
print("Active contract:", ch.get('active_contract', {}).get('id'), "version_number:", ch.get('active_contract', {}).get('version_number'))
print("All contract items:")
for it in ch.get('items', []):
    print(f"  ID: {it['id']} | Rev: {it['version_number']} | Status: {it['status']} | BaselinePin: {it.get('approved_definition', {}).get('version_id')}")

print("\nRecent checks:")
for chk in ch.get('recent_checks', []):
    print(f"  Check ID: {chk['id']} | Hash: {chk['report_hash']} | Status: {chk['status']} | RevID: {chk['contract_revision_id']}")
