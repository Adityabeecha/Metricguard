import httpx, json
from pathlib import Path

DATA_DIR = Path('data/v8_live_retest')
keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
pid = 'ws-relational-live'
c = httpx.Client(base_url='http://127.0.0.1:8001', headers={'Authorization': f"Bearer {keys['owner']['api_key']}", 'X-Project-Id': pid})

def_id = '6cd16cec-8efa-4ecd-9194-bca3c9f33243'
r_def = c.get(f'/api/v1/definitions/{def_id}')
print(json.dumps(r_def.json(), indent=2))
