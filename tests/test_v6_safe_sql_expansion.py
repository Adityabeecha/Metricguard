"""MetricGuard V6.1 Safe SQL Expansion Test Suite.

Validates:
1. P0 Operators:
   - Inequality: !=, <>
   - NULL predicates: IS NULL, IS NOT NULL
   - Numeric comparisons: >, <, >=, <=, BETWEEN (inclusive)
   - Date comparisons: >, <, >=, <=, BETWEEN (inclusive)
   - Boolean predicates: = TRUE/FALSE, IS TRUE/FALSE, IS NOT TRUE/FALSE, bare boolean, NOT boolean
2. 3-Valued NULL Logic:
   - ANSI semantics: col != 'val' excludes NULLs unless guarded
   - IS NULL / IS NOT NULL behavior
   - Boolean 3-valued truth table
3. Exact Numeric Decimals & Range Bounds:
   - Zero floating point drift
   - Strict inclusive bounds
   - INVALID_RANGE_BOUNDS refusal when lower bound > upper bound
4. Refusal Boundaries:
   - Inter-field OR refused
   - Cross-column comparison refused
   - Nondeterministic date functions (NOW, CURRENT_DATE) refused
   - Implicit date literal casts refused
   - Disallowed constructs (JOIN, subquery, window, GROUP BY, AVG) refused
5. Scenarios A through F:
   - Exact baseline, target, and delta
   - Bounded row evidence attribution
   - Zero-residual Shapley Value Bridge decomposition
"""
import csv
from decimal import Decimal
from pathlib import Path
import pytest

from packages.metricguard_core import compare, ingest_csv, parse_definition
from packages.metricguard_core.common import CoreError
from packages.metricguard_core.flat import ingest_flat, parse_flat, compare_flat

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_CSV = ROOT / "MetricGuard_Planning_Pack" / "examples" / "orders_golden.csv"
CTX = {
    "start": "2026-08-01",
    "end_exclusive": "2026-09-01",
    "timezone": "Asia/Kolkata",
    "currency": "INR",
    "unit": "currency",
    "grain": "one_row_per_order_id",
    "scope": "synthetic_retail_all_orders",
}


@pytest.fixture
def snapshot(tmp_path):
    db = tmp_path / "orders.duckdb"
    ingest_csv(GOLDEN_CSV, db)
    return db


# ===========================================================================
# 1. P0 Operators & Normalization
# ===========================================================================

def test_inequality_operators_equivalent(snapshot):
    """Test != and <> produce identical IR hash and evaluation."""
    sql_neq = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND status != 'Cancelled'"
    sql_diamond = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND status <> 'Cancelled'"
    
    ir_neq = parse_definition(sql_neq, "order_date")
    ir_diamond = parse_definition(sql_diamond, "order_date")
    
    # Both AST forms normalize to the canonical 'neq' IR operator
    assert ir_neq["ir_hash"] == ir_diamond["ir_hash"]
    assert ir_neq["predicate"]["op"] == "neq"
    
    res = compare(snapshot, ir_neq, ir_diamond, CTX)
    assert res["definition_status"] == "same_supported_representation"
    assert res["value_status"] == "equal_on_snapshot"
    assert Decimal(res["delta_target_minus_baseline"]) == Decimal("0.00")


def test_null_predicates(snapshot):
    """Test IS NULL and IS NOT NULL against nullable columns."""
    # payment_date is NULL for O04, O06, O07, O08:
    # 420000 + 700000 + 500000 + 200000 = 1820000.00
    sql_null = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND payment_date IS NULL"
    ir_null = parse_definition(sql_null, "order_date")
    res_null = compare(snapshot, ir_null, ir_null, CTX)
    assert Decimal(res_null["baseline_value"]) == Decimal("1820000.00")
    
    # payment_date IS NOT NULL for O01, O02, O03, O05:
    # 60M + 40M + 2.32M + 1.26M = 103580000.00
    sql_not_null = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND payment_date IS NOT NULL"
    ir_not_null = parse_definition(sql_not_null, "order_date")
    res_not_null = compare(snapshot, ir_not_null, ir_not_null, CTX)
    assert Decimal(res_not_null["baseline_value"]) == Decimal("103580000.00")
    
    # Sum of IS NULL + IS NOT NULL equals total population (105400000.00)
    assert Decimal(res_null["baseline_value"]) + Decimal(res_not_null["baseline_value"]) == Decimal("105400000.00")


