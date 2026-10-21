"""Tests for FlatContract v4 typed snapshot storage, IR validation, and deterministic execution."""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
import duckdb

from packages.metricguard_core.common import CoreError, digest
from packages.metricguard_core.flat import (
    ALLOWED_DIMENSION_TYPES,
    FIELDS,
    FIELDS_V4,
    compare_flat,
    infer_dimension_type,
    infer_dimension_types,
    ingest_flat,
    parse_flat,
    validate_contract,
)


def sample_v3_contract_payload():
    return {
        'version': 3,
        'mode': 'flat_snapshot',
        'name': 'Orders v3',
        'row_key': 'order_id',
        'measure': 'revenue',
        'precision': 18,
        'scale': 2,
        'categories': ['status', 'region', 'order_date', 'is_active'],
        'measure_meaning': 'Order gross revenue',
        'unit_label': 'INR',
        'values_comparable': True,
        'scope': 'entire_supplied_snapshot',
        'null_policy': 'blank_nullable',
    }


def sample_v4_contract_payload():
    return {
        'version': 4,
        'mode': 'flat_snapshot',
        'name': 'Orders v4',
        'row_key': 'order_id',
        'measure': 'revenue',
        'precision': 18,
        'scale': 2,
        'categories': ['status', 'region', 'order_date', 'deleted_at', 'is_active'],
        'dimension_types': {
            'status': 'string',
            'region': 'string',
            'order_date': 'date',
            'deleted_at': 'date',
            'is_active': 'boolean',
        },
        'nullable_categories': ['deleted_at'],
        'measure_meaning': 'Order gross revenue',
        'unit_label': 'INR',
        'values_comparable': True,
        'scope': 'entire_supplied_snapshot',
        'null_policy': 'blank_nullable',
    }


def make_sample_csv(path: Path):
    rows = [
        ['order_id', 'revenue', 'status', 'region', 'order_date', 'deleted_at', 'is_active'],
        ['O001', '1200.00', 'completed', 'North', '2026-01-05', '', 'true'],
        ['O002', '1500.00', 'completed', 'South', '2026-01-08', '', 'true'],
        ['O003', '500.00', 'returned', 'North', '2026-01-10', '2026-01-12', 'false'],
        ['O004', '2000.00', 'completed', 'West', '2026-01-15', '', 'true'],
        ['O005', '800.00', 'pending', 'East', '2026-01-20', '', 'false'],
    ]
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    return path


# ==============================================================================
# 1 & 2. v3 Backward Compatibility & Hash Invariance
# ==============================================================================

