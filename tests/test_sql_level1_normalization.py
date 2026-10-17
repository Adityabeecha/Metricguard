"""Verification Test Suite: Restricted Level-1 SQL Normalization (V5.1).

Validates:
1. Positive normalization, IR equivalence, and exact decimal parity against golden DuckDB oracle.
2. Refusal with granular diagnostic codes for all disallowed constructs.
3. Provenance and DefinitionVersion stability.
4. Seamless integration with V5-01 change sets, V5-02 metric impact, and V5-03 Metric Diff.
"""
from decimal import Decimal
import hashlib
from pathlib import Path
import pytest

from packages.metricguard_core import compare, ingest_csv, parse_definition
from packages.metricguard_core.common import CoreError
from packages.metricguard_core.normalization import normalize_level1_cte
from services.api.metadata_adapters import analyze_dbt_sql, parse_dbt_project_artifacts
from services.api.store import Store

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
def snapshot_db(tmp_path):
    db_file = tmp_path / "orders.duckdb"
    ingest_csv(GOLDEN_CSV, db_file)
    return db_file


@pytest.fixture
def store(tmp_path):
    db_file = tmp_path / "app.db"
    return Store(f"sqlite:///{db_file}")


# ===========================================================================
# 1. POSITIVE TESTS: Normalization, IR Equivalence & Decimal Parity
# ===========================================================================