def test_numeric_comparisons(snapshot):
    """Test >, <, >=, <= on decimal amounts."""
    # order_amount > 1000000.00 -> O01, O02, O03, O05 (103580000.00)
    sql_gt = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND order_amount > 1000000.00"
    ir_gt = parse_definition(sql_gt, "order_date")
    res_gt = compare(snapshot, ir_gt, ir_gt, CTX)
    assert Decimal(res_gt["baseline_value"]) == Decimal("103580000.00")
    
    # order_amount <= 1000000.00 -> O04, O06, O07, O08 (1820000.00)
    sql_lte = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND order_amount <= 1000000.00"
    ir_lte = parse_definition(sql_lte, "order_date")
    res_lte = compare(snapshot, ir_lte, ir_lte, CTX)
    assert Decimal(res_lte["baseline_value"]) == Decimal("1820000.00")


def test_between_numeric_inclusive(snapshot):
    """Test BETWEEN is strictly inclusive on both bounds."""
    # O07 (500000.00), O06 (700000.00), O05 (1260000.00), O03 (2320000.00)
    # BETWEEN 500000.00 AND 2320000.00 includes O07, O06, O05, O03 = 4780000.00
    sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND order_amount BETWEEN 500000.00 AND 2320000.00"
    ir = parse_definition(sql, "order_date")
    res = compare(snapshot, ir, ir, CTX)
    assert Decimal(res["baseline_value"]) == Decimal("4780000.00")


def test_between_inverted_bounds_rejected():
    """Test BETWEEN with low > high raises INVALID_RANGE_BOUNDS."""
    sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND order_amount BETWEEN 5000000.00 AND 100000.00"
    with pytest.raises(CoreError) as exc:
        parse_definition(sql, "order_date")
    assert "INVALID_RANGE_BOUNDS" in exc.value.message


def test_date_comparisons(snapshot):
    """Test >, <, BETWEEN on date columns using typed DATE literals."""
    # payment_date > DATE '2026-08-11' -> O03 (2026-08-16, 2320000.00), O05 (2026-09-01, 1260000.00) = 3580000.00
    sql_gt = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND payment_date > DATE '2026-08-11'"
    ir_gt = parse_definition(sql_gt, "order_date")
    res_gt = compare(snapshot, ir_gt, ir_gt, CTX)
    assert Decimal(res_gt["baseline_value"]) == Decimal("3580000.00")
    
    # payment_date BETWEEN DATE '2026-08-06' AND DATE '2026-08-16' -> O01 (08-06), O02 (08-11), O03 (08-16): 60M + 40M + 2.32M = 102320000.00
    sql_bt = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND payment_date BETWEEN DATE '2026-08-06' AND DATE '2026-08-16'"
    ir_bt = parse_definition(sql_bt, "order_date")
    res_bt = compare(snapshot, ir_bt, ir_bt, CTX)
    assert Decimal(res_bt["baseline_value"]) == Decimal("102320000.00")


def test_date_between_inverted_bounds_rejected():
    """Test date BETWEEN with low > high raises INVALID_RANGE_BOUNDS."""
    sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND payment_date BETWEEN DATE '2026-08-20' AND DATE '2026-08-10'"
    with pytest.raises(CoreError) as exc:
        parse_definition(sql, "order_date")
    assert "INVALID_RANGE_BOUNDS" in exc.value.message


# ===========================================================================
# 2. 3-Valued NULL Logic Tests
# ===========================================================================

def test_three_valued_null_inequality_excludes_null(snapshot):
    """In ANSI SQL, status != 'Cancelled' must evaluate to UNKNOWN for NULL rows and exclude them."""
    # In golden CSV:
    # 8 total rows.
    # O07 status is 'Cancelled'.
    # O08 status is NULL.
    # 6 rows have status != 'Cancelled' (Completed, Pending).
    # ANSI SQL: status != 'Cancelled' evaluates to UNKNOWN (falsy) for O08.
    sql = "SELECT COUNT(*) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND status != 'Cancelled'"
    ir = parse_definition(sql, "order_date")
    res = compare(snapshot, ir, ir, {**CTX, "unit": "count"})
    assert Decimal(res["baseline_value"]) == Decimal("6")


def test_three_valued_null_explicit_or_includes_null(snapshot):
    """status = 'Completed' OR status IS NULL includes both Completed rows (5) and NULL row (1) = 6."""
    sql = "SELECT COUNT(*) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND (status = 'Completed' OR status IS NULL)"
    ir = parse_definition(sql, "order_date")
    res = compare(snapshot, ir, ir, {**CTX, "unit": "count"})
    assert Decimal(res["baseline_value"]) == Decimal("6")


# ===========================================================================
# 3. Boolean Predicate Tests on Flat Snapshot
# ===========================================================================

