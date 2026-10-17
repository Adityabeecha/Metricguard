"""Fresh public-data audit. Oracle uses csv + Decimal, never MetricGuard SQL.

Source: Fanaee-T (2013), UCI Bike Sharing, DOI 10.24432/C5W894, CC BY 4.0.
Download source.zip from https://archive.ics.uci.edu/static/public/275/bike%2Bsharing%2Bdataset.zip
into --directory first. Executes real Docker jobs sequentially in a separate database.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import traceback
import zipfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.supervisor.worker import DockerLauncher, Supervisor


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(directory, only=None):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report = {'source': 'https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset',
              'credit': 'Fanaee-T (2013), DOI 10.24432/C5W894, CC BY 4.0',
              'checks': [], 'scope': 'Public-data engineering checks, not analyst validation'}
    def record(name, fn):
        if only and name not in only:
            return None
        try:
            details = fn()
            item = {'name': name, 'status': 'PASS', 'details': details}
        except Exception as e:
            item = {'name': name, 'status': 'FAIL', 'error': str(e), 'traceback': traceback.format_exc()}
        report['checks'].append(item)
        (directory / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(name, item['status'], item.get('error', ''), flush=True)
        return item

    with zipfile.ZipFile(directory / 'source.zip') as z:
        hours = list(csv.DictReader(io.StringIO(z.read('hour.csv').decode('utf-8-sig'))))
        days = list(csv.DictReader(io.StringIO(z.read('day.csv').decode('utf-8-sig'))))
    # Independent source cross-check before preparing any application input.
    hourly_daily = defaultdict(int)
    for r in hours:
        assert int(r['cnt']) == int(r['casual']) + int(r['registered'])
        hourly_daily[r['dteday']] += int(r['cnt'])
    assert {r['dteday']: int(r['cnt']) for r in days} == dict(hourly_daily)
    assert len({r['instant'] for r in hours}) == len(hours)
    flat = [{'rental_record': r['instant'], 'rides': r['cnt'], 'workday': r['workingday'],
             'weather': r['weathersit'], 'rental_date': r['dteday']} for r in hours]
    write_csv(directory / 'bike-hours.csv', flat)
    # External projection into a star schema, no imputation or invented dimensions.
    fact = [{'rental_record': r['instant'], 'rides': r['cnt'], 'rental_date': r['dteday'], 'weather': r['weathersit']} for r in hours]
    dimension = [{'rental_date': r['dteday'], 'workday': r['workingday'], 'season': r['season']} for r in days]
    assert len({r['rental_date'] for r in dimension}) == len(dimension)
    write_csv(directory / 'bike-fact.csv', fact)
    write_csv(directory / 'bike-calendar.csv', dimension)
    report['source_validation'] = {'hour_rows': len(hours), 'day_rows': len(days), 'daily_totals_match_every_hour_group': True,
        'preparation': 'Project original fields; rename cnt to rides and instant to rental_record; retain original date/category strings. No rounding, conversion or missing-value imputation.'}
    report['hashes'] = {p.name: sha(p) for p in directory.glob('*.csv')}
    report['hashes']['source.zip'] = sha(directory / 'source.zip')
    appdir = directory / ('app-' + uuid4().hex[:8])
    appdir.mkdir()
    os.environ['METRICGUARD_ALLOW_UNVERIFIED_LOCAL'] = 'false'
    app = create_app(database_url=f'sqlite:///{appdir / "app.db"}', data_dir=appdir, mode='isolated')
    store = app.state.store
    user, key = store.create_user('Fresh dataset auditor', f'audit-{uuid4().hex}@example.invalid')
    store.add_project_member(store.project_id, user['id'], 'owner')
    worker = Supervisor(store, DockerLauncher(), appdir / 'jobs', 'fresh-data-auditor', appdir)
    report['app_directory'] = str(appdir)
    # Local-only credential for optional browser verification, never included in report.
    (appdir / '.audit-key').write_text(key, encoding='utf-8')
    def ok(r, codes=(200, 201, 202)):
        assert r.status_code in codes, f'{r.status_code}: {r.text[:1500]}'
        return r.json()
    with TestClient(app, headers={'X-API-Key': key}) as c:
        def upload(mode, contract, paths, expect_failure=False):
            files = {('file' if mode == 'flat' else ['fact_file', 'dimension_file'][i]): (p.name, p.read_bytes(), 'text/csv') for i, p in enumerate(paths)}
            r = c.post(f'/api/v1/{mode}/snapshots', data={'contract_id': contract['id'], 'name': paths[0].stem}, files=files)
            if expect_failure and r.status_code >= 400:
                return {'http_status': r.status_code, 'error': r.json()}
            item = ok(r)
            assert worker.run_once(), 'Import job was not claimed'
            item = ok(c.get('/api/v1/snapshot-imports/' + item['id']))
            if expect_failure:
                assert item['state'] == 'failed', item
                return {'state': item['state'], 'error': item['error']}
            assert item['state'] == 'validated', item
            return item['snapshot_id']

        def compare(name, mode, contract, snapshot, sqls, rows, terms, replay=False):
            versions = [ok(c.post(f'/api/v1/{mode}/definitions', json={'name': name + str(i), 'raw_sql': sql, 'contract_id': contract['id']}))['versions'][0]['id'] for i, sql in enumerate(sqls)]
            context = {'mode': mode + '_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'], 'unit_label': contract['unit_label'], 'values_comparable': True}
            run = ok(c.post(f'/api/v1/{mode}/comparisons', json={'baseline_version_id': versions[0], 'target_version_id': versions[1], 'snapshot_id': snapshot, 'context': context}, headers={'Idempotency-Key': uuid4().hex}))
            assert worker.run_once()
            run = ok(c.get('/api/v1/comparisons/' + run['id']))
            assert run['state'] in ('succeeded', 'partial'), run.get('error')
            result = run['result']
            values = [[], []]
            expected = {}
            for row in rows:
                a, b = terms(row)
                for i, v in enumerate((a, b)):
                    if v is not None:
                        values[i].append(v)
                expected[row['rental_record']] = (b or Decimal(0)) - (a or Decimal(0))
            sums = [sum(v, Decimal(0)) if v else None for v in values]
            gap = sums[1] - sums[0] if all(v is not None for v in sums) else None
            assert run['state'] == ('succeeded' if gap is not None else 'partial')
            for field, want in zip(('baseline_value', 'target_value', 'delta_target_minus_baseline'), [*sums, gap]):
                got = result[field]
                assert (Decimal(got) if got is not None else None) == want, (field, got, want)
            assert (Decimal(result['residual']) if result.get('residual') is not None else None) == (Decimal(0) if gap is not None else None)
            artifact = appdir / 'evidence' / f"{run['id']}-{run['attempt']}.jsonl"
            assert sha(artifact) == result['evidence_artifact']['sha256']
            evidence = [json.loads(line) for line in artifact.read_text(encoding='utf-8').splitlines()]
            # Check first/middle/final API pages plus independently check every
            # artifact row. Each API page revalidates the whole artifact; avoid
            # quadratic re-reading during repeated audits on a laptop.
            for offset in sorted({0, (len(rows) // 200) * 100, ((len(rows) - 1) // 100) * 100}):
                page = ok(c.get(f'/api/v1/comparisons/{run["id"]}/evidence?offset={offset}&limit=100'))
                assert page['total'] == len(rows)
                assert page['items'] == evidence[offset:offset + 100]
            assert len(evidence) == len(expected)
            actual = {str(e.get('fact_key', e.get('row_key', e.get('order_id')))): Decimal(e['delta']) for e in evidence}
            assert actual == expected, 'Per-record contributions differ from independent oracle'
            if gap is not None:
                assert sum(actual.values()) == gap
            assert c.get(f'/api/v1/comparisons/{run["id"]}/evidence?limit=101').status_code == 422
            exported = ok(c.get(f'/api/v1/comparisons/{run["id"]}/export'))
            assert exported['evidence_coverage']['total_rows'] == len(rows)
            assert exported['evidence_coverage']['included_rows'] == min(100, len(rows))
            if mode == 'relational':
                assert c.get(f'/api/v1/comparisons/{run["id"]}/join-evidence?limit=100').status_code == 200
            receipt = {'run_id': run['id'], 'policy': run['execution_policy'], 'baseline': result['baseline_value'], 'target': result['target_value'], 'gap': result['delta_target_minus_baseline'], 'rows_verified': len(evidence), 'snapshot_id': snapshot, 'contract_hash': contract['contract_hash']}
            if replay:
                case = ok(c.post('/api/v1/investigations', json={'title': name, 'run_id': run['id'], 'decision': 'Independent public-data or deliberately synthetic engineering check.', 'actor_label': 'Audit'}))
                rerun = ok(c.post(f'/api/v1/investigations/{case["id"]}/replays', headers={'Idempotency-Key': uuid4().hex}))
                assert worker.run_once()
                rerun = ok(c.get('/api/v1/comparisons/' + rerun['id']))
                assert rerun['state'] == 'succeeded', rerun.get('error')
                assert [rerun['result'][k] for k in ('baseline_value', 'target_value', 'delta_target_minus_baseline')] == [result[k] for k in ('baseline_value', 'target_value', 'delta_target_minus_baseline')]
                assert rerun['result']['evidence_artifact']['sha256'] == result['evidence_artifact']['sha256']
                receipt.update(case_id=case['id'], replay_run_id=rerun['id'], evidence_hash=result['evidence_artifact']['sha256'])
            return receipt

        payload = {'version': 5, 'mode': 'flat_snapshot', 'name': 'UCI hourly rental counts', 'row_key': 'rental_record', 'measure': 'rides', 'precision': 18, 'scale': 0, 'categories': ['workday', 'weather', 'rental_date'], 'dimension_types': {'workday': 'string', 'weather': 'string', 'rental_date': 'date'}, 'predicate_policy': 'flat-boolean-v1', 'measure_meaning': 'Count of bicycle rentals in the source hour', 'unit_label': 'rentals', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}
        contract = ok(c.post('/api/v1/flat/contracts', json=payload))
        snapshot = upload('flat', contract, [directory / 'bike-hours.csv'])
        base = 'SELECT SUM(rides) FROM snapshot'
        for name, where, pred in [
            ('public_workdays', "workday = '1'", lambda r: r['workday'] == '1'),
            ('public_boolean', "(weather = '1' OR weather = '2') AND NOT workday = '0'", lambda r: r['weather'] in ('1','2') and r['workday'] != '0'),
            ('public_date_boundary', "rental_date >= DATE '2012-01-01' AND rental_date < DATE '2012-02-01'", lambda r: '2012-01-01' <= r['rental_date'] < '2012-02-01'),
            ('public_empty_sum', "weather = '99'", lambda r: False),
        ]:
            record(name, lambda n=name, w=where, p=pred: compare(n, 'flat', contract, snapshot, [base, base + ' WHERE ' + w], flat, lambda r: (Decimal(r['rides']), Decimal(r['rides']) if p(r) else None), replay=n == 'public_workdays'))
        for sql in ['SELECT AVG(rides) FROM snapshot', 'SELECT SUM(not_registered) FROM snapshot', 'SELECT SUM(rides) FROM snapshot GROUP BY workday', 'SELECT SUM(rides * 2) FROM snapshot', 'SELECT SUM(rides) FROM snapshot; DROP TABLE snapshot']:
            def refuse(sql=sql):
                r = c.post('/api/v1/flat/definitions', json={'name': 'Refusal probe', 'raw_sql': sql, 'contract_id': contract['id']})
                assert r.status_code == 422, r.text
                return r.json()
            record('refuse_' + sql, refuse)

        rel_payload = {'version': 1, 'mode': 'relational_snapshot', 'policy': 'relational-snapshot-v1', 'name': 'UCI hourly rentals with day context',
            'fact': {'table': 'rental_hours', 'key': 'rental_record', 'grain': 'One source hour', 'categories': ['weather']},
            'dimension': {'table': 'calendar_days', 'key': 'rental_date', 'grain': 'One source day', 'categories': ['workday', 'season']},
            'relationship': {'fact_foreign_key': 'rental_date', 'dimension_key': 'rental_date', 'cardinality': 'many_to_one', 'missing_dimension_policy': 'preserve_and_report'},
            'measure': {'column': 'rides', 'precision': 18, 'scale': 0}, 'measure_meaning': payload['measure_meaning'], 'unit_label': 'rentals', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}
        rel = ok(c.post('/api/v1/relational/contracts', json=rel_payload))
        rs = upload('relational', rel, [directory / 'bike-fact.csv', directory / 'bike-calendar.csv'])
        left = 'SELECT SUM(f.rides) FROM rental_hours f LEFT JOIN calendar_days d ON f.rental_date = d.rental_date'
        right = left.replace('LEFT JOIN', 'INNER JOIN') + " WHERE d.workday = '1'"
        record('public_relational_workdays', lambda: compare('public_relational_workdays', 'relational', rel, rs, [left, right], flat, lambda r: (Decimal(r['rides']), Decimal(r['rides']) if r['workday'] == '1' else None), replay=True))
        bad_dim = directory / 'duplicate-calendar.csv'
        write_csv(bad_dim, dimension + [dimension[0]])
        record('reject_dimension_fanout', lambda: upload('relational', rel, [directory / 'bike-fact.csv', bad_dim], True))

        # Additional policies use the distinct daily source, with separately supplied
        # casual counts as a subtraction oracle (cnt - registered == casual).
        daily = [{'rental_record': r['instant'], 'rides': r['cnt'], 'members': r['registered'],
                  'casual': r['casual'], 'workday': r['workingday'], 'weather': r['weathersit'],
                  'rental_date': r['dteday']} for r in days]
        daily_path = directory / 'bike-daily.csv'
        write_csv(daily_path, daily)
        subtraction = {k:v for k,v in payload.items() if k not in ('measure', 'precision')}
        subtraction.update(version=6, name='Daily casual rentals by subtraction', measure_policy='flat-subtraction-v1',
            decimal_columns=[{'name': col, 'precision': 18, 'scale': 0, 'unit_label': 'rentals'} for col in ('rides', 'members')],
            measure_expression={'op': 'sub', 'minuend': 'rides', 'subtrahend': 'members'})
        sc = ok(c.post('/api/v1/flat/contracts', json=subtraction))
        ss = upload('flat', sc, [daily_path])
        sub_sql = 'SELECT SUM(rides - members) FROM snapshot'
        record('public_daily_subtraction', lambda: compare('public_daily_subtraction', 'flat', sc, ss,
            [sub_sql, sub_sql + " WHERE workday = '1'"], daily,
            lambda r: (Decimal(r['casual']), Decimal(r['casual']) if r['workday'] == '1' else None), replay=True))
        cc = ok(c.post('/api/v1/flat/contracts', json={**payload, 'version': 7, 'name': 'Daily conditional rentals', 'measure_policy': 'flat-case-v1'}))
        cs = upload('flat', cc, [daily_path])
        case_sql = "SELECT SUM(CASE WHEN workday = '1' THEN rides ELSE 0 END) FROM snapshot"
        record('public_daily_case', lambda: compare('public_daily_case', 'flat', cc, cs, [case_sql, case_sql + " WHERE weather = '1'"], daily,
            lambda r: (Decimal(r['rides']) if r['workday'] == '1' else Decimal(0), (Decimal(r['rides']) if r['workday'] == '1' else Decimal(0)) if r['weather'] == '1' else None), replay=True))

        # New intentionally synthetic decimals and NULLs, kept separate from public rows.
        adversarial = [dict(rental_record=str(i), rides=v, workday=w, weather='1', rental_date='2020-02-29') for i, (v,w) in enumerate([('90071992547409.91','keep'), ('-90071992547409.90','drop'), ('0.01','keep'), ('','null'), ('-0.02','drop')])]
        payload.update(name='Fresh exact decimal adversarial fixture', scale=2, precision=18)
        ac = ok(c.post('/api/v1/flat/contracts', json=payload))
        ap = directory / 'synthetic-decimals.csv'
        write_csv(ap, adversarial)
        ass = upload('flat', ac, [ap])
        for label, category in [('exact_cancellation', 'keep'), ('all_null_sum', 'null'), ('empty_sum', 'absent')]:
            record(label, lambda label=label, category=category: compare(label, 'flat', ac, ass, [base, base + f" WHERE workday = '{category}'"], adversarial, lambda r: (Decimal(r['rides']) if r['rides'] else None, Decimal(r['rides']) if r['rides'] and r['workday'] == category else None), replay=label == 'exact_cancellation'))
        for label, rows in [('duplicate_key', adversarial + [adversarial[0]]), ('null_key', [{**adversarial[0], 'rental_record': ''}]), ('over_scale', [{**adversarial[0], 'rides': '1.001'}]), ('nan', [{**adversarial[0], 'rides': 'NaN'}]), ('missing_measure', [{k:v for k,v in adversarial[0].items() if k != 'rides'}])]:
            path = directory / (label + '.csv')
            write_csv(path, rows)
            record('reject_' + label, lambda p=path: upload('flat', ac, [p], True))
        synthetic_facts = [{'rental_record': 'edge-' + str(i), 'rides': amount, 'rental_date': day, 'weather': 'edge'}
            for i, (amount, day) in enumerate([('2.37', 'known'), ('-1.19', 'other'), ('10.01', 'missing'), ('0.02', ''), ('', 'known')])]
        synthetic_dim = [{'rental_date': 'known', 'workday': '1', 'season': 'alpha'}, {'rental_date': 'other', 'workday': '0', 'season': 'beta'}]
        sf, sd = directory / 'synthetic-fact.csv', directory / 'synthetic-dimension.csv'
        write_csv(sf, synthetic_facts)
        write_csv(sd, synthetic_dim)
        sr = ok(c.post('/api/v1/relational/contracts', json={**rel_payload, 'name': 'Synthetic missing joins', 'measure': {'column': 'rides', 'precision': 18, 'scale': 2}}))
        srs = upload('relational', sr, [sf, sd])
        record('synthetic_relational_null_unmatched', lambda: compare('synthetic_relational_null_unmatched', 'relational', sr, srs,
            [left, right], synthetic_facts,
            lambda r: (Decimal(r['rides']) if r['rides'] else None, Decimal(r['rides']) if r['rides'] and r['rental_date'] == 'known' else None), replay=True))
    report['status'] = 'PASS' if all(x['status'] == 'PASS' for x in report['checks']) else 'FAIL'
    (directory / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('data/evaluation/fresh-public-20261017'))
    parser.add_argument('--only', nargs='+', help='Run only the named checks; still prepares/imports the datasets')
    args = parser.parse_args()
    raise SystemExit(main(args.directory, args.only))
