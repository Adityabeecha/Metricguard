"""COUNT(*) on single-table snapshots, record-context files, and breakdowns."""
import csv
import json
from decimal import Decimal

import pytest

from packages.metricguard_core.common import CoreError
from packages.metricguard_core.flat import compare_flat, ingest_flat, parse_flat, validate_contract
from services.api import evidence_cache
from services.supervisor.evidence_artifact import verify_artifact, write_artifact
from services.supervisor.row_context import validate_context, write_flat_context
from services.supervisor.worker import validate_result

ROWS = [['row_id', 'amount', 'status', 'region'],
        ['R1', '120.00', 'completed', 'north'], ['R2', '80.50', 'completed', 'south'],
        ['R3', '200.00', 'pending', 'north'], ['R4', '50.00', 'returned', 'west'],
        ['R5', '', 'completed', 'south'], ['R6', '90.00', 'cancelled', '']]


def _contract(version=5):
    payload = {'version': version, 'mode': 'flat_snapshot', 'name': 'Count test', 'row_key': 'row_id', 'measure': 'amount',
               'precision': 18, 'scale': 2, 'categories': ['status', 'region'],
               'dimension_types': {'status': 'string', 'region': 'string'}, 'nullable_categories': ['region'],
               'measure_meaning': 'Order amount', 'unit_label': 'INR', 'values_comparable': True,
               'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}
    if version == 5:
        payload['predicate_policy'] = 'flat-boolean-v1'
    return validate_contract(payload)


@pytest.fixture
def snapshot(tmp_path):
    source = tmp_path / 'orders.csv'
    with source.open('w', newline='') as stream:
        csv.writer(stream).writerows(ROWS)
    c = _contract()
    database = tmp_path / 'snapshot.duckdb'
    manifest = ingest_flat(source, database, c)
    context = {'mode': 'flat_snapshot', 'scope': c['scope'], 'contract_hash': c['contract_hash'],
               'unit_label': c['unit_label'], 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}
    return c, database, context


def test_count_star_counts_records_including_blank_amounts(snapshot):
    c, database, context = snapshot
    base = parse_flat('SELECT COUNT(*) FROM snapshot', c)
    target = parse_flat("SELECT COUNT(*) FROM snapshot WHERE status = 'completed'", c)
    assert base['aggregate'] == 'count' and base['measure'] == '*'
    result = compare_flat(database, base, target, context)
    assert (result['baseline_value'], result['target_value'], result['delta_target_minus_baseline']) == ('6', '3', '-3')
    assert result['aggregate'] == 'count' and result['residual'] == '0'
    changed = sorted(r['order_id'] for r in result['evidence'] if Decimal(r['delta']))
    assert changed == ['R3', 'R4', 'R6']
    # the same result passes the supervisor's validator and the evidence artifact round trip
    checked = validate_result(result)
    path = database.parent / 'evidence.jsonl'
    manifest = write_artifact(path, checked['evidence'])
    verify_artifact(path, manifest, checked['delta_target_minus_baseline'], expected_result={**checked, 'evidence': []})


def test_count_of_nothing_is_zero_not_undefined(snapshot):
    c, database, context = snapshot
    base = parse_flat('SELECT COUNT(*) FROM snapshot', c)
    target = parse_flat("SELECT COUNT(*) FROM snapshot WHERE status = 'nope'", c)
    result = compare_flat(database, base, target, context)
    assert result['target_value'] == '0' and result['delta_target_minus_baseline'] == '-6'


def test_count_and_sum_cannot_be_compared_and_other_counts_are_refused(snapshot):
    c, database, context = snapshot
    with pytest.raises(CoreError, match='same aggregate'):
        compare_flat(database, parse_flat('SELECT SUM(amount) FROM snapshot', c), parse_flat('SELECT COUNT(*) FROM snapshot', c), context)
    # COUNT(DISTINCT column) is supported since 2026-10-18 (tests/test_distinct_and_ratio.py).
    for sql in ['SELECT COUNT(region) FROM snapshot', 'SELECT COUNT(1) FROM snapshot']:
        with pytest.raises(CoreError, match='Only COUNT'):
            parse_flat(sql, c)


def test_existing_sum_ir_hash_is_unchanged():
    c = _contract(version=4)
    ir = parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'completed'", c)
    assert ir['aggregate'] == 'sum' and ir['measure'] == 'amount'
    assert set(ir) == {'mode', 'policy', 'contract_hash', 'table', 'aggregate', 'measure', 'predicates', 'ir_hash'}


def test_context_file_matches_evidence_and_drives_exact_breakdown(snapshot, tmp_path):
    c, database, context = snapshot
    result = compare_flat(database, parse_flat('SELECT SUM(amount) FROM snapshot', c),
                          parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'completed'", c), context)
    evidence_path = tmp_path / 'run-1.jsonl'
    manifest = write_artifact(evidence_path, result['evidence'])
    ctx_path = tmp_path / 'run-1.context.jsonl'
    info = write_flat_context(database, c, result['evidence'], ctx_path)
    assert validate_context(ctx_path, evidence_path, c['categories'])['sha256'] == info['sha256']

    evidence_cache.clear()
    key = (str(evidence_path), 'run', 1, manifest['sha256'])
    values = evidence_cache.load_context(ctx_path, info)
    assert values['R6'] == {'status': 'cancelled', 'region': None}
    split = evidence_cache.breakdown(evidence_path, key, lambda: None, 'status', values)
    by_status = {g['value']: g for g in split['groups']}
    assert by_status['pending']['change'] == '-200.00' and by_status['cancelled']['change'] == '-90.00'
    assert by_status['completed']['change'] == '0.00' or Decimal(by_status['completed']['change']) == 0
    assert Decimal(split['total_change']) == Decimal(result['delta_target_minus_baseline']) == Decimal('-340.00')


def test_context_that_does_not_match_evidence_is_rejected(snapshot, tmp_path):
    c, database, context = snapshot
    result = compare_flat(database, parse_flat('SELECT SUM(amount) FROM snapshot', c),
                          parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'completed'", c), context)
    evidence_path = tmp_path / 'run-2.jsonl'
    write_artifact(evidence_path, result['evidence'])
    ctx_path = tmp_path / 'run-2.context.jsonl'
    write_flat_context(database, c, result['evidence'], ctx_path)
    lines = ctx_path.read_text(encoding='utf-8').splitlines()
    lines[0], lines[1] = lines[1], lines[0]  # out of order
    ctx_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    with pytest.raises(ValueError):
        validate_context(ctx_path, evidence_path, c['categories'])
    bad = json.loads(lines[0]); bad['v'] = {'secret_column': 'x'}
    ctx_path.write_text('\n'.join([json.dumps(bad), *lines[1:]]) + '\n', encoding='utf-8')
    with pytest.raises(ValueError):
        validate_context(ctx_path, evidence_path, c['categories'])