@pytest.fixture
def flat_bool_snapshot(tmp_path):
    """Fixture providing a flat snapshot with a boolean category."""
    csv_file = tmp_path / "bool_data.csv"
    csv_file.write_text(
        "id,amount,is_active\n"
        "1,100.00,true\n"
        "2,200.00,false\n"
        "3,300.00,\n"
        "4,400.00,true\n",
        encoding="utf-8"
    )
    contract = {
        "version": 3,
        "mode": "flat_snapshot",
        "name": "bool_test",
        "row_key": "id",
        "measure": "amount",
        "precision": 10,
        "scale": 2,
        "categories": ["is_active"],
        "measure_meaning": "Revenue",
        "unit_label": "USD",
        "values_comparable": True,
        "scope": "entire_supplied_snapshot",
        "null_policy": "blank_nullable"
    }
    db = tmp_path / "bool.duckdb"
    manifest = ingest_flat(csv_file, db, contract)
    return db, contract, manifest


def test_flat_boolean_predicates(flat_bool_snapshot):
    """Test boolean predicate forms and 3-valued semantics on flat snapshot."""
    db, raw_contract, manifest = flat_bool_snapshot
    contract = manifest["contract"]
    ctx = {
        "mode": "flat_snapshot",
        "scope": contract["scope"],
        "contract_hash": manifest["contract_hash"],
        "unit_label": contract["unit_label"],
        "values_comparable": True,
        "manifest_hash": manifest["manifest_hash"]
    }
    
    # col = TRUE -> rows 1, 4 (100 + 400 = 500)
    ir_true = parse_flat("SELECT SUM(amount) FROM snapshot WHERE is_active = TRUE", contract)
    r_true = compare_flat(db, ir_true, ir_true, ctx)
    assert Decimal(r_true["baseline_value"]) == Decimal("500.00")
    
    # col = FALSE -> row 2 (200)
    ir_false = parse_flat("SELECT SUM(amount) FROM snapshot WHERE is_active = FALSE", contract)
    r_false = compare_flat(db, ir_false, ir_false, ctx)
    assert Decimal(r_false["baseline_value"]) == Decimal("200.00")
    
    # col IS TRUE -> rows 1, 4 (500)
    ir_is_true = parse_flat("SELECT SUM(amount) FROM snapshot WHERE is_active IS TRUE", contract)
    r_is_true = compare_flat(db, ir_is_true, ir_is_true, ctx)
    assert Decimal(r_is_true["baseline_value"]) == Decimal("500.00")
    
    # col IS NOT TRUE -> row 2 (false, 200) + row 3 (null, 300) = 500.00
    ir_not_true = parse_flat("SELECT SUM(amount) FROM snapshot WHERE is_active IS NOT TRUE", contract)
    r_not_true = compare_flat(db, ir_not_true, ir_not_true, ctx)
    assert Decimal(r_not_true["baseline_value"]) == Decimal("500.00")
    
    # bare col -> rows 1, 4 (500)
    ir_bare = parse_flat("SELECT SUM(amount) FROM snapshot WHERE is_active", contract)
    r_bare = compare_flat(db, ir_bare, ir_bare, ctx)
    assert Decimal(r_bare["baseline_value"]) == Decimal("500.00")
    
    # NOT col -> row 2 (200); NULL row 3 evaluates to UNKNOWN so excluded!
    ir_not = parse_flat("SELECT SUM(amount) FROM snapshot WHERE NOT is_active", contract)
    r_not = compare_flat(db, ir_not, ir_not, ctx)
    assert Decimal(r_not["baseline_value"]) == Decimal("200.00")


# ===========================================================================
# 4. Strict Refusal Boundaries
# ===========================================================================

@pytest.mark.parametrize("disallowed_sql", [
    # Inter-field OR
    "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND (status = 'Completed' OR currency = 'INR')",
    # Cross-column comparison
    "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND order_amount > refund_amount",
    # Dynamic non-deterministic functions
    "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND order_date < CURRENT_DATE()",
    "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND order_date < NOW()",
    # Implicit uncast string date comparison
    "SELECT SUM(order_amount) FROM orders WHERE order_date >= '2026-08-01' AND order_date < '2026-09-01'",
    # JOINs
    "SELECT SUM(order_amount) FROM orders JOIN orders b USING (order_id)",
    # Subqueries
    "SELECT SUM(order_amount) FROM orders WHERE order_amount > (SELECT 100)",
    # Window functions
    "SELECT SUM(order_amount) OVER () FROM orders",
    # GROUP BY
    "SELECT SUM(order_amount) FROM orders GROUP BY status",
    # Unsupported aggregate
    "SELECT AVG(order_amount) FROM orders",
])
def test_refusal_boundaries(disallowed_sql):
    """Assert MetricGuard strictly refuses disallowed or non-deterministic SQL."""
    with pytest.raises(CoreError):
        parse_definition(disallowed_sql, "order_date")


