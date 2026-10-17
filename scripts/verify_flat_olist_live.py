"""Exercise the public Olist flat case through API, Docker supervisor, evidence, and replay."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store
from services.supervisor.worker import DockerLauncher, Supervisor


def verify(directory=Path('data/public-case-olist'), app_dir=Path('data/evaluation/flat-olist-live')):
    directory, app_dir = Path(directory), Path(app_dir)
    source = directory / 'olist-order-flat.csv'
    prepared = json.loads((directory / 'olist-order-flat-manifest.json').read_text(encoding='utf-8'))
    independent = json.loads((directory / 'results.json').read_text(encoding='utf-8'))
    assert prepared['source_hashes'] == independent['source_hashes']
    assert prepared['orders'] == independent['orders']
    app_dir.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{(app_dir / 'app.db').resolve()}"
    worker = Supervisor(Store(url), DockerLauncher(), app_dir / 'jobs', 'flat-olist-verifier', app_dir)
    with TestClient(create_app(database_url=url, data_dir=app_dir, mode='isolated')) as client:
        contract_response = client.post('/api/v1/flat/contracts', json={
            'version': 3, 'mode': 'flat_snapshot', 'name': 'Olist recorded payment by order',
            'row_key': 'row_id', 'measure': 'payment_value', 'precision': 18, 'scale': 2,
            'categories': ['order_status'], 'measure_meaning': 'Recorded payment value summed by order',
            'unit_label': 'source numeric units (currency unknown)', 'values_comparable': True,
            'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
        assert contract_response.status_code == 201, contract_response.text
        contract = contract_response.json()
        with source.open('rb') as stream:
            upload = client.post('/api/v1/flat/snapshots',
                files={'file': (source.name, stream, 'text/csv')},
                data={'contract_id': contract['id'], 'name': 'Prepared Olist order snapshot'})
        assert upload.status_code in (200, 202), upload.text
        item = upload.json()
        if 'state' in item:
            assert worker.run_once()
            item = client.get(f"/api/v1/snapshot-imports/{item['id']}").json()
            assert item['state'] == 'validated', item['error']
            snapshot_id = item['snapshot_id']
        else:
            snapshot_id = item['id']
        versions = []
        for name, sql in [
            ('All recorded payment value', 'SELECT SUM(payment_value) FROM snapshot'),
            ('Delivered or shipped payment value',
             "SELECT SUM(payment_value) FROM snapshot WHERE order_status IN ('delivered', 'shipped')"),
        ]:
            response = client.post('/api/v1/flat/definitions', json={
                'name': name, 'raw_sql': sql, 'contract_id': contract['id']})
            assert response.status_code == 201, response.text
            versions.append(response.json()['versions'][0]['id'])
        body = {'baseline_version_id': versions[0], 'target_version_id': versions[1],
                'snapshot_id': snapshot_id,
                'context': {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot',
                            'contract_hash': contract['contract_hash'],
                            'unit_label': contract['unit_label'], 'values_comparable': True}}
        run = client.post('/api/v1/flat/comparisons', json=body,
                          headers={'Idempotency-Key': uuid4().hex})
        assert run.status_code == 202, run.text
        assert worker.run_once()
        finished = client.get(f"/api/v1/comparisons/{run.json()['id']}").json()
        assert finished['state'] == 'succeeded', finished['error']
        result = finished['result']
        assert result['baseline_value'] == independent['baseline_payment_value']
        assert result['target_value'] == independent['target_delivered_or_shipped_payment_value']
        assert result['delta_target_minus_baseline'] == independent['target_minus_baseline']
        page = client.get(f"/api/v1/comparisons/{finished['id']}/evidence?limit=100")
        assert page.status_code == 200, page.text
        assert page.json()['total'] == prepared['orders']
        artifact = app_dir / 'evidence' / f"{finished['id']}-{finished['attempt']}.jsonl"
        delta = Decimal(0)
        null_count = count = 0
        with artifact.open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                delta += Decimal(row['delta'])
                count += 1
                null_count += int(row['baseline_null_term'])
        assert count == prepared['orders']
        assert null_count == prepared['orders_without_payment_records']
        assert delta == Decimal(independent['target_minus_baseline'])
        saved = client.post('/api/v1/investigations', json={
            'title': 'Olist public snapshot check', 'run_id': finished['id'],
            'decision': 'Public-data coverage experiment; no original dashboard value or root cause claim.',
            'actor_label': 'Local verification'})
        assert saved.status_code == 201, saved.text
        replay = client.post(f"/api/v1/investigations/{saved.json()['id']}/replays",
                             headers={'Idempotency-Key': uuid4().hex})
        assert replay.status_code == 202, replay.text
        assert worker.run_once()
        replayed = client.get(f"/api/v1/comparisons/{replay.json()['id']}").json()
        assert replayed['state'] == 'succeeded', replayed['error']
        assert replayed['result']['delta_target_minus_baseline'] == independent['target_minus_baseline']
        report = {'status': 'passed', 'execution_policy': 'flat_snapshot_v1',
                  'snapshot_id': snapshot_id, 'contract_hash': contract['contract_hash'],
                  'run_id': finished['id'], 'replay_run_id': replayed['id'],
                  'case_id': saved.json()['id'], 'baseline': result['baseline_value'],
                  'target': result['target_value'], 'gap': result['delta_target_minus_baseline'],
                  'evidence_rows': count, 'null_measure_rows': null_count,
                  'evidence_delta': format(delta, 'f'), 'prepared_sha256': prepared['flat_sha256']}
        (app_dir / 'verification-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('data/public-case-olist'))
    parser.add_argument('--app-dir', type=Path, default=Path('data/evaluation/flat-olist-live'))
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.directory, arguments.app_dir), indent=2))
