"""COUNT(DISTINCT) and ratio metrics on single-table snapshots.

Fixtures are small enough to check by hand (see
docs/superpowers/specs/2026-10-18-distinct-and-ratio-design.md §6). The oracle below
uses only Python Fractions over the fixture rows; it imports nothing from the engine.
"""
import csv
import json
import random
from decimal import Decimal
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from packages.metricguard_core.common import CoreError
from packages.metricguard_core.flat import compare_flat, ingest_flat, parse_flat, validate_contract
from packages.metricguard_core.flat_distinct_evidence import validate_members, write_members
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.evidence_artifact import aggregate_contexts, expected_artifact_delta, verify_artifact, write_artifact
from services.supervisor.runner import ingest_job, run_job
from services.supervisor.worker import Supervisor, validate_result


def _contract(categories, types, version=5, scale=2):
    payload = {'version': version, 'mode': 'flat_snapshot', 'name': 'fixture', 'row_key': 'k', 'measure': 'amount',
               'precision': 18, 'scale': scale, 'categories': categories, 'measure_meaning': 'Amount',
               'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot',
               'null_policy': 'blank_nullable'}
    if version >= 4:
        payload.update(dimension_types=types, nullable_categories=categories)
    if version == 5:
        payload['predicate_policy'] = 'flat-boolean-v1'
    return validate_contract(payload)


def _snapshot(tmp_path, name, header, rows, contract):
    source = tmp_path / f'{name}.csv'
    with source.open('w', newline='', encoding='utf-8') as stream:
        csv.writer(stream).writerows([header, *rows])
    database = tmp_path / f'{name}.duckdb'
    manifest = ingest_flat(source, database, contract)
    context = {'mode': 'flat_snapshot', 'scope': contract['scope'], 'contract_hash': contract['contract_hash'],
               'unit_label': contract['unit_label'], 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}
    return database, context


# ---------- COUNT(DISTINCT) ----------

DISTINCT_ROWS = [['R1', '1.00', 'C1', 'paid', 'retail', 'N'], ['R2', '1.00', 'C1', 'refunded', 'retail', 'N'],
                 ['R3', '1.00', 'C2', 'paid', 'trade', 'N'], ['R4', '1.00', 'C3', 'refunded', 'retail', 'S'],
                 ['R5', '1.00', 'C3', 'refunded', 'retail', 'S'], ['R6', '1.00', '', 'paid', 'retail', 'N'],
                 ['R7', '1.00', 'C4', 'pending', 'trade', 'S'], ['R8', '1.00', 'C2', 'paid', 'trade', 'S']]
DISTINCT_HEADER = ['k', 'amount', 'customer_id', 'status', 'segment', 'region']
CATS = ['customer_id', 'status', 'segment', 'region']


@pytest.fixture
def distinct_snapshot(tmp_path):
    c = _contract(CATS, {name: 'string' for name in CATS})
    database, context = _snapshot(tmp_path, 'distinct', DISTINCT_HEADER, DISTINCT_ROWS, c)
    return c, database, context


def test_distinct_gain_has_entity_evidence_that_adds_up(distinct_snapshot):
    c, database, context = distinct_snapshot
    a = parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status = 'paid'", c)
    b = parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status IN ('paid', 'refunded')", c)
    assert a['aggregate'] == 'count_distinct' and a['measure'] == 'customer_id' and a['aggregate_policy'] == 'flat-distinct-v1'
    result = compare_flat(database, a, b, context)
    assert (result['baseline_value'], result['target_value'], result['delta_target_minus_baseline']) == ('2', '3', '1')
    rows = {r['order_id']: r for r in result['evidence']}
    assert set(rows) == {'C1', 'C2', 'C3', 'C4'}
    assert (rows['C1']['baseline_supporting_rows'], rows['C1']['target_supporting_rows']) == (1, 2)
    assert rows['C3']['membership'] == 'target_only' and rows['C3']['delta'] == '1'
    assert rows['C4']['membership'] == 'included_in_neither'
    assert sum(int(r['delta']) for r in result['evidence']) == 1
    assert result['distinct']['gained'] == 1 and result['distinct']['lost'] == 0
    assert result['distinct']['baseline_null_entity_rows'] == 1 and result['distinct']['target_null_entity_rows'] == 1
    assert result['provenance']['evidence_unit'] == 'entity:customer_id'


