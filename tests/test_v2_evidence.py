"""Isolated evidence stays bounded and is verified before it is served."""

import hashlib
import json
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest

from scripts.seed_demo import seed
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.runner import run_job
from services.supervisor.evidence_artifact import verify_artifact, write_artifact
from services.supervisor.worker import Supervisor


def test_verifier_rejects_rows_without_inclusion_and_null_flags(tmp_path):
    path = tmp_path / 'incomplete.jsonl'
    raw = (json.dumps({'order_id': 'x', 'membership': 'target_only',
                       'baseline_contribution': '0', 'target_contribution': '1',
                       'delta': '1'}) + '\n').encode()
    path.write_bytes(raw)
    manifest = {'sha256': hashlib.sha256(raw).hexdigest(), 'row_count': 1,
                'byte_count': len(raw), 'delta_sum': '1'}

    with pytest.raises(ValueError, match='EVIDENCE_INVALID'):
        verify_artifact(path, manifest, '1', limit=1)


@pytest.mark.parametrize('membership', ['arbitrary', 'included_in_neither'])
def test_verifier_rejects_membership_that_disagrees_with_flags(tmp_path, membership):
    path = tmp_path / 'bad-membership.jsonl'
    row = {'order_id': 'x', 'baseline_included': False, 'target_included': True,
           'baseline_null_term': False, 'target_null_term': False,
           'membership': membership, 'baseline_contribution': '0',
           'target_contribution': '1', 'delta': '1'}
    raw = (json.dumps(row) + '\n').encode()
    path.write_bytes(raw)
    manifest = {'sha256': hashlib.sha256(raw).hexdigest(), 'row_count': 1,
                'byte_count': len(raw), 'delta_sum': '1'}

    with pytest.raises(ValueError, match='EVIDENCE_INVALID'):
        verify_artifact(path, manifest, '1', limit=1)


def test_runner_writes_a_compact_summary_and_row_artifact(monkeypatch, tmp_path):
    input_dir, output_dir = tmp_path / 'input', tmp_path / 'output'
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / 'snapshot.duckdb').write_bytes(b'frozen snapshot')
    (input_dir / 'job.json').write_text(json.dumps({
        'snapshot_hash': hashlib.sha256(b'frozen snapshot').hexdigest(),
        'baseline_ir': {}, 'target_ir': {}, 'context': {},
    }), encoding='utf-8')
    expected = {
        'baseline_value': '10', 'target_value': '13',
        'delta_target_minus_baseline': '3', 'absolute_gap': '3',
        'residual': '3', 'steps': [], 'provenance': {},
        'explanation_status': 'partial',
        'evidence': [
            {'order_id': 'a', 'delta': '1', 'membership': 'target_only',
             'baseline_included': False, 'target_included': True,
             'baseline_null_term': False, 'target_null_term': False,
             'baseline_contribution': '0', 'target_contribution': '1'},
            {'order_id': 'b', 'delta': '2', 'membership': 'target_only',
             'baseline_included': False, 'target_included': True,
             'baseline_null_term': False, 'target_null_term': False,
             'baseline_contribution': '0', 'target_contribution': '2'},
        ],
    }
    monkeypatch.setattr('packages.metricguard_core.compare', lambda *_: expected)

    run_job(input_dir, output_dir)

    summary = json.loads((output_dir / 'result.json').read_text(encoding='utf-8'))
    raw = (output_dir / 'evidence.jsonl').read_bytes()
    assert summary['evidence'] == []
    assert summary['evidence_artifact'] == {
        'sha256': hashlib.sha256(raw).hexdigest(),
        'row_count': 2,
        'byte_count': len(raw),
        'delta_sum': '3',
    }
    assert [json.loads(line) for line in raw.splitlines()] == expected['evidence']
    assert len((output_dir / 'result.json').read_bytes()) < len(json.dumps(expected).encode('utf-8'))