def test_v3_contracts_remain_replayable(tmp_path):
    csv_file = tmp_path / 'v3_orders.csv'
    rows = [
        ['order_id', 'revenue', 'status', 'region', 'order_date', 'is_active'],
        ['O001', '1200.00', 'completed', 'North', '2026-01-05', 'true'],
        ['O002', '1500.00', 'completed', 'South', '2026-01-08', 'true'],
        ['O003', '500.00', 'returned', 'North', '2026-01-10', 'false'],
    ]
    with csv_file.open('w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(rows)

    c3 = validate_contract(sample_v3_contract_payload())
    db = tmp_path / 'v3.duckdb'
    manifest = ingest_flat(csv_file, db, c3)

    # In v3, order_date and is_active are VARCHAR categories
    base_ir = parse_flat('SELECT SUM(revenue) FROM snapshot', c3)
    target_ir = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE status != 'returned'", c3)

    result = compare_flat(db, base_ir, target_ir, {
        'mode': 'flat_snapshot',
        'scope': c3['scope'],
        'contract_hash': c3['contract_hash'],
        'unit_label': c3['unit_label'],
        'values_comparable': True,
        'manifest_hash': manifest['manifest_hash'],
    })

    assert Decimal(result['baseline_value']) == Decimal('3200.00')
    assert Decimal(result['target_value']) == Decimal('2700.00')
    assert Decimal(result['delta_target_minus_baseline']) == Decimal('-500.00')
    assert Decimal(result['residual']) == Decimal('0.00')
    baseline_only = [r for r in result['evidence'] if r['membership'] == 'baseline_only']
    assert len(baseline_only) == 1
    assert baseline_only[0]['order_id'] == 'O003'


def test_v3_hash_unchanged():
    payload = sample_v3_contract_payload()
    c3 = validate_contract(payload)
    # Ensure digest matches exact Fields subset without dimension_types
    raw_sub = {k: payload[k] for k in FIELDS}
    expected_hash = digest(raw_sub)
    assert c3['contract_hash'] == expected_hash

    # v3 payload with dimension_types must be rejected
    bad_v3 = {**payload, 'dimension_types': {'status': 'string'}}
    with pytest.raises(CoreError, match='column types'):
        validate_contract(bad_v3)


# ==============================================================================
# 3. v4 Contract Serialization & Validation
# ==============================================================================

def test_v4_contract_serialization_and_hash():
    payload = sample_v4_contract_payload()
    c4 = validate_contract(payload)
    assert c4['version'] == 4
    assert 'dimension_types' in c4
    assert c4['dimension_types']['order_date'] == 'date'
    assert c4['dimension_types']['is_active'] == 'boolean'
    assert c4['dimension_types']['status'] == 'string'

    frozen_dict = {k: c4[k] for k in FIELDS_V4 | ({'nullable_categories'} if 'nullable_categories' in c4 else set())}
    assert c4['contract_hash'] == digest(frozen_dict)

    # Missing dimension_types
    bad_payload = dict(payload)
    del bad_payload['dimension_types']
    with pytest.raises(CoreError, match='incomplete or unsupported'):
        validate_contract(bad_payload)

    # Dimension type key mismatch with categories
    bad_payload2 = dict(payload)
    bad_payload2['dimension_types'] = {'status': 'string'}  # missing other categories
    with pytest.raises(CoreError, match='dimension_types keys must match categories exactly'):
        validate_contract(bad_payload2)

    # Invalid dimension type
    bad_payload3 = dict(payload)
    bad_payload3['dimension_types'] = dict(payload['dimension_types'])
    bad_payload3['dimension_types']['status'] = 'float'
    with pytest.raises(CoreError, match='Unsupported dimension type'):
        validate_contract(bad_payload3)


# ==============================================================================
# 4 & 5. Strict Deterministic Ingestion Type Inference
# ==============================================================================

def test_strict_boolean_inference():
    # Only canonical true/false (case-insensitive) should infer boolean
    assert infer_dimension_type(['true', 'false']) == 'boolean'
    assert infer_dimension_type(['True', 'False']) == 'string'
    assert infer_dimension_type([' true ']) == 'string'
    assert infer_dimension_type(['true', '', None, 'false']) == 'boolean'

    # Non-canonical representations must remain string
    assert infer_dimension_type(['yes', 'no']) == 'string'
    assert infer_dimension_type(['1', '0']) == 'string'
    assert infer_dimension_type(['Y', 'N']) == 'string'
    assert infer_dimension_type(['true', 'false', 'maybe']) == 'string'
    assert infer_dimension_type(['t', 'f']) == 'string'


def test_strict_iso_date_inference():
    # Only strict YYYY-MM-DD valid calendar dates
    assert infer_dimension_type(['2026-01-01', '2026-02-28', '2026-12-31']) == 'date'
    assert infer_dimension_type(['2026-01-01', '', None, '2026-02-15']) == 'date'

    # Invalid calendar dates or formats must remain string
    assert infer_dimension_type(['2026-02-31']) == 'string'  # Feb 31 does not exist
    assert infer_dimension_type(['2026/01/01']) == 'string'  # Slash format
    assert infer_dimension_type(['01-01-2026']) == 'string'  # DD-MM-YYYY
    assert infer_dimension_type(['2026-1-1']) == 'string'    # Missing leading zeros
    assert infer_dimension_type(['Jan 10, 2026']) == 'string'
    assert infer_dimension_type(['2026-01-05', 'invalid-date']) == 'string'


def test_strict_timestamp_inference():
    assert infer_dimension_type(['2026-01-01T12:00:00Z', '2026-01-02T15:30:00+05:30']) == 'string'
    assert infer_dimension_type(['2026-01-01T12:00:00', '2026-01-02T15:30:00.123456']) == 'timestamp'
    assert infer_dimension_type(['2026-01-01T12:00:00.1234567']) == 'string'
    assert infer_dimension_type(['2026-01-01 25:00:00']) == 'string'  # Invalid hour


def test_infer_dimension_types_multiple():
    sample_rows = [
        {'cat_bool': 'true', 'cat_date': '2026-01-10', 'cat_str': 'hello'},
        {'cat_bool': 'false', 'cat_date': '2026-01-11', 'cat_str': 'world'},
        {'cat_bool': '', 'cat_date': '', 'cat_str': '123'},
    ]
    types = infer_dimension_types(sample_rows, ['cat_bool', 'cat_date', 'cat_str'])
    assert types == {
        'cat_bool': 'boolean',
        'cat_date': 'date',
        'cat_str': 'string',
    }


# ==============================================================================
# 6, 7 & 8. Physical DuckDB Storage: DATE, BOOLEAN, VARCHAR
# ==============================================================================

def test_physical_storage_types(tmp_path):
    csv_file = make_sample_csv(tmp_path / 'v4_orders.csv')
    c4 = validate_contract(sample_v4_contract_payload())
    db = tmp_path / 'v4.duckdb'
    manifest = ingest_flat(csv_file, db, c4)
    assert manifest['contract_version'] == 4

    conn = duckdb.connect(str(db), read_only=True)
    table_info = conn.execute("PRAGMA table_info('snapshot')").fetchall()
    col_types = {row[1]: row[2] for row in table_info}
    conn.close()

    assert col_types['order_id'] == 'VARCHAR'
    assert col_types['revenue'] == 'DECIMAL(18,2)'
    assert col_types['status'] == 'VARCHAR'
    assert col_types['region'] == 'VARCHAR'
    assert col_types['order_date'] == 'DATE'
    assert col_types['deleted_at'] == 'DATE'
    assert col_types['is_active'] == 'BOOLEAN'


def test_nullable_date_storage_and_query(tmp_path):
    csv_file = make_sample_csv(tmp_path / 'v4_orders.csv')
    c4 = validate_contract(sample_v4_contract_payload())
    db = tmp_path / 'v4.duckdb'
    ingest_flat(csv_file, db, c4)

    conn = duckdb.connect(str(db), read_only=True)
    rows = conn.execute("SELECT order_id, deleted_at FROM snapshot WHERE deleted_at IS NULL").fetchall()
    conn.close()

    # O001, O002, O004, O005 have empty deleted_at (SQL NULL)
    null_ids = {r[0] for r in rows}
    assert null_ids == {'O001', 'O002', 'O004', 'O005'}


# ==============================================================================
# 9. DATE Literal Parsing & Date Comparisons
# ==============================================================================

def test_date_literal_parsing_and_ir():
    c4 = validate_contract(sample_v4_contract_payload())

    # Supported: DATE '2026-01-10'
    ir1 = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE order_date >= DATE '2026-01-10'", c4)
    assert ir1['predicates'][0] == {
        'field': 'order_date',
        'op': 'gte',
        'values': ['2026-01-10'],
        'type': 'date',
    }

    # Range with BETWEEN
    ir2 = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE order_date BETWEEN DATE '2026-01-05' AND DATE '2026-01-15'", c4)
    assert ir2['predicates'][0] == {
        'field': 'order_date',
        'op': 'between',
        'values': ['2026-01-05', '2026-01-15'],
        'type': 'date',
    }

    # Date IN list
    ir3 = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE order_date IN (DATE '2026-01-05', DATE '2026-01-10')", c4)
    assert ir3['predicates'][0] == {
        'field': 'order_date',
        'op': 'in',
        'values': ['2026-01-05', '2026-01-10'],
        'type': 'date',
    }


def test_untyped_date_literal_refused():
    c4 = validate_contract(sample_v4_contract_payload())
    # In v4, comparing a date column with a raw untyped string must be refused!
    with pytest.raises(CoreError) as exc_info:
        parse_flat("SELECT SUM(revenue) FROM snapshot WHERE order_date >= '2026-01-10'", c4)
    assert 'Implicit string-to-date comparison' in str(exc_info.value)


def test_invalid_calendar_date_literal_refused():
    c4 = validate_contract(sample_v4_contract_payload())
    with pytest.raises(CoreError) as exc_info:
        parse_flat("SELECT SUM(revenue) FROM snapshot WHERE order_date = DATE '2026-02-31'", c4)
    assert 'Invalid DATE literal' in str(exc_info.value)


# ==============================================================================
# 10. Boolean Typed Predicates & Semantics
# ==============================================================================

def test_boolean_typed_predicates():
    c4 = validate_contract(sample_v4_contract_payload())

    # = TRUE / = FALSE
    ir_eq = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE is_active = TRUE", c4)
    assert ir_eq['predicates'][0] == {'field': 'is_active', 'op': 'eq_bool', 'values': ['true'], 'type': 'boolean'}

    # IS TRUE / IS FALSE
    ir_istrue = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE is_active IS TRUE", c4)
    assert ir_istrue['predicates'][0] == {'field': 'is_active', 'op': 'is_true', 'values': [], 'type': 'boolean'}

    # IS NOT TRUE / IS NOT FALSE
    ir_isnottrue = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE is_active IS NOT TRUE", c4)
    assert ir_isnottrue['predicates'][0] == {'field': 'is_active', 'op': 'is_not_true', 'values': [], 'type': 'boolean'}

    # Bare boolean: WHERE is_active
    ir_bare = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE is_active", c4)
    assert ir_bare['predicates'][0] == {'field': 'is_active', 'op': 'bare_bool', 'values': [], 'type': 'boolean'}

    # NOT boolean: WHERE NOT is_active
    ir_not = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE NOT is_active", c4)
    assert ir_not['predicates'][0] == {'field': 'is_active', 'op': 'not_bool', 'values': [], 'type': 'boolean'}


# ==============================================================================
# 11. Refusal Boundaries: Type Mismatch, Cross-Column, Invalid Bounds
# ==============================================================================

def test_type_mismatch_refused():
    c4 = validate_contract(sample_v4_contract_payload())

    # status is string, comparing with TRUE must fail
    with pytest.raises(CoreError) as exc_info:
        parse_flat("SELECT SUM(revenue) FROM snapshot WHERE status = TRUE", c4)
    assert 'Boolean comparison requires boolean column' in str(exc_info.value)

    # is_active is boolean, comparing with string 'true' must fail
    with pytest.raises(CoreError) as exc_info:
        parse_flat("SELECT SUM(revenue) FROM snapshot WHERE is_active = 'true'", c4)
    assert 'Implicit string-to-boolean comparison' in str(exc_info.value)

    # order_date is date, comparing with boolean must fail
    with pytest.raises(CoreError) as exc_info:
        parse_flat("SELECT SUM(revenue) FROM snapshot WHERE order_date = TRUE", c4)
    assert 'Boolean comparison requires boolean column' in str(exc_info.value)

    # Bare boolean on string column must fail
    with pytest.raises(CoreError) as exc_info:
        parse_flat("SELECT SUM(revenue) FROM snapshot WHERE status", c4)
    assert 'Bare boolean predicate requires boolean column' in str(exc_info.value)


def test_cross_column_comparison_refused():
    c4 = validate_contract(sample_v4_contract_payload())
    with pytest.raises(CoreError, match='Cross-column comparison'):
        parse_flat("SELECT SUM(revenue) FROM snapshot WHERE order_date > revenue", c4)


def test_invalid_range_bounds_refused():
    c4 = validate_contract(sample_v4_contract_payload())
    # BETWEEN low > high
    with pytest.raises(CoreError) as exc_info:
        parse_flat("SELECT SUM(revenue) FROM snapshot WHERE order_date BETWEEN DATE '2026-01-20' AND DATE '2026-01-10'", c4)
    assert 'INVALID_RANGE_BOUNDS' in str(exc_info.value)


# ==============================================================================
# 12, 13 & 14. Execution, Evidence Attribution & Value Bridge Invariance
# ==============================================================================

def test_v4_date_and_boolean_execution(tmp_path):
    csv_file = make_sample_csv(tmp_path / 'v4_exec.csv')
    c4 = validate_contract(sample_v4_contract_payload())
    db = tmp_path / 'v4_exec.duckdb'
    manifest = ingest_flat(csv_file, db, c4)

    # Query 1: Filter on DATE >= DATE '2026-01-10'
    base_ir = parse_flat("SELECT SUM(revenue) FROM snapshot", c4)
    target_ir = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE order_date >= DATE '2026-01-10'", c4)

    result = compare_flat(db, base_ir, target_ir, {
        'mode': 'flat_snapshot',
        'scope': c4['scope'],
        'contract_hash': c4['contract_hash'],
        'unit_label': c4['unit_label'],
        'values_comparable': True,
        'manifest_hash': manifest['manifest_hash'],
    })

    # O001 (1200, Jan 05) excluded
    # O002 (1500, Jan 08) excluded
    # O003 (500, Jan 10) included
    # O004 (2000, Jan 15) included
    # O005 (800, Jan 20) included
    # Total baseline = 6000.00
    # Target = 3300.00
    # Delta = -2700.00
    assert Decimal(result['baseline_value']) == Decimal('6000.00')
    assert Decimal(result['target_value']) == Decimal('3300.00')
    assert Decimal(result['delta_target_minus_baseline']) == Decimal('-2700.00')
    assert Decimal(result['residual']) == Decimal('0.00')

    # Evidence invariance
    evidence_sum = sum(Decimal(r['delta']) for r in result['evidence'])
    assert evidence_sum == Decimal('-2700.00')


def test_v4_boolean_filter_execution(tmp_path):
    csv_file = make_sample_csv(tmp_path / 'v4_exec_bool.csv')
    c4 = validate_contract(sample_v4_contract_payload())
    db = tmp_path / 'v4_exec_bool.duckdb'
    manifest = ingest_flat(csv_file, db, c4)

    base_ir = parse_flat("SELECT SUM(revenue) FROM snapshot", c4)
    target_ir = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE is_active = TRUE", c4)

    result = compare_flat(db, base_ir, target_ir, {
        'mode': 'flat_snapshot',
        'scope': c4['scope'],
        'contract_hash': c4['contract_hash'],
        'unit_label': c4['unit_label'],
        'values_comparable': True,
        'manifest_hash': manifest['manifest_hash'],
    })

    # is_active = TRUE rows: O001 (1200), O002 (1500), O004 (2000) -> 4700.00
    # Baseline: 6000.00
    # Delta: -1300.00 (O003: -500, O005: -800)
    assert Decimal(result['baseline_value']) == Decimal('6000.00')
    assert Decimal(result['target_value']) == Decimal('4700.00')
    assert Decimal(result['delta_target_minus_baseline']) == Decimal('-1300.00')
    assert Decimal(result['residual']) == Decimal('0.00')


def test_v4_is_null_predicate_execution(tmp_path):
    csv_file = make_sample_csv(tmp_path / 'v4_exec_null.csv')
    c4 = validate_contract(sample_v4_contract_payload())
    db = tmp_path / 'v4_exec_null.duckdb'
    manifest = ingest_flat(csv_file, db, c4)

    base_ir = parse_flat("SELECT SUM(revenue) FROM snapshot", c4)
    target_ir = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE deleted_at IS NULL", c4)

    result = compare_flat(db, base_ir, target_ir, {
        'mode': 'flat_snapshot',
        'scope': c4['scope'],
        'contract_hash': c4['contract_hash'],
        'unit_label': c4['unit_label'],
        'values_comparable': True,
        'manifest_hash': manifest['manifest_hash'],
    })

    # deleted_at IS NULL rows: O001 (1200), O002 (1500), O004 (2000), O005 (800) = 5500.00
    # Baseline: 6000.00
    # Delta: -500.00 (O003 deleted_at is '2026-01-12' so excluded)
    assert Decimal(result['baseline_value']) == Decimal('6000.00')
    assert Decimal(result['target_value']) == Decimal('5500.00')
    assert Decimal(result['delta_target_minus_baseline']) == Decimal('-500.00')
    assert Decimal(result['residual']) == Decimal('0.00')
    baseline_only = [r for r in result['evidence'] if r['membership'] == 'baseline_only']
    assert len(baseline_only) == 1
    assert baseline_only[0]['order_id'] == 'O003'


def test_semantic_hash_stability():
    c4 = validate_contract(sample_v4_contract_payload())
    # Differing whitespace or keyword casing should yield the identical IR hash
    ir1 = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE is_active = TRUE", c4)
    ir2 = parse_flat("select  sum(revenue)  from  snapshot  where  is_active  =  true", c4)
    assert ir1['ir_hash'] == ir2['ir_hash']
    assert ir1['predicates'] == ir2['predicates']

    # Different predicate value must yield different IR hash
    ir3 = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE is_active = FALSE", c4)
    assert ir1['ir_hash'] != ir3['ir_hash']


def test_no_implicit_boolean_coercion(tmp_path):
    # Verify that DuckDB physically stores BOOLEAN and rejects passing string parameters to boolean columns
    csv_file = make_sample_csv(tmp_path / 'v4_bool_coercion.csv')
    c4 = validate_contract(sample_v4_contract_payload())
    db = tmp_path / 'v4_bool_coercion.duckdb'
    ingest_flat(csv_file, db, c4)

    conn = duckdb.connect(str(db), read_only=True)
    # Check physical column type is BOOLEAN
    res = conn.execute("SELECT typeof(is_active) FROM snapshot LIMIT 1").fetchone()
    assert res[0] == 'BOOLEAN'

    # In v4, parse_flat rejects string comparison with boolean column
    with pytest.raises(CoreError) as exc_info:
        parse_flat("SELECT SUM(revenue) FROM snapshot WHERE is_active = 'true'", c4)
    assert 'Implicit string-to-boolean comparison' in str(exc_info.value)


@pytest.mark.parametrize('predicate', [
    "order_date = DATE '20260101'",
    "order_date BETWEEN DATE order_date AND DATE '2026-01-10'",
    "status IN ('completed', 'returned') OR is_active",
    "snapshot.is_active",
    "status IN (SELECT status FROM snapshot)",
])
def test_v4_refuses_noncanonical_or_unbounded_predicates(predicate):
    with pytest.raises(CoreError):
        parse_flat('SELECT SUM(revenue) FROM snapshot WHERE ' + predicate, sample_v4_contract_payload())


def test_v4_revalidates_forged_ir_at_execution(tmp_path):
    c = validate_contract(sample_v4_contract_payload())
    db = tmp_path / 'forged.duckdb'
    ingest_flat(make_sample_csv(tmp_path / 'source.csv'), db, c)
    ir = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE status = 'completed'", c)
    ir['predicates'][0]['op'] = 'gt'
    ir['ir_hash'] = digest({k: v for k, v in ir.items() if k != 'ir_hash'})
    with pytest.raises(CoreError, match='type|operator'):
        compare_flat(db, ir, ir, {'mode': 'flat_snapshot', 'scope': c['scope'], 'contract_hash': c['contract_hash'], 'unit_label': c['unit_label'], 'values_comparable': True})


def test_v4_decimal_measure_and_timestamp_comparison(tmp_path):
    c = sample_v4_contract_payload()
    c['categories'] = ['event_at']
    c['dimension_types'] = {'event_at': 'timestamp'}
    c['nullable_categories'] = ['event_at']
    c = validate_contract(c)
    source = tmp_path / 'events.csv'
    source.write_text('order_id,revenue,event_at\nA,1.10,2026-01-01T01:02:03.123456\nB,2.20,2026-01-02T01:02:03\nC,3.30,\n')
    db = tmp_path / 'events.duckdb'
    ingest_flat(source, db, c)
    base = parse_flat('SELECT SUM(revenue) FROM snapshot', c)
    target = parse_flat("SELECT SUM(revenue) FROM snapshot WHERE revenue BETWEEN 2.20 AND 3.30 AND event_at >= TIMESTAMP '2026-01-02T00:00:00'", c)
    result = compare_flat(db, base, target, {'mode': 'flat_snapshot', 'scope': c['scope'], 'contract_hash': c['contract_hash'], 'unit_label': c['unit_label'], 'values_comparable': True})
    assert result['target_value'] == '2.20'
    assert result['delta_target_minus_baseline'] == '-4.40'
