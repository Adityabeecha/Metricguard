"""Snapshots over FULL_EVIDENCE_ROWS: streamed ingestion and changed-records evidence.

The threshold is lowered on a small fixture so the large path can be compared,
value for value, with the full-evidence path on the same data.
"""
import csv
import json
from decimal import Decimal

import pytest

import packages.metricguard_core.disk_evidence as disk_evidence
import packages.metricguard_core.flat as flat
from packages.metricguard_core.common import CoreError
from packages.metricguard_core.flat_distinct_evidence import validate_members
from services.supervisor.evidence_artifact import aggregate_contexts, expected_artifact_delta, verify_artifact, write_artifact
from services.supervisor.runner import run_job
from services.supervisor.worker import file_hash, validate_result

ROWS = [['k', 'amount', 'status', 'region'],
        ['A', '10.00', 'paid', 'N'], ['B', '5.50', 'refunded', 'S'], ['C', '', 'paid', 'S'],
        ['D', '-2.25', 'void', 'N'], ['E', '7.00', 'refunded', 'N'], ['F', '1.00', 'paid', '']]


def _setup(tmp_path):
    c = flat.validate_contract({'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'large',
        'row_key': 'k', 'measure': 'amount', 'precision': 18, 'scale': 2, 'categories': ['status', 'region'],
        'dimension_types': {'status': 'string', 'region': 'string'}, 'nullable_categories': ['region'],
        'measure_meaning': 'm', 'unit_label': 'INR', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
    source = tmp_path / 'large.csv'
    with source.open('w', newline='', encoding='utf-8') as stream:
        csv.writer(stream).writerows(ROWS)
    database = tmp_path / 'large.duckdb'
    manifest = flat.ingest_flat(source, database, c)
    context = {'mode': 'flat_snapshot', 'scope': c['scope'], 'contract_hash': c['contract_hash'],
               'unit_label': 'INR', 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}
    return c, database, context


def _checked(result, **contexts):
    """Validate a disk-backed result the way the isolated runner does (rows are checked at write time)."""
    evidence = result['evidence']
    placeholder = {'row_count': len(evidence), 'storage_policy': evidence.artifact_policy}
    checked = validate_result({**result, 'evidence': [], 'evidence_artifact': placeholder}, **contexts)
    return {**{key: value for key, value in checked.items() if key != 'evidence_artifact'}, 'evidence': evidence}


@pytest.mark.parametrize('sql_a,sql_b', [
    ("SELECT SUM(amount) FROM snapshot WHERE status = 'paid'", "SELECT SUM(amount) FROM snapshot WHERE status IN ('paid', 'refunded')"),
    ("SELECT COUNT(*) FROM snapshot WHERE region = 'N'", 'SELECT COUNT(*) FROM snapshot'),
    ("SELECT SUM(amount) FROM snapshot WHERE status = 'void'", "SELECT SUM(amount) FROM snapshot WHERE status = 'nope'"),
])
def test_large_path_matches_full_path(tmp_path, monkeypatch, sql_a, sql_b):
    c, database, context = _setup(tmp_path)
    a, b = flat.parse_flat(sql_a, c), flat.parse_flat(sql_b, c)
    full = flat.compare_flat(database, a, b, context)
    monkeypatch.setattr(flat, 'FULL_EVIDENCE_ROWS', 3)
    large = flat.compare_flat(database, a, b, context)
    for field in ('baseline_value', 'target_value', 'delta_target_minus_baseline', 'residual', 'value_status', 'explanation_status'):
        assert large[field] == full[field], field
    changed = [row for row in full['evidence'] if row['baseline_included'] != row['target_included']]
    assert list(large['evidence']) == changed  # identical rows, only the records whose inclusion changed
    assert large['evidence_coverage']['total_records'] == len(ROWS) - 1
    assert large['evidence_coverage']['listed_records'] == len(changed)
    if large['delta_target_minus_baseline'] is not None:
        assert sum(Decimal(r['delta']) for r in large['evidence']) == Decimal(large['delta_target_minus_baseline'])
        checked = _checked(large)
        stored = {**checked, 'evidence': []}
        path = tmp_path / 'e.jsonl'
        manifest = write_artifact(path, checked['evidence'], expected_result=stored)
        assert manifest['storage_policy'] == disk_evidence.STORAGE_POLICY
        verify_artifact(path, manifest, checked['delta_target_minus_baseline'], expected_result=stored)


@pytest.mark.parametrize('sql_a,sql_b', [
    ("SELECT AVG(amount) FROM snapshot WHERE status = 'paid'", 'SELECT AVG(amount) FROM snapshot'),
    ("SELECT SUM(amount) / COUNT(*) FROM snapshot WHERE region = 'N'",
     "SELECT SUM(amount) / COUNT(*) FROM snapshot WHERE status <> 'void'"),
    ("SELECT COUNT(DISTINCT region) FROM snapshot WHERE status = 'paid'",
     "SELECT COUNT(DISTINCT region) FROM snapshot WHERE status IN ('paid', 'refunded')"),
    ("SELECT COUNT(DISTINCT status) FROM snapshot WHERE region = 'S'", 'SELECT COUNT(DISTINCT status) FROM snapshot'),
])
def test_large_ratio_and_distinct_match_full_path(tmp_path, monkeypatch, sql_a, sql_b):
    """Ratios and distinct counts over FULL_EVIDENCE_ROWS keep the same evidence, stored on disk."""
    c, database, context = _setup(tmp_path)
    a, b = flat.parse_flat(sql_a, c), flat.parse_flat(sql_b, c)
    full = flat.compare_flat(database, a, b, context)
    monkeypatch.setattr(flat, 'FULL_EVIDENCE_ROWS', 3)
    large = flat.compare_flat(database, a, b, context)
    for field in ('baseline_value', 'target_value', 'delta_target_minus_baseline', 'residual', 'value_status',
                  'explanation_status', 'aggregate', 'shapley', 'ratio', 'distinct'):
        assert large.get(field) == full.get(field), field
    assert isinstance(large['evidence'], disk_evidence.DiskRows)
    assert list(large['evidence']) == full['evidence']
    assert large['provenance']['evidence_storage_policy'] == disk_evidence.STORAGE_POLICY
    members = large.pop('members', None)
    if members is not None:
        assert list(members) == full['members']
    ratio_context, distinct_context = aggregate_contexts(c, a, b)
    checked = _checked(large, ratio_context=ratio_context, distinct_context=distinct_context)
    stored = {**checked, 'evidence': []}
    path = tmp_path / 'e.jsonl'
    manifest = write_artifact(path, checked['evidence'], ratio_context=ratio_context, distinct_context=distinct_context,
                              expected_result=stored)
    verify_artifact(path, manifest, expected_artifact_delta(stored, ratio_context), expected_result=stored,
                    ratio_context=ratio_context, distinct_context=distinct_context)


def test_large_subtraction_datasets_are_refused(tmp_path, monkeypatch):
    c = flat.validate_contract({'version': 6, 'mode': 'flat_snapshot', 'name': 'net', 'row_key': 'k',
        'decimal_columns': [{'name': 'gross', 'precision': 18, 'scale': 2, 'unit_label': 'INR'},
                            {'name': 'refund', 'precision': 18, 'scale': 2, 'unit_label': 'INR'}],
        'measure_expression': {'op': 'sub', 'minuend': 'gross', 'subtrahend': 'refund'},
        'scale': 2, 'categories': ['s'], 'dimension_types': {'s': 'string'}, 'measure_meaning': 'm',
        'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot',
        'null_policy': 'blank_nullable', 'measure_policy': 'flat-subtraction-v1', 'predicate_policy': 'flat-boolean-v1'})
    source, database = tmp_path / 'net.csv', tmp_path / 'net.duckdb'
    source.write_text('k,gross,refund,s\nA,5.00,1.00,x\nB,4.00,0.00,y\nC,3.00,1.00,x\nD,2.00,0.00,y\n', encoding='utf-8')
    manifest = flat.ingest_flat(source, database, c)
    context = {'mode': 'flat_snapshot', 'scope': c['scope'], 'contract_hash': c['contract_hash'],
               'unit_label': c['unit_label'], 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}
    monkeypatch.setattr(flat, 'FULL_EVIDENCE_ROWS', 3)
    with pytest.raises(CoreError) as error:
        flat.compare_flat(database, flat.parse_flat('SELECT SUM(gross - refund) FROM snapshot', c),
                          flat.parse_flat("SELECT SUM(gross - refund) FROM snapshot WHERE s = 'x'", c), context)
    assert error.value.code == 'too_many_rows'


def test_streamed_ingest_checks_duplicates_beyond_the_in_memory_window(tmp_path, monkeypatch):
    monkeypatch.setattr(flat, 'FULL_EVIDENCE_ROWS', 2)
    c = flat.validate_contract({'version': 3, 'mode': 'flat_snapshot', 'name': 'dupes', 'row_key': 'k', 'measure': 'amount',
        'precision': 10, 'scale': 2, 'categories': ['s'], 'measure_meaning': 'm', 'unit_label': 'INR',
        'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
    source = tmp_path / 'dupes.csv'
    source.write_text('k,amount,s\nA,1.00,x\nB,1.00,x\nC,1.00,x\nD,1.00,x\nB,2.00,y\n', encoding='utf-8')
    with pytest.raises(CoreError, match="duplicate row key 'B'"):
        flat.ingest_flat(source, tmp_path / 'dupes.duckdb', c)
    assert not (tmp_path / 'dupes.duckdb').exists()


@pytest.mark.parametrize('aggregate', ['SUM(amount)', 'COUNT(*)'])
def test_large_identical_definitions_report_all_unchanged(tmp_path, monkeypatch, aggregate):
    c, database, context = _setup(tmp_path)
    monkeypatch.setattr(flat, 'FULL_EVIDENCE_ROWS', 3)
    ir = flat.parse_flat(f'SELECT {aggregate} FROM snapshot', c)
    result = flat.compare_flat(database, ir, ir, context)
    assert list(result['evidence']) == []
    assert result['delta_target_minus_baseline'] in ('0', '0.00')
    assert result['evidence_coverage'] == {
        'mode': 'changed_records', 'total_records': 6, 'listed_records': 0,
        'unchanged_records': 6, 'unchanged_included_in_both': 6,
        'unchanged_included_in_neither': 0}


@pytest.mark.parametrize('bound,value', [('MAX_DISK_ROWS', 1), ('MAX_DISK_BYTES', 1), ('MAX_SCRATCH_BYTES', 1)])
def test_large_changed_evidence_is_bounded(tmp_path, monkeypatch, bound, value):
    """Changed records are no longer capped at 100,000; only the disk row and byte budgets apply."""
    c, database, context = _setup(tmp_path)
    monkeypatch.setattr(flat, 'FULL_EVIDENCE_ROWS', 3)
    monkeypatch.setattr(disk_evidence, bound, value)
    a = flat.parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'paid'", c)
    b = flat.parse_flat('SELECT SUM(amount) FROM snapshot', c)
    with pytest.raises(CoreError) as error:
        flat.compare_flat(database, a, b, context)
    assert error.value.code == 'evidence_limit'


def test_large_sum_allows_intermediate_overflow_with_exact_cancellation(tmp_path, monkeypatch):
    monkeypatch.setattr(flat, 'FULL_EVIDENCE_ROWS', 2)
    c = flat.validate_contract({'version': 3, 'mode': 'flat_snapshot', 'name': 'cancel',
        'row_key': 'k', 'measure': 'amount', 'precision': 38, 'scale': 0,
        'categories': ['s'], 'measure_meaning': 'm', 'unit_label': 'items',
        'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
    source, database = tmp_path / 'cancel.csv', tmp_path / 'cancel.duckdb'
    maximum = '9' * 38
    source.write_text(f'k,amount,s\nA,{maximum},x\nB,{maximum},x\nC,-{maximum},x\nD,-{maximum},x\n', encoding='utf-8')
    manifest = flat.ingest_flat(source, database, c)
    context = {'mode': 'flat_snapshot', 'scope': c['scope'], 'contract_hash': c['contract_hash'],
               'unit_label': c['unit_label'], 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}
    ir = flat.parse_flat('SELECT SUM(amount) FROM snapshot', c)
    result = flat.compare_flat(database, ir, ir, context)
    assert result['baseline_value'] == result['target_value'] == '0'
    assert result['residual'] == '0'


def test_failed_streamed_validation_closes_sink_before_cleanup(tmp_path, monkeypatch):
    sinks = []
    original = flat._RowSink.__init__
    def capture(self, *args, **kwargs):
        original(self, *args, **kwargs)
        sinks.append(self)
    monkeypatch.setattr(flat._RowSink, '__init__', capture)
    c, _, _ = _setup(tmp_path)
    source = tmp_path / 'invalid.csv'
    source.write_text('k,amount,status,region\nA,not-a-number,paid,N\n', encoding='utf-8')
    with pytest.raises(CoreError):
        flat.ingest_flat(source, tmp_path / 'invalid.duckdb', c)
    assert all(sink.stream.closed for sink in sinks)
    assert not list(tmp_path.glob('flat-*'))


@pytest.mark.parametrize('field', ['total_records', 'listed_records', 'unchanged_records',
                                  'unchanged_included_in_both', 'unchanged_included_in_neither'])
def test_supervisor_rejects_inconsistent_large_coverage(tmp_path, monkeypatch, field):
    c, database, context = _setup(tmp_path)
    monkeypatch.setattr(flat, 'FULL_EVIDENCE_ROWS', 3)
    ir = flat.parse_flat('SELECT COUNT(*) FROM snapshot', c)
    result = flat.compare_flat(database, ir, ir, context)
    result['evidence_coverage'][field] += 1
    with pytest.raises(ValueError, match='coverage'):
        _checked(result)


def test_empty_changed_record_context_does_not_open_snapshot(tmp_path, monkeypatch):
    import duckdb
    from services.supervisor.row_context import write_flat_context
    def unexpected_connection(*args, **kwargs):
        raise AssertionError('Empty context must not open the snapshot')
    monkeypatch.setattr(duckdb, 'connect', unexpected_connection)
    path = tmp_path / 'empty.context.jsonl'
    info = write_flat_context(tmp_path / 'absent.duckdb', {'row_key': 'k', 'categories': ['s']}, [], path)
    assert info['row_count'] == info['byte_count'] == 0
    assert path.read_bytes() == b''


def test_ratio_and_distinct_over_100k_rows_through_the_runner(tmp_path):
    """A real snapshot over FULL_EVIDENCE_ROWS: a ratio and a distinct count run end to end
    in the runner, and the supervisor's artifact and member checks accept the output."""
    rows = flat.FULL_EVIDENCE_ROWS + 1
    c = flat.validate_contract({'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot',
        'name': 'big', 'row_key': 'k', 'measure': 'amount', 'precision': 18, 'scale': 2,
        'categories': ['status', 'customer'], 'dimension_types': {'status': 'string', 'customer': 'string'},
        'nullable_categories': ['customer'], 'measure_meaning': 'm', 'unit_label': 'INR', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
    source = tmp_path / 'big.csv'
    statuses = ('paid', 'refunded', 'void')
    with source.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['k', 'amount', 'status', 'customer'])
        for i in range(rows):
            writer.writerow([f'R{i:07d}', f'{i % 97}.{i % 100:02d}', statuses[i % 3],
                             '' if i % 50 == 0 else f'C{i % 40_000:05d}'])
    input_dir = tmp_path / 'input'
    input_dir.mkdir()
    database = input_dir / 'snapshot.duckdb'
    manifest = flat.ingest_flat(source, database, c)
    assert manifest['row_count'] == rows > flat.FULL_EVIDENCE_ROWS
    context = {'mode': 'flat_snapshot', 'scope': c['scope'], 'contract_hash': c['contract_hash'],
               'unit_label': c['unit_label'], 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}
    cases = {'ratio': ("SELECT AVG(amount) FROM snapshot WHERE status = 'paid'",
                       "SELECT AVG(amount) FROM snapshot WHERE status IN ('paid', 'refunded')"),
             'distinct': ("SELECT COUNT(DISTINCT customer) FROM snapshot WHERE status = 'paid'",
                          "SELECT COUNT(DISTINCT customer) FROM snapshot WHERE status <> 'void'")}
    for name, (sql_a, sql_b) in cases.items():
        a, b = flat.parse_flat(sql_a, c), flat.parse_flat(sql_b, c)
        (input_dir / 'job.json').write_text(json.dumps({
            'execution_policy': 'flat_snapshot_v1', 'snapshot_hash': file_hash(database),
            'baseline_ir': a, 'target_ir': b, 'context': context}), encoding='utf-8')
        output_dir = tmp_path / f'out-{name}'
        run_job(input_dir, output_dir)
        result = json.loads((output_dir / 'result.json').read_text(encoding='utf-8'))
        artifact = result['evidence_artifact']
        assert artifact['storage_policy'] == disk_evidence.STORAGE_POLICY
        assert result['provenance']['evidence_storage_policy'] == disk_evidence.STORAGE_POLICY
        ratio_context, distinct_context = aggregate_contexts(c, a, b)
        verify_artifact(output_dir / 'evidence.jsonl', artifact, expected_artifact_delta(result, ratio_context),
                        expected_result=result, ratio_context=ratio_context, distinct_context=distinct_context)
        if name == 'ratio':
            assert result['aggregate'] == 'ratio' and result['value_status'] == 'defined'
            assert artifact['row_count'] == rows  # every record carries its share of the ratio change
        else:
            assert result['aggregate'] == 'count_distinct'
            entities = len({i % 40_000 for i in range(rows) if i % 50})
            assert int(result['baseline_value']) < int(result['target_value']) <= entities
            assert artifact['row_count'] == entities  # one evidence row per customer
            assert validate_members(output_dir / 'members.jsonl', output_dir / 'evidence.jsonl', result,
                                    row_count=rows)['row_count'] == rows