def test_undefined_endpoint_retains_verified_rows_without_claiming_a_gap(tmp_path):
    path = tmp_path / 'undefined.jsonl'
    rows = [{'order_id': 'null-baseline', 'delta': '2',
             'membership': 'target_only', 'baseline_contribution': '0',
             'target_contribution': '2', 'baseline_included': False,
             'target_included': True, 'baseline_null_term': False,
             'target_null_term': False}]
    manifest = write_artifact(path, rows)

    assert verify_artifact(path, manifest, None, offset=0, limit=1) == rows

    empty = tmp_path / 'empty.jsonl'
    empty_manifest = write_artifact(empty, [])
    assert verify_artifact(empty, empty_manifest, None, offset=0, limit=1) == []


class _CompletedProcess:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def _queue_golden(client):
    bootstrap = client.get('/api/v1/bootstrap').json()
    context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01',
               'timezone': 'Asia/Kolkata', 'currency': 'INR', 'unit': 'currency',
               'grain': 'one_row_per_order_id', 'scope': 'synthetic_retail_all_orders'}
    body = {'baseline_version_id': bootstrap['definitions'][0]['versions'][0]['id'],
            'target_version_id': bootstrap['definitions'][2]['versions'][0]['id'],
            'snapshot_id': bootstrap['snapshots'][0]['id'], 'context': context,
            'relationship': 'expected_agreement'}
    response = client.post('/api/v1/comparisons', json=body,
                           headers={'Idempotency-Key': 'v2-evidence-golden'})
    assert response.status_code == 202, response.text
    return response.json()['id']


def _run_golden(tmp_path, launcher=None):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    app = create_app(database_url=url, data_dir=tmp_path, mode='isolated')
    client = TestClient(app)
    run_id = _queue_golden(client)

    class InProcessLauncher:
        def start(self, input_dir, output_dir):
            run_job(input_dir, output_dir)
            if launcher:
                launcher(output_dir)
            return _CompletedProcess()

    supervisor = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs',
                            'test-worker', data_dir=tmp_path)
    assert supervisor.run_once()
    return client, run_id


def test_supervisor_publishes_verified_evidence_and_api_pages_it(tmp_path):
    client, run_id = _run_golden(tmp_path)
    try:
        run = client.get(f'/api/v1/comparisons/{run_id}').json()
        assert run['state'] == 'succeeded', run['error']
        assert run['result']['evidence'] == []
        assert run['result']['evidence_artifact']['row_count'] == 8
        assert str(tmp_path) not in json.dumps(run)

        page = client.get(f'/api/v1/comparisons/{run_id}/evidence?offset=2&limit=3')
        assert page.status_code == 200, page.text
        assert page.json()['total'] == 8
        assert len(page.json()['items']) == 3
        all_rows = client.get(f'/api/v1/comparisons/{run_id}/evidence').json()['items']
        assert sum((Decimal(row['delta']) for row in all_rows), Decimal(0)) == Decimal('-6000000')

        exported = client.get(f'/api/v1/comparisons/{run_id}/export?format=json')
        assert exported.status_code == 200, exported.text
        assert exported.json()['evidence_coverage'] == {
            'total_rows': 8, 'included_rows': 8, 'omitted_rows': 0,
            'detail': 'first_rows',
        }
        assert len(exported.json()['result']['evidence']) == 8
    finally:
        client.close()


def test_supervisor_refuses_false_artifact_count_before_publishing(tmp_path):
    def falsify_count(output_dir):
        path = output_dir / 'result.json'
        result = json.loads(path.read_text(encoding='utf-8'))
        result['evidence_artifact']['row_count'] += 1
        path.write_text(json.dumps(result), encoding='utf-8')

    client, run_id = _run_golden(tmp_path, falsify_count)
    try:
        run = client.get(f'/api/v1/comparisons/{run_id}').json()
        assert run['state'] == 'failed'
        assert run['error']['code'] == 'RUNNER_OUTPUT_INVALID'
        assert not list((tmp_path / 'evidence').glob('*.jsonl'))
    finally:
        client.close()


