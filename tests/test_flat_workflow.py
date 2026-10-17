import pytest
import csv
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store
from services.supervisor.runner import ingest_job, run_job
from services.supervisor.worker import Supervisor


class CompletedProcess:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class InProcessLauncher:
    def start(self, input_dir, output_dir, command=None):
        if command == 'ingest':
            ingest_job(input_dir, output_dir)
        elif command is None:
            run_job(input_dir, output_dir)
        else:
            raise AssertionError(command)
        return CompletedProcess()


@pytest.mark.parametrize('contract_version', [3, 4])
def test_flat_http_supervisor_runner_evidence_save_and_replay(tmp_path, contract_version):
    source = tmp_path / 'source.csv'
    with source.open('w', newline='', encoding='utf-8') as output:
        writer = csv.writer(output)
        writer.writerows([
            ['item_id', 'score', 'stage'],
            ['A', '10.25', 'ready'], ['B', '2.00', 'held'],
            ['C', '', 'ready'], ['D', '-1.00', 'done'],
        ])
    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'flat-test-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        contract_response = client.post('/api/v1/flat/contracts', json={
            'version': contract_version, 'mode': 'flat_snapshot',
            **({'dimension_types': {'stage': 'string'}} if contract_version == 4 else {}), 'name': 'Scores', 'row_key': 'item_id',
            'measure': 'score', 'precision': 18, 'scale': 2, 'categories': ['stage'],
            'measure_meaning': 'Recorded score', 'unit_label': 'points',
            'values_comparable': True, 'scope': 'entire_supplied_snapshot',
            'null_policy': 'blank_nullable'})
        assert contract_response.status_code == 201, contract_response.text
        contract = contract_response.json()
        with source.open('rb') as content:
            uploaded = client.post('/api/v1/flat/snapshots',
                files={'file': ('scores.csv', content, 'text/csv')},
                data={'contract_id': contract['id'], 'name': 'Scores'})
        assert uploaded.status_code == 202, uploaded.text
        assert worker.run_once()
        imported = client.get(f"/api/v1/snapshot-imports/{uploaded.json()['id']}").json()
        assert imported['state'] == 'validated', imported['error']
        snapshot_id = imported['snapshot_id']
        versions = []
        for name, sql in [('All', 'SELECT SUM(score) FROM snapshot'),
                          ('Ready', "SELECT SUM(score) FROM snapshot WHERE stage IN ('ready', 'done')")]:
            response = client.post('/api/v1/flat/definitions', json={
                'name': name, 'raw_sql': sql, 'contract_id': contract['id']})
            assert response.status_code == 201, response.text
            versions.append(response.json()['versions'][0]['id'])
        assert client.get('/api/v1/bootstrap').status_code == 200
        body = {'baseline_version_id': versions[0], 'target_version_id': versions[1],
                'snapshot_id': snapshot_id,
                'context': {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot',
                            'contract_hash': contract['contract_hash'], 'unit_label': 'points',
                            'values_comparable': True}}
        assert client.post('/api/v1/comparisons', json=body,
            headers={'Idempotency-Key': 'retail-route'}).status_code == 422
        queued = client.post('/api/v1/flat/comparisons', json=body,
            headers={'Idempotency-Key': 'flat-run'})
        assert queued.status_code == 202, queued.text
        assert worker.run_once()
        result = client.get(f"/api/v1/comparisons/{queued.json()['id']}").json()
        assert result['state'] == 'succeeded', result['error']
        assert result['result']['baseline_value'] == '11.25'
        assert result['result']['target_value'] == '9.25'
        assert result['result']['delta_target_minus_baseline'] == '-2.00'
        evidence = client.get(f"/api/v1/comparisons/{result['id']}/evidence?limit=100").json()
        assert evidence['total'] == 4
        assert sum(float(row['delta']) for row in evidence['items']) == -2
        assert any(row['baseline_null_term'] for row in evidence['items'])
        exported = client.get(f"/api/v1/comparisons/{result['id']}/export?format=json")
        assert exported.status_code == 200, exported.text
        html = client.get(f"/api/v1/comparisons/{result['id']}/export?format=html")
        assert html.status_code == 200, html.text
        assert 'Filter-change explanation' in html.text
        assert 'Ordered rule bridge' not in html.text
        assert '9.25 points' in html.text
        saved = client.post('/api/v1/investigations', json={
            'title': 'Scores review', 'run_id': result['id'],
            'decision': 'Review held items', 'actor_label': 'Tester'})
        assert saved.status_code == 201, saved.text
        replay = client.post(f"/api/v1/investigations/{saved.json()['id']}/replays",
            headers={'Idempotency-Key': 'flat-replay'})
        assert replay.status_code == 202, replay.text
        assert worker.run_once()
        replayed = client.get(f"/api/v1/comparisons/{replay.json()['id']}").json()
        assert replayed['state'] == 'succeeded', replayed['error']
        assert replayed['result']['delta_target_minus_baseline'] == '-2.00'


