"""Restricted Level-1 SQL Normalization for MetricGuard.

Performs closed, semantics-preserving syntactic unwrapping of single-use,
non-recursive CTE filter/projection wrappers into canonical single-table SQL.
Never adds new analytical semantics or multi-table JOIN execution.
"""
from __future__ import annotations

import hashlib
from typing import Any

import sqlglot
from sqlglot import exp

from .common import SCHEMA, CoreError

NORMALIZATION_KIND = "restricted_single_cte_wrapper"
NORMALIZATION_VERSION = "v5.1"


class NormalizationRefusal(Exception):
    """Refusal during Level-1 SQL normalization with exact diagnostic code."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def normalize_level1_cte(sql: str) -> tuple[bool, str, str | None, dict[str, Any] | None]:
    """
    Attempt Restricted Level-1 CTE normalization on compiled SQL.

    Returns:
        (success, normalized_sql_or_original, diagnostic_code_or_none, metadata_dict_or_none)
    """
    if not isinstance(sql, str) or not sql.strip():
        return False, sql, "MISSING_COMPILED_SQL", None

    # Parse with sqlglot
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:
        try:
            statements = sqlglot.parse(sql, read="postgres")
        except Exception:
            return False, sql, "UNPARSEABLE_SQL", None

    if len(statements) != 1:
        return False, sql, "UNSUPPORTED_SQL", None

    stmt = statements[0]
    if isinstance(stmt, (exp.Union, exp.Intersect, exp.Except)):
        return (
            False,
            sql,
            "SET_OPERATION_DETECTED: Set operations (UNION/INTERSECT/EXCEPT) are outside single-table analytical boundary.",
            None,
        )

    if not isinstance(stmt, exp.Select):
        return False, sql, "UNSUPPORTED_SQL", None

    tree = stmt

    # Check if a CTE (WITH clause) is present
    with_clause = tree.args.get("with_") or tree.find(exp.With)
    ctes = list(tree.find_all(exp.CTE))

    if not with_clause and not ctes:
        # Not a CTE query: nothing to normalize
        return False, sql, None, None

    original_sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()

    try:
        normalized_tree = _unwrap_single_cte(tree)
        normalized_sql = normalized_tree.sql(dialect="duckdb")
        normalized_sql_hash = hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest()

        metadata = {
            "normalization_applied": True,
            "normalization_kind": NORMALIZATION_KIND,
            "rules_version": NORMALIZATION_VERSION,
            "original_sql_hash": original_sql_hash,
            "normalized_sql_hash": normalized_sql_hash,
        }
        return True, normalized_sql, None, metadata

    except NormalizationRefusal as refusal:
        diag = f"{refusal.code}: {refusal.message}"
        return False, sql, diag, None


def _unwrap_single_cte(tree: exp.Select) -> exp.Select:
    """Internal AST unwrapping worker. Raises NormalizationRefusal if any safety invariant is violated."""
    # 1. Exactly one CTE check
    ctes = list(tree.find_all(exp.CTE))
    if len(ctes) != 1:
        raise NormalizationRefusal(
            "CTE_MULTIPLE",
            f"Multiple CTEs ({len(ctes)} found) exceed Restricted Level-1 single-CTE normalization. Must be materialized or flattened upstream."
        )

    with_clause = tree.args.get("with_") or tree.find(exp.With)
    if with_clause and with_clause.args.get("recursive"):
        raise NormalizationRefusal(
            "CTE_RECURSIVE",
            "Recursive CTEs are disallowed due to unbounded execution and non-deterministic graph semantics."
        )

    cte = ctes[0]
    cte_name = cte.alias.lower() if cte.alias else ""
    if not cte_name:
        raise NormalizationRefusal("CTE_UNNAMED", "Common Table Expression lacks an identifier alias.")

    cte_query = cte.this
    if not isinstance(cte_query, exp.Select):
        raise NormalizationRefusal(
            "SET_OPERATION_DETECTED",
            "Set operation or non-SELECT CTE expression is outside single-table analytical boundary."
        )

    # 2. Structural safety inside CTE query
    if cte_query.args.get("with_") or len(list(cte_query.find_all(exp.CTE))) > 0:
        raise NormalizationRefusal("CTE_MULTIPLE", "Nested CTE inside CTE body is unsupported.")

    if list(cte_query.find_all(exp.Join)):
        raise NormalizationRefusal(
            "CTE_CONTAINS_JOIN",
            "Multi-table JOIN inside CTE is not supported without a proven 1:1 cardinality contract."
        )

    if list(cte_query.find_all(exp.Window)):
        raise NormalizationRefusal(
            "CTE_CONTAINS_WINDOW",
            "Window function inside CTE introduces order-dependent state outside IR."
        )

    if list(cte_query.find_all(exp.AggFunc)):
        raise NormalizationRefusal(
            "CTE_CONTAINS_AGGREGATION",
            "Aggregate function inside CTE alters row grain; sum-of-sums reconciliation is prohibited."
        )

    if cte_query.args.get("group") or list(cte_query.find_all(exp.Group)):
        raise NormalizationRefusal(
            "CTE_CONTAINS_GROUP_BY",
            "GROUP BY clause inside CTE alters row grain and collapses distinct records."
        )

    if cte_query.args.get("distinct") or list(cte_query.find_all(exp.Distinct)):
        raise NormalizationRefusal(
            "DISTINCT_ROW_SEMANTICS",
            "SELECT DISTINCT inside CTE alters dataset row cardinality and record multiplicity."
        )

    if cte_query.args.get("limit") or cte_query.args.get("offset") or list(cte_query.find_all((exp.Limit, exp.Offset))):
        raise NormalizationRefusal(
            "UNSUPPORTED_LIMIT_OFFSET",
            "LIMIT/OFFSET inside CTE creates non-deterministic truncated population scope."
        )

    if list(cte_query.find_all((exp.Union, exp.Intersect, exp.Except))):
        raise NormalizationRefusal(
            "SET_OPERATION_DETECTED",
            "Set operation inside CTE is outside single-table analytical boundary."
        )

    for func in cte_query.find_all(exp.Func):
        fname = func.sql_name().lower() if hasattr(func, "sql_name") else type(func).__name__.lower()
        if fname in ("random", "rand", "now", "current_date", "current_timestamp", "today", "getdate", "clock_timestamp"):
            raise NormalizationRefusal(
                "VOLATILE_FUNCTION_DETECTED",
                f"Function {fname}() introduces non-deterministic sampling violating repeatable reconciliation."
            )

    # 3. Base table extraction from CTE
    cte_from = cte_query.args.get("from_")
    if not cte_from or not isinstance(cte_from.this, exp.Table):
        raise NormalizationRefusal("CTE_CONTAINS_JOIN", "CTE must read from exactly one base table.")

    base_table = cte_from.this
    base_table_name = base_table.name.lower()
    base_table_alias = base_table.alias.lower() if base_table.alias else base_table_name

    # Check for extra tables in CTE FROM
    other_cte_tables = list(cte_query.find_all(exp.Table))
    if len(other_cte_tables) != 1:
        raise NormalizationRefusal("CTE_CONTAINS_JOIN", "Multiple source tables detected inside CTE.")

    # 4. Outer query structural checks
    # Outer table references check
    outer_tables = [t for t in tree.find_all(exp.Table) if t is not base_table]
    if not outer_tables:
        raise NormalizationRefusal("CTE_UNREFERENCED", f"CTE '{cte_name}' is not referenced in outer query.")

    outer_table_names = [t.name.lower() for t in outer_tables if t.name]
    if any(name != cte_name for name in outer_table_names):
        raise NormalizationRefusal(
            "OUTER_QUERY_CONTAINS_JOIN",
            f"Outer query references tables beyond single CTE: {outer_table_names}"
        )

    if len(outer_tables) > 1:
        raise NormalizationRefusal(
            "CTE_MULTI_REFERENCE",
            f"CTE '{cte_name}' is referenced {len(outer_tables)} times in outer query. Level-1 normalization only supports single linear consumption."
        )

    if list(tree.find_all(exp.Join)):
        raise NormalizationRefusal(
            "OUTER_QUERY_CONTAINS_JOIN",
            "Outer query joins CTE with secondary table. Multi-table joins are unsupported."
        )

    if list(tree.find_all(exp.Window)):
        raise NormalizationRefusal(
            "WINDOW_DETECTED",
            "Window function OVER (...) in outer query is unsupported; exactly one scalar aggregate required."
        )

    if tree.args.get("limit") or tree.args.get("offset") or list(tree.find_all((exp.Limit, exp.Offset))):
        raise NormalizationRefusal("UNSUPPORTED_LIMIT_OFFSET", "LIMIT/OFFSET on scalar aggregate query is disallowed.")

    if list(tree.find_all((exp.Union, exp.Intersect, exp.Except))):
        raise NormalizationRefusal("SET_OPERATION_DETECTED", "Set operation UNION in outer query is unsupported.")

    for func in tree.find_all(exp.Func):
        fname = func.sql_name().lower() if hasattr(func, "sql_name") else type(func).__name__.lower()
        if fname in ("random", "rand", "now", "current_date", "current_timestamp", "today", "getdate", "clock_timestamp"):
            raise NormalizationRefusal(
                "VOLATILE_FUNCTION_DETECTED",
                f"Function {fname}() introduces non-deterministic sampling violating repeatable reconciliation."
            )

    if tree.args.get("distinct"):
        raise NormalizationRefusal(
            "DISTINCT_ROW_SEMANTICS",
            "SELECT DISTINCT in outer query changes row semantics."
        )

    for agg in tree.find_all(exp.AggFunc):
        is_distinct = bool(agg.args.get("distinct") or isinstance(agg.this, exp.Distinct))
        if is_distinct:
            # Only COUNT(DISTINCT customer_id) is allowed
            is_count_distinct_cust = (
                isinstance(agg, exp.Count)
                and any(isinstance(c, exp.Column) and c.name.lower() in ("customer_id", "cust_id") for c in agg.find_all(exp.Column))
            )
            if not is_count_distinct_cust:
                raise NormalizationRefusal(
                    "DISTINCT_ROW_SEMANTICS",
                    "SUM(DISTINCT ...) changes additive accounting semantics; only COUNT(DISTINCT customer_id) is supported."
                )

    outer_cte_table = outer_tables[0]
    outer_cte_alias = outer_cte_table.alias.lower() if outer_cte_table.alias else cte_name

    # 5. Projection & Alias Mapping
    is_star = False
    projection_map: dict[str, exp.Expression] = {}
    seen_aliases: set[str] = set()

    for expr in cte_query.expressions:
        if isinstance(expr, exp.Star):
            is_star = True
        elif isinstance(expr, exp.Alias):
            alias_name = expr.alias.lower()
            if alias_name in seen_aliases:
                raise NormalizationRefusal(
                    "ALIAS_RESOLUTION_FAILURE",
                    f"Duplicate alias '{alias_name}' in CTE projection creates ambiguous column reference."
                )
            seen_aliases.add(alias_name)

            inner_expr = expr.this
            while isinstance(inner_expr, exp.Paren):
                inner_expr = inner_expr.this

            # Check if alias shadows an existing schema column with different underlying column
            if alias_name in SCHEMA and isinstance(inner_expr, exp.Column):
                if inner_expr.name.lower() != alias_name:
                    raise NormalizationRefusal(
                        "ALIAS_RESOLUTION_FAILURE",
                        f"Alias '{alias_name}' shadows an existing canonical column with different semantics."
                    )

            # Check for supported expression inside alias
            if isinstance(inner_expr, (exp.Add, exp.Sub, exp.Mul, exp.Coalesce)):
                for n in inner_expr.walk():
                    if isinstance(n, (exp.Div, exp.Case, exp.Anonymous, exp.Window)):
                        raise NormalizationRefusal(
                            "COMPUTED_EXPRESSION_UNSUPPORTED",
                            f"Operator or construct '{type(n).__name__}' in CTE projection exceeds supported IR arithmetic (+, -, *, COALESCE)."
                        )
            elif not isinstance(inner_expr, (exp.Column, exp.Literal)):
                raise NormalizationRefusal(
                    "COMPUTED_EXPRESSION_UNSUPPORTED",
                    f"Expression '{type(inner_expr).__name__}' inside CTE projection cannot be lowered to MetricGuard IR."
                )

            projection_map[alias_name] = inner_expr
        elif isinstance(expr, exp.Column):
            col_name = expr.name.lower()
            if col_name in seen_aliases:
                raise NormalizationRefusal(
                    "ALIAS_RESOLUTION_FAILURE",
                    f"Duplicate column '{col_name}' in CTE projection creates ambiguous column reference."
                )
            seen_aliases.add(col_name)
            projection_map[col_name] = expr
        else:
            raise NormalizationRefusal(
                "COMPUTED_EXPRESSION_UNSUPPORTED",
                f"Unaliased expression '{type(expr).__name__}' in CTE projection is outside IR grammar."
            )

    # 6. Replace column references in outer query
    # Find all columns in outer query excluding those inside the CTE definition
    outer_columns = [c for c in tree.find_all(exp.Column) if not _is_inside_node(c, cte)]

    for col in outer_columns:
        tbl_qual = col.table.lower() if col.table else None
        if tbl_qual and tbl_qual not in (cte_name, outer_cte_alias, base_table_alias, base_table_name):
            raise NormalizationRefusal(
                "ALIAS_RESOLUTION_FAILURE",
                f"Unbound table qualifier '{tbl_qual}' in outer query."
            )

        col_name = col.name.lower()

        if not is_star:
            if col_name not in projection_map:
                raise NormalizationRefusal(
                    "ALIAS_RESOLUTION_FAILURE",
                    f"Column '{col_name}' referenced in outer query is missing from CTE projection."
                )
            mapped_expr = projection_map[col_name].copy()
            # If base table has alias, ensure column matches base alias; if not, strip table qualifier
            for inner_col in mapped_expr.find_all(exp.Column):
                if base_table.alias:
                    inner_col.set("table", exp.to_identifier(base_table.alias))
                else:
                    inner_col.set("table", None)
            col.replace(mapped_expr)
        else:
            # Strip CTE table qualifier from column
            if col.table:
                if base_table.alias:
                    col.set("table", exp.to_identifier(base_table.alias))
                else:
                    col.set("table", None)

    # 7. Predicate Composition
    inner_where = cte_query.args.get("where")
    outer_where = tree.args.get("where")

    if inner_where and outer_where:
        inner_cond = exp.Paren(this=inner_where.this.copy())
        outer_cond = exp.Paren(this=outer_where.this.copy())
        merged_cond = exp.And(this=inner_cond, expression=outer_cond)
        final_where = exp.Where(this=merged_cond)
    elif inner_where:
        final_where = exp.Where(this=inner_where.this.copy())
    elif outer_where:
        final_where = exp.Where(this=outer_where.this.copy())
    else:
        final_where = None

    # 8. Construct Normalized AST
    # Replace outer FROM with base table
    tree.set("from_", exp.From(this=base_table.copy()))
    tree.set("where", final_where)
    tree.set("with_", None)

    # 9. Target shape validation
    # Outer query must have exactly one scalar aggregate
    outer_aggs = list(tree.find_all(exp.AggFunc))
    if len(outer_aggs) != 1:
        raise NormalizationRefusal(
            "NORMALIZED_QUERY_UNSUPPORTED",
            "Normalized query fails core IR validation: Exactly one scalar aggregate is required."
        )

    agg = outer_aggs[0]
    if not isinstance(agg, (exp.Sum, exp.Count)):
        raise NormalizationRefusal(
            "NORMALIZED_QUERY_UNSUPPORTED",
            f"Normalized query fails core IR validation: Only SUM and COUNT are supported, found {type(agg).__name__}."
        )

    if tree.args.get("group") or list(tree.find_all(exp.Group)):
        raise NormalizationRefusal(
            "NORMALIZED_QUERY_UNSUPPORTED",
            "Normalized query fails core IR validation: GROUP BY in outer query is unsupported."
        )

    if base_table_name != "orders":
        raise NormalizationRefusal(
            "NORMALIZED_QUERY_UNSUPPORTED",
            f"Normalized query fails core IR validation: Only the registered orders table is allowed (found '{base_table_name}')."
        )

    return tree


def _is_inside_node(child: exp.Expression, parent: exp.Expression) -> bool:
    """Check if child AST node is a descendant of parent AST node."""
    curr = child.parent
    while curr:
        if curr is parent:
            return True
        curr = curr.parent
    return False
