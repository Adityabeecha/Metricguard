"""Measure a synthetic mapped investigation through the real isolated runner."""

import csv
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import time

from fastapi.testclient import TestClient
import sqlglot
from sqlglot import exp

from packages.metricguard_core.common import SCHEMA
from scripts.evaluate_release import _independent_totals
from scripts.scale_fixture import generate_fixture
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.worker import DockerLauncher, Supervisor


ROOT = Path(__file__).resolve().parents[1]


def verify_scale():
    (ROOT / '.test-tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='v2-scale-', dir=ROOT / '.test-tmp',
                                     ignore_cleanup_errors=True) as directory:
        work = Path(directory)
        source = work / 'orders.csv'
        oracle = generate_fixture(source, work / 'oracle.json', rows=100_000, seed=20260923)
        totals = _independent_totals(source)
        assert totals == oracle['august_totals']
        mapped_source = work / 'mapped.csv'
        columns = {role: f'src_{role}' for role in SCHEMA}
        with source.open(encoding='utf-8', newline='') as original, mapped_source.open('w', encoding='utf-8', newline='') as output:
            reader, writer = csv.reader(original), csv.writer(output)
            next(reader)
            writer.writerow(columns.values())
            writer.writerows(reader)
        url = f"sqlite:///{work / 'app.db'}"
        store = Store(url)
        worker = Supervisor(store, DockerLauncher(), work / 'jobs', 'v2-scale-check', data_dir=work)
        timings = {}
        with TestClient(create_app(database_url=url, data_dir=work, mode='isolated')) as client:
            def post(path, **kwargs):
                response = client.post('/api/v1' + path, **kwargs)
                assert response.status_code in {201, 202}, response.text
                return response.json()

            contract = post('/dataset-contracts', json={
                'version': 2, 'name': 'Synthetic scale', 'currency': oracle['currency'],
                'timezone': oracle['timezone'], 'scope': oracle['scope'],
                'grain': oracle['grain'], 'cutoff_at': oracle['cutoff_at'],
                'null_policy': 'blank_nullable', 'row_key': 'order_id',
                'entity_key': 'customer_id', 'decimal_scale': 2, 'schema': SCHEMA})
            mapping = post('/column-mappings', json={'contract_id': contract['id'], 'columns': columns})
            started = time.perf_counter()
            upload = post('/mapped-snapshots', files={'file': ('mapped.csv', mapped_source.read_bytes())},
                          data={'mapping_id': mapping['id'], 'name': '100,000 synthetic rows'})
            assert worker.run_once()
            imported = store.get_import(upload['id'])
            assert imported['state'] == 'validated', imported.get('error')
            timings['upload_and_isolated_ingestion'] = round(time.perf_counter() - started, 4)
            snapshot = store.snapshot(imported['snapshot_id'])
            assert snapshot['row_count'] == 100_000
            versions = []
            for name, date_role in [('dashboard_a', 'order_date'), ('finance', 'payment_date')]:
                sql = (ROOT / 'MetricGuard_Planning_Pack' / 'examples' / f'{name}.sql').read_text(encoding='utf-8')
                tree = sqlglot.parse_one(sql, read='duckdb')
                for column in tree.find_all(exp.Column):
                    column.set('this', exp.to_identifier(columns[column.name], quoted=True))
                definition = post('/definitions', json={'name': name, 'raw_sql': tree.sql(dialect='duckdb'),
                                  'time_column': columns[date_role], 'mapping_id': mapping['id']})
                versions.append(definition['versions'][0]['id'])
            context = {key: oracle[key] for key in ('currency', 'timezone', 'grain', 'scope')}
            context.update(start='2026-08-01', end_exclusive='2026-09-01', unit='currency',
                           contract_hash=contract['contract_hash'], mapping_hash=mapping['mapping_hash'])
            started = time.perf_counter()
            queued = post('/comparisons', json={'baseline_version_id': versions[0], 'target_version_id': versions[1],
                          'snapshot_id': snapshot['id'], 'context': context, 'relationship': 'expected_agreement'},
                          headers={'Idempotency-Key': 'v2-scale'})
            assert worker.run_once()
            run = store.get_run(queued['id'])
            assert run['state'] == 'succeeded', run.get('error')
            timings['isolated_comparison_and_publication'] = round(time.perf_counter() - started, 4)
            result = run['result']
            assert result['baseline_value'] == totals['dashboard_a']
            assert result['target_value'] == totals['finance']
            assert Decimal(result['delta_target_minus_baseline']) == Decimal(totals['finance']) - Decimal(totals['dashboard_a'])
            assert Decimal(result['residual']) == 0
            assert result['evidence'] == []
            assert result['evidence_artifact']['row_count'] == 100_000
            started = time.perf_counter()
            page = client.get(f"/api/v1/comparisons/{run['id']}/evidence", params={'offset': 99950, 'limit': 100})
            assert page.status_code == 200, page.text
            assert page.json()['total'] == 100_000 and len(page.json()['items']) == 50
            timings['verified_last_page'] = round(time.perf_counter() - started, 4)
            exported = client.get(f"/api/v1/comparisons/{run['id']}/export")
            assert exported.status_code == 200, exported.text
            assert exported.json()['evidence_coverage'] == {
                'total_rows': 100_000, 'included_rows': 100, 'omitted_rows': 99900, 'detail': 'first_rows'}
            report = {'status': 'passed', 'evidence_kind': 'synthetic_development_rehearsal',
                      'pilot_acceptance': 'not_evaluated', 'method': 'live_docker_supervisor',
                      'rows': 100_000, 'timings_seconds': timings,
                      'source_hash': snapshot['source_hash'], 'mapping_hash': mapping['mapping_hash'],
                      'contract_hash': contract['contract_hash'], 'independent_totals': totals,
                      'delta': result['delta_target_minus_baseline'], 'residual': result['residual'],
                      'stored_result_bytes': len(json.dumps(result).encode()),
                      'evidence_artifact': result['evidence_artifact'],
                      'limitations': ['One synthetic workload on this local machine; no concurrent-service or real-case acceptance claim.']}
        store.engine.dispose()
        client.app.state.store.engine.dispose()
        return report


if __name__ == '__main__':
    report = verify_scale()
    output = ROOT / 'data/evaluation/v2_scale_report.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(output.read_text(encoding='utf-8'))