def test_flat_preparation_manifest_is_pinned_and_checked(tmp_path):
    source = tmp_path / 'prepared.csv'
    source.write_text('item_id,score,stage\nA,1.25,ready\n', encoding='utf-8')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    preparation = {
        'version': 1, 'classification': 'external_preparation',
        'flat_sha256': digest, 'lineage_file': 'lineage.csv', 'lineage_sha256': 'a' * 64,
        'source_hashes': {'original': 'b' * 64},
        'script': 'prepare_example.py', 'script_version': '1',
        'transformation': 'Grouped original records by item.',
    }
    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'prep-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/flat/contracts', json={
            'version': 3, 'mode': 'flat_snapshot', 'name': 'Scores', 'row_key': 'item_id',
            'measure': 'score', 'precision': 18, 'scale': 2, 'categories': ['stage'],
            'measure_meaning': 'Recorded score', 'unit_label': 'points',
            'values_comparable': True, 'scope': 'entire_supplied_snapshot',
            'null_policy': 'blank_nullable'}).json()
        def upload(prep):
            return client.post('/api/v1/flat/snapshots', files={
                'file': ('prepared.csv', source.read_bytes(), 'text/csv'),
                'preparation_manifest': ('preparation.json', json.dumps(prep).encode(), 'application/json')},
                data={'contract_id': contract['id'], 'name': 'Prepared'})
        wrong = upload({**preparation, 'flat_sha256': '0' * 64})
        assert wrong.status_code == 422
        unsafe = upload({**preparation, 'lineage_file': '../outside.csv'})
        assert unsafe.status_code == 422
        assert not client.get('/api/v1/bootstrap').json()['snapshots']
        queued = upload(preparation)
        assert queued.status_code == 202, queued.text
        assert worker.run_once()
        snapshot_id = client.get(f"/api/v1/snapshot-imports/{queued.json()['id']}").json()['snapshot_id']
        snapshot = next(s for s in client.get('/api/v1/bootstrap').json()['snapshots'] if s['id'] == snapshot_id)
        assert snapshot['preparation']['flat_sha256'] == digest
        assert snapshot['preparation']['lineage_sha256'] == 'a' * 64
        assert len(snapshot['preparation_manifest_hash']) == 64
        versions = []
        for name, sql in [('All', 'SELECT SUM(score) FROM snapshot'),
                          ('Ready', "SELECT SUM(score) FROM snapshot WHERE stage = 'ready'")]:
            response = client.post('/api/v1/flat/definitions', json={
                'name': name, 'raw_sql': sql, 'contract_id': contract['id']})
            versions.append(response.json()['versions'][0]['id'])
        body = {'baseline_version_id': versions[0], 'target_version_id': versions[1],
                'snapshot_id': snapshot_id, 'context': {'mode': 'flat_snapshot',
                'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
                'unit_label': 'points', 'values_comparable': True}}
        run = client.post('/api/v1/flat/comparisons', json=body,
                          headers={'Idempotency-Key': 'prepared-run'}).json()
        assert worker.run_once()
        saved = client.post('/api/v1/investigations', json={
            'title': 'Prepared', 'run_id': run['id'], 'decision': 'Checked',
            'actor_label': 'Tester'}).json()
        assert saved['preparation_manifest_hash'] == snapshot['preparation_manifest_hash']
        detail = client.get(f"/api/v1/investigations/{saved['id']}").json()
        assert detail['snapshot']['preparation'] == preparation
        replay = client.post(f"/api/v1/investigations/{saved['id']}/replays",
                             headers={'Idempotency-Key': 'prepared-replay'}).json()
        assert worker.run_once()
        assert client.get(f"/api/v1/comparisons/{replay['id']}").json()['state'] == 'succeeded'
