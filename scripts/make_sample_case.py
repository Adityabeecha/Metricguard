"""Build the public 5,000-order sample case and seal it with the real engine.

    python -m scripts.make_sample_case

Writes apps/web/public/samples/sample_case_orders.csv (fixed seed, so the file never changes)
and prints the totals, case number and fingerprints that apps/web/src/demoCase.ts publishes.
The comparison, sealing and replay run through the same API and isolated runner as the app.

The data is synthetic with one planted cause, so "Where it sits" has something real to find:
refunds in the app channel during the second half of October run at about 30% instead of 4%
(as if the app's refund sync double-posted). The dashboard counts every order except cancelled
ones; finance counts paid orders only.
"""
from __future__ import annotations

import csv
import json
import random
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'apps' / 'web' / 'public' / 'samples' / 'sample_case_orders.csv'
COLUMNS = ['order_id', 'order_date', 'region', 'channel', 'status', 'customer_id', 'amount']
CURRENT_SQL = "SELECT SUM(amount) FROM snapshot WHERE status <> 'cancelled'"
PROPOSED_SQL = "SELECT SUM(amount) FROM snapshot WHERE status = 'paid'"


def rows(count: int = 5000, seed: int = 20261101) -> list[list[str]]:
    rng = random.Random(seed)
    out = []
    for n in range(1, count + 1):
        day = date(2026, 10, 1) + timedelta(days=rng.randrange(31))
        channel = rng.choices(['web', 'store', 'app'], [36, 32, 32])[0]
        refund_rate = 0.30 if channel == 'app' and day.day >= 16 else 0.04
        roll = rng.random()
        status = ('refunded' if roll < refund_rate else 'cancelled' if roll < refund_rate + 0.06
                  else 'pending' if roll < refund_rate + 0.15 else 'paid')
        amount = '' if rng.random() < 0.004 else f'{min(25000.0, rng.lognormvariate(7.6, 0.6)):.2f}'
        out.append([f'ORD-{n:05d}', day.isoformat(), rng.choice(['north', 'south', 'east', 'west']), channel, status,
                    f'CUST-{rng.randrange(1, 1800):04d}', amount])
    return out


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(COLUMNS)
        writer.writerows(rows())

    from fastapi.testclient import TestClient
    from services.api.app import create_app
    from services.api.store import Store
    from services.supervisor.worker import Supervisor
    from tests.test_distinct_and_ratio import _InProcess

    with tempfile.TemporaryDirectory(prefix='metricguard-sample-', ignore_cleanup_errors=True) as folder:
        folder = Path(folder)
        url = f"sqlite:///{folder / 'app.db'}"
        worker = Supervisor(Store(url), _InProcess(), folder / 'jobs', 'sample-worker', data_dir=folder)
        with TestClient(create_app(database_url=url, data_dir=folder, mode='isolated')) as client:
            contract = client.post('/api/v1/flat/contracts', json={
                'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'Sample orders, October 2026',
                'row_key': 'order_id', 'measure': 'amount', 'precision': 18, 'scale': 2,
                'categories': ['region', 'channel', 'status', 'order_date'],
                'dimension_types': {'region': 'string', 'channel': 'string', 'status': 'string', 'order_date': 'date'},
                'nullable_categories': [], 'measure_meaning': 'Order amount', 'unit_label': 'INR', 'values_comparable': True,
                'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}).json()
            with OUTPUT.open('rb') as content:
                uploaded = client.post('/api/v1/flat/snapshots', files={'file': ('sample_case_orders.csv', content, 'text/csv')},
                                       data={'contract_id': contract['id'], 'name': 'Sample orders, October 2026'}).json()
            assert worker.run_once()
            imported = client.get(f"/api/v1/snapshot-imports/{uploaded['id']}").json()
            assert imported['state'] == 'validated', imported
            snapshot = client.get(f"/api/v1/snapshots/{imported['snapshot_id']}").json()
            versions = [client.post('/api/v1/flat/definitions', json={'name': name, 'raw_sql': sql, 'contract_id': contract['id']}).json()['versions'][0]
                        for name, sql in (('Revenue, dashboard', CURRENT_SQL), ('Revenue, finance', PROPOSED_SQL))]
            context = {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
                       'unit_label': 'INR', 'values_comparable': True}
            queued = client.post('/api/v1/flat/comparisons', headers={'Idempotency-Key': 'sample-case'}, json={
                'baseline_version_id': versions[0]['id'], 'target_version_id': versions[1]['id'],
                'snapshot_id': snapshot['id'], 'context': context}).json()
            assert worker.run_once()
            run = client.get(f"/api/v1/comparisons/{queued['id']}").json()
            case = client.post('/api/v1/investigations', json={'run_id': run['id'], 'title': 'Revenue: dashboard vs finance, October',
                                                               'decision': 'sample', 'actor_label': 'MetricGuard sample'}).json()
            replay = client.post(f"/api/v1/investigations/{case['id']}/replays", headers={'Idempotency-Key': 'sample-replay'}).json()
            assert worker.run_once()
            replayed = client.get(f"/api/v1/comparisons/{replay['id']}").json()
            result, again = run['result'], replayed['result']
            assert result['evidence_artifact']['sha256'] == again['evidence_artifact']['sha256'], 'replay differs'
            print(json.dumps({
                'case_number': 'MG-' + run['id'].replace('-', '')[:4].upper(),
                'current': result['baseline_value'], 'proposed': result['target_value'],
                'delta': result['delta_target_minus_baseline'], 'residual': result['residual'],
                'records': snapshot['row_count'],
                'hashes': {'dataset': snapshot['manifest_hash'], 'current': versions[0]['ir_hash'],
                           'proposed': versions[1]['ir_hash'], 'evidence': result['evidence_artifact']['sha256']},
            }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
