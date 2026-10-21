"""One flat calculation compared across two data loads of the same dataset contract."""
import csv
import json
import shutil
from decimal import Decimal

import pytest

from packages.metricguard_core.common import CoreError, file_hash
from packages.metricguard_core.flat import ingest_flat, parse_flat, validate_contract
from packages.metricguard_core.flat_change import compare_snapshots
from services.supervisor.evidence_artifact import verify_artifact, write_artifact
from services.supervisor.runner import run_job
from services.supervisor.worker import validate_result

HEADER = ['row_id', 'amount', 'status']
SEPTEMBER = [['R1', '120.00', 'completed'], ['R2', '80.50', 'completed'], ['R3', '200.00', 'pending'],
             ['R4', '50.00', 'returned'], ['R5', '', 'completed'], ['R6', '90.00', 'cancelled'],
             ['R8', '10.00', 'completed']]
OCTOBER = [['R1', '120.00', 'returned'], ['R2', '95.50', 'completed'], ['R3', '200.00', 'completed'],
           ['R5', '', 'completed'], ['R6', '90.00', 'cancelled'], ['R7', '30.00', 'completed']]


def _contract(row_key='row_id'):
    return validate_contract({
        'version': 5, 'mode': 'flat_snapshot', 'name': 'Monthly orders', 'row_key': row_key, 'measure': 'amount',
        'precision': 18, 'scale': 2, 'categories': ['status'], 'dimension_types': {'status': 'string'},
        'nullable_categories': [], 'measure_meaning': 'Order amount', 'unit_label': 'INR', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable', 'predicate_policy': 'flat-boolean-v1'})


def _load(tmp_path, name, rows, contract, header=HEADER):
    source = tmp_path / f'{name}.csv'
    with source.open('w', newline='') as stream:
        csv.writer(stream).writerows([header, *rows])
    database = tmp_path / f'{name}.duckdb'
    return database, ingest_flat(source, database, contract)


def _context(contract, before=None, after=None):
    return {'mode': 'flat_snapshot', 'scope': contract['scope'], 'contract_hash': contract['contract_hash'],
            'unit_label': contract['unit_label'], 'values_comparable': True, 'comparison': 'snapshot_change',
            **({'manifest_hash': before['manifest_hash']} if before else {}),
            **({'target_manifest_hash': after['manifest_hash']} if after else {})}


@pytest.fixture
def loads(tmp_path):
    c = _contract()
    sep, sep_manifest = _load(tmp_path, 'september', SEPTEMBER, c)
    oct_, oct_manifest = _load(tmp_path, 'october', OCTOBER, c)
    return c, sep, oct_, _context(c, sep_manifest, oct_manifest)


def _by_key(result):
    return {row['order_id']: row for row in result['evidence']}


def test_sum_change_is_explained_by_added_removed_and_changed_records(loads, tmp_path):
    c, sep, oct_, context = loads
    ir = parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'completed'", c)
    result = compare_snapshots(sep, oct_, ir, context)
    assert (result['baseline_value'], result['target_value'], result['delta_target_minus_baseline']) == ('210.50', '325.50', '115.00')
    assert result['explanation_status'] == 'complete_snapshot_change' and result['residual'] == '0'
    rows = _by_key(result)
    assert {key: (row['change'], row['delta']) for key, row in rows.items()} == {
        'R1': ('left_filter', '-120.00'), 'R2': ('value_changed', '15.00'), 'R3': ('entered_filter', '200.00'),
        'R4': ('removed', '0'), 'R7': ('added', '30.00'), 'R8': ('removed', '-10.00')}
    assert rows['R7']['baseline_present'] is False and rows['R8']['target_present'] is False
    # Unchanged records (R5 blank in both loads, R6) are counted, not listed: they add nothing to the change.
    assert result['change_summary']['unchanged'] == {'records': 2, 'delta': '0'}
    assert sum(Decimal(row['delta']) for row in result['evidence']) == Decimal('115.00')
    assert result['change_summary']['removed'] == {'records': 2, 'delta': '-10.00'}
    assert result['provenance']['target_snapshot_manifest_hash'] == context['target_manifest_hash']
    # the supervisor validator and the evidence artifact accept it
    checked = validate_result(result)
    path = tmp_path / 'evidence.jsonl'
    manifest = write_artifact(path, checked['evidence'])
    verify_artifact(path, manifest, checked['delta_target_minus_baseline'], expected_result={**checked, 'evidence': []})


@pytest.mark.parametrize('tamper', [
    {'change': 'unchanged'},                                    # wrong label for R7
    {'baseline_present': True},                                 # an added record claimed in both loads
    {'baseline_present': False, 'target_present': False},       # present in neither load
])
def test_tampered_change_rows_are_rejected(loads, tmp_path, tamper):
    c, sep, oct_, context = loads
    result = compare_snapshots(sep, oct_, parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'completed'", c), context)
    rows = [dict(row, **tamper) if row['order_id'] == 'R7' else row for row in result['evidence']]
    with pytest.raises(ValueError):
        write_artifact(tmp_path / 'evidence.jsonl', rows)


def test_removed_record_cannot_carry_a_value_in_the_later_load(loads, tmp_path):
    c, sep, oct_, context = loads
    result = compare_snapshots(sep, oct_, parse_flat('SELECT SUM(amount) FROM snapshot', c), context)
    rows = [dict(row, target_included=True, target_contribution='10.00', delta='0.00', membership='included_in_both')
            if row['order_id'] == 'R8' else row for row in result['evidence']]
    with pytest.raises(ValueError):
        write_artifact(tmp_path / 'evidence.jsonl', rows)


def test_count_star_change_counts_added_and_removed_records(loads):
    c, sep, oct_, context = loads
    result = compare_snapshots(sep, oct_, parse_flat('SELECT COUNT(*) FROM snapshot', c), context)
    assert (result['baseline_value'], result['target_value'], result['delta_target_minus_baseline']) == ('7', '6', '-1')
    assert result['aggregate'] == 'count'
    changed = {row['order_id']: (row['change'], row['delta']) for row in result['evidence'] if Decimal(row['delta'])}
    assert changed == {'R4': ('removed', '-1'), 'R7': ('added', '1'), 'R8': ('removed', '-1')}
    # records whose amount changed are still counted once in each load, so they are not listed
    assert 'R2' not in _by_key(result)
    assert result['change_summary']['unchanged']['records'] == 5
    validate_result(result)


def test_loads_with_no_common_row_keys_still_reconcile(tmp_path):
    c = _contract()
    sep, sep_manifest = _load(tmp_path, 'sep', [['A1', '5.00', 'completed'], ['A2', '7.00', 'completed']], c)
    oct_, oct_manifest = _load(tmp_path, 'oct', [['B1', '3.00', 'completed']], c)
    result = compare_snapshots(sep, oct_, parse_flat('SELECT SUM(amount) FROM snapshot', c), _context(c, sep_manifest, oct_manifest))
    assert result['delta_target_minus_baseline'] == '-9.00'
    assert {row['order_id']: row['change'] for row in result['evidence']} == {'A1': 'removed', 'A2': 'removed', 'B1': 'added'}
    assert result['change_summary']['added'] == {'records': 1, 'delta': '3.00'}


def test_loads_under_different_row_keys_are_refused(tmp_path):
    by_row, by_order = _contract('row_id'), _contract('order_id')
    sep, sep_manifest = _load(tmp_path, 'sep', SEPTEMBER, by_row)
    oct_, _ = _load(tmp_path, 'oct', OCTOBER, by_order, header=['order_id', 'amount', 'status'])
    with pytest.raises(CoreError) as error:
        compare_snapshots(sep, oct_, parse_flat('SELECT SUM(amount) FROM snapshot', by_row), _context(by_row, sep_manifest))
    assert error.value.code == 'snapshot_contract_mismatch'


def test_context_must_name_the_comparison_and_pin_both_loads(loads):
    c, sep, oct_, context = loads
    ir = parse_flat('SELECT SUM(amount) FROM snapshot', c)
    with pytest.raises(CoreError, match='context'):
        compare_snapshots(sep, oct_, ir, {k: v for k, v in context.items() if k != 'comparison'})
    with pytest.raises(CoreError) as error:
        compare_snapshots(oct_, sep, ir, context)  # loads swapped against the pinned hashes
    assert error.value.code == 'snapshot_integrity'


def test_runner_reads_the_target_load_and_checks_its_hash(loads, tmp_path):
    c, sep, oct_, context = loads
    ir = parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'completed'", c)
    input_dir, output_dir = tmp_path / 'input', tmp_path / 'output'
    input_dir.mkdir()
    for source, name in ((sep, 'snapshot.duckdb'), (oct_, 'target_snapshot.duckdb')):
        shutil.copyfile(source, input_dir / name)
        shutil.copyfile(source.with_name(source.name + '.manifest.json'), input_dir / (name + '.manifest.json'))
    job = {'snapshot_hash': file_hash(sep), 'target_snapshot_hash': file_hash(oct_), 'baseline_ir': ir, 'target_ir': ir,
           'context': context, 'execution_policy': 'flat_snapshot_v1'}
    (input_dir / 'job.json').write_text(json.dumps(job), encoding='utf-8')
    run_job(input_dir, output_dir)
    result = json.loads((output_dir / 'result.json').read_text(encoding='utf-8'))
    assert result['method'] == 'flat_snapshot_change' and result['delta_target_minus_baseline'] == '115.00'
    verify_artifact(output_dir / 'evidence.jsonl', result['evidence_artifact'], result['delta_target_minus_baseline'],
                    expected_result=result)
    # Two-load runs now write record context: later values, plus the earlier ones under "b", validated against the evidence.
    from services.supervisor.row_context import validate_context
    lines = [json.loads(line) for line in (output_dir / 'context.jsonl').read_text(encoding='utf-8').splitlines()]
    assert {line['k']: (line['b'], line['v']) for line in lines}['R1'] == ({'status': 'completed'}, {'status': 'returned'})
    assert validate_context(output_dir / 'context.jsonl', output_dir / 'evidence.jsonl', ['status'])['row_count'] == len(lines)
    (output_dir / 'context.jsonl').unlink()
    (output_dir / 'context.json').unlink(missing_ok=True)
    # a different target file is refused before any query runs
    job['target_snapshot_hash'] = file_hash(sep)
    (input_dir / 'job.json').write_text(json.dumps(job), encoding='utf-8')
    shutil.rmtree(output_dir)
    with pytest.raises(ValueError, match='SNAPSHOT_HASH_MISMATCH'):
        run_job(input_dir, output_dir)


def test_http_compare_two_loads_end_to_end(tmp_path):
    from fastapi.testclient import TestClient
    from services.api.app import create_app
    from services.api.store import Store
    from services.supervisor.worker import Supervisor
    from tests.test_flat_workflow import InProcessLauncher

    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'change-test-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/flat/contracts', json={
            'version': 4, 'mode': 'flat_snapshot', 'dimension_types': {'status': 'string'}, 'name': 'Orders',
            'row_key': 'row_id', 'measure': 'amount', 'precision': 18, 'scale': 2, 'categories': ['status'],
            'measure_meaning': 'Order amount', 'unit_label': 'INR', 'values_comparable': True,
            'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
        assert contract.status_code == 201, contract.text
        contract = contract.json()
        snapshots = []
        for name, rows in (('September', SEPTEMBER), ('October', OCTOBER)):
            source = tmp_path / f'{name}.csv'
            with source.open('w', newline='') as stream:
                csv.writer(stream).writerows([HEADER, *rows])
            with source.open('rb') as content:
                uploaded = client.post('/api/v1/flat/snapshots', files={'file': (f'{name}.csv', content, 'text/csv')},
                                       data={'contract_id': contract['id'], 'name': name})
            assert uploaded.status_code == 202, uploaded.text
            assert worker.run_once()
            imported = client.get(f"/api/v1/snapshot-imports/{uploaded.json()['id']}").json()
            assert imported['state'] == 'validated', imported['error']
            snapshots.append(imported['snapshot_id'])
        definition = client.post('/api/v1/flat/definitions', json={
            'name': 'Completed', 'raw_sql': "SELECT SUM(amount) FROM snapshot WHERE status = 'completed'",
            'contract_id': contract['id']})
        assert definition.status_code == 201, definition.text
        body = {'version_id': definition.json()['versions'][0]['id'], 'baseline_snapshot_id': snapshots[0],
                'target_snapshot_id': snapshots[1],
                'context': {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot',
                            'contract_hash': contract['contract_hash'], 'unit_label': 'INR', 'values_comparable': True}}
        same = client.post('/api/v1/flat/snapshot-changes/validate', json={**body, 'target_snapshot_id': snapshots[0]})
        assert same.status_code == 422, same.text
        checked = client.post('/api/v1/flat/snapshot-changes/validate', json=body)
        assert checked.status_code == 200, checked.text
        queued = client.post('/api/v1/flat/snapshot-changes', json=body, headers={'Idempotency-Key': 'loads'})
        assert queued.status_code == 202, queued.text
        assert worker.run_once()
        run = client.get(f"/api/v1/comparisons/{queued.json()['id']}").json()
        assert run['state'] == 'succeeded', run['error']
        assert run['target_snapshot_id'] == snapshots[1]
        assert run['result']['delta_target_minus_baseline'] == '115.00'
        assert run['result']['change_summary']['added'] == {'records': 1, 'delta': '30.00'}
        evidence = client.get(f"/api/v1/comparisons/{run['id']}/evidence?limit=100").json()
        assert {row['order_id']: row['change'] for row in evidence['items']}['R7'] == 'added'
        assert sum(Decimal(row['delta']) for row in evidence['items']) == Decimal('115.00')
        assert client.get(f"/api/v1/comparisons/{run['id']}/export?format=html").status_code == 200
        # Where it sits works for two loads: grouped by the later value (the earlier one for removed records).
        split = client.get(f"/api/v1/comparisons/{run['id']}/evidence-breakdown?column=status").json()
        assert split['available'], split
        by_status = {g['value']: Decimal(g['change']) for g in split['groups']}
        assert by_status['returned'] == Decimal('-120.00') and by_status['completed'] == Decimal('235.00')
        assert sum(by_status.values()) == Decimal('115.00')
        # Each record shows its value in each load.
        rows = {r['order_id']: r for r in client.get(f"/api/v1/comparisons/{run['id']}/evidence?limit=100&with_context=true").json()['items']}
        assert rows['R1']['baseline_context'] == {'status': 'completed'} and rows['R1']['target_context'] == {'status': 'returned'}
        assert rows['R7']['baseline_context'] is None and rows['R7']['context'] == {'status': 'completed'}
        assert rows['R8']['target_context'] is None and rows['R8']['context'] == {'status': 'completed'}
        group = client.get(f"/api/v1/comparisons/{run['id']}/evidence-group",
                           params={'column': 'status', 'value': '"completed"', 'with_context': 'true'}).json()
        assert {r['order_id'] for r in group['items']} == {'R2', 'R3', 'R7', 'R8'}
        assert all('baseline_context' in r for r in group['items'])
        assert client.get(f"/api/v1/comparisons/{run['id']}/evidence-concentration").json()['available'] is True
        saved = client.post('/api/v1/investigations', json={
            'title': 'Sep vs Oct', 'run_id': run['id'], 'decision': 'Explained', 'actor_label': 'Tester'})
        assert saved.status_code == 201, saved.text
        replay = client.post(f"/api/v1/investigations/{saved.json()['id']}/replays", headers={'Idempotency-Key': 'loads-replay'})
        assert replay.status_code == 202, replay.text
        assert worker.run_once()
        replayed = client.get(f"/api/v1/comparisons/{replay.json()['id']}").json()
        assert replayed['state'] == 'succeeded', replayed['error']
        assert replayed['result']['delta_target_minus_baseline'] == '115.00'


def test_large_loads_stream_and_list_only_changed_records(loads, tmp_path, monkeypatch):
    """Over FULL_EVIDENCE_ROWS the loads are merged in key order and evidence is kept on disk."""
    import packages.metricguard_core.flat_change as flat_change
    from packages.metricguard_core.disk_evidence import DiskRows, STORAGE_POLICY
    c, sep, oct_, context = loads
    ir = parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'completed'", c)
    small = compare_snapshots(sep, oct_, ir, context)
    monkeypatch.setattr(flat_change, 'FULL_EVIDENCE_ROWS', 3)
    large = compare_snapshots(sep, oct_, ir, context)
    assert isinstance(large['evidence'], DiskRows)
    assert list(large['evidence']) == small['evidence']
    assert large['change_summary'] == small['change_summary']
    assert large['provenance']['evidence_storage_policy'] == STORAGE_POLICY
    evidence = large.pop('evidence')
    stored = {**large, 'evidence': []}
    manifest = write_artifact(tmp_path / 'large.jsonl', evidence, expected_result=stored)
    assert manifest['row_count'] == len(small['evidence'])
    verify_artifact(tmp_path / 'large.jsonl', manifest, large['delta_target_minus_baseline'], expected_result=stored)
