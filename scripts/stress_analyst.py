"""Analyst-scale stress test of a running MetricGuard (isolated mode + supervisor).

Covers what a senior analyst would lean on: a 1M-row single table, every calculation kind
(SUM, COUNT(*), COUNT(DISTINCT), AVG, rate, share, margin), a fact table with three lookups,
a queued burst with parallel readers, replay, and malformed or oversized inputs. Each engine
value is checked against an independent Python calculation (integers and Fractions).

    .venv\\Scripts\\python -m scripts.stress_analyst --api http://127.0.0.1:8010 [--rows 1000000]
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
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parent.parent
STATUSES = ['paid', 'pending', 'refunded', 'cancelled']
REGIONS = ['north', 'south', 'east', 'west', 'central']
DONE = ('succeeded', 'partial', 'failed')


def cents(text: str) -> int:
    whole, _, frac = text.partition('.')
    sign = -1 if whole.startswith('-') else 1
    return sign * (abs(int(whole or '0')) * 100 + int((frac + '00')[:2]))


class Harness:
    def __init__(self, api: str, workspace: str, out: Path):
        self.api, self.ws, self.out = api, workspace, out
        self.c = httpx.Client(base_url=api, headers={'X-Project-Id': workspace}, timeout=900)
        self.results: list[dict] = []

    def record(self, name, ok, **details):
        self.results.append({'check': name, 'status': 'PASS' if ok else 'FAIL', **details})
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {json.dumps(details, default=str)[:400]}", flush=True)

    def ok(self, r, *codes):
        if r.status_code not in (codes or (200, 201, 202)):
            raise RuntimeError(f'{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}')
        return r.json()

    def wait(self, path, done, timeout=1800):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            item = self.ok(self.c.get(path))
            if item.get('state') in done:
                return item
            time.sleep(0.3)
        raise TimeoutError(path)

    def upload_flat(self, contract_id, path, name):
        """Returns (snapshot_id, None) or (None, error message)."""
        with path.open('rb') as f:
            r = self.c.post('/api/v1/flat/snapshots', files={'file': (path.name, f, 'text/csv')},
                            data={'contract_id': contract_id, 'name': name})
        if r.status_code not in (200, 201, 202):
            return None, f'HTTP {r.status_code}: {r.text[:200]}'
        item = r.json()
        if 'state' not in item:
            return item['id'], None
        job = self.wait(f"/api/v1/snapshot-imports/{item['id']}", ('validated', 'failed'))
        if job['state'] != 'validated':
            return None, json.dumps(job.get('error'))[:240]
        return job['snapshot_id'], None

    def define(self, endpoint, contract_id, name, sql):
        r = self.c.post(endpoint, json={'name': name, 'raw_sql': sql, 'contract_id': contract_id})
        if r.status_code != 201:
            return None, r.json().get('error', {}).get('message')
        v = r.json()['versions'][-1]
        return (v['id'], None) if v['support_state'] == 'executable' else (None, v.get('support_reason'))

    def start(self, endpoint, base, target, snapshot, context):
        return self.ok(self.c.post(endpoint, json={'baseline_version_id': base, 'target_version_id': target, 'snapshot_id': snapshot,
                                                   'relationship': 'candidate', 'context': context},
                                   headers={'Idempotency-Key': uuid4().hex}))

    def compare(self, contract, snap, base_sql, target_sql, label, endpoint='flat'):
        b, why_b = self.define(f'/api/v1/{endpoint}/definitions', contract['id'], f'{label} current', base_sql)
        t, why_t = self.define(f'/api/v1/{endpoint}/definitions', contract['id'], f'{label} proposed', target_sql)
        if not (b and t):
            raise RuntimeError(f'{label} refused: {why_b or why_t}')
        mode = 'relational_snapshot' if endpoint == 'relational' else 'flat_snapshot'
        ctx = {'mode': mode, 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
               'unit_label': contract['unit_label'], 'values_comparable': True}
        t0 = time.monotonic()
        run = self.start(f'/api/v1/{endpoint}/comparisons', b, t, snap, ctx)
        run = self.wait(f"/api/v1/comparisons/{run['id']}", DONE)
        return run, round(time.monotonic() - t0, 1)

    def csv_rows(self, run_id):
        t0 = time.monotonic()
        r = self.c.get(f'/api/v1/comparisons/{run_id}/evidence.csv')
        rows = list(csv.DictReader(io.StringIO(r.text)))
        return r.status_code, rows, round(time.monotonic() - t0, 1), round(len(r.content) / 2**20, 1)

    # ------------------------------------------------------------------ data
    def make_single(self, rows):
        rng = random.Random(20261018)
        path = self.out / f'analyst_single_{rows}.csv'
        data = []
        with path.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['order_id', 'amount', 'customer_id', 'status', 'region', 'converted'])
            for i in range(rows):
                amt = None if rng.random() < 0.01 else rng.randint(-5_000, 2_000_000)
                cust = '' if rng.random() < 0.005 else f'C{rng.randint(0, rows // 20):06d}'
                row = (f'O{i:07d}', amt, cust, rng.choice(STATUSES), rng.choice(REGIONS), rng.random() < 0.3)
                data.append(row)
                w.writerow([row[0], '' if amt is None else f"{'-' if amt < 0 else ''}{abs(amt) // 100}.{abs(amt) % 100:02d}",
                            cust, row[3], row[4], 'true' if row[5] else 'false'])
        return path, data

    def make_margin(self, rows):
        rng = random.Random(42)
        path = self.out / f'analyst_margin_{rows}.csv'
        data = []
        with path.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['line_id', 'revenue', 'cost', 'region', 'status'])
            for i in range(rows):
                rev = rng.randint(100, 500_000)
                cost = None if rng.random() < 0.02 else rng.randint(50, rev)
                row = (f'L{i:07d}', rev, cost, rng.choice(REGIONS), rng.choice(STATUSES))
                data.append(row)
                w.writerow([row[0], f'{rev // 100}.{rev % 100:02d}', '' if cost is None else f'{cost // 100}.{cost % 100:02d}', row[3], row[4]])
        return path, data

    # ------------------------------------------------------------------ checks
    def single_table(self, rows):
        path, data = self.make_single(rows)
        self.record('generate_single_table', True, rows=rows, mib=round(path.stat().st_size / 2**20, 1))
        contract = self.ok(self.c.post('/api/v1/flat/contracts', json={
            'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': f'Analyst stress {rows}',
            'row_key': 'order_id', 'measure': 'amount', 'precision': 18, 'scale': 2,
            'categories': ['customer_id', 'status', 'region', 'converted'],
            'dimension_types': {'customer_id': 'string', 'status': 'string', 'region': 'string', 'converted': 'boolean'},
            'nullable_categories': ['customer_id'], 'measure_meaning': 'Order value', 'unit_label': 'INR',
            'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}))
        t0 = time.monotonic(); snap, err = self.upload_flat(contract['id'], path, f'Analyst stress {rows}')
        self.record('import_single_table', bool(snap), seconds=round(time.monotonic() - t0, 1), error=err)
        if not snap:
            return
        amts = lambda pred: [r[1] for r in data if pred(r) and r[1] is not None]

        # A broad change (all orders vs paid orders) changes most records.
        run, secs = self.compare(contract, snap, 'SELECT SUM(amount) FROM snapshot',
                                 "SELECT SUM(amount) FROM snapshot WHERE status = 'paid'", 'broad')
        changed = sum(1 for r in data if r[3] != 'paid')
        res = run['result'] or {}
        exp = (sum(amts(lambda r: True)), sum(amts(lambda r: r[3] == 'paid')))
        ok = run['state'] == 'succeeded' and (cents(res.get('baseline_value') or '0'), cents(res.get('target_value') or '0')) == exp \
            and cents(res.get('residual') or '1') == 0
        self.record('broad_change_750k_records_exact', ok, changed_records=changed, state=run['state'],
                    error=(run.get('error') or {}).get('message'), seconds=secs)
        if ok:
            code, ev, csecs, mib = self.csv_rows(run['id'])
            listed = sum(cents(r['change']) for r in ev if r['change'])
            self.record('broad_change_listed_records_add_up', code == 200 and listed == exp[1] - exp[0],
                        listed=len(ev), seconds=csecs, mib=mib)

        # The remaining checks use a narrower change that stays under the listing cap:
        # current = paid orders, proposed = paid orders outside one region.
        BASE = lambda r: r[3] == 'paid'
        paid = lambda r: r[3] == 'paid' and r[4] != 'west'
        W_BASE, W_TARGET = "WHERE status = 'paid'", "WHERE status = 'paid' AND region <> 'west'"

        # SUM
        run, secs = self.compare(contract, snap, f'SELECT SUM(amount) FROM snapshot {W_BASE}',
                                 f'SELECT SUM(amount) FROM snapshot {W_TARGET}', 'sum')
        res = run['result']
        exp = (sum(amts(BASE)), sum(amts(paid)))
        self.record('sum_exact', (cents(res['baseline_value']), cents(res['target_value'])) == exp and cents(res['residual']) == 0,
                    seconds=secs, state=run['state'], coverage=res.get('evidence_coverage'))
        sum_run = run
        code, ev, csecs, mib = self.csv_rows(run['id'])
        listed = sum(cents(r['change']) for r in ev if r['change'])
        self.record('sum_listed_records_add_up', code == 200 and listed == exp[1] - exp[0], seconds=csecs, mib=mib, listed=len(ev))
        t0 = time.monotonic()
        split = self.ok(self.c.get(f"/api/v1/comparisons/{run['id']}/evidence-breakdown?column=region"))
        exp_split = {}
        for r in data:
            if r[1] is not None and BASE(r) and not paid(r):
                exp_split[r[4]] = exp_split.get(r[4], 0) - r[1]
        got = {g['value']: cents(g['change']) for g in split.get('groups', [])}
        self.record('sum_breakdown_by_region_exact', got == exp_split, seconds=round(time.monotonic() - t0, 1))
        t0 = time.monotonic()
        lat = []
        for off in (0, 5000, 100000, 300000):
            s = time.monotonic(); self.ok(self.c.get(f"/api/v1/comparisons/{run['id']}/evidence?offset={off}&limit=100&with_context=true")); lat.append(time.monotonic() - s)
        self.record('evidence_pages_with_context', max(lat) < 3, max_seconds=round(max(lat), 2), median=round(statistics.median(lat), 2))

        self.snapshot_change(contract, snap, data, sum_run)

        # COUNT(*)
        run, secs = self.compare(contract, snap, f'SELECT COUNT(*) FROM snapshot {W_BASE}',
                                 f'SELECT COUNT(*) FROM snapshot {W_TARGET}', 'count')
        res = run['result'] or {}
        exp = (str(sum(1 for r in data if BASE(r))), str(sum(1 for r in data if paid(r))))
        self.record('count_exact', (res.get('baseline_value'), res.get('target_value')) == exp, seconds=secs, got=[res.get('baseline_value'), res.get('target_value')])

        # COUNT(DISTINCT)
        run, secs = self.compare(contract, snap, f'SELECT COUNT(DISTINCT customer_id) FROM snapshot {W_BASE}',
                                 f'SELECT COUNT(DISTINCT customer_id) FROM snapshot {W_TARGET}', 'distinct')
        res = run['result'] or {}
        exp = (len({r[2] for r in data if r[2] and BASE(r)}), len({r[2] for r in data if r[2] and paid(r)}))
        self.record('distinct_exact', (res.get('baseline_value'), res.get('target_value')) == tuple(map(str, exp)),
                    seconds=secs, state=run['state'], error=(run.get('error') or {}).get('message'),
                    got=[res.get('baseline_value'), res.get('target_value')], expected=exp)
        distinct_run = run
        if res:
            code, ev, csecs, mib = self.csv_rows(run['id'])
            self.record('distinct_values_add_up', code == 200 and sum(int(r['change'] or 0) for r in ev) == exp[1] - exp[0],
                        seconds=csecs, values=len(ev))
            sample = next((r['record'] for r in ev if r['change'] == '-1'), None)
            if sample:
                t0 = time.monotonic(); er = self.c.get(f"/api/v1/comparisons/{run['id']}/entity-records", params={'entity': sample})
                exp_n = sum(1 for r in data if r[2] == sample)
                self.record('distinct_entity_records', er.status_code == 200 and er.json().get('total') == exp_n,
                            seconds=round(time.monotonic() - t0, 2), entity=sample)

        # AVG, rate, share: exact fractions
        def frac(nums):
            return Fraction(sum(nums), 100 * len(nums)) if nums else None
        ratio_cases = [
            ('avg', f'SELECT AVG(amount) FROM snapshot {W_BASE}', f'SELECT AVG(amount) FROM snapshot {W_TARGET}',
             (frac(amts(BASE)), frac(amts(paid)))),
            ('rate', f'SELECT COUNT(*) FILTER (WHERE converted = true) / COUNT(*) FROM snapshot {W_BASE}',
             f'SELECT COUNT(*) FILTER (WHERE converted = true) / COUNT(*) FROM snapshot {W_TARGET}',
             (Fraction(sum(1 for r in data if r[5] and BASE(r)), sum(1 for r in data if BASE(r))),
              Fraction(sum(1 for r in data if r[5] and paid(r)), sum(1 for r in data if paid(r))))),
            ('share', f"SELECT SUM(amount) FILTER (WHERE region = 'north') / SUM(amount) FROM snapshot {W_BASE}",
             f"SELECT SUM(amount) FILTER (WHERE region = 'north') / SUM(amount) FROM snapshot {W_TARGET}",
             (Fraction(sum(amts(lambda r: r[4] == 'north' and BASE(r))), sum(amts(BASE))),
              Fraction(sum(amts(lambda r: r[4] == 'north' and paid(r))), sum(amts(paid))))),
        ]
        ratio_runs = []
        for label, b, t, exp in ratio_cases:
            run, secs = self.compare(contract, snap, b, t, label)
            ratio = (run['result'] or {}).get('ratio') or {}
            got = tuple(Fraction(ratio[s]['exact']) if ratio.get(s, {}).get('exact') else None for s in ('baseline', 'target'))
            self.record(f'{label}_exact_fraction', got == exp, seconds=secs, state=run['state'], error=(run.get('error') or {}).get('message'), display=[run['result'] and run['result']['baseline_value'], run['result'] and run['result']['target_value']])
            ratio_runs.append((label, run, exp))
        label, run, exp = ratio_runs[0]
        code, ev, csecs, mib = self.csv_rows(run['id']) if run.get('result') else (0, [], 0, 0)
        effects = sum((Fraction(r['effect_exact']) for r in ev if r['effect_exact']), Fraction(0))
        self.record('avg_record_effects_sum_to_gap', code == 200 and effects == exp[1] - exp[0], seconds=csecs, listed=len(ev))

        # replay of a 1M-row distinct case and a ratio case
        for label, run in (('distinct', distinct_run), ('avg', ratio_runs[0][1]), ('sum', sum_run)):
            if not run.get('result'):
                continue
            case = self.ok(self.c.post('/api/v1/investigations', json={'title': f'Stress {label} {rows}', 'run_id': run['id'],
                                                                       'decision': 'stress', 'actor_label': 'stress'}))
            t0 = time.monotonic()
            rep = self.ok(self.c.post(f"/api/v1/investigations/{case['id']}/replays", headers={'Idempotency-Key': uuid4().hex}))
            rep = self.wait(f"/api/v1/comparisons/{rep['id']}", DONE)
            same = (rep.get('result') or {}).get('evidence_artifact', {}).get('sha256') == run['result']['evidence_artifact']['sha256']
            self.record(f'replay_{label}_identical', same, seconds=round(time.monotonic() - t0, 1))

        # queued burst + parallel readers while it runs
        pairs = [(f"SELECT SUM(amount) FROM snapshot WHERE region = '{g}'", f"SELECT SUM(amount) FROM snapshot WHERE region = '{g}' AND status <> 'paid'") for g in REGIONS] + \
                [(f"SELECT COUNT(*) FROM snapshot WHERE status = '{s}'", f"SELECT COUNT(*) FROM snapshot WHERE status = '{s}' AND converted = true") for s in STATUSES[:3]]
        ids = []
        for i, (b, t) in enumerate(pairs):
            bid, _ = self.define('/api/v1/flat/definitions', contract['id'], f'burst {i} b', b)
            tid, _ = self.define('/api/v1/flat/definitions', contract['id'], f'burst {i} t', t)
            ids.append((bid, tid, t))
        ctx = {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
               'unit_label': 'INR', 'values_comparable': True}
        t0 = time.monotonic()
        started = [(self.start('/api/v1/flat/comparisons', b, t, snap, ctx), sql) for b, t, sql in ids]

        def hit(i):
            with httpx.Client(base_url=self.api, headers={'X-Project-Id': self.ws}, timeout=120) as c:
                s = time.monotonic()
                path = '/api/v1/bootstrap?project_id=' + self.ws if i % 3 == 0 else f"/api/v1/comparisons/{sum_run['id']}/evidence?offset={(i * 997) % 100000}&limit=100"
                return c.get(path).status_code, time.monotonic() - s
        with ThreadPoolExecutor(20) as pool:
            reads = list(pool.map(hit, range(200)))
        bad = []
        for r, sql in started:
            done = self.wait(f"/api/v1/comparisons/{r['id']}", DONE)
            want = None
            for g in REGIONS:
                if f"region = '{g}'" in sql and 'SUM' in sql:
                    want = str(sum(amts(lambda x, g=g: x[4] == g and x[3] != 'paid')))
            for s in STATUSES:
                if f"status = '{s}'" in sql and 'COUNT' in sql:
                    want = str(sum(1 for x in data if x[3] == s and x[5]))
            got = (done.get('result') or {}).get('target_value')
            got_n = str(cents(got)) if got and 'SUM' in sql else got
            if done['state'] != 'succeeded' or got_n != want:
                bad.append({'sql': sql, 'state': done['state'], 'got': got, 'want': want})
        lat = sorted(x[1] for x in reads)
        self.record('burst_8_comparisons_exact', not bad, seconds=round(time.monotonic() - t0, 1), mismatches=bad)
        self.record('parallel_200_reads_during_burst', all(x[0] == 200 for x in reads), p50=round(lat[100], 2),
                    p95=round(lat[189], 2), max=round(lat[-1], 2), errors=sum(1 for x in reads if x[0] != 200))

        # a duplicate key far beyond the in-memory duplicate window
        dup = self.out / 'analyst_duplicate_late.csv'
        with path.open('rb') as src, dup.open('wb') as dst:
            dst.write(src.read())
            dst.write(b'O0000005,1.00,C000001,paid,north,false\n')
        t0 = time.monotonic(); s, err = self.upload_flat(contract['id'], dup, 'duplicate late')
        self.record('reject_duplicate_key_after_1m_rows', s is None and 'duplicate' in (err or '').lower(),
                    seconds=round(time.monotonic() - t0, 1), error=err)

    def snapshot_change(self, contract, snap, data, sum_run):
        """Same SUM definition on a second load: 2% of amounts edited, 1% of rows removed, 5,000 new rows."""
        rng = random.Random(7)
        later = []
        for r in data:
            roll = rng.random()
            if roll < 0.01:
                continue
            if roll < 0.03 and r[1] is not None:
                r = (r[0], r[1] + rng.randint(-10_000, 10_000), *r[2:])
            later.append(r)
        for i in range(5_000):
            later.append((f'N{i:07d}', rng.randint(1, 500_000), f'C{i:06d}', 'paid', rng.choice(REGIONS), False))
        p2 = self.out / f'analyst_single_{len(data)}_load2.csv'
        with p2.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['order_id', 'amount', 'customer_id', 'status', 'region', 'converted'])
            for r in later:
                amt = r[1]
                w.writerow([r[0], '' if amt is None else f"{'-' if amt < 0 else ''}{abs(amt) // 100}.{abs(amt) % 100:02d}",
                            r[2], r[3], r[4], 'true' if r[5] else 'false'])
        t0 = time.monotonic(); snap2, err = self.upload_flat(contract['id'], p2, 'Analyst stress load 2')
        self.record('import_second_load', bool(snap2), seconds=round(time.monotonic() - t0, 1), error=err)
        if not snap2:
            return
        ctx = {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
               'unit_label': contract['unit_label'], 'values_comparable': True}
        body = {'version_id': sum_run['baseline_version_id'], 'baseline_snapshot_id': snap, 'target_snapshot_id': snap2, 'context': ctx}
        t0 = time.monotonic()
        r = self.c.post('/api/v1/flat/snapshot-changes', json=body, headers={'Idempotency-Key': uuid4().hex})
        if r.status_code != 202:
            self.record('snapshot_change_exact', False, http_status=r.status_code, error=r.text[:300]); return
        run = self.wait(f"/api/v1/comparisons/{r.json()['id']}", DONE)
        res = run['result'] or {}
        paid_sum = lambda rows: sum(x[1] for x in rows if x[3] == 'paid' and x[1] is not None)
        exp = (paid_sum(data), paid_sum(later))
        got = (cents(res.get('baseline_value') or '0'), cents(res.get('target_value') or '0'))
        self.record('snapshot_change_exact', run['state'] == 'succeeded' and got == exp and cents(res.get('residual') or '1') == 0,
                    seconds=round(time.monotonic() - t0, 1), state=run['state'], error=(run.get('error') or {}).get('message'))
        if res:
            code, ev, csecs, mib = self.csv_rows(run['id'])
            listed = sum(cents(r['change']) for r in ev if r.get('change'))
            self.record('snapshot_change_records_add_up', code == 200 and listed == exp[1] - exp[0], listed=len(ev), seconds=csecs,
                        columns=list(ev[0].keys()) if ev else [])
        same = self.c.post('/api/v1/flat/snapshot-changes', json={**body, 'target_snapshot_id': snap}, headers={'Idempotency-Key': uuid4().hex})
        self.record('snapshot_change_refuses_same_load', same.status_code == 422, http_status=same.status_code)

    def margin(self, rows):
        path, data = self.make_margin(rows)
        contract = self.ok(self.c.post('/api/v1/flat/contracts', json={
            'version': 8, 'mode': 'flat_snapshot', 'name': f'Margin stress {rows}', 'row_key': 'line_id',
            'decimal_columns': [{'name': 'revenue', 'precision': 14, 'scale': 2, 'unit_label': 'INR'},
                                {'name': 'cost', 'precision': 14, 'scale': 2, 'unit_label': 'INR'}],
            'scale': 2, 'categories': ['region', 'status'], 'dimension_types': {'region': 'string', 'status': 'string'},
            'nullable_categories': [], 'measure_meaning': 'Order economics', 'unit_label': 'INR', 'values_comparable': True,
            'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable',
            'measure_policy': 'flat-multi-measure-v1', 'predicate_policy': 'flat-boolean-v1'}))
        t0 = time.monotonic(); snap, err = self.upload_flat(contract['id'], path, f'Margin stress {rows}')
        self.record('import_several_amounts', bool(snap), rows=rows, seconds=round(time.monotonic() - t0, 1), error=err)
        if not snap:
            return
        run, secs = self.compare(contract, snap, "SELECT SUM(revenue - cost) / SUM(revenue) FROM snapshot WHERE status = 'paid'",
                                 "SELECT SUM(revenue - cost) / SUM(revenue) FROM snapshot WHERE status = 'paid' AND region <> 'west'", 'margin')
        def m(pred):
            top = sum(r[1] - r[2] for r in data if pred(r) and r[2] is not None)
            return Fraction(top, sum(r[1] for r in data if pred(r)))
        exp = (m(lambda r: r[4] == 'paid'), m(lambda r: r[4] == 'paid' and r[3] != 'west'))
        ratio = (run['result'] or {}).get('ratio') or {}
        got = tuple(Fraction(ratio[s]['exact']) if ratio.get(s, {}).get('exact') else None for s in ('baseline', 'target'))
        self.record('margin_exact_fraction', got == exp, seconds=secs, state=run['state'], error=(run.get('error') or {}).get('message'),
                    display=[(run['result'] or {}).get('baseline_value'), (run['result'] or {}).get('target_value')])
        b, why = self.define('/api/v1/flat/definitions', contract['id'], 'distinct on v8', 'SELECT COUNT(DISTINCT region) FROM snapshot')
        self.record('note_distinct_on_several_amounts', True, supported=bool(b), reason=(why or '')[:200])

    def star(self, rows):
        rng = random.Random(3)
        lookups = {'customer': ('customers', 'customer_id', 'segment', ['retail', 'trade', 'vip'], 20_000),
                   'product': ('products', 'product_id', 'category', ['books', 'toys', 'food', 'tech'], 5_000),
                   'store': ('stores', 'store_id', 'city', ['delhi', 'mumbai', 'pune', 'goa'], 200)}
        files, maps = [], {}
        for role, (table, key, cat, values, n) in lookups.items():
            buf = io.StringIO(); w = csv.writer(buf); w.writerow([key, cat]); maps[role] = {}
            for i in range(n):
                k = f'{role[0].upper()}{i:05d}'; maps[role][k] = rng.choice(values); w.writerow([k, maps[role][k]])
            files.append((f'lookup_{role}', (f'{table}.csv', buf.getvalue().encode(), 'text/csv')))
        buf = io.StringIO(); w = csv.writer(buf); w.writerow(['order_id', 'customer_id', 'product_id', 'store_id', 'amount', 'status'])
        facts = []
        for i in range(rows):
            fk = {role: ('' if rng.random() < 0.01 else (f'{role[0].upper()}99999' if rng.random() < 0.01 else
                         f'{role[0].upper()}{rng.randint(0, lookups[role][4] - 1):05d}')) for role in lookups}
            amt = rng.randint(1, 1_000_000); st = rng.choice(STATUSES)
            facts.append((fk, amt, st)); w.writerow([f'F{i:06d}', fk['customer'], fk['product'], fk['store'], f'{amt // 100}.{amt % 100:02d}', st])
        files.insert(0, ('fact_file', ('orders.csv', buf.getvalue().encode(), 'text/csv')))
        contract = self.ok(self.c.post('/api/v1/relational/contracts', json={
            'version': 2, 'mode': 'relational_snapshot', 'policy': 'relational-star-v1', 'name': 'Star stress',
            'fact': {'table': 'orders', 'key': 'order_id', 'grain': 'one row per order', 'categories': ['status']},
            'dimensions': [{'role': role, 'table': t, 'key': k, 'grain': f'one row per {role}', 'categories': [c], 'fact_foreign_key': k}
                           for role, (t, k, c, _, _) in lookups.items()],
            'measure': {'column': 'amount', 'precision': 14, 'scale': 2}, 'measure_meaning': 'Order amount',
            'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}))
        t0 = time.monotonic()
        item = self.ok(self.c.post('/api/v1/relational/snapshots', data={'contract_id': contract['id'], 'name': 'Star stress'}, files=files))
        snap = item['id'] if 'state' not in item else self.wait(f"/api/v1/snapshot-imports/{item['id']}", ('validated', 'failed')).get('snapshot_id')
        self.record('star_import_100k_x3_lookups', bool(snap), seconds=round(time.monotonic() - t0, 1))
        if not snap:
            return
        run, secs = self.compare(contract, snap, 'SELECT SUM(o.amount) FROM orders o',
                                 'SELECT SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.customer_id '
                                 'LEFT JOIN products p ON o.product_id = p.product_id JOIN stores s ON o.store_id = s.store_id '
                                 "WHERE c.segment = 'vip' AND s.city IN ('delhi', 'mumbai', 'pune')", 'star', endpoint='relational')
        exp_b = sum(a for _, a, _ in facts)
        exp_t = sum(a for fk, a, _ in facts if maps['customer'].get(fk['customer']) == 'vip'
                    and fk['store'] in maps['store'] and maps['store'][fk['store']] != 'goa')
        res = run['result'] or {}
        self.record('star_three_lookups_exact', (cents(res.get('baseline_value', '0')), cents(res.get('target_value', '0'))) == (exp_b, exp_t),
                    seconds=secs, state=run['state'])
        over = io.StringIO(); over.write('order_id,customer_id,product_id,store_id,amount,status\n')
        for i in range(100_001):
            over.write(f'X{i},,,,1.00,paid\n')
        big = [('fact_file', ('orders.csv', over.getvalue().encode(), 'text/csv'))] + files[1:]
        r = self.c.post('/api/v1/relational/snapshots', data={'contract_id': contract['id'], 'name': 'too many'}, files=big)
        refused = r.status_code >= 400 or (r.json().get('state') and self.wait(f"/api/v1/snapshot-imports/{r.json()['id']}", ('validated', 'failed'))['state'] == 'failed')
        self.record('star_v2_refuses_over_100k_facts', bool(refused), http_status=r.status_code)

    def star_large(self, rows):
        """Prepared (v3) star contract: many more facts than the v2 limit, two lookups."""
        rng = random.Random(11)
        cust = {f'C{i:06d}': rng.choice(['retail', 'trade', 'vip']) for i in range(50_000)}
        store = {f'S{i:04d}': rng.choice(['delhi', 'mumbai', 'pune', 'goa']) for i in range(300)}
        d = self.out / f'star_{rows}'; d.mkdir(exist_ok=True)
        with (d / 'customers.csv').open('w', newline='') as f:
            w = csv.writer(f); w.writerow(['customer_id', 'segment']); w.writerows(cust.items())
        with (d / 'stores.csv').open('w', newline='') as f:
            w = csv.writer(f); w.writerow(['store_id', 'city']); w.writerows(store.items())
        facts = []
        ck, sk = list(cust), list(store)
        with (d / 'orders.csv').open('w', newline='') as f:
            w = csv.writer(f); w.writerow(['order_id', 'customer_id', 'store_id', 'amount', 'status'])
            for i in range(rows):
                c = '' if rng.random() < 0.01 else rng.choice(ck); s = rng.choice(sk)
                amt = rng.randint(1, 1_000_000); st = rng.choice(STATUSES)
                facts.append((c, s, amt, st)); w.writerow([f'F{i:07d}', c, s, f'{amt // 100}.{amt % 100:02d}', st])
        contract = self.ok(self.c.post('/api/v1/relational/contracts', json={
            'version': 3, 'mode': 'relational_snapshot', 'policy': 'relational-star-prepared-v1', 'name': f'Star large {rows}',
            'fact': {'table': 'orders', 'key': 'order_id', 'grain': 'one row per order', 'categories': ['status']},
            'dimensions': [{'role': 'customer', 'table': 'customers', 'key': 'customer_id', 'grain': 'one row per customer', 'categories': ['segment'], 'fact_foreign_key': 'customer_id'},
                           {'role': 'store', 'table': 'stores', 'key': 'store_id', 'grain': 'one row per store', 'categories': ['city'], 'fact_foreign_key': 'store_id'}],
            'measure': {'column': 'amount', 'precision': 14, 'scale': 2}, 'measure_meaning': 'Order amount',
            'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}))
        t0 = time.monotonic()
        with (d / 'orders.csv').open('rb') as fo, (d / 'customers.csv').open('rb') as fc, (d / 'stores.csv').open('rb') as fs:
            r = self.c.post('/api/v1/relational/snapshots', data={'contract_id': contract['id'], 'name': f'Star large {rows}'},
                            files=[('fact_file', ('orders.csv', fo, 'text/csv')), ('lookup_customer', ('customers.csv', fc, 'text/csv')),
                                   ('lookup_store', ('stores.csv', fs, 'text/csv'))])
        if r.status_code >= 400:
            self.record('star_large_import', False, http_status=r.status_code, error=r.text[:300]); return
        item = r.json()
        job = item if 'state' not in item else self.wait(f"/api/v1/snapshot-imports/{item['id']}", ('validated', 'failed'))
        snap = item['id'] if 'state' not in item else job.get('snapshot_id')
        self.record('star_large_import', bool(snap), rows=rows, seconds=round(time.monotonic() - t0, 1),
                    error=None if snap else json.dumps(job.get('error'))[:240])
        if not snap:
            return
        run, secs = self.compare(contract, snap, "SELECT SUM(o.amount) FROM orders o WHERE o.status = 'paid'",
                                 "SELECT SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
                                 "JOIN stores s ON o.store_id = s.store_id WHERE o.status = 'paid' AND c.segment = 'vip' AND s.city IN ('delhi', 'mumbai', 'pune')",
                                 'star large', endpoint='relational')
        res = run['result'] or {}
        exp = (sum(a for _, _, a, st in facts if st == 'paid'),
               sum(a for c, s, a, st in facts if st == 'paid' and cust.get(c) == 'vip' and store[s] != 'goa'))
        self.record('star_large_exact', run['state'] == 'succeeded' and (cents(res.get('baseline_value') or '0'), cents(res.get('target_value') or '0')) == exp,
                    seconds=secs, state=run['state'], error=(run.get('error') or {}).get('message'))

    def edge_cases(self):
        contract = self.ok(self.c.post('/api/v1/flat/contracts', json={
            'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'Edge cases',
            'row_key': 'id', 'measure': 'amount', 'precision': 12, 'scale': 2, 'categories': ['status'],
            'dimension_types': {'status': 'string'}, 'nullable_categories': ['status'], 'measure_meaning': 'm',
            'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}))
        cases = {
            # name: (bytes, should_accept)
            'utf8_bom_crlf_quotes_unicode': ('\ufeffid,amount,status\r\n"A,1",1.00,paid\r\n"नमस्ते",2.50,"pa""id"\r\n🙂,3.00,\r\n'.encode('utf-8'), True),
            'formula_like_keys': (b'id,amount,status\n=1+1,1.00,paid\n-2,2.00,paid\n@x,3.00,paid\n', True),
            'long_key_10k_chars': (b'id,amount,status\n' + b'K' * 10_000 + b',1.00,paid\n', False),
            'key_256_chars': (b'id,amount,status\n' + b'K' * 256 + b',1.00,paid\n', True),
            'header_only': (b'id,amount,status\n', True),
            'empty_file': (b'', False),
            'too_many_decimals': (b'id,amount,status\nA,1.234,paid\n', False),
            'scientific_notation': (b'id,amount,status\nA,1e5,paid\n', False),
            'text_amount': (b'id,amount,status\nA,abc,paid\n', False),
            'thousands_separator': (b'id,amount,status\nA,"1,000.00",paid\n', False),
            'over_precision': (b'id,amount,status\nA,99999999999.99,paid\n', False),
            'missing_column': (b'id,amount\nA,1.00\n', False),
            'extra_column_ignored': (b'id,amount,status,x\nA,1.00,paid,1\n', True),
            'blank_key': (b'id,amount,status\n,1.00,paid\n', False),
            'duplicate_key': (b'id,amount,status\nA,1.00,paid\nA,2.00,paid\n', False),
            'ragged_row': (b'id,amount,status\nA,1.00\n', False),
            'latin1_bytes': (b'id,amount,status\nA\xe9,1.00,paid\n', False),
        }
        for name, (content, accept) in cases.items():
            p = self.out / f'edge_{name}.csv'; p.write_bytes(content)
            snap, err = self.upload_flat(contract['id'], p, name)
            self.record(f'edge_{name}', bool(snap) == accept, accepted=bool(snap), error=(err or '')[:160])

        # 5,000,001 rows: refused at the row limit
        p = self.out / 'edge_over_5m_rows.csv'
        with p.open('w', newline='') as f:
            f.write('id,amount,status\n')
            for i in range(5_000_001):
                f.write(f'{i},1.00,\n')
        t0 = time.monotonic(); snap, err = self.upload_flat(contract['id'], p, 'over 5M')
        self.record('reject_over_5m_rows', snap is None, seconds=round(time.monotonic() - t0, 1), error=(err or '')[:160])

        # > 512 MiB streamed without a length: refused without exhausting memory
        class Endless(io.RawIOBase):
            def __init__(self, size):
                self.left = size; self.first = True
            def readable(self):
                return True
            def readinto(self, b):
                if self.left <= 0:
                    return 0
                chunk = (b'id,amount,status\n' if self.first else b'') + b'x,1.00,paid\n' * (len(b) // 12)
                self.first = False
                n = min(len(chunk), len(b), self.left)
                b[:n] = chunk[:n]; self.left -= n
                return n
        t0 = time.monotonic()
        try:
            r = self.c.post('/api/v1/flat/snapshots', files={'file': ('huge.csv', io.BufferedReader(Endless(530 * 2**20)), 'text/csv')},
                            data={'contract_id': contract['id'], 'name': 'huge'})
            code = r.status_code
        except httpx.HTTPError as e:
            code = f'connection closed ({type(e).__name__})'
        self.record('reject_over_512mib_upload', code == 413 or isinstance(code, str), http_status=code, seconds=round(time.monotonic() - t0, 1))

        for name, sql in [('window', 'SELECT SUM(amount) OVER () FROM snapshot'), ('having', 'SELECT SUM(amount) FROM snapshot HAVING SUM(amount) > 0'),
                          ('union', 'SELECT SUM(amount) FROM snapshot UNION SELECT 1'), ('cte', 'WITH x AS (SELECT * FROM snapshot) SELECT SUM(amount) FROM x'),
                          ('comment_injection', "SELECT SUM(amount) FROM snapshot WHERE status = 'a' -- ' OR 1=1"),
                          ('stacked', 'SELECT SUM(amount) FROM snapshot; DELETE FROM snapshot'),
                          ('function_call', "SELECT SUM(amount) FROM snapshot WHERE lower(status) = 'paid'"),
                          ('read_file', "SELECT SUM(amount) FROM read_csv_auto('C:/Windows/win.ini')")]:
            vid, why = self.define('/api/v1/flat/definitions', contract['id'], f'refuse {name}', sql)
            self.record(f'refuse_{name}', vid is None, reason=(why or '')[:140])


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--api', default='http://127.0.0.1:8010')
    p.add_argument('--workspace', default='local-retail')
    p.add_argument('--rows', type=int, default=1_000_000)
    p.add_argument('--margin-rows', type=int, default=500_000)
    p.add_argument('--out', default='data/evaluation/stress_analyst')
    p.add_argument('--star-rows', type=int, default=500_000)
    p.add_argument('--only', default='single,margin,star,starlarge,edge')
    a = p.parse_args(argv)
    out = ROOT / a.out; out.mkdir(parents=True, exist_ok=True)
    h = Harness(a.api, a.workspace, out)
    t0 = time.monotonic()
    for part in a.only.split(','):
        try:
            {'single': lambda: h.single_table(a.rows), 'margin': lambda: h.margin(a.margin_rows),
             'star': lambda: h.star(100_000), 'starlarge': lambda: h.star_large(a.star_rows), 'edge': h.edge_cases}[part]()
        except Exception as e:  # keep partial results
            h.record(f'harness_error_{part}', False, error=f'{type(e).__name__}: {e}'[:400])
    h.record('total_wall_clock', True, minutes=round((time.monotonic() - t0) / 60, 1))
    report = {'status': 'PASS' if all(r['status'] == 'PASS' for r in h.results) else 'FAIL', 'rows': a.rows, 'checks': h.results}
    (out / 'stress_report.json').write_text(json.dumps(report, indent=2, default=str) + '\n', encoding='utf-8')
    print('REPORT', report['status'])
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
