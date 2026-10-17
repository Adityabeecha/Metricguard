"""Seed the beginner demo into a running MetricGuard (isolated mode + supervisor).

Creates, in the default workspace of a clean data directory:
  1. "Demo · Completed orders only"  — single table, 750.75 → 410.75 INR (−340.00)
  2. "Demo · North segment via join" — fact + dimension, 65.00 → 10.00 points (−55.00)
and checks one intentionally unsupported query is refused, not computed.

Expected totals are recomputed here from the CSV files with Python's Decimal, without
MetricGuard code, and compared with the engine's answer. Re-running is safe: cases
that already exist (by title) are skipped.

    .venv\\Scripts\\python -m scripts.seed_closeout_demo [--api http://127.0.0.1:8000] [--key mg_...]
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / 'demo' / 'closeout'
FLAT_TITLE = 'Demo · Completed orders only'
REL_TITLE = 'Demo · North segment via join'


def _check(response: httpx.Response, *ok: int) -> dict:
    if response.status_code not in ok:
        raise SystemExit(f'{response.request.method} {response.request.url.path} -> {response.status_code}: {response.text[:400]}')
    return response.json()


def _wait(client: httpx.Client, path: str, done: tuple[str, ...], timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = _check(client.get(path), 200)
        if item.get('state') in done:
            return item
        time.sleep(1)
    raise SystemExit(f'Timed out waiting for {path}. Is the supervisor running? (scripts\\mg.ps1 status)')


def _snapshot_from_upload(client: httpx.Client, item: dict) -> str:
    if 'state' not in item:
        return item['id']
    job = _wait(client, f"/api/v1/snapshot-imports/{item['id']}", ('validated', 'failed'))
    if job['state'] != 'validated':
        raise SystemExit(f"Import failed: {job.get('error')}")
    return job['snapshot_id']


def _compare(client, endpoint, base_id, target_id, snapshot_id, context) -> dict:
    run = _check(client.post(endpoint, json={'baseline_version_id': base_id, 'target_version_id': target_id,
                                             'snapshot_id': snapshot_id, 'relationship': 'candidate', 'context': context},
                             headers={'Idempotency-Key': uuid4().hex}), 202)
    run = _wait(client, f"/api/v1/comparisons/{run['id']}", ('succeeded', 'partial', 'failed'))
    if run['state'] == 'failed':
        raise SystemExit(f"Comparison failed: {run.get('error')}")
    return run


def _expect(result: dict, base: Decimal, target: Decimal) -> None:
    got = (Decimal(result['baseline_value']), Decimal(result['target_value']), Decimal(result['delta_target_minus_baseline']))
    want = (base, target, target - base)
    if got != want:
        raise SystemExit(f'Engine result {got} does not match independent calculation {want}.')
    if Decimal(result.get('residual') or 0) != 0:
        raise SystemExit(f"Unexpected unexplained difference {result['residual']}.")


def seed_flat(client: httpx.Client, existing: set[str]) -> str:
    rows = list(csv.DictReader((DEMO / 'sales_orders.csv').open(encoding='utf-8')))
    all_total = sum(Decimal(r['sales_amount']) for r in rows if r['sales_amount'])
    completed = sum(Decimal(r['sales_amount']) for r in rows if r['sales_amount'] and r['status'] == 'completed')
    if FLAT_TITLE in existing:
        return f'{FLAT_TITLE}: already present'
    contract = _check(client.post('/api/v1/flat/contracts', json={
        'version': 4, 'mode': 'flat_snapshot', 'name': 'Demo sales orders', 'row_key': 'row_id',
        'measure': 'sales_amount', 'precision': 18, 'scale': 2,
        'categories': ['status', 'region', 'channel', 'is_active', 'order_day'],
        'dimension_types': {'status': 'string', 'region': 'string', 'channel': 'string', 'is_active': 'boolean', 'order_day': 'date'},
        'nullable_categories': ['region', 'is_active'],
        'measure_meaning': 'Sales value of one order', 'unit_label': 'INR', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}), 201)
    with (DEMO / 'sales_orders.csv').open('rb') as stream:
        upload = _check(client.post('/api/v1/flat/snapshots', files={'file': ('sales_orders.csv', stream, 'text/csv')},
                                    data={'contract_id': contract['id'], 'name': 'Demo sales orders (8 rows)'}), 200, 202)
    snapshot_id = _snapshot_from_upload(client, upload)
    ids = []
    for name, sql in [('All orders', 'SELECT SUM(sales_amount) FROM snapshot'),
                      ('Completed orders only', "SELECT SUM(sales_amount) FROM snapshot WHERE status = 'completed'")]:
        ids.append(_check(client.post('/api/v1/flat/definitions', json={'name': name, 'raw_sql': sql, 'contract_id': contract['id']}), 201)['versions'][-1]['id'])
    context = {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
               'unit_label': 'INR', 'values_comparable': True}
    run = _compare(client, '/api/v1/flat/comparisons', ids[0], ids[1], snapshot_id, context)
    _expect(run['result'], all_total, completed)

    # Honest refusal: AVG is outside the supported grammar and must not produce a number.
    refused = client.post('/api/v1/flat/definitions', json={'name': 'Unsupported average', 'raw_sql': 'SELECT AVG(sales_amount) FROM snapshot', 'contract_id': contract['id']})
    version = refused.json().get('versions', [{}])[-1] if refused.status_code == 201 else {}
    if refused.status_code == 201 and version.get('support_state') == 'executable':
        raise SystemExit('AVG was accepted; expected an honest refusal.')
    reason = version.get('support_reason') or refused.json().get('error', {}).get('message')

    _check(client.post('/api/v1/investigations', json={
        'title': FLAT_TITLE, 'run_id': run['id'], 'snapshot_id': snapshot_id, 'actor_label': 'Demo seed',
        'decision': 'Limiting the total to completed orders removes three orders (pending R003 200.00, returned R004 50.00, '
                    'cancelled R007 90.00): 340.00 INR lower. R006 has a blank amount and is ignored by both totals.'}), 201)
    return f'{FLAT_TITLE}: {all_total} → {completed} INR (refusal check: {reason})'


def seed_relational(client: httpx.Client, existing: set[str]) -> str:
    facts = list(csv.DictReader((DEMO / 'fact_items.csv').open(encoding='utf-8')))
    segment = {r['group_id']: r['segment'] for r in csv.DictReader((DEMO / 'dim_groups.csv').open(encoding='utf-8'))}
    left_total = sum(Decimal(r['amount']) for r in facts if r['amount'])
    north = sum(Decimal(r['amount']) for r in facts if r['amount'] and segment.get(r['group_id']) == 'north')
    if REL_TITLE in existing:
        return f'{REL_TITLE}: already present'
    contract = _check(client.post('/api/v1/relational/contracts', json={
        'version': 1, 'mode': 'relational_snapshot', 'policy': 'relational-snapshot-v1', 'name': 'Demo points by group',
        'fact': {'table': 'fact_items', 'key': 'fact_id', 'grain': 'One point item', 'categories': ['kind']},
        'dimension': {'table': 'dim_groups', 'key': 'group_id', 'grain': 'One group', 'categories': ['segment']},
        'relationship': {'fact_foreign_key': 'group_id', 'dimension_key': 'group_id', 'cardinality': 'many_to_one',
                         'missing_dimension_policy': 'preserve_and_report'},
        'measure': {'column': 'amount', 'precision': 10, 'scale': 2}, 'measure_meaning': 'Points on one item',
        'unit_label': 'points', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}), 201)
    with (DEMO / 'fact_items.csv').open('rb') as fact, (DEMO / 'dim_groups.csv').open('rb') as dim:
        upload = _check(client.post('/api/v1/relational/snapshots',
                                    files={'fact_file': ('fact_items.csv', fact, 'text/csv'), 'dimension_file': ('dim_groups.csv', dim, 'text/csv')},
                                    data={'contract_id': contract['id'], 'name': 'Demo points (5 items, 2 groups)'}), 200, 202)
    snapshot_id = _snapshot_from_upload(client, upload)
    ids = []
    for name, sql in [('All items', 'SELECT SUM(f.amount) FROM fact_items f LEFT JOIN dim_groups d ON f.group_id = d.group_id'),
                      ('North segment only', "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE d.segment = 'north'")]:
        ids.append(_check(client.post('/api/v1/relational/definitions', json={'name': name, 'raw_sql': sql, 'contract_id': contract['id']}), 201)['versions'][-1]['id'])
    context = {'mode': 'relational_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
               'unit_label': 'points', 'values_comparable': True}
    run = _compare(client, '/api/v1/relational/comparisons', ids[0], ids[1], snapshot_id, context)
    _expect(run['result'], left_total, north)
    _check(client.post('/api/v1/investigations', json={
        'title': REL_TITLE, 'run_id': run['id'], 'snapshot_id': snapshot_id, 'actor_label': 'Demo seed',
        'decision': 'Restricting to the north segment with an INNER JOIN drops F2 (south, 20.00), F3 (unknown group DX, 30.00) '
                    'and F4 (no group, 5.00): 55 points lower. F5 has a blank amount and adds nothing to either total.'}), 201)
    return f'{REL_TITLE}: {left_total} → {north} points'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--api', default='http://127.0.0.1:8000')
    parser.add_argument('--key', default=None, help='API key, when the server requires sign-in.')
    parser.add_argument('--workspace', default='local-retail')
    args = parser.parse_args(argv)
    headers = {'X-Project-Id': args.workspace, **({'X-API-Key': args.key} if args.key else {})}
    with httpx.Client(base_url=args.api, headers=headers, timeout=60) as client:
        health = _check(client.get('/api/v1/health'), 200)
        if health.get('mode') != 'isolated':
            print('The API is in demo mode. Start it with scripts\\mg.ps1 start (isolated mode + supervisor).', file=sys.stderr)
            return 2
        boot = _check(client.get(f'/api/v1/bootstrap?project_id={args.workspace}'), 200)
        existing = {i['title'] for i in boot.get('investigations', [])}
        print(seed_flat(client, existing))
        print(seed_relational(client, existing))
    return 0


if __name__ == '__main__':
    sys.exit(main())