@pytest.mark.parametrize("test_id,cte_sql,equivalent_direct_sql,expected_value", [
    (
        "P-01_filter_wrapper",
        """
        WITH filtered AS (
            SELECT order_id, order_amount, order_date, status
            FROM orders WHERE status = 'Completed'
        )
        SELECT SUM(order_amount)
        FROM filtered
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        """
        SELECT SUM(order_amount)
        FROM orders
        WHERE status = 'Completed'
          AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("104000000.00"),
    ),
    (
        "P-03_date_in_cte",
        """
        WITH aug_orders AS (
            SELECT order_id, order_amount, order_date, status
            FROM orders
            WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        )
        SELECT SUM(order_amount)
        FROM aug_orders WHERE status = 'Completed'
        """,
        """
        SELECT SUM(order_amount)
        FROM orders
        WHERE (order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01')
          AND status = 'Completed'
        """,
        Decimal("104000000.00"),
    ),
    (
        "P-04_column_pruning",
        """
        WITH pruned AS (
            SELECT order_id, customer_id, order_date, order_amount
            FROM orders
        )
        SELECT SUM(order_amount)
        FROM pruned
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        """
        SELECT SUM(order_amount)
        FROM orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("105400000.00"),
    ),
    (
        "P-05_select_star",
        """
        WITH all_orders AS (
            SELECT * FROM orders
        )
        SELECT SUM(order_amount)
        FROM all_orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        """
        SELECT SUM(order_amount)
        FROM orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("105400000.00"),
    ),
    (
        "P-07_column_aliasing",
        """
        WITH renamed AS (
            SELECT order_amount AS amount, order_date, status FROM orders
        )
        SELECT SUM(amount) FROM renamed
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        """
        SELECT SUM(order_amount) FROM orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("105400000.00"),
    ),
    (
        "P-08_date_aliasing",
        """
        WITH renamed_dt AS (
            SELECT order_amount, order_date AS dt, status FROM orders
        )
        SELECT SUM(order_amount) FROM renamed_dt
        WHERE dt >= DATE '2026-08-01' AND dt < DATE '2026-09-01'
        """,
        """
        SELECT SUM(order_amount) FROM orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("105400000.00"),
    ),
    (
        "P-09_table_qualifiers",
        """
        WITH o_sub AS (
            SELECT o.order_amount AS net_val, o.order_date, o.status
            FROM orders o
        )
        SELECT SUM(c.net_val) AS gross_rev
        FROM o_sub c
        WHERE c.order_date >= DATE '2026-08-01' AND c.order_date < DATE '2026-09-01'
        """,
        """
        SELECT SUM(order_amount) FROM orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("105400000.00"),
    ),
    (
        "P-10_aliased_arithmetic",
        """
        WITH net_calc AS (
            SELECT (order_amount - refund_amount) AS net_amt, order_date FROM orders
        )
        SELECT SUM(net_amt) FROM net_calc
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        """
        SELECT SUM(order_amount - refund_amount) FROM orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("101080000.00"),
    ),
    (
        "P-11_boolean_precedence_parentheses",
        """
        WITH multi_status AS (
            SELECT order_amount, order_date, status, payment_status
            FROM orders
            WHERE status = 'Completed' OR status = 'Shipped'
        )
        SELECT SUM(order_amount) FROM multi_status
        WHERE (payment_status = 'Paid' OR payment_status IS NULL)
          AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        """
        SELECT SUM(order_amount) FROM orders
        WHERE (status = 'Completed' OR status = 'Shipped')
          AND (payment_status = 'Paid' OR payment_status IS NULL)
          AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("103580000.00"),
    ),
    (
        "P-12_split_interval_bounds",
        """
        WITH inner_bound AS (
            SELECT order_amount, order_date FROM orders
            WHERE order_date >= DATE '2026-08-01'
        )
        SELECT SUM(order_amount) FROM inner_bound
        WHERE order_date < DATE '2026-09-01'
        """,
        """
        SELECT SUM(order_amount) FROM orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("105400000.00"),
    ),
    (
        "P-13_count_distinct_aliasing",
        """
        WITH cust_slice AS (
            SELECT customer_id AS cust_id, order_date FROM orders
        )
        SELECT COUNT(DISTINCT cust_id) FROM cust_slice
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        """
        SELECT COUNT(DISTINCT customer_id) FROM orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("7"),
    ),
    (
        "P-14_cosmetic_case_and_comments",
        """
        with CTE_Orders as (
            select ORDER_AMOUNT as amount, Order_Date as od
            from Orders -- inline comment
        )
        select sum(amount) from cte_orders
        where od >= date '2026-08-01' and od < date '2026-09-01'
        """,
        """
        SELECT SUM(order_amount) FROM orders
        WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
        """,
        Decimal("105400000.00"),
    ),
])
def test_positive_level1_normalization(snapshot_db, test_id, cte_sql, equivalent_direct_sql, expected_value):
    # 1. Metadata classification: must become executable
    present, support_state, diags = analyze_dbt_sql(cte_sql)
    assert present is True
    assert support_state == "executable", f"Failed for {test_id}: {diags}"
    assert not any("CTE_DETECTED" in d for d in diags)

    # 2. Structural IR lowering: must match equivalent unnested direct SQL ir_hash
    cte_ir = parse_definition(cte_sql, "order_date")
    direct_ir = parse_definition(equivalent_direct_sql, "order_date")
    assert cte_ir["ir_hash"] == direct_ir["ir_hash"], f"IR Hash mismatch for {test_id}"

    # 3. Numeric Oracle verification: must yield exact decimal value on golden dataset
    ctx = dict(CTX)
    if "count" in test_id.lower():
        ctx["unit"] = "count"
    res = compare(snapshot_db, cte_ir, direct_ir, ctx)
    assert Decimal(res["baseline_value"]) == expected_value
    assert Decimal(res["target_value"]) == expected_value
    assert Decimal(res["delta_target_minus_baseline"]) == Decimal("0")


def test_positive_cte_without_date_needs_context():
    sql = "WITH c AS (SELECT order_amount, status FROM orders WHERE status = 'Completed') SELECT SUM(order_amount) FROM c"
    present, support_state, diags = analyze_dbt_sql(sql)
    assert present is True
    assert support_state == "needs_context"
    assert any("CONTEXT_REQUIRED" in d for d in diags)


def test_positive_count_star():
    sql = "WITH raw_events AS (SELECT order_id FROM orders WHERE status = 'Completed') SELECT COUNT(*) FROM raw_events"
    present, support_state, diags = analyze_dbt_sql(sql)
    assert present is True
    assert support_state == "needs_context"
    assert not diags or any("CONTEXT_REQUIRED" in d for d in diags)


# ===========================================================================
# 2. NEGATIVE / REFUSED TESTS: Granular Diagnostic Error Codes
# ===========================================================================

@pytest.mark.parametrize("case_id,expected_diag_code,disallowed_sql", [
    (
        "N-01_cte_multiple",
        "CTE_MULTIPLE",
        "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b",
    ),
    (
        "N-02_cte_recursive",
        "CTE_RECURSIVE",
        "WITH RECURSIVE r AS (SELECT * FROM orders UNION ALL SELECT * FROM orders) SELECT SUM(order_amount) FROM r",
    ),
    (
        "N-03_cte_multi_ref",
        "CTE_MULTI_REFERENCE",
        "WITH c AS (SELECT * FROM orders) SELECT SUM(a.order_amount) FROM c a JOIN c b ON a.order_id = b.order_id",
    ),
    (
        "N-04_cte_contains_join",
        "CTE_CONTAINS_JOIN",
        "WITH j AS (SELECT o.order_amount FROM orders o JOIN cust c ON o.customer_id = c.id) SELECT SUM(order_amount) FROM j",
    ),
    (
        "N-05_outer_contains_join",
        "OUTER_QUERY_CONTAINS_JOIN",
        "WITH c AS (SELECT * FROM orders) SELECT SUM(c.order_amount) FROM c JOIN customers cust ON c.customer_id = cust.id",
    ),
    (
        "N-06_cte_contains_aggregation",
        "CTE_CONTAINS_AGGREGATION",
        "WITH agg AS (SELECT customer_id, SUM(order_amount) as tot FROM orders GROUP BY customer_id) SELECT SUM(tot) FROM agg",
    ),
    (
        "N-07_cte_contains_group_by",
        "CTE_CONTAINS_GROUP_BY",
        "WITH grp AS (SELECT status, order_amount FROM orders GROUP BY status, order_amount) SELECT SUM(order_amount) FROM grp",
    ),
    (
        "N-08A_cte_contains_window",
        "CTE_CONTAINS_WINDOW",
        "WITH w AS (SELECT order_amount, ROW_NUMBER() OVER () as rn FROM orders) SELECT SUM(order_amount) FROM w WHERE rn = 1",
    ),
    (
        "N-08B_outer_contains_window",
        "WINDOW_DETECTED",
        "WITH c AS (SELECT * FROM orders) SELECT SUM(order_amount) OVER () FROM c",
    ),
    (
        "N-09A_distinct_row_semantics",
        "DISTINCT_ROW_SEMANTICS",
        "WITH d AS (SELECT DISTINCT order_amount, customer_id FROM orders) SELECT SUM(order_amount) FROM d",
    ),
    (
        "N-09B_outer_distinct_sum",
        "DISTINCT_ROW_SEMANTICS",
        "WITH c AS (SELECT order_amount FROM orders) SELECT SUM(DISTINCT order_amount) FROM c",
    ),
    (
        "N-10A_cte_limit",
        "UNSUPPORTED_LIMIT_OFFSET",
        "WITH l AS (SELECT order_amount FROM orders LIMIT 10) SELECT SUM(order_amount) FROM l",
    ),
    (
        "N-10B_outer_limit",
        "UNSUPPORTED_LIMIT_OFFSET",
        "WITH c AS (SELECT * FROM orders) SELECT SUM(order_amount) FROM c LIMIT 1",
    ),
    (
        "N-11A_cte_union",
        "SET_OPERATION_DETECTED",
        "WITH u AS (SELECT order_amount FROM orders UNION ALL SELECT order_amount FROM orders) SELECT SUM(order_amount) FROM u",
    ),
    (
        "N-11B_outer_union",
        "SET_OPERATION_DETECTED",
        "WITH c AS (SELECT order_amount FROM orders) SELECT SUM(order_amount) FROM c UNION ALL SELECT 0",
    ),
    (
        "N-12A_volatile_random",
        "VOLATILE_FUNCTION_DETECTED",
        "WITH r AS (SELECT order_amount FROM orders WHERE random() > 0.5) SELECT SUM(order_amount) FROM r",
    ),
    (
        "N-12B_volatile_time",
        "VOLATILE_FUNCTION_DETECTED",
        "WITH t AS (SELECT order_amount FROM orders WHERE order_date >= current_date) SELECT SUM(order_amount) FROM t",
    ),
    (
        "N-13A_alias_missing_col",
        "ALIAS_RESOLUTION_FAILURE",
        "WITH p AS (SELECT order_id, order_date FROM orders) SELECT SUM(order_amount) FROM p",
    ),
    (
        "N-13B_duplicate_alias",
        "ALIAS_RESOLUTION_FAILURE",
        "WITH dup AS (SELECT order_id AS x, customer_id AS x, order_amount FROM orders) SELECT SUM(order_amount) FROM dup WHERE x = 'O01'",
    ),
    (
        "N-13C_alias_shadows_column",
        "ALIAS_RESOLUTION_FAILURE",
        "WITH masked AS (SELECT refund_amount AS order_amount, order_date FROM orders) SELECT SUM(order_amount) FROM masked",
    ),
    (
        "N-14A_unsupported_division",
        "COMPUTED_EXPRESSION_UNSUPPORTED",
        "WITH s AS (SELECT order_amount / 2 AS half_amt FROM orders) SELECT SUM(half_amt) FROM s",
    ),
    (
        "N-14B_unsupported_case",
        "COMPUTED_EXPRESSION_UNSUPPORTED",
        "WITH c AS (SELECT CASE WHEN status = 'A' THEN order_amount ELSE 0 END AS x FROM orders) SELECT SUM(x) FROM c",
    ),
    (
        "N-15A_normalized_unsupported_avg",
        "NORMALIZED_QUERY_UNSUPPORTED",
        "WITH c AS (SELECT order_amount FROM orders) SELECT AVG(order_amount) FROM c",
    ),
    (
        "N-15B_normalized_non_aggregate",
        "NORMALIZED_QUERY_UNSUPPORTED",
        "WITH c AS (SELECT order_id, order_amount FROM orders) SELECT order_id, order_amount FROM c",
    ),
    (
        "N-15C_unsupported_source_table",
        "NORMALIZED_QUERY_UNSUPPORTED",
        "WITH c AS (SELECT amount FROM unapproved_payments) SELECT SUM(amount) FROM c",
    ),
])
def test_negative_level1_refusal(case_id, expected_diag_code, disallowed_sql):
    # 1. Metadata classification: must be metadata_only
    present, support_state, diags = analyze_dbt_sql(disallowed_sql)
    assert present is True
    assert support_state == "metadata_only", f"Case {case_id} should be metadata_only"

    # 2. Diagnostic code verification: must contain the exact expected code
    matching = [d for d in diags if expected_diag_code in d]
    assert len(matching) > 0, (
        f"Case {case_id} missing diagnostic code '{expected_diag_code}'. Found: {diags}"
    )

    # 3. Closed IR lowering must fail: parse_definition must raise CoreError
    with pytest.raises(CoreError):
        parse_definition(disallowed_sql, "order_date")


# ===========================================================================
# 3. PROVENANCE & DEFINITIONVERSION STABILITY TESTS
# ===========================================================================

def test_provenance_and_version_stability(store):
    project_id = "test-proj-v51"
    store.create_project("V5.1 Test Project", project_id=project_id)

    cte_sql = """
    WITH filtered AS (
        SELECT order_id, order_amount, order_date, status
        FROM orders WHERE status = 'Completed'
    )
    SELECT SUM(order_amount)
    FROM filtered
    WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
    """

    # Record definition with CTE SQL
    defn = store.record_definition(
        name="kpi_attributed_rev",
        source_label="dbt",
        owner_label="test",
        raw_sql=cte_sql,
        time_column="order_date",
        project_id=project_id,
    )

    ver = defn["versions"][-1]
    # Invariant 1: raw_sql remains authentic compiled source text
    assert ver["raw_sql"].strip() == cte_sql.strip()
    assert ver["raw_hash"] == hashlib.sha256(cte_sql.encode("utf-8")).hexdigest()

    # Invariant 2: normalized_sql and normalization metadata are recorded
    assert ver.get("normalized_sql") is not None
    assert "FROM orders" in ver["normalized_sql"]
    assert ver.get("normalization") is not None
    assert ver["normalization"]["normalization_applied"] is True
    assert ver["normalization"]["normalization_kind"] == "restricted_single_cte_wrapper"
    assert ver["support_state"] == "executable"

    # Invariant 3: Re-recording identical SQL reuses DefinitionVersion without inserting new row
    defn_repeat = store.record_definition(
        name="kpi_attributed_rev",
        source_label="dbt",
        owner_label="test",
        raw_sql=cte_sql,
        time_column="order_date",
        definition_id=defn["id"],
        project_id=project_id,
    )
    assert len(defn_repeat["versions"]) == 1
    assert defn_repeat["versions"][0]["id"] == ver["id"]


# ===========================================================================
# 4. END-TO-END V5 INTEGRATION TESTS: Change Set, Impact, and Metric Diff
# ===========================================================================

def test_v5_end_to_end_integration_with_normalization(store, tmp_path):
    project_id = "proj-v51-e2e"
    store.create_project("V5.1 E2E Project", project_id=project_id)
    owner, _ = store.create_user("Owner", "owner@v51.io")

    # Ingest golden snapshot
    snap_file = tmp_path / "snap.duckdb"
    manifest = ingest_csv(GOLDEN_CSV, snap_file)
    snap = store.record_snapshot("orders_golden", manifest, snap_file, project_id=project_id)

    # Manifest baseline: Direct query
    base_manifest = {
        "metadata": {
            "dbt_version": "1.7.0",
            "project_name": "jaffle_norm",
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json",
        },
        "nodes": {
            "model.jaffle_norm.kpi_orders": {
                "unique_id": "model.jaffle_norm.kpi_orders",
                "resource_type": "model",
                "name": "kpi_orders",
                "original_file_path": "models/kpi_orders.sql",
                "compiled_code": "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                "depends_on": {"nodes": []},
            }
        },
        "exposures": {},
        "child_map": {"model.jaffle_norm.kpi_orders": []},
    }

    # Manifest target: Equivalent CTE wrapper (cosmetic change)
    target_manifest = {
        "metadata": {
            "dbt_version": "1.7.0",
            "project_name": "jaffle_norm",
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json",
        },
        "nodes": {
            "model.jaffle_norm.kpi_orders": {
                "unique_id": "model.jaffle_norm.kpi_orders",
                "resource_type": "model",
                "name": "kpi_orders",
                "original_file_path": "models/kpi_orders.sql",
                "compiled_code": """
                WITH active AS (
                    SELECT order_id, order_amount, order_date, status
                    FROM orders WHERE status = 'Completed'
                )
                SELECT SUM(order_amount)
                FROM active
                WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'
                """,
                "depends_on": {"nodes": []},
            }
        },
        "exposures": {},
        "child_map": {"model.jaffle_norm.kpi_orders": []},
    }

    import json
    base_rev = store.record_dbt_import(json.dumps(base_manifest).encode(), project_id=project_id)
    target_rev = store.record_dbt_import(json.dumps(target_manifest).encode(), project_id=project_id)

    # 1. Verify V5-01 Change Set: cosmetic change because normalized IR is identical
    change_set = store.compute_dbt_change_set(
        base_rev["id"], target_rev["id"], project_id=project_id
    )
    res_change = change_set["resources"][0]
    assert res_change["change_category"] == "modified"
    assert "compiled_sql_changed" in res_change["change_dimensions"]
    assert "support_state_changed" not in res_change["change_dimensions"]
    assert res_change["new_support_state"] == "executable"

    # Declare a business Metric
    metric = store.create_metric(
        display_name="KPI Orders Revenue",
        importance="critical",
        project_id=project_id,
    )
    store.attach_external_metric_reference(
        metric_id=metric["id"],
        provider="dbt",
        external_id="model.jaffle_norm.kpi_orders",
        project_id=project_id,
    )
    base_res = base_rev["resources"][0]
    store.bind_metric_definition(
        metric_id=metric["id"],
        definition_id=base_res["definition_id"],
        status="active",
        approved_version_id=base_res["definition_version_id"],
        project_id=project_id,
    )

    # 2. Verify V5-02 Controlled Metric Impact: executes cleanly with zero delta
    impact_report = store.execute_metric_impact(
        baseline_revision_id=base_rev["id"],
        target_revision_id=target_rev["id"],
        snapshot_id=snap["id"],
        project_id=project_id,
    )
    assert len(impact_report["metrics"]) == 1
    metric_impact = impact_report["metrics"][0]
    assert metric_impact["execution_status"] == "executed"
    assert metric_impact["materiality"]["is_material"] is False
    assert Decimal(metric_impact["exact_delta"]) == Decimal("0")

    # 3. Verify V5-03 Revision-Level Metric Diff: clean completed outcome
    diff_report = store.execute_metric_diff(
        baseline_revision_id=base_rev["id"],
        target_revision_id=target_rev["id"],
        snapshot_id=snap["id"],
        mode="enforcing",
        project_id=project_id,
    )
    assert diff_report["overall_outcome"] == "completed"
    assert diff_report["exit_code"] == 0
    assert diff_report["coverage"]["declared_metrics"]["analyzable"] == 1
    assert diff_report["coverage"]["declared_metrics"]["refused"] == 0
    assert diff_report["coverage"]["declared_metrics"]["coverage_pct"] == 100.0
