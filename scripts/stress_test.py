"""Stress test a running MetricGuard (isolated mode + supervisor) at its documented limits.

Every engine total is compared with an independent Python Decimal calculation.
Docker jobs are queued against the single supervisor, as in normal use.

    .venv\\Scripts\\python -m scripts.stress_test --api http://127.0.0.1:8020 [--rows 100000]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parent.parent
STATUSES = ['completed', 'pending', 'returned', 'cancelled', 'refunded']
REGIONS = ['north', 'south', 'east', 'west', 'central', 'islands', 'metro', 'rural']
CHANNELS = ['web', 'store', 'app']


class Stress:
    def __init__(self, api: str, workspace: str, out_dir: Path):
        self.api, self.ws, self.out = api, workspace, out_dir
        self.c = httpx.Client(base_url=api, headers={'X-Project-Id': workspace}, timeout=300)
        self.results: list[dict] = []

    # ---------- helpers ----------
    def record(self, name, ok, **details):
        self.results.append({**details, 'check': name, 'status': 'PASS' if ok else 'FAIL'})
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {json.dumps(details, default=str)[:300]}", flush=True)

    def ok(self, r, *codes):
        if r.status_code not in (codes or (200, 201, 202)):
            raise RuntimeError(f'{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}')
        return r.json()

    def wait(self, path, done, timeout=600):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            item = self.ok(self.c.get(path))
            if item.get('state') in done:
                return item
            time.sleep(0.5)
        raise TimeoutError(path)

    def upload_flat(self, contract, path, name):
        with path.open('rb') as f:
            item = self.ok(self.c.post('/api/v1/flat/snapshots', files={'file': (path.name, f, 'text/csv')},
                                       data={'contract_id': contract['id'], 'name': name}))
        if 'state' not in item:
            return item['id']
        job = self.wait(f"/api/v1/snapshot-imports/{item['id']}", ('validated', 'failed'))
        if job['state'] != 'validated':
            raise RuntimeError(f"import failed: {job.get('error')}")
        return job['snapshot_id']

    def define(self, endpoint, contract, name, sql):
        r = self.c.post(endpoint, json={'name': name, 'raw_sql': sql, 'contract_id': contract['id']})
        if r.status_code != 201:
            return None, r.json().get('error', {}).get('message')
        v = r.json()['versions'][-1]
        return (v['id'], None) if v['support_state'] == 'executable' else (None, v.get('support_reason'))

    def start(self, endpoint, base, target, snapshot, context):
        return self.ok(self.c.post(endpoint, json={'baseline_version_id': base, 'target_version_id': target, 'snapshot_id': snapshot,
                                                   'relationship': 'candidate', 'context': context},
                                   headers={'Idempotency-Key': uuid4().hex}))

    # ---------- data ----------
    def make_flat(self, rows: int):
        rng = random.Random(20261018)
        path = self.out / f'stress_flat_{rows}.csv'
        data = []
        start = date(2026, 1, 1)
        with path.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['row_id', 'amount', 'status', 'region', 'channel', 'order_day'])
            for i in range(rows):
                # Mix of magnitudes incl. large values, negatives and ~1% blank amounts.
                roll = rng.random()
                amt = None if roll < 0.01 else Decimal(rng.randint(-50000, 9_999_999_99)) / 100
                if rng.random() < 0.001:
                    amt = Decimal('9999999999999.99')
                row = (f'R{i:07d}', amt, rng.choice(STATUSES), rng.choice(REGIONS), rng.choice(CHANNELS),
                       (start + timedelta(days=rng.randint(0, 364))).isoformat())
                data.append(row)
                w.writerow([row[0], '' if amt is None else f'{amt:.2f}', *row[2:]])
        return path, data

    @staticmethod
    def total(data, pred):
        vals = [r[1] for r in data if pred(r) and r[1] is not None]
        return (sum(vals, Decimal(0)) if vals else None)

    # ---------- checks ----------
    def run(self, rows: int):
        t0 = time.monotonic()
        path, data = self.make_flat(rows)
        self.record('generate_csv', True, rows=rows, mib=round(path.stat().st_size / 2**20, 2))

        contract = self.ok(self.c.post('/api/v1/flat/contracts', json={
            'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': f'Stress {rows}',
            'row_key': 'row_id', 'measure': 'amount', 'precision': 18, 'scale': 2,
            'categories': ['status', 'region', 'channel', 'order_day'],
            'dimension_types': {'status': 'string', 'region': 'string', 'channel': 'string', 'order_day': 'date'},
            'nullable_categories': [], 'measure_meaning': 'Synthetic order amount', 'unit_label': 'INR',
            'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}))
        t = time.monotonic(); snap = self.upload_flat(contract, path, f'Stress {rows} rows')
        self.record('import_max_rows', True, seconds=round(time.monotonic() - t, 1))
        ctx = {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
               'unit_label': 'INR', 'values_comparable': True}

        cases = {
            'all': ('SELECT SUM(amount) FROM snapshot', lambda r: True),
            'completed': ("SELECT SUM(amount) FROM snapshot WHERE status = 'completed'", lambda r: r[2] == 'completed'),
            'or_tree': ("SELECT SUM(amount) FROM snapshot WHERE (status = 'pending' OR status = 'returned') AND NOT region = 'rural'",
                        lambda r: r[2] in ('pending', 'returned') and r[3] != 'rural'),
            'date_range': ("SELECT SUM(amount) FROM snapshot WHERE order_day >= DATE '2026-04-01' AND order_day < DATE '2026-07-01'",
                           lambda r: '2026-04-01' <= r[5] < '2026-07-01'),
            'in_list': ("SELECT SUM(amount) FROM snapshot WHERE region IN ('north', 'metro', 'islands') AND channel != 'store'",
                        lambda r: r[3] in ('north', 'metro', 'islands') and r[4] != 'store'),
            'empty': ("SELECT SUM(amount) FROM snapshot WHERE status = 'nonexistent'", lambda r: False),
        }
        ids = {}
        for key, (sql, _) in cases.items():
            vid, why = self.define('/api/v1/flat/definitions', contract, f'stress {key}', sql)
            if not vid:
                raise RuntimeError(f'{key} refused: {why}')
            ids[key] = vid
        expected = {k: self.total(data, p) for k, (_, p) in cases.items()}

        # 1. one large comparison, timed end to end
        t = time.monotonic()
        run = self.start('/api/v1/flat/comparisons', ids['all'], ids['completed'], snap, ctx)
        run = self.wait(f"/api/v1/comparisons/{run['id']}", ('succeeded', 'partial', 'failed'))
        res = run['result']
        good = Decimal(res['baseline_value']) == expected['all'] and Decimal(res['target_value']) == expected['completed'] \
            and Decimal(res['residual']) == 0
        self.record('compare_100k_exact', good, seconds=round(time.monotonic() - t, 1), baseline=res['baseline_value'],
                    target=res['target_value'], expected=[str(expected['all']), str(expected['completed'])])
        big_run = run

        # 1b. breakdown by status over all 100k records, exact per group
        t = time.monotonic()
        split = self.ok(self.c.get(f"/api/v1/comparisons/{run['id']}/evidence-breakdown?column=status"))
        exp_split = {}
        for r in data:
            if r[1] is not None and r[2] != 'completed':
                exp_split[r[2]] = exp_split.get(r[2], Decimal(0)) - r[1]
        got_split = {g['value']: Decimal(g['change']) for g in split.get('groups', [])}
        good = split.get('available') and all(got_split.get(k, Decimal(0)) == v for k, v in exp_split.items()) \
            and Decimal(split['total_change']) == Decimal(res['delta_target_minus_baseline'])
        self.record('breakdown_by_status_exact', bool(good), seconds=round(time.monotonic() - t, 2), groups=len(split.get('groups', [])))
        t = time.monotonic()
        page = self.ok(self.c.get(f"/api/v1/comparisons/{run['id']}/evidence?offset=500&limit=100&with_context=true"))
        by_key = {r[0]: r for r in data}
        ok_ctx = all(row['context'].get('region') == by_key[row['order_id']][3] for row in page['items'])
        self.record('record_context_columns', ok_ctx, seconds=round(time.monotonic() - t, 2))

        # 1c. COUNT(*) on the same 100k rows
        c_all, _ = self.define('/api/v1/flat/definitions', contract, 'count all', 'SELECT COUNT(*) FROM snapshot')
        c_done, _ = self.define('/api/v1/flat/definitions', contract, 'count completed', "SELECT COUNT(*) FROM snapshot WHERE status = 'completed'")
        t = time.monotonic()
        cr = self.start('/api/v1/flat/comparisons', c_all, c_done, snap, ctx)
        cr = self.wait(f"/api/v1/comparisons/{cr['id']}", ('succeeded', 'partial', 'failed'))
        exp_c = (str(rows), str(sum(1 for r in data if r[2] == 'completed')))
        got_c = (cr['result']['baseline_value'], cr['result']['target_value']) if cr.get('result') else None
        self.record('count_100k_exact', got_c == exp_c, seconds=round(time.monotonic() - t, 1), got=got_c, expected=exp_c)

        # 2. queued burst: 5 comparisons submitted at once
        t = time.monotonic()
        pairs = [('all', k) for k in ('or_tree', 'date_range', 'in_list', 'empty')] + [('completed', 'in_list')]
        started = [(a, b, self.start('/api/v1/flat/comparisons', ids[a], ids[b], snap, ctx)) for a, b in pairs]
        bad = []
        for a, b, r in started:
            done = self.wait(f"/api/v1/comparisons/{r['id']}", ('succeeded', 'partial', 'failed'))
            got = done['result']['target_value'] if done.get('result') else None
            exp = expected[b]
            if (exp is None and got is not None) or (exp is not None and (got is None or Decimal(got) != exp)):
                bad.append({'pair': f'{a}->{b}', 'got': got, 'expected': str(exp), 'state': done['state']})
        self.record('queued_burst_5_exact', not bad, seconds=round(time.monotonic() - t, 1), mismatches=bad,
                    note='includes OR tree, date range, IN list and an empty (undefined) total')

        # 3. evidence: first full verification, warm pages, filters, summary, CSV
        rid = big_run['id']
        t = time.monotonic(); s = self.ok(self.c.get(f'/api/v1/comparisons/{rid}/evidence-summary')); first = time.monotonic() - t
        exp_changed = sum(1 for r in data if r[2] != 'completed' and r[1] is not None and r[1] != 0)
        self.record('evidence_summary_exact', s['changed'] == exp_changed and s['total'] == rows,
                    first_read_seconds=round(first, 2), changed=s['changed'], expected_changed=exp_changed)
        times = []
        for off in (0, rows // 2, rows - 100, 40000, 80000):
            t = time.monotonic(); self.ok(self.c.get(f'/api/v1/comparisons/{rid}/evidence?offset={off}&limit=100')); times.append(time.monotonic() - t)
        self.record('evidence_warm_pages', max(times) < 2, max_seconds=round(max(times), 3), median_seconds=round(statistics.median(times), 3))
        t = time.monotonic(); v = self.ok(self.c.get(f'/api/v1/comparisons/{rid}/evidence?changed_only=true&order=impact&limit=100'))
        mags = [abs(Decimal(r['delta'])) for r in v['items']]
        self.record('evidence_filter_sort', v['total'] == exp_changed and mags == sorted(mags, reverse=True), seconds=round(time.monotonic() - t, 2))
        t = time.monotonic(); r = self.c.get(f'/api/v1/comparisons/{rid}/evidence.csv')
        lines = r.text.strip().splitlines(); dsum = sum((Decimal(x.split(',')[5]) for x in lines[1:] if x.split(',')[5]), Decimal(0))
        self.record('evidence_csv_full', len(lines) == rows + 1 and dsum == Decimal(res['delta_target_minus_baseline']),
                    seconds=round(time.monotonic() - t, 1), mib=round(len(r.content) / 2**20, 2))

        # 4. parallel readers: 20 threads, 200 requests across bootstrap and evidence pages
        def hit(i):
            with httpx.Client(base_url=self.api, headers={'X-Project-Id': self.ws}, timeout=120) as c:
                t = time.monotonic()
                path = '/api/v1/bootstrap?project_id=' + self.ws if i % 4 == 0 else f'/api/v1/comparisons/{rid}/evidence?offset={(i * 997) % rows}&limit=100'
                code = c.get(path).status_code
                return code, time.monotonic() - t
        t = time.monotonic()
        with ThreadPoolExecutor(20) as pool:
            out = list(pool.map(hit, range(200)))
        lat = sorted(x[1] for x in out)
        self.record('parallel_200_requests', all(x[0] == 200 for x in out), wall_seconds=round(time.monotonic() - t, 1),
                    p50=round(lat[100], 3), p95=round(lat[189], 3), max=round(lat[-1], 3),
                    errors=sum(1 for x in out if x[0] != 200))

        # 5. replay of the large saved case
        case = self.ok(self.c.post('/api/v1/investigations', json={'title': 'Stress 100k', 'run_id': rid, 'decision': 'Stress test', 'actor_label': 'stress'}))
        t = time.monotonic()
        rep = self.ok(self.c.post(f"/api/v1/investigations/{case['id']}/replays", headers={'Idempotency-Key': uuid4().hex}))
        rep = self.wait(f"/api/v1/comparisons/{rep['id']}", ('succeeded', 'partial', 'failed'))
        self.record('replay_100k_identical', rep['result']['evidence_artifact']['sha256'] == res['evidence_artifact']['sha256'],
                    seconds=round(time.monotonic() - t, 1))

        # 6. limits and refusals
        too_many = self.out / 'too_many_rows.csv'
        with too_many.open('w', newline='') as f:
            f.write('row_id,amount,status,region,channel,order_day\n')
            for i in range(rows + 1):
                f.write(f'X{i},1.00,completed,north,web,2026-01-01\n')
        try:
            self.upload_flat(contract, too_many, 'too many rows'); self.record('reject_over_row_limit', False)
        except RuntimeError as e:
            self.record('reject_over_row_limit', True, reason=str(e)[:160])
        big = io.BytesIO(b'row_id,amount\n' + b'A,1.00\n' * (4 * 1024 * 1024))  # ~28 MiB
        r = self.c.post('/api/v1/flat/snapshots', files={'file': ('big.csv', big, 'text/csv')}, data={'contract_id': contract['id'], 'name': 'big'})
        self.record('reject_over_25mib', r.status_code == 413, http_status=r.status_code)
        leaves = ' OR '.join(f"region = '{REGIONS[i % 8]}'" for i in range(17))
        vid, why = self.define('/api/v1/flat/definitions', contract, 'too many leaves', f'SELECT SUM(amount) FROM snapshot WHERE {leaves}')
        self.record('refuse_17_filter_leaves', vid is None, reason=(why or '')[:160])
        for name, sql in [('avg', 'SELECT AVG(amount) FROM snapshot'), ('group_by', 'SELECT SUM(amount) FROM snapshot GROUP BY region'),
                          ('subquery', 'SELECT SUM(amount) FROM (SELECT * FROM snapshot)'), ('injection', "SELECT SUM(amount) FROM snapshot; DROP TABLE snapshot")]:
            vid, why = self.define('/api/v1/flat/definitions', contract, f'refuse {name}', sql)
            self.record(f'refuse_{name}', vid is None, reason=(why or '')[:120])

        # 7. two tables at scale: 100k facts x 1,000 lookup rows
        self.relational(rows)
        self.record('total_wall_clock', True, minutes=round((time.monotonic() - t0) / 60, 1))

    def relational(self, rows):
        rng = random.Random(7)
        fact, dim = self.out / 'stress_fact.csv', self.out / 'stress_dim.csv'
        segments = {}
        with dim.open('w', newline='') as f:
            f.write('shop_id,segment\n')
            for i in range(1000):
                segments[f'S{i:04d}'] = rng.choice(['north', 'south', 'east', 'west'])
                f.write(f'S{i:04d},{segments[f"S{i:04d}"]}\n')
        facts = []
        with fact.open('w', newline='') as f:
            f.write('sale_id,shop_id,amount,kind\n')
            for i in range(rows):
                shop = '' if rng.random() < 0.01 else ('S9999' if rng.random() < 0.01 else f'S{rng.randint(0, 999):04d}')
                amt = Decimal(rng.randint(1, 500000)) / 100
                facts.append((shop, amt)); f.write(f'F{i:07d},{shop},{amt:.2f},{rng.choice(["web", "store"])}\n')
        contract = self.ok(self.c.post('/api/v1/relational/contracts', json={
            'version': 1, 'mode': 'relational_snapshot', 'policy': 'relational-snapshot-v1', 'name': 'Stress two tables',
            'fact': {'table': 'sales', 'key': 'sale_id', 'grain': 'One sale', 'categories': ['kind']},
            'dimension': {'table': 'shops', 'key': 'shop_id', 'grain': 'One shop', 'categories': ['segment']},
            'relationship': {'fact_foreign_key': 'shop_id', 'dimension_key': 'shop_id', 'cardinality': 'many_to_one', 'missing_dimension_policy': 'preserve_and_report'},
            'measure': {'column': 'amount', 'precision': 18, 'scale': 2}, 'measure_meaning': 'Sale amount', 'unit_label': 'INR',
            'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}))
        t = time.monotonic()
        with fact.open('rb') as a, dim.open('rb') as b:
            item = self.ok(self.c.post('/api/v1/relational/snapshots', files={'fact_file': ('sales.csv', a, 'text/csv'), 'dimension_file': ('shops.csv', b, 'text/csv')},
                                       data={'contract_id': contract['id'], 'name': 'Stress two tables'}))
        snap = item['id'] if 'state' not in item else self.wait(f"/api/v1/snapshot-imports/{item['id']}", ('validated', 'failed'))['snapshot_id']
        self.record('relational_import_100k', bool(snap), seconds=round(time.monotonic() - t, 1))
        base, _ = self.define('/api/v1/relational/definitions', contract, 'all sales', 'SELECT SUM(f.amount) FROM sales f LEFT JOIN shops d ON f.shop_id = d.shop_id')
        targ, _ = self.define('/api/v1/relational/definitions', contract, 'north shops', "SELECT SUM(f.amount) FROM sales f INNER JOIN shops d ON f.shop_id = d.shop_id WHERE d.segment = 'north'")
        ctx = {'mode': 'relational_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'], 'unit_label': 'INR', 'values_comparable': True}
        t = time.monotonic()
        run = self.start('/api/v1/relational/comparisons', base, targ, snap, ctx)
        run = self.wait(f"/api/v1/comparisons/{run['id']}", ('succeeded', 'partial', 'failed'))
        exp_all = sum((a for _, a in facts), Decimal(0))
        exp_north = sum((a for s, a in facts if segments.get(s) == 'north'), Decimal(0))
        res = run['result']
        self.record('relational_compare_100k_exact', Decimal(res['baseline_value']) == exp_all and Decimal(res['target_value']) == exp_north,
                    seconds=round(time.monotonic() - t, 1), baseline=res['baseline_value'], target=res['target_value'])
        t = time.monotonic(); r = self.c.get(f"/api/v1/comparisons/{run['id']}/join-evidence?offset=0&limit=100")
        self.record('relational_join_evidence_page', r.status_code == 200, seconds=round(time.monotonic() - t, 2))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--api', default='http://127.0.0.1:8020')
    p.add_argument('--workspace', default='local-retail')
    p.add_argument('--rows', type=int, default=100000)
    p.add_argument('--out', default='data/evaluation/stress')
    a = p.parse_args(argv)
    out = ROOT / a.out; out.mkdir(parents=True, exist_ok=True)
    s = Stress(a.api, a.workspace, out)
    try:
        s.run(a.rows)
    except Exception as e:  # keep partial results
        s.record('harness_error', False, error=f'{type(e).__name__}: {e}'[:400])
    report = {'status': 'PASS' if all(r['status'] == 'PASS' for r in s.results) else 'FAIL', 'rows': a.rows, 'checks': s.results}
    (out / 'stress_report.json').write_text(json.dumps(report, indent=2, default=str) + '\n', encoding='utf-8')
    print('REPORT', report['status'])
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
