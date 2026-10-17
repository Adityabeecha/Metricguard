import httpx, json
from pathlib import Path

DATA_DIR = Path('data/v8_live_retest')
keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
pid = 'ws-relational-live'
mid = 'metric-e8cb92dbef384193ae56cad1f887fdff'

c = httpx.Client(base_url='http://127.0.0.1:8001', headers={'Authorization': f"Bearer {keys['owner']['api_key']}", 'X-Project-Id': pid})

b = c.get('/api/v1/bootstrap').json()
m = next(x for x in b['metrics'] if x['id'] == mid)
print("Metric from bootstrap:")
print(json.dumps(m, indent=2))