def test_distinct_zero_net_change_still_shows_churn(distinct_snapshot):
    c, database, context = distinct_snapshot
    result = compare_flat(database, parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status = 'paid'", c),
                          parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status = 'refunded'", c), context)
    assert result['delta_target_minus_baseline'] == '0'
    assert (result['distinct']['gained'], result['distinct']['lost'], result['distinct']['kept']) == (1, 1, 1)
    assert result['distinct']['target_null_entity_rows'] == 0


def test_distinct_of_nothing_is_zero(distinct_snapshot):
    c, database, context = distinct_snapshot
    result = compare_flat(database, parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status = 'paid'", c),
                          parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status = 'void'", c), context)
    assert result['target_value'] == '0' and result['delta_target_minus_baseline'] == '-2'


@pytest.mark.parametrize('sql', ['SELECT COUNT(DISTINCT k) FROM snapshot', 'SELECT COUNT(DISTINCT amount) FROM snapshot',
                                 'SELECT COUNT(DISTINCT customer_id, status) FROM snapshot',
                                 'SELECT COUNT(DISTINCT missing) FROM snapshot'])
def test_distinct_refusals(sql):
    with pytest.raises(CoreError):
        parse_flat(sql, _contract(CATS, {name: 'string' for name in CATS}))


def test_distinct_refuses_different_columns_and_mixed_aggregates(distinct_snapshot):
    c, database, context = distinct_snapshot
    with pytest.raises(CoreError, match='same column'):
        compare_flat(database, parse_flat('SELECT COUNT(DISTINCT customer_id) FROM snapshot', c),
                     parse_flat('SELECT COUNT(DISTINCT segment) FROM snapshot', c), context)
    with pytest.raises(CoreError, match='same aggregate'):
        compare_flat(database, parse_flat('SELECT COUNT(*) FROM snapshot', c),
                     parse_flat('SELECT COUNT(DISTINCT segment) FROM snapshot', c), context)


@pytest.mark.parametrize('version', [3, 4])
def test_distinct_on_older_contracts(tmp_path, version):
    c = _contract(CATS, {name: 'string' for name in CATS}, version=version)
    database, context = _snapshot(tmp_path, f'v{version}', DISTINCT_HEADER, DISTINCT_ROWS, c)
    result = compare_flat(database, parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status = 'paid'", c),
                          parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status = 'refunded'", c), context)
    assert (result['baseline_value'], result['target_value']) == ('2', '2')


def test_distinct_artifact_members_and_tampering(distinct_snapshot, tmp_path):
    c, database, context = distinct_snapshot
    a = parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status = 'paid'", c)
    b = parse_flat("SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status IN ('paid', 'refunded')", c)
    result = compare_flat(database, a, b, context)
    members = result.pop('members')
    ratio_context, distinct_context = aggregate_contexts(c, a, b)
    assert ratio_context is None
    checked = validate_result(result, distinct_context=distinct_context)
    path = tmp_path / 'distinct.jsonl'
    manifest = write_artifact(path, checked['evidence'], distinct_context=distinct_context, expected_result=checked)
    stored = {**checked, 'evidence': []}
    verify_artifact(path, manifest, expected_artifact_delta(stored, None), expected_result=stored, distinct_context=distinct_context)
    members_path = tmp_path / 'members.jsonl'
    write_members(members_path, members)
    assert validate_members(members_path, path, stored, row_count=len(DISTINCT_ROWS))['row_count'] == 8
    # Without the distinct context, the artifact is refused (its binding says what it is).
    with pytest.raises(ValueError):
        verify_artifact(path, manifest, stored['delta_target_minus_baseline'], expected_result=stored)
    # A flipped inclusion in the membership list no longer matches the entity counts.
    lines = members_path.read_text(encoding='utf-8').splitlines()
    first = json.loads(lines[0]); first['t'] = not first['t']
    members_path.write_text('\n'.join([json.dumps(first), *lines[1:]]) + '\n', encoding='utf-8')
    with pytest.raises(ValueError):
        validate_members(members_path, path, stored)
    # A tampered entity row fails its own checks.
    rows = path.read_text(encoding='utf-8').splitlines()
    row = json.loads(rows[2]); row['delta'] = '0'
    path.write_text('\n'.join([*rows[:2], json.dumps(row, separators=(',', ':')), *rows[3:]]) + '\n', encoding='utf-8')
    with pytest.raises(ValueError):
        verify_artifact(path, manifest, stored['delta_target_minus_baseline'], expected_result=stored, distinct_context=distinct_context)


# ---------- ratios ----------

RATIO_HEADER = ['k', 'amount', 'status', 'channel', 'converted']
RATIO_ROWS = [['O1', '100.00', 'paid', 'web', 'true'], ['O2', '50.00', 'paid', 'app', 'false'],
              ['O3', '30.00', 'refunded', 'web', 'true'], ['O4', '', 'paid', 'app', 'false'],
              ['O5', '20.00', 'pending', 'web', 'false']]
RATIO_TYPES = {'status': 'string', 'channel': 'string', 'converted': 'boolean'}


@pytest.fixture
def ratio_snapshot(tmp_path):
    c = _contract(list(RATIO_TYPES), RATIO_TYPES)
    database, context = _snapshot(tmp_path, 'ratio', RATIO_HEADER, RATIO_ROWS, c)
    return c, database, context


def test_aov_against_avg_with_a_blank_amount(ratio_snapshot):
    """RT-1: 50 -> 60. O3 joins (below the pooled average 55), O4 (blank) leaves AVG's denominator."""
    c, database, context = ratio_snapshot
    a = parse_flat("SELECT SUM(amount) / COUNT(*) FROM snapshot WHERE status = 'paid'", c)
    b = parse_flat("SELECT AVG(amount) FROM snapshot WHERE status IN ('paid', 'refunded')", c)
    result = compare_flat(database, a, b, context)
    ratio = result['ratio']
    assert (ratio['baseline']['numerator'], ratio['baseline']['denominator'], ratio['baseline']['exact']) == ('150.00', '3', '50')
    assert (ratio['target']['numerator'], ratio['target']['denominator'], ratio['target']['exact']) == ('180.00', '3', '60')
    assert (ratio['scale_divisor'], ratio['scaled_gap'], ratio['gap_exact'], ratio['pooled_average']) == ('18', '180.00', '10', '55')
    assert [e['exact'] for e in ratio['effects']] == ['10', '0']
    shares = {r['order_id']: r['delta'] for r in result['evidence']}
    assert shares == {'O1': '0.00', 'O2': '0.00', 'O3': '-150.00', 'O4': '330.00', 'O5': '0.00'}
    assert (result['baseline_value'], result['target_value'], result['delta_target_minus_baseline']) == ('50.000000', '60.000000', '10.000000')
    assert result['residual'] == '0' and result['aggregate'] == 'ratio'


def test_avg_and_sum_over_count_of_amount_share_one_ir(ratio_snapshot):
    c, _, _ = ratio_snapshot
    assert (parse_flat('SELECT AVG(amount) FROM snapshot', c)['ir_hash'] ==
            parse_flat('SELECT (SUM(amount)) / (COUNT(amount)) FROM snapshot', c)['ir_hash'])


def test_conversion_rate_non_terminating(ratio_snapshot):
    """RT-3: 2/5 -> 2/3; change 4/15; record shares O2 = O4 = 4 over Q = 30."""
    c, database, context = ratio_snapshot
    result = compare_flat(database, parse_flat('SELECT COUNT(*) FILTER (WHERE converted) / COUNT(*) FROM snapshot', c),
                          parse_flat("SELECT COUNT(*) FILTER (WHERE converted = TRUE) / COUNT(*) FROM snapshot WHERE channel = 'web'", c),
                          context)
    ratio = result['ratio']
    assert (ratio['baseline']['exact'], ratio['target']['exact'], ratio['gap_exact']) == ('2/5', '2/3', '4/15')
    assert (ratio['scaled_gap'], ratio['scale_divisor']) == ('8', '30')
    assert {r['order_id']: r['delta'] for r in result['evidence']} == {'O1': '0', 'O2': '4', 'O3': '0', 'O4': '4', 'O5': '0'}
    assert result['delta_target_minus_baseline'] == '0.266667'  # 0.666667 - 0.400000
    assert ratio['unit'] == 'share'


def test_share_with_part_filter(ratio_snapshot):
    c, database, context = ratio_snapshot
    result = compare_flat(database, parse_flat("SELECT SUM(amount) FILTER (WHERE channel = 'web') / SUM(amount) FROM snapshot", c),
                          parse_flat("SELECT SUM(amount) FILTER (WHERE channel = 'web') / SUM(amount) FROM snapshot WHERE status = 'paid'", c),
                          context)
    assert (result['ratio']['baseline']['exact'], result['ratio']['target']['exact'], result['ratio']['gap_exact']) == ('3/4', '2/3', '-1/12')


def test_negative_denominator_keeps_divisor_positive(tmp_path):
    c = _contract(['grp'], {'grp': 'string'})
    rows = [['A', '10.00', 'x'], ['B', '-30.00', 'y'], ['C', '5.00', 'x']]
    database, context = _snapshot(tmp_path, 'neg', ['k', 'amount', 'grp'], rows, c)
    a = parse_flat("SELECT SUM(amount) FILTER (WHERE grp = 'x') / SUM(amount) FROM snapshot WHERE grp = 'x'", c)
    b = parse_flat("SELECT SUM(amount) FILTER (WHERE grp = 'x') / SUM(amount) FROM snapshot", c)
    result = compare_flat(database, a, b, context)
    ratio = result['ratio']
    assert ratio['sigma'] == '-1' and not ratio['scale_divisor'].startswith('-')
    total = sum(Fraction(Decimal(r['delta'])) for r in result['evidence']) / Fraction(Decimal(ratio['scale_divisor']))
    assert total == Fraction(15, -15) - Fraction(15, 15)  # 15/-15 - 15/15 = -2


@pytest.mark.parametrize('sql,reason', [
    ("SELECT COUNT(*) FILTER (WHERE converted) / COUNT(*) FROM snapshot WHERE channel = 'store'", 'zero_denominator'),
    ('SELECT SUM(amount) FILTER (WHERE amount IS NULL) / COUNT(*) FROM snapshot', 'null_numerator'),
    ("SELECT SUM(amount) / SUM(amount) FILTER (WHERE status = 'void') FROM snapshot", 'null_denominator'),
])
def test_undefined_ratios_keep_component_evidence(ratio_snapshot, sql, reason):
    c, database, context = ratio_snapshot
    base_sql = ('SELECT COUNT(*) FILTER (WHERE converted) / COUNT(*) FROM snapshot' if 'converted' in sql
                else 'SELECT SUM(amount) / COUNT(*) FROM snapshot' if 'COUNT(*)' in sql
                else 'SELECT SUM(amount) / SUM(amount) FROM snapshot')
    result = compare_flat(database, parse_flat(base_sql, c), parse_flat(sql, c), context)
    assert result['ratio']['target']['undefined_reason'] == reason
    assert result['delta_target_minus_baseline'] is None and result['residual'] is None and result['steps'] == []
    assert result['baseline_value'] is not None
    assert result['explanation_status'] == 'components_only'
    assert all(Decimal(r['delta']) == 0 for r in result['evidence'])
    validate_result(result, ratio_context=aggregate_contexts(c, parse_flat(base_sql, c), parse_flat(sql, c))[0])


@pytest.mark.parametrize('sql', ['SELECT SUM(amount) / COUNT(*) * 100 FROM snapshot', 'SELECT SUM(amount) / NULLIF(COUNT(*), 0) FROM snapshot',
                                 'SELECT AVG(amount) / COUNT(*) FROM snapshot', 'SELECT SUM(amount / 2) / COUNT(*) FROM snapshot',
                                 'SELECT COUNT(DISTINCT status) / COUNT(*) FROM snapshot', 'SELECT SUM(status) / COUNT(*) FROM snapshot'])
def test_ratio_refusals(ratio_snapshot, sql):
    with pytest.raises(CoreError):
        parse_flat(sql, ratio_snapshot[0])


def test_ratio_needs_contract_v5_and_matching_shapes(tmp_path, ratio_snapshot):
    with pytest.raises(CoreError, match='v5'):
        parse_flat('SELECT AVG(amount) FROM snapshot', _contract(list(RATIO_TYPES), RATIO_TYPES, version=4))
    c, database, context = ratio_snapshot
    with pytest.raises(CoreError, match='same kind of total'):
        compare_flat(database, parse_flat('SELECT SUM(amount) / COUNT(*) FROM snapshot', c),
                     parse_flat('SELECT SUM(amount) / SUM(amount) FROM snapshot', c), context)
    with pytest.raises(CoreError, match='same aggregate'):
        compare_flat(database, parse_flat('SELECT SUM(amount) FROM snapshot', c), parse_flat('SELECT AVG(amount) FROM snapshot', c), context)


def test_ratio_artifact_round_trip_and_tampering(ratio_snapshot, tmp_path):
    c, database, context = ratio_snapshot
    a = parse_flat("SELECT SUM(amount) / COUNT(*) FROM snapshot WHERE status = 'paid'", c)
    b = parse_flat("SELECT AVG(amount) FROM snapshot WHERE status IN ('paid', 'refunded')", c)
    result = compare_flat(database, a, b, context)
    ratio_context, _ = aggregate_contexts(c, a, b)
    checked = validate_result(result, ratio_context=ratio_context)
    path = tmp_path / 'ratio.jsonl'
    manifest = write_artifact(path, checked['evidence'], ratio_context=ratio_context, expected_result=checked)
    stored = {**checked, 'evidence': []}
    assert manifest['delta_sum'] == stored['ratio']['scaled_gap'] == expected_artifact_delta(stored, ratio_context)
    verify_artifact(path, manifest, expected_artifact_delta(stored, ratio_context), expected_result=stored, ratio_context=ratio_context)
    # The rounded display change is not the artifact's total.
    with pytest.raises(ValueError):
        verify_artifact(path, manifest, stored['delta_target_minus_baseline'], expected_result=stored, ratio_context=ratio_context)
    # Claimed ratio values that the rows do not support are refused.
    forged = json.loads(json.dumps(stored)); forged['ratio']['target']['exact'] = '61'
    with pytest.raises(ValueError):
        verify_artifact(path, manifest, manifest['delta_sum'], expected_result=forged, ratio_context=ratio_context)
    # A changed record value or share breaks the artifact.
    for field, value in (('target_numerator', '31.00'), ('delta', '-151.00'), ('target_denominator', '0')):
        lines = path.read_text(encoding='utf-8').splitlines()
        row = json.loads(lines[2]); row[field] = value
        bad = tmp_path / f'bad-{field}.jsonl'
        bad.write_text('\n'.join([*lines[:2], json.dumps(row, separators=(',', ':')), *lines[3:]]) + '\n', encoding='utf-8')
        with pytest.raises(ValueError):
            write_artifact(tmp_path / f'rewrite-{field}.jsonl', [json.loads(x) for x in bad.read_text(encoding='utf-8').splitlines()],
                           ratio_context=ratio_context, expected_result=stored)
    # A SUM verifier cannot be pointed at ratio evidence.
    with pytest.raises(ValueError):
        verify_artifact(path, manifest, manifest['delta_sum'], expected_result=stored)


# ---------- independent oracle ----------

LEAVES = [("status = 'paid'", lambda r: r['status'] == 'paid'), ("status = 'refunded'", lambda r: r['status'] == 'refunded'),
          ("channel = 'web'", lambda r: r['channel'] == 'web'), ('converted', lambda r: r['converted'] is True)]


def _oracle(rows, where, num, den):
    """N, D per SQL rules with Fractions; returns (ratio or None, N, D)."""
    def part(kind, cond):
        total, seen = Fraction(0), False
        for r in rows:
            if not (where(r) and cond(r)):
                continue
            if kind == 'sum':
                if r['amount'] is not None:
                    total += r['amount']; seen = True
            elif kind == 'count':
                total += 1; seen = True
            else:  # count of amount
                total += r['amount'] is not None; seen = True
        return total if seen or kind != 'sum' else None
    n, d = part(*num), part(*den)
    return (None if n is None or d is None or d == 0 else n / d), n, d


def test_random_ratios_agree_with_the_oracle(tmp_path):
    generator = random.Random(20261018)
    c = _contract(list(RATIO_TYPES), RATIO_TYPES, scale=3)
    for case in range(40):
        rows, raw = [], []
        for i in range(generator.randint(1, 25)):
            amount = None if generator.random() < 0.15 else Fraction(generator.randint(-5000, 20000), 1000)
            row = {'k': f'R{i:03d}', 'amount': amount, 'status': generator.choice(['paid', 'refunded', 'void']),
                   'channel': generator.choice(['web', 'app']), 'converted': generator.random() < 0.4}
            rows.append(row)
            raw.append([row['k'], '' if amount is None else format(Decimal(amount.numerator) / Decimal(amount.denominator), 'f'),
                        row['status'], row['channel'], 'true' if row['converted'] else 'false'])
        database, context = _snapshot(tmp_path, f'random{case}', RATIO_HEADER, raw, c)
        shapes = [(('sum', 'amount'), ('count', '*')), (('sum', 'amount'), ('countm', 'amount')), (('count', '*'), ('count', '*')),
                  (('sum', 'amount'), ('sum', 'amount'))]
        (nk, _), (dk, _) = generator.choice(shapes)
        sides = []
        for _ in range(2):
            where_sql, where = generator.choice([(None, lambda r: True)] + LEAVES)
            cond_sql, cond = generator.choice([(None, lambda r: True)] + LEAVES)
            n_sql = {'sum': 'SUM(amount)', 'count': 'COUNT(*)'}[nk] + (f' FILTER (WHERE {cond_sql})' if cond_sql else '')
            d_sql = {'sum': 'SUM(amount)', 'count': 'COUNT(*)', 'countm': 'COUNT(amount)'}[dk]
            sql = f'SELECT {n_sql} / {d_sql} FROM snapshot' + (f' WHERE {where_sql}' if where_sql else '')
            sides.append((parse_flat(sql, c), _oracle(rows, where, (nk, cond), (dk, lambda r: True))))
        (a, (r0, n0, d0)), (b, (r1, n1, d1)) = sides
        try:
            result = compare_flat(database, a, b, context)
        except CoreError as error:
            assert error.code == 'different_ratio_shape'
            continue
        ratio = result['ratio']
        for side, n, d in (('baseline', n0, d0), ('target', n1, d1)):
            assert (None if ratio[side]['numerator'] is None else Fraction(Decimal(ratio[side]['numerator']))) == n
            assert (None if ratio[side]['denominator'] is None else Fraction(Decimal(ratio[side]['denominator']))) == d
        if r0 is None or r1 is None:
            assert ratio['status'] == 'components_only'
            continue
        gap = Fraction(ratio['gap_exact'])
        assert gap == r1 - r0
        shares = sum((Fraction(Decimal(r['delta'])) for r in result['evidence']), Fraction(0))
        assert shares / Fraction(Decimal(ratio['scale_divisor'])) == r1 - r0
        assert sum(Fraction(e['exact']) for e in ratio['effects']) == r1 - r0
        assert Fraction(ratio['baseline']['exact']) == r0 and Fraction(ratio['target']['exact']) == r1


# ---------- compatibility ----------

GOLDEN_IR_HASHES = {  # recorded from commit 86f7ffb before distinct/ratio support was added
    3: {'SELECT SUM(amount) FROM snapshot': 'f0f483fc301ea73a485b9365ccf1a15997c4ee8e00b96fa71a2a4a9210d06e33',
        'SELECT COUNT(*) FROM snapshot': 'b53122b3934c31aa7a9f8ecdf44c041c7ff20a75f2f53cbe4887dcdd37df7407'},
}


def test_existing_sum_and_count_ir_hashes_are_unchanged():
    c = validate_contract({'mode': 'flat_snapshot', 'name': 'g', 'row_key': 'k', 'measure': 'amount', 'precision': 10, 'scale': 2,
                           'categories': ['status', 'region', 'flag', 'day'], 'measure_meaning': 'm', 'unit_label': 'INR',
                           'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable',
                           'version': 3})
    for sql, expected in GOLDEN_IR_HASHES[3].items():
        ir = parse_flat(sql, c)
        assert ir['ir_hash'] == expected and 'aggregate_policy' not in ir


# ---------- HTTP, supervisor, runner, breakdown, replay ----------

class _Done:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class _InProcess:
    def start(self, input_dir, output_dir, command=None):
        (ingest_job if command == 'ingest' else run_job)(input_dir, output_dir)
        return _Done()


def test_http_distinct_and_ratio_runs_breakdowns_and_replay(tmp_path):
    source = tmp_path / 'orders.csv'
    with source.open('w', newline='', encoding='utf-8') as stream:
        csv.writer(stream).writerows([['k', 'amount', 'customer_id', 'status', 'segment', 'region'],
                                      *[[r[0], r[1] if r[0] != 'R6' else '', *r[2:]] for r in DISTINCT_ROWS]])
    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), _InProcess(), tmp_path / 'jobs', 'distinct-ratio-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/flat/contracts', json={
            'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'Orders', 'row_key': 'k',
            'measure': 'amount', 'precision': 18, 'scale': 2, 'categories': CATS,
            'dimension_types': {name: 'string' for name in CATS}, 'nullable_categories': CATS,
            'measure_meaning': 'Order amount', 'unit_label': 'INR', 'values_comparable': True,
            'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
        assert contract.status_code == 201, contract.text
        contract = contract.json()
        with source.open('rb') as content:
            uploaded = client.post('/api/v1/flat/snapshots', files={'file': ('orders.csv', content, 'text/csv')},
                                   data={'contract_id': contract['id'], 'name': 'Orders'})
        assert uploaded.status_code == 202, uploaded.text
        assert worker.run_once()
        snapshot_id = client.get(f"/api/v1/snapshot-imports/{uploaded.json()['id']}").json()['snapshot_id']
        context = {'mode': 'flat_snapshot', 'scope': 'entire_supplied_snapshot', 'contract_hash': contract['contract_hash'],
                   'unit_label': 'INR', 'values_comparable': True}

        def run(name, sql_a, sql_b):
            ids = []
            for label, sql in ((name + ' current', sql_a), (name + ' proposed', sql_b)):
                response = client.post('/api/v1/flat/definitions', json={'name': label, 'raw_sql': sql, 'contract_id': contract['id']})
                assert response.status_code == 201, response.text
                ids.append(response.json()['versions'][0]['id'])
            queued = client.post('/api/v1/flat/comparisons', headers={'Idempotency-Key': name}, json={
                'baseline_version_id': ids[0], 'target_version_id': ids[1], 'snapshot_id': snapshot_id, 'context': context})
            assert queued.status_code == 202, queued.text
            assert worker.run_once()
            done = client.get(f"/api/v1/comparisons/{queued.json()['id']}").json()
            assert done['state'] == 'succeeded', (done['state'], done['error'])
            return done

        distinct = run('distinct', "SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status = 'paid'",
                       "SELECT COUNT(DISTINCT customer_id) FROM snapshot WHERE status IN ('paid', 'refunded')")
        assert distinct['result']['delta_target_minus_baseline'] == '1'
        split = client.get(f"/api/v1/comparisons/{distinct['id']}/evidence-breakdown?column=segment").json()
        assert split['available'] and {g['value']: g['change'] for g in split['groups']} == {'retail': '1', 'trade': '0'}
        assert 'region' in split['unavailable_columns']  # C2 appears under N and S
        records = client.get(f"/api/v1/comparisons/{distinct['id']}/entity-records?entity=C3").json()
        assert [r['record'] for r in records['items']] == ['R4', 'R5']

        ratio = run('ratio', "SELECT SUM(amount) / COUNT(*) FROM snapshot WHERE status = 'paid'",
                    "SELECT AVG(amount) FROM snapshot WHERE status IN ('paid', 'refunded')")
        assert ratio['result']['ratio']['status'] == 'defined'
        page = client.get(f"/api/v1/comparisons/{ratio['id']}/evidence?order=impact&limit=3").json()
        assert all('effect' in row for row in page['items'])
        by_segment = client.get(f"/api/v1/comparisons/{ratio['id']}/evidence-breakdown?column=segment").json()
        assert by_segment['ratio'] and by_segment['total_change_exact'] == ratio['result']['ratio']['gap_exact']
        csv_text = client.get(f"/api/v1/comparisons/{ratio['id']}/evidence.csv").text
        assert csv_text.splitlines()[0].startswith('record,current_numerator_input,current_denominator_input')
        for run_id in (ratio['id'], distinct['id']):
            for fmt in ('json', 'html'):
                exported = client.get(f'/api/v1/comparisons/{run_id}/export?format={fmt}')
                assert exported.status_code == 200, exported.text

        saved = client.post('/api/v1/investigations', json={'title': 'AOV', 'run_id': ratio['id'],
                                                           'decision': 'AVG skips blank amounts', 'actor_label': 'Tester'})
        assert saved.status_code == 201, saved.text
        replay = client.post(f"/api/v1/investigations/{saved.json()['id']}/replays", headers={'Idempotency-Key': 'ratio-replay'})
        assert replay.status_code == 202, replay.text
        assert worker.run_once()
        replayed = client.get(f"/api/v1/comparisons/{replay.json()['id']}").json()
        assert replayed['state'] == 'succeeded', replayed['error']
        assert replayed['result']['evidence_artifact']['sha256'] == ratio['result']['evidence_artifact']['sha256']


# ---------- several amount columns (FlatContract v8): margin % ----------

def _contract_v8(**overrides):
    payload = {'version': 8, 'mode': 'flat_snapshot', 'name': 'economics', 'row_key': 'k',
               'decimal_columns': [{'name': 'revenue', 'precision': 12, 'scale': 2, 'unit_label': 'INR'},
                                   {'name': 'cost', 'precision': 12, 'scale': 2, 'unit_label': 'INR'},
                                   {'name': 'units', 'precision': 10, 'scale': 0, 'unit_label': 'units'}],
               'scale': 2, 'categories': ['region', 'status'],
               'dimension_types': {'region': 'string', 'status': 'string'}, 'nullable_categories': ['region', 'status'],
               'measure_meaning': 'Order economics', 'unit_label': 'INR', 'values_comparable': True,
               'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable',
               'measure_policy': 'flat-multi-measure-v1', 'predicate_policy': 'flat-boolean-v1'}
    payload.update(overrides)
    return validate_contract(payload)


V8_ROWS = [['A', '100.00', '60.00', '2', 'N', 'paid'], ['B', '200.00', '150.00', '5', 'S', 'paid'],
           ['C', '50.00', '', '1', 'N', 'refunded'], ['D', '80.00', '20.00', '3', 'S', 'paid']]
V8_HEADER = ['k', 'revenue', 'cost', 'units', 'region', 'status']


@pytest.fixture
def v8_snapshot(tmp_path):
    c = _contract_v8()
    database, context = _snapshot(tmp_path, 'v8', V8_HEADER, V8_ROWS, c)
    return c, database, context


def test_v8_contract_rules():
    with pytest.raises(CoreError):
        _contract_v8(scale=0)  # must equal the largest amount scale
    with pytest.raises(CoreError):
        _contract_v8(unit_label='USD')  # must be the first amount's unit
    with pytest.raises(CoreError):
        _contract_v8(decimal_columns=[{'name': 'revenue', 'precision': 12, 'scale': 2, 'unit_label': 'INR'}])
    with pytest.raises(CoreError):
        _contract_v8(measure='revenue')  # no single-measure field
    with pytest.raises(CoreError):
        _contract_v8(measure_policy='flat-subtraction-v1')


def test_v8_margin_counts_blank_cost_only_in_revenue(v8_snapshot):
    """Paid: margin 150 / revenue 380 = 15/38. All: C has no cost, so it adds 50 to revenue only: 150/430 = 15/43."""
    c, database, context = v8_snapshot
    a = parse_flat("SELECT SUM(revenue - cost) / SUM(revenue) FROM snapshot WHERE status = 'paid'", c)
    b = parse_flat('SELECT SUM(revenue - cost) / SUM(revenue) FROM snapshot', c)
    assert a['numerator'] == {'aggregate': 'sum', 'measure': 'revenue', 'minus': 'cost', 'filter': None}
    result = compare_flat(database, a, b, context)
    ratio = result['ratio']
    assert (ratio['baseline']['exact'], ratio['target']['exact'], ratio['gap_exact']) == ('15/38', '15/43', '-75/1634')
    assert ratio['unit'] == 'share'
    rows = {r['order_id']: r for r in result['evidence']}
    assert rows['C']['target_numerator_input'] is None and rows['C']['target_null_term'] is True
    assert rows['C']['target_denominator'] == '50.00' and rows['C']['delta'] == '-15000.0000'
    assert sum(Fraction(Decimal(r['delta'])) for r in result['evidence']) / Fraction(Decimal(ratio['scale_divisor'])) == Fraction(-75, 1634)
    ratio_context, _ = aggregate_contexts(c, a, b)
    checked = validate_result(result, ratio_context=ratio_context)
    path = database.parent / 'margin.jsonl'
    manifest = write_artifact(path, checked['evidence'], ratio_context=ratio_context, expected_result=checked)
    stored = {**checked, 'evidence': []}
    verify_artifact(path, manifest, expected_artifact_delta(stored, ratio_context), expected_result=stored, ratio_context=ratio_context)


def test_v8_totals_and_ratios_per_column(v8_snapshot):
    c, database, context = v8_snapshot
    units = compare_flat(database, parse_flat("SELECT SUM(units) FROM snapshot WHERE region = 'N'", c),
                         parse_flat('SELECT SUM(units) FROM snapshot', c), context)
    assert (units['baseline_value'], units['target_value'], units['unit_label']) == ('3', '11', 'units')
    per_unit = compare_flat(database, parse_flat("SELECT SUM(revenue) / SUM(units) FROM snapshot WHERE status = 'paid'", c),
                            parse_flat('SELECT SUM(revenue) / SUM(units) FROM snapshot', c), context)
    assert (per_unit['ratio']['baseline']['exact'], per_unit['ratio']['target']['exact']) == ('38', '430/11')
    assert per_unit['ratio']['unit'] == 'amount_per_amount'
    with pytest.raises(CoreError, match='same amount column'):
        compare_flat(database, parse_flat('SELECT SUM(revenue) FROM snapshot', c), parse_flat('SELECT SUM(cost) FROM snapshot', c), context)
    with pytest.raises(CoreError, match='same kind of total'):
        compare_flat(database, parse_flat('SELECT SUM(cost) / SUM(revenue) FROM snapshot', c),
                     parse_flat('SELECT SUM(cost) / SUM(units) FROM snapshot', c), context)
    for sql in ['SELECT SUM(region) FROM snapshot', 'SELECT SUM(revenue - units) / SUM(revenue) FROM snapshot',
                'SELECT SUM(revenue - revenue) / SUM(revenue) FROM snapshot', 'SELECT COUNT(DISTINCT cost) FROM snapshot']:
        with pytest.raises(CoreError):
            parse_flat(sql, c)


def test_v8_ratio_cannot_swap_denominator_amount_columns(v8_snapshot):
    c, database, context = v8_snapshot
    baseline = parse_flat('SELECT SUM(revenue) / SUM(revenue) FROM snapshot', c)
    target = parse_flat('SELECT SUM(revenue) / SUM(cost) FROM snapshot', c)
    with pytest.raises(CoreError, match='same denominator amount expression'):
        compare_flat(database, baseline, target, context)


def test_sum_of_difference_in_a_ratio_needs_v8(ratio_snapshot):
    with pytest.raises(CoreError, match='several amount columns'):
        parse_flat('SELECT SUM(amount - amount) / COUNT(*) FROM snapshot', ratio_snapshot[0])