def test_supervisor_refuses_a_changed_publish_copy(monkeypatch, tmp_path):
    from services.supervisor import worker

    original_copy = worker.shutil.copyfile

    def changing_copy(source, destination, *args, **kwargs):
        copied = original_copy(source, destination, *args, **kwargs)
        if str(destination).endswith('.tmp'):
            path = worker.Path(destination)
            raw = bytearray(path.read_bytes())
            start = raw.index(b'"order_id":"') + len(b'"order_id":"')
            raw[start] = ord('Z') if raw[start] != ord('Z') else ord('Y')
            path.write_bytes(raw)
        return copied

    monkeypatch.setattr(worker.shutil, 'copyfile', changing_copy)
    client, run_id = _run_golden(tmp_path)
    try:
        run = client.get(f'/api/v1/comparisons/{run_id}').json()
        assert run['state'] == 'failed'
        assert run['error']['code'] == 'RUNNER_OUTPUT_INVALID'
        assert not list((tmp_path / 'evidence').glob('*.jsonl'))
        assert not list((tmp_path / 'evidence').glob('*.tmp'))
    finally:
        client.close()


def test_api_refuses_tampered_evidence_for_pages_and_exports(tmp_path):
    client, run_id = _run_golden(tmp_path)
    try:
        artifact = next((tmp_path / 'evidence').glob('*.jsonl'))
        raw = bytearray(artifact.read_bytes())
        key_start = raw.index(b'"order_id":"') + len(b'"order_id":"')
        raw[key_start] = ord('X') if raw[key_start] != ord('X') else ord('Y')
        artifact.write_bytes(raw)
        page = client.get(f'/api/v1/comparisons/{run_id}/evidence')
        exported = client.get(f'/api/v1/comparisons/{run_id}/export?format=json')
        assert page.status_code == 422
        assert page.json()['error']['code'] == 'EVIDENCE_INTEGRITY'
        assert exported.status_code == 422
        assert exported.json()['error']['code'] == 'EVIDENCE_INTEGRITY'
    finally:
        client.close()


def test_export_limits_row_detail_and_reports_coverage(monkeypatch, tmp_path):
    rows = [{'order_id': f'row-{index:03d}', 'delta': '1',
             'membership': 'target_only', 'baseline_contribution': '0',
             'target_contribution': '1', 'baseline_included': False,
             'target_included': True, 'baseline_null_term': False,
             'target_null_term': False} for index in range(101)]
    result = {'baseline_value': '0', 'target_value': '101',
              'delta_target_minus_baseline': '101', 'absolute_gap': '101',
              'residual': '101', 'steps': [], 'provenance': {'evidence_unit': 'order_id'},
              'explanation_status': 'partial', 'definition_status': 'different',
              'value_status': 'different_on_snapshot', 'currency': 'INR',
              'limitations': [], 'evidence': rows}
    monkeypatch.setattr('packages.metricguard_core.compare', lambda *_: result)
    client, run_id = _run_golden(tmp_path)
    try:
        exported = client.get(f'/api/v1/comparisons/{run_id}/export?format=json')
        assert exported.status_code == 200, exported.text
        bundle = exported.json()
        assert bundle['evidence_coverage'] == {
            'total_rows': 101, 'included_rows': 100, 'omitted_rows': 1,
            'detail': 'first_rows',
        }
        assert len(bundle['result']['evidence']) == 100
        assert bundle['result']['evidence'][0]['order_id'] == 'row-000'
        assert bundle['result']['evidence'][-1]['order_id'] == 'row-099'
        html = client.get(f'/api/v1/comparisons/{run_id}/export?format=html')
        assert html.status_code == 200, html.text
        assert 'Included 100 of 101 rows; omitted 1' in html.text
        assert 'row-100' not in html.text
    finally:
        client.close()
