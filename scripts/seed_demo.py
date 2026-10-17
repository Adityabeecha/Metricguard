"""Generate evidence ONLY for repository-owned synthetic SQL and CSV fixtures.

No user SQL/path input is accepted. This command is distinct from the HTTP API.
"""
from pathlib import Path
import hashlib
import json
import os

from packages.metricguard_core import compare, ingest_csv
from services.api.store import Store, fingerprint

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
           'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id', 'scope': 'synthetic_retail_all_orders'}


def seed(database_url=None, data_dir=None):
    data_dir = Path(data_dir or os.environ.get('METRICGUARD_DATA_DIR', ROOT / 'data')).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(database_url or os.environ.get('METRICGUARD_DATABASE_URL', f"sqlite:///{data_dir / 'metricguard.db'}"))
    fixtures = ROOT / 'MetricGuard_Planning_Pack/examples'
    database = data_dir / 'snapshots/golden.duckdb'
    if database.exists():
        from packages.metricguard_core import inspect_snapshot
        manifest = inspect_snapshot(database)
    else:
        manifest = ingest_csv(fixtures / 'orders_golden.csv', database, {**CONTEXT, 'cutoff_at': '2026-09-02'})
    snapshot = store.record_snapshot('August 2026 · synthetic orders', manifest, str(database))
    existing = store.bootstrap()['definitions']
    demo_definitions = []
    sources = [('Dashboard A', fixtures / 'dashboard_a.sql', 'order_date'),
               ('Dashboard B', fixtures / 'dashboard_b.sql', 'order_date'),
               ('Finance', fixtures / 'finance.sql', 'payment_date'),
               ('Dashboard A (equal-value variant)', ROOT / 'demo/equal_value_variant.sql', 'order_date')]
    for name, file, field in sources:
        source_label = 'Synthetic fixture / ' + file.name
        trusted_sql = file.read_text(encoding='utf-8')
        # Source labels are display metadata supplied by users. Reuse a record
        # only when the immutable SQL bytes and date metadata match the file.
        version = next((v for d in existing for v in d['versions'] if
                        d['source_label'] == source_label and v['raw_sql'] == trusted_sql and
                        v['time_column'] == field and v['support_state'] == 'executable'), None)
        if version is None:
            definition = store.record_definition(name, source_label, 'Local analyst', trusted_sql, field)
            version = definition['versions'][0]
        demo_definitions.append(version)
    changed_sql = (ROOT / 'demo/date_rule_variant.sql').read_text(encoding='utf-8')
    changed = store.record_definition('Dashboard A', 'Synthetic fixture / dashboard_a.sql',
                                      'Local analyst', changed_sql, 'payment_date',
                                      definition_id=demo_definitions[0]['definition_id'])['versions'][-1]
    demo_definitions.append(changed)
    evidence_dir = data_dir / 'evidence'
    evidence_dir.mkdir(exist_ok=True)
    catalog = {}
    for a in demo_definitions:
        for b in demo_definitions:
            result = compare(database, a['ir'], b['ir'], CONTEXT)
            raw = json.dumps(result, indent=2, ensure_ascii=False).encode()
            digest = hashlib.sha256(raw).hexdigest()
            (evidence_dir / (digest + '.json')).write_bytes(raw)
            key = fingerprint({'a': a['ir_hash'], 'b': b['ir_hash'], 'snapshot': snapshot['manifest_hash'], 'context': CONTEXT})
            catalog[key] = {'hash': digest}
    (data_dir / 'demo-catalog.json').write_text(json.dumps(catalog, indent=2), encoding='utf-8')
    return store


if __name__ == '__main__':
    state = seed().bootstrap()
    print(f"Verified demo ready: {len(state['definitions'])} definitions, {len(state['snapshots'])} frozen snapshot(s), 25 precomputed comparisons.")