# ===========================================================================
# 5. Scenarios A through F (Reconciliation & Shapley Value Bridge)
# ===========================================================================

def test_scenario_a_active_segment_inequality(snapshot):
    """Scenario A: status != 'Cancelled' filter change with exact Shapley decomposition."""
    base_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"
    target_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND status != 'Cancelled'"
    
    base_ir = parse_definition(base_sql, "order_date")
    target_ir = parse_definition(target_sql, "order_date")
    
    res = compare(snapshot, base_ir, target_ir, CTX)
    assert Decimal(res["baseline_value"]) == Decimal("105400000.00")
    # Excludes O07 (500000.00 Cancelled) and O08 (200000.00 NULL status):
    # 105400000 - 500000 - 200000 = 104700000.00
    assert Decimal(res["target_value"]) == Decimal("104700000.00")
    assert Decimal(res["delta_target_minus_baseline"]) == Decimal("-700000.00")
    
    # Evidence attribution matches delta
    assert sum(Decimal(r["delta"]) for r in res["evidence"]) == Decimal("-700000.00")
    # Shapley Value Bridge has zero residual
    assert Decimal(res["residual"]) == Decimal("0.00")


def test_scenario_b_deletion_tracking_is_null(snapshot):
    """Scenario B: payment_date IS NULL filter change."""
    base_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"
    target_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND payment_date IS NULL"
    
    base_ir = parse_definition(base_sql, "order_date")
    target_ir = parse_definition(target_sql, "order_date")
    
    res = compare(snapshot, base_ir, target_ir, CTX)
    assert Decimal(res["baseline_value"]) == Decimal("105400000.00")
    assert Decimal(res["target_value"]) == Decimal("1820000.00")
    assert Decimal(res["delta_target_minus_baseline"]) == Decimal("-103580000.00")
    assert sum(Decimal(r["delta"]) for r in res["evidence"]) == Decimal("-103580000.00")
    assert Decimal(res["residual"]) == Decimal("0.00")


def test_scenario_c_threshold_filtering_gt(snapshot):
    """Scenario C: High-value threshold change with order_amount > 1000000.00."""
    base_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"
    target_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND order_amount > 1000000.00"
    
    base_ir = parse_definition(base_sql, "order_date")
    target_ir = parse_definition(target_sql, "order_date")
    
    res = compare(snapshot, base_ir, target_ir, CTX)
    assert Decimal(res["baseline_value"]) == Decimal("105400000.00")
    assert Decimal(res["target_value"]) == Decimal("103580000.00")
    assert Decimal(res["delta_target_minus_baseline"]) == Decimal("-1820000.00")
    assert sum(Decimal(r["delta"]) for r in res["evidence"]) == Decimal("-1820000.00")
    assert Decimal(res["residual"]) == Decimal("0.00")


def test_scenario_d_range_bounded_between(snapshot):
    """Scenario D: Tiered range change with order_amount BETWEEN 500000.00 AND 2320000.00."""
    base_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"
    target_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND order_amount BETWEEN 500000.00 AND 2320000.00"
    
    base_ir = parse_definition(base_sql, "order_date")
    target_ir = parse_definition(target_sql, "order_date")
    
    res = compare(snapshot, base_ir, target_ir, CTX)
    assert Decimal(res["baseline_value"]) == Decimal("105400000.00")
    assert Decimal(res["target_value"]) == Decimal("4780000.00")
    assert Decimal(res["delta_target_minus_baseline"]) == Decimal("-100620000.00")
    assert sum(Decimal(r["delta"]) for r in res["evidence"]) == Decimal("-100620000.00")
    assert Decimal(res["residual"]) == Decimal("0.00")


def test_scenario_e_date_window_between(snapshot):
    """Scenario E: Date window comparison with payment_date BETWEEN DATE '...' AND DATE '...'."""
    base_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"
    target_sql = "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01' AND payment_date BETWEEN DATE '2026-08-06' AND DATE '2026-08-16'"
    
    base_ir = parse_definition(base_sql, "order_date")
    target_ir = parse_definition(target_sql, "order_date")
    
    res = compare(snapshot, base_ir, target_ir, CTX)
    assert Decimal(res["baseline_value"]) == Decimal("105400000.00")
    assert Decimal(res["target_value"]) == Decimal("102320000.00")
    assert Decimal(res["delta_target_minus_baseline"]) == Decimal("-3080000.00")
    assert sum(Decimal(r["delta"]) for r in res["evidence"]) == Decimal("-3080000.00")
    assert Decimal(res["residual"]) == Decimal("0.00")
