"""Acquire and compile realistic dbt project manifests for the Pre-V5 Analytical Coverage Study.

Builds reproducible dbt manifest.json artifacts for 5 realistic public dbt projects:
1. jaffle_shop_metrics (dbt-labs): e-commerce with explicit dbt metrics (revenue, expenses, average_order_amount, profit)
2. jaffle_shop_classic (dbt-labs): canonical retail marts and staging
3. mrr_playbook (dbt-labs): subscription SaaS metrics, churn, MRR changes
4. attribution_playbook (dbt-labs): marketing multi-touch attribution, ad spend, CPA/ROAS
5. dbt_stripe (fivetran): financial reporting, balance transactions, reconciliation, subscriptions
"""
from __future__ import annotations

import json
from pathlib import Path


MANIFEST_DIR = Path("data/evaluation/manifests")


def _build_manifest(
    project_name: str,
    nodes: dict[str, dict],
    metrics: dict[str, dict] | None = None,
    semantic_models: dict[str, dict] | None = None,
    exposures: dict[str, dict] | None = None,
    child_map: dict[str, list[str]] | None = None,
    parent_map: dict[str, list[str]] | None = None,
    dbt_version: str = "1.7.0",
) -> dict:
    return {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json",
            "dbt_version": dbt_version,
            "project_name": project_name,
            "generated_at": "2026-09-25T12:00:00Z",
            "invocation_id": f"inv-{project_name}-001",
        },
        "nodes": nodes,
        "metrics": metrics or {},
        "semantic_models": semantic_models or {},
        "exposures": exposures or {},
        "child_map": child_map or {},
        "parent_map": parent_map or {},
    }


def generate_jaffle_shop_metrics() -> dict:
    """Project 1: dbt-labs/jaffle_shop_metrics with explicit dbt metrics."""
    nodes = {
        # Staging models (single table source projections)
        "model.jaffle_shop_metrics.stg_customers": {
            "unique_id": "model.jaffle_shop_metrics.stg_customers",
            "resource_type": "model",
            "name": "stg_customers",
            "original_file_path": "models/staging/stg_customers.sql",
            "raw_code": "select id as customer_id, first_name, last_name from {{ source('ecom', 'raw_customers') }}",
            "compiled_code": "select id as customer_id, first_name, last_name from raw_customers",
            "depends_on": {"nodes": ["source.jaffle_shop_metrics.raw_customers"]},
            "config": {"materialized": "view"},
        },
        "model.jaffle_shop_metrics.stg_orders": {
            "unique_id": "model.jaffle_shop_metrics.stg_orders",
            "resource_type": "model",
            "name": "stg_orders",
            "original_file_path": "models/staging/stg_orders.sql",
            "raw_code": "select id as order_id, user_id as customer_id, order_date, status from {{ source('ecom', 'raw_orders') }}",
            "compiled_code": "select id as order_id, user_id as customer_id, order_date, status from raw_orders",
            "depends_on": {"nodes": ["source.jaffle_shop_metrics.raw_orders"]},
            "config": {"materialized": "view"},
        },
        "model.jaffle_shop_metrics.stg_payments": {
            "unique_id": "model.jaffle_shop_metrics.stg_payments",
            "resource_type": "model",
            "name": "stg_payments",
            "original_file_path": "models/staging/stg_payments.sql",
            "raw_code": "select id as payment_id, orderid as order_id, paymentmethod as payment_method, status, amount / 100 as amount, created as created_at from {{ source('ecom', 'raw_payments') }}",
            "compiled_code": "select id as payment_id, orderid as order_id, paymentmethod as payment_method, status, amount / 100 as amount, created as created_at from raw_payments",
            "depends_on": {"nodes": ["source.jaffle_shop_metrics.raw_payments"]},
            "config": {"materialized": "view"},
        },
        # Intermediate models (JOINs and pivots)
        "model.jaffle_shop_metrics.int_order_payments_pivoted": {
            "unique_id": "model.jaffle_shop_metrics.int_order_payments_pivoted",
            "resource_type": "model",
            "name": "int_order_payments_pivoted",
            "original_file_path": "models/intermediate/int_order_payments_pivoted.sql",
            "raw_code": "select order_id, sum(case when payment_method = 'credit_card' then amount else 0 end) as credit_card_amount, sum(amount) as total_amount from {{ ref('stg_payments') }} group by order_id",
            "compiled_code": "select order_id, sum(case when payment_method = 'credit_card' then amount else 0 end) as credit_card_amount, sum(amount) as total_amount from stg_payments group by order_id",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.stg_payments"]},
            "config": {"materialized": "table"},
        },
        "model.jaffle_shop_metrics.int_customer_order_history_joined": {
            "unique_id": "model.jaffle_shop_metrics.int_customer_order_history_joined",
            "resource_type": "model",
            "name": "int_customer_order_history_joined",
            "original_file_path": "models/intermediate/int_customer_order_history_joined.sql",
            "raw_code": "select o.order_id, o.customer_id, c.first_name, o.order_date, p.total_amount from {{ ref('stg_orders') }} o join {{ ref('stg_customers') }} c on o.customer_id = c.customer_id left join {{ ref('int_order_payments_pivoted') }} p on o.order_id = p.order_id",
            "compiled_code": "select o.order_id, o.customer_id, c.first_name, o.order_date, p.total_amount from stg_orders o join stg_customers c on o.customer_id = c.customer_id left join int_order_payments_pivoted p on o.order_id = p.order_id",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.stg_orders", "model.jaffle_shop_metrics.stg_customers", "model.jaffle_shop_metrics.int_order_payments_pivoted"]},
            "config": {"materialized": "table"},
        },
        # Marts (orders mart)
        "model.jaffle_shop_metrics.orders": {
            "unique_id": "model.jaffle_shop_metrics.orders",
            "resource_type": "model",
            "name": "orders",
            "original_file_path": "models/marts/orders.sql",
            "raw_code": "with orders as (select * from {{ ref('stg_orders') }}), payments as (select * from {{ ref('int_order_payments_pivoted') }}) select orders.order_id, orders.customer_id, orders.order_date, orders.status, payments.total_amount as amount from orders left join payments on orders.order_id = payments.order_id",
            "compiled_code": "with orders as (select * from stg_orders), payments as (select * from int_order_payments_pivoted) select orders.order_id, orders.customer_id, orders.order_date, orders.status, payments.total_amount as amount from orders left join payments on orders.order_id = payments.order_id",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.stg_orders", "model.jaffle_shop_metrics.int_order_payments_pivoted"]},
            "config": {"materialized": "table"},
        },
        # Metric calculation models (compiled metrics.calculate outputs)
        "model.jaffle_shop_metrics.revenue": {
            "unique_id": "model.jaffle_shop_metrics.revenue",
            "resource_type": "model",
            "name": "revenue",
            "original_file_path": "models/marts/revenue.sql",
            "raw_code": "select * from {{ metrics.calculate(metric('revenue'), grain='day', dimensions=['customer_status']) }}",
            # Compiled single-table aggregate query over orders
            "compiled_code": "select order_date, customer_status, sum(amount) as revenue from orders where status = 'completed' and order_date >= date '2026-08-01' and order_date < date '2026-09-01' group by 1, 2",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.orders"]},
            "config": {"materialized": "table"},
        },
        "model.jaffle_shop_metrics.kpi_orders_summary": {
            "unique_id": "model.jaffle_shop_metrics.kpi_orders_summary",
            "resource_type": "model",
            "name": "kpi_orders_summary",
            "original_file_path": "models/marts/kpi_orders_summary.sql",
            "raw_code": "select sum(order_amount) from orders where order_date >= date '2026-08-01' and order_date < date '2026-09-01'",
            # Exact MetricGuard V1 supported shape on orders table
            "compiled_code": "select sum(order_amount) from orders where order_date >= date '2026-08-01' and order_date < date '2026-09-01'",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.orders"]},
            "config": {"materialized": "table"},
        },
        "model.jaffle_shop_metrics.average_order_amount": {
            "unique_id": "model.jaffle_shop_metrics.average_order_amount",
            "resource_type": "model",
            "name": "average_order_amount",
            "original_file_path": "models/marts/average_order_amount.sql",
            "raw_code": "select * from {{ metrics.calculate(metric('average_order_amount'), grain='week') }}",
            "compiled_code": "select order_date, avg(amount) as average_order_amount from orders group by 1",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.orders"]},
            "config": {"materialized": "table"},
        },
        "model.jaffle_shop_metrics.profit": {
            "unique_id": "model.jaffle_shop_metrics.profit",
            "resource_type": "model",
            "name": "profit",
            "original_file_path": "models/marts/profit.sql",
            "raw_code": "select * from {{ metrics.calculate(metric('profit'), grain='month') }}",
            "compiled_code": "select order_date, sum(amount) - sum(amount / 4) as profit from orders where status = 'completed' group by 1",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.orders"]},
            "config": {"materialized": "table"},
        },
    }

    metrics = {
        "metric.jaffle_shop_metrics.revenue": {
            "unique_id": "metric.jaffle_shop_metrics.revenue",
            "resource_type": "metric",
            "name": "revenue",
            "label": "Revenue",
            "calculation_method": "sum",
            "expression": "amount",
            "timestamp": "order_date",
            "time_grains": ["day", "week", "month", "year"],
            "dimensions": ["customer_status", "had_credit_card_payment"],
            "model": "ref('orders')",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.orders"]},
        },
        "metric.jaffle_shop_metrics.average_order_amount": {
            "unique_id": "metric.jaffle_shop_metrics.average_order_amount",
            "resource_type": "metric",
            "name": "average_order_amount",
            "label": "Average Order Amount",
            "calculation_method": "average",
            "expression": "amount",
            "timestamp": "order_date",
            "time_grains": ["day", "week", "month"],
            "model": "ref('orders')",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.orders"]},
        },
        "metric.jaffle_shop_metrics.expenses": {
            "unique_id": "metric.jaffle_shop_metrics.expenses",
            "resource_type": "metric",
            "name": "expenses",
            "label": "Expenses",
            "calculation_method": "sum",
            "expression": "amount / 4",
            "timestamp": "order_date",
            "model": "ref('orders')",
            "depends_on": {"nodes": ["model.jaffle_shop_metrics.orders"]},
        },
        "metric.jaffle_shop_metrics.profit": {
            "unique_id": "metric.jaffle_shop_metrics.profit",
            "resource_type": "metric",
            "name": "profit",
            "label": "Profit",
            "calculation_method": "derived",
            "expression": "revenue - expenses",
            "timestamp": "order_date",
            "depends_on": {"nodes": ["metric.jaffle_shop_metrics.revenue", "metric.jaffle_shop_metrics.expenses"]},
        },
    }

    exposures = {
        "exposure.jaffle_shop_metrics.executive_kpi_dashboard": {
            "unique_id": "exposure.jaffle_shop_metrics.executive_kpi_dashboard",
            "name": "executive_kpi_dashboard",
            "type": "dashboard",
            "depends_on": {
                "nodes": [
                    "model.jaffle_shop_metrics.revenue",
                    "model.jaffle_shop_metrics.kpi_orders_summary",
                    "model.jaffle_shop_metrics.profit",
                ]
            },
        }
    }

    child_map = {
        "model.jaffle_shop_metrics.stg_payments": ["model.jaffle_shop_metrics.int_order_payments_pivoted"],
        "model.jaffle_shop_metrics.stg_orders": ["model.jaffle_shop_metrics.int_customer_order_history_joined", "model.jaffle_shop_metrics.orders"],
        "model.jaffle_shop_metrics.stg_customers": ["model.jaffle_shop_metrics.int_customer_order_history_joined"],
        "model.jaffle_shop_metrics.int_order_payments_pivoted": ["model.jaffle_shop_metrics.int_customer_order_history_joined", "model.jaffle_shop_metrics.orders"],
        "model.jaffle_shop_metrics.orders": [
            "model.jaffle_shop_metrics.revenue",
            "model.jaffle_shop_metrics.kpi_orders_summary",
            "model.jaffle_shop_metrics.average_order_amount",
            "model.jaffle_shop_metrics.profit",
        ],
        "model.jaffle_shop_metrics.revenue": [],
        "model.jaffle_shop_metrics.kpi_orders_summary": [],
        "model.jaffle_shop_metrics.average_order_amount": [],
        "model.jaffle_shop_metrics.profit": [],
    }

    return _build_manifest("jaffle_shop_metrics", nodes, metrics=metrics, exposures=exposures, child_map=child_map)


def generate_jaffle_shop_classic() -> dict:
    """Project 2: dbt-labs/jaffle-shop-classic (canonical dbt tutorial/marts)."""
    nodes = {
        "model.jaffle_shop.stg_customers": {
            "unique_id": "model.jaffle_shop.stg_customers",
            "resource_type": "model",
            "name": "stg_customers",
            "original_file_path": "models/staging/stg_customers.sql",
            "raw_code": "select id as customer_id, first_name, last_name from {{ source('jaffle_shop', 'customers') }}",
            "compiled_code": "select id as customer_id, first_name, last_name from customers",
            "depends_on": {"nodes": ["source.jaffle_shop.customers"]},
            "config": {"materialized": "view"},
        },
        "model.jaffle_shop.stg_orders": {
            "unique_id": "model.jaffle_shop.stg_orders",
            "resource_type": "model",
            "name": "stg_orders",
            "original_file_path": "models/staging/stg_orders.sql",
            "raw_code": "select id as order_id, user_id as customer_id, order_date, status from {{ source('jaffle_shop', 'orders') }}",
            "compiled_code": "select id as order_id, user_id as customer_id, order_date, status from raw_orders",
            "depends_on": {"nodes": ["source.jaffle_shop.orders"]},
            "config": {"materialized": "view"},
        },
        "model.jaffle_shop.stg_payments": {
            "unique_id": "model.jaffle_shop.stg_payments",
            "resource_type": "model",
            "name": "stg_payments",
            "original_file_path": "models/staging/stg_payments.sql",
            "raw_code": "select id as payment_id, orderid as order_id, paymentmethod as payment_method, status, amount / 100 as amount from {{ source('stripe', 'payment') }}",
            "compiled_code": "select id as payment_id, orderid as order_id, paymentmethod as payment_method, status, amount / 100 as amount from payments",
            "depends_on": {"nodes": ["source.stripe.payment"]},
            "config": {"materialized": "view"},
        },
        "model.jaffle_shop.customers": {
            "unique_id": "model.jaffle_shop.customers",
            "resource_type": "model",
            "name": "customers",
            "original_file_path": "models/customers.sql",
            "raw_code": "with customers as (select * from {{ ref('stg_customers') }}), orders as (select * from {{ ref('stg_orders') }}), payments as (select * from {{ ref('stg_payments') }}), customer_orders as (select customer_id, min(order_date) as first_order, max(order_date) as most_recent_order, count(order_id) as number_of_orders from orders group by customer_id), customer_payments as (select orders.customer_id, sum(amount) as total_amount from payments left join orders on payments.order_id = orders.order_id group by orders.customer_id), final as (select customers.customer_id, customers.first_name, customers.last_name, customer_orders.first_order, customer_orders.most_recent_order, customer_orders.number_of_orders, customer_payments.total_amount as customer_lifetime_value from customers left join customer_orders on customers.customer_id = customer_orders.customer_id left join customer_payments on customers.customer_id = customer_payments.customer_id) select * from final",
            "compiled_code": "with customers as (select * from stg_customers), orders as (select * from stg_orders), payments as (select * from stg_payments), customer_orders as (select customer_id, min(order_date) as first_order, max(order_date) as most_recent_order, count(order_id) as number_of_orders from orders group by customer_id), customer_payments as (select orders.customer_id, sum(amount) as total_amount from payments left join orders on payments.order_id = orders.order_id group by orders.customer_id), final as (select customers.customer_id, customers.first_name, customers.last_name, customer_orders.first_order, customer_orders.most_recent_order, customer_orders.number_of_orders, customer_payments.total_amount as customer_lifetime_value from customers left join customer_orders on customers.customer_id = customer_orders.customer_id left join customer_payments on customers.customer_id = customer_payments.customer_id) select * from final",
            "depends_on": {"nodes": ["model.jaffle_shop.stg_customers", "model.jaffle_shop.stg_orders", "model.jaffle_shop.stg_payments"]},
            "config": {"materialized": "table"},
        },
        "model.jaffle_shop.orders": {
            "unique_id": "model.jaffle_shop.orders",
            "resource_type": "model",
            "name": "orders",
            "original_file_path": "models/orders.sql",
            "raw_code": "with orders as (select * from {{ ref('stg_orders') }}), payments as (select * from {{ ref('stg_payments') }}), order_payments as (select order_id, sum(case when payment_method = 'credit_card' then amount else 0 end) as credit_card_amount, sum(amount) as total_amount from payments group by order_id), final as (select orders.order_id, orders.customer_id, orders.order_date, orders.status, order_payments.credit_card_amount, order_payments.total_amount as amount from orders left join order_payments on orders.order_id = order_payments.order_id) select * from final",
            "compiled_code": "with orders as (select * from stg_orders), payments as (select * from stg_payments), order_payments as (select order_id, sum(case when payment_method = 'credit_card' then amount else 0 end) as credit_card_amount, sum(amount) as total_amount from payments group by order_id), final as (select orders.order_id, orders.customer_id, orders.order_date, orders.status, order_payments.credit_card_amount, order_payments.total_amount as amount from orders left join order_payments on orders.order_id = order_payments.order_id) select * from final",
            "depends_on": {"nodes": ["model.jaffle_shop.stg_orders", "model.jaffle_shop.stg_payments"]},
            "config": {"materialized": "table"},
        },
    }

    exposures = {
        "exposure.jaffle_shop.customer_orders_dashboard": {
            "unique_id": "exposure.jaffle_shop.customer_orders_dashboard",
            "name": "customer_orders_dashboard",
            "type": "dashboard",
            "depends_on": {"nodes": ["model.jaffle_shop.customers", "model.jaffle_shop.orders"]},
        }
    }

    child_map = {
        "model.jaffle_shop.stg_customers": ["model.jaffle_shop.customers"],
        "model.jaffle_shop.stg_orders": ["model.jaffle_shop.customers", "model.jaffle_shop.orders"],
        "model.jaffle_shop.stg_payments": ["model.jaffle_shop.customers", "model.jaffle_shop.orders"],
        "model.jaffle_shop.customers": [],
        "model.jaffle_shop.orders": [],
    }

    return _build_manifest("jaffle_shop_classic", nodes, exposures=exposures, child_map=child_map)


def generate_mrr_playbook() -> dict:
    """Project 3: dbt-labs/mrr-playbook (Subscription SaaS metrics)."""
    nodes = {
        "model.mrr_playbook.util_months": {
            "unique_id": "model.mrr_playbook.util_months",
            "resource_type": "model",
            "name": "util_months",
            "original_file_path": "models/utils/util_months.sql",
            "raw_code": "select date_day as date_month from {{ ref('date_spine') }}",
            "compiled_code": "select date_day as date_month from date_spine",
            "depends_on": {"nodes": []},
            "config": {"materialized": "table"},
        },
        "model.mrr_playbook.subscription_periods": {
            "unique_id": "model.mrr_playbook.subscription_periods",
            "resource_type": "model",
            "name": "subscription_periods",
            "original_file_path": "models/subscription_periods.sql",
            "raw_code": "select subscription_id, customer_id, start_date, end_date, monthly_amount from {{ source('billing', 'subscriptions') }}",
            "compiled_code": "select subscription_id, customer_id, start_date, end_date, monthly_amount from raw_subscriptions",
            "depends_on": {"nodes": ["source.mrr_playbook.subscriptions"]},
            "config": {"materialized": "table"},
        },
        "model.mrr_playbook.customer_revenue_by_month": {
            "unique_id": "model.mrr_playbook.customer_revenue_by_month",
            "resource_type": "model",
            "name": "customer_revenue_by_month",
            "original_file_path": "models/customer_revenue_by_month.sql",
            "raw_code": "with subscription_periods as (select * from {{ ref('subscription_periods') }}), months as (select * from {{ ref('util_months') }}), customers as (select customer_id, min(start_date) as date_month_start, max(end_date) as date_month_end from subscription_periods group by 1) select m.date_month, sp.customer_id, coalesce(sum(sp.monthly_amount), 0) as mrr from months m join subscription_periods sp on m.date_month >= sp.start_date and m.date_month < sp.end_date group by 1, 2",
            "compiled_code": "with subscription_periods as (select * from subscription_periods), months as (select * from util_months), customers as (select customer_id, min(start_date) as date_month_start, max(end_date) as date_month_end from subscription_periods group by 1) select m.date_month, sp.customer_id, coalesce(sum(sp.monthly_amount), 0) as mrr from months m join subscription_periods sp on m.date_month >= sp.start_date and m.date_month < sp.end_date group by 1, 2",
            "depends_on": {"nodes": ["model.mrr_playbook.subscription_periods", "model.mrr_playbook.util_months"]},
            "config": {"materialized": "table"},
        },
        "model.mrr_playbook.customer_churn_month": {
            "unique_id": "model.mrr_playbook.customer_churn_month",
            "resource_type": "model",
            "name": "customer_churn_month",
            "original_file_path": "models/customer_churn_month.sql",
            "raw_code": "with mrr as (select * from {{ ref('customer_revenue_by_month') }}) select dateadd('month', 1, date_month) as date_month, customer_id, 0 as mrr, false as is_active from mrr",
            "compiled_code": "with mrr as (select * from customer_revenue_by_month) select dateadd('month', 1, date_month) as date_month, customer_id, 0 as mrr, false as is_active from mrr",
            "depends_on": {"nodes": ["model.mrr_playbook.customer_revenue_by_month"]},
            "config": {"materialized": "table"},
        },
        "model.mrr_playbook.mrr": {
            "unique_id": "model.mrr_playbook.mrr",
            "resource_type": "model",
            "name": "mrr",
            "original_file_path": "models/mrr.sql",
            "raw_code": "with unioned as (select * from {{ ref('customer_revenue_by_month') }} union all select * from {{ ref('customer_churn_month') }}), mrr_with_changes as (select *, lag(mrr) over (partition by customer_id order by date_month) as previous_month_mrr from unioned) select date_month, customer_id, mrr, previous_month_mrr, mrr - coalesce(previous_month_mrr, 0) as mrr_change from mrr_with_changes",
            "compiled_code": "with unioned as (select * from customer_revenue_by_month union all select * from customer_churn_month), mrr_with_changes as (select *, lag(mrr) over (partition by customer_id order by date_month) as previous_month_mrr from unioned) select date_month, customer_id, mrr, previous_month_mrr, mrr - coalesce(previous_month_mrr, 0) as mrr_change from mrr_with_changes",
            "depends_on": {"nodes": ["model.mrr_playbook.customer_revenue_by_month", "model.mrr_playbook.customer_churn_month"]},
            "config": {"materialized": "table"},
        },
        "model.mrr_playbook.kpi_monthly_mrr_summary": {
            "unique_id": "model.mrr_playbook.kpi_monthly_mrr_summary",
            "resource_type": "model",
            "name": "kpi_monthly_mrr_summary",
            "original_file_path": "models/kpi_monthly_mrr_summary.sql",
            # Potential Level-1 Normalization Candidate: Single CTE filter wrapper around mrr table
            "raw_code": "with active_subscriptions as (select * from {{ ref('subscription_periods') }} where monthly_amount > 0) select sum(monthly_amount) as total_mrr from active_subscriptions",
            "compiled_code": "with active_subscriptions as (select * from subscription_periods where monthly_amount > 0) select sum(monthly_amount) as total_mrr from active_subscriptions",
            "depends_on": {"nodes": ["model.mrr_playbook.subscription_periods"]},
            "config": {"materialized": "table"},
        },
    }

    exposures = {
        "exposure.mrr_playbook.saas_board_deck": {
            "unique_id": "exposure.mrr_playbook.saas_board_deck",
            "name": "saas_board_deck",
            "type": "dashboard",
            "depends_on": {"nodes": ["model.mrr_playbook.mrr", "model.mrr_playbook.kpi_monthly_mrr_summary"]},
        }
    }

    child_map = {
        "model.mrr_playbook.util_months": ["model.mrr_playbook.customer_revenue_by_month"],
        "model.mrr_playbook.subscription_periods": ["model.mrr_playbook.customer_revenue_by_month", "model.mrr_playbook.kpi_monthly_mrr_summary"],
        "model.mrr_playbook.customer_revenue_by_month": ["model.mrr_playbook.customer_churn_month", "model.mrr_playbook.mrr"],
        "model.mrr_playbook.customer_churn_month": ["model.mrr_playbook.mrr"],
        "model.mrr_playbook.mrr": [],
        "model.mrr_playbook.kpi_monthly_mrr_summary": [],
    }

    return _build_manifest("mrr_playbook", nodes, exposures=exposures, child_map=child_map)


def generate_attribution_playbook() -> dict:
    """Project 4: dbt-labs/attribution-playbook (Marketing multi-touch attribution)."""
    nodes = {
        "model.attribution_playbook.stg_sessions": {
            "unique_id": "model.attribution_playbook.stg_sessions",
            "resource_type": "model",
            "name": "stg_sessions",
            "original_file_path": "models/staging/stg_sessions.sql",
            "raw_code": "select session_id, customer_id, started_at, utm_source, utm_medium from {{ source('web', 'sessions') }}",
            "compiled_code": "select session_id, customer_id, started_at, utm_source, utm_medium from raw_sessions",
            "depends_on": {"nodes": ["source.attribution_playbook.sessions"]},
            "config": {"materialized": "view"},
        },
        "model.attribution_playbook.stg_conversions": {
            "unique_id": "model.attribution_playbook.stg_conversions",
            "resource_type": "model",
            "name": "stg_conversions",
            "original_file_path": "models/staging/stg_conversions.sql",
            "raw_code": "select conversion_id, customer_id, converted_at, revenue from {{ source('web', 'conversions') }}",
            "compiled_code": "select conversion_id, customer_id, converted_at, revenue from raw_conversions",
            "depends_on": {"nodes": ["source.attribution_playbook.conversions"]},
            "config": {"materialized": "view"},
        },
        "model.attribution_playbook.attribution_touches": {
            "unique_id": "model.attribution_playbook.attribution_touches",
            "resource_type": "model",
            "name": "attribution_touches",
            "original_file_path": "models/attribution_touches.sql",
            "raw_code": "with sessions as (select * from {{ ref('stg_sessions') }}), conversions as (select * from {{ ref('stg_conversions') }}), joined as (select s.session_id, s.customer_id, s.started_at, s.utm_source, c.revenue, row_number() over (partition by s.customer_id order by s.started_at) as touch_number, count(*) over (partition by s.customer_id) as total_touches from sessions s join conversions c on s.customer_id = c.customer_id) select * from joined",
            "compiled_code": "with sessions as (select * from stg_sessions), conversions as (select * from stg_conversions), joined as (select s.session_id, s.customer_id, s.started_at, s.utm_source, c.revenue, row_number() over (partition by s.customer_id order by s.started_at) as touch_number, count(*) over (partition by s.customer_id) as total_touches from sessions s join conversions c on s.customer_id = c.customer_id) select * from joined",
            "depends_on": {"nodes": ["model.attribution_playbook.stg_sessions", "model.attribution_playbook.stg_conversions"]},
            "config": {"materialized": "table"},
        },
        "model.attribution_playbook.cpa_and_roas": {
            "unique_id": "model.attribution_playbook.cpa_and_roas",
            "resource_type": "model",
            "name": "cpa_and_roas",
            "original_file_path": "analysis/cpa_and_roas.sql",
            "raw_code": "with touches as (select * from {{ ref('attribution_touches') }}), ad_spend as (select * from {{ ref('stg_ad_spend') }}) select t.utm_source, sum(t.revenue) as attributed_revenue, sum(s.spend) as ad_spend from touches t join ad_spend s on t.utm_source = s.utm_source group by 1",
            "compiled_code": "with touches as (select * from attribution_touches), ad_spend as (select * from stg_ad_spend) select t.utm_source, sum(t.revenue) as attributed_revenue, sum(s.spend) as ad_spend from touches t join ad_spend s on t.utm_source = s.utm_source group by 1",
            "depends_on": {"nodes": ["model.attribution_playbook.attribution_touches", "model.attribution_playbook.stg_ad_spend"]},
            "config": {"materialized": "table"},
        },
        "model.attribution_playbook.kpi_total_attributed_revenue": {
            "unique_id": "model.attribution_playbook.kpi_total_attributed_revenue",
            "resource_type": "model",
            "name": "kpi_total_attributed_revenue",
            "original_file_path": "models/marts/kpi_total_attributed_revenue.sql",
            # Potential Level-1 Normalization Candidate: Single CTE wrapper around orders table
            "raw_code": "with qualified_orders as (select * from orders where status = 'delivered') select sum(order_amount) from qualified_orders where order_date >= date '2026-08-01' and order_date < date '2026-09-01'",
            "compiled_code": "with qualified_orders as (select * from orders where status = 'delivered') select sum(order_amount) from qualified_orders where order_date >= date '2026-08-01' and order_date < date '2026-09-01'",
            "depends_on": {"nodes": ["model.attribution_playbook.stg_conversions"]},
            "config": {"materialized": "table"},
        },
    }

    exposures = {
        "exposure.attribution_playbook.marketing_performance": {
            "unique_id": "exposure.attribution_playbook.marketing_performance",
            "name": "marketing_performance",
            "type": "dashboard",
            "depends_on": {
                "nodes": [
                    "model.attribution_playbook.cpa_and_roas",
                    "model.attribution_playbook.kpi_total_attributed_revenue",
                ]
            },
        }
    }

    child_map = {
        "model.attribution_playbook.stg_sessions": ["model.attribution_playbook.attribution_touches"],
        "model.attribution_playbook.stg_conversions": ["model.attribution_playbook.attribution_touches", "model.attribution_playbook.kpi_total_attributed_revenue"],
        "model.attribution_playbook.attribution_touches": ["model.attribution_playbook.cpa_and_roas"],
        "model.attribution_playbook.cpa_and_roas": [],
        "model.attribution_playbook.kpi_total_attributed_revenue": [],
    }

    return _build_manifest("attribution_playbook", nodes, exposures=exposures, child_map=child_map)


def generate_dbt_stripe() -> dict:
    """Project 5: fivetran/dbt_stripe (Financial & billing reporting)."""
    nodes = {
        # Staging
        "model.dbt_stripe.stg_stripe__balance_transaction": {
            "unique_id": "model.dbt_stripe.stg_stripe__balance_transaction",
            "resource_type": "model",
            "name": "stg_stripe__balance_transaction",
            "original_file_path": "models/staging/stg_stripe__balance_transaction.sql",
            "raw_code": "select id as balance_transaction_id, amount, currency, status, type, created as created_at from {{ source('stripe', 'balance_transaction') }}",
            "compiled_code": "select id as balance_transaction_id, amount, currency, status, type, created as created_at from raw_balance_transaction",
            "depends_on": {"nodes": ["source.dbt_stripe.balance_transaction"]},
            "config": {"materialized": "view"},
        },
        "model.dbt_stripe.stg_stripe__customer": {
            "unique_id": "model.dbt_stripe.stg_stripe__customer",
            "resource_type": "model",
            "name": "stg_stripe__customer",
            "original_file_path": "models/staging/stg_stripe__customer.sql",
            "raw_code": "select id as customer_id, email, description, created as created_at from {{ source('stripe', 'customer') }}",
            "compiled_code": "select id as customer_id, email, description, created as created_at from raw_customer",
            "depends_on": {"nodes": ["source.dbt_stripe.customer"]},
            "config": {"materialized": "view"},
        },
        "model.dbt_stripe.stg_stripe__invoice": {
            "unique_id": "model.dbt_stripe.stg_stripe__invoice",
            "resource_type": "model",
            "name": "stg_stripe__invoice",
            "original_file_path": "models/staging/stg_stripe__invoice.sql",
            "raw_code": "select id as invoice_id, customer_id, amount_due, amount_paid, status, due_date from {{ source('stripe', 'invoice') }}",
            "compiled_code": "select id as invoice_id, customer_id, amount_due, amount_paid, status, due_date from raw_invoice",
            "depends_on": {"nodes": ["source.dbt_stripe.invoice"]},
            "config": {"materialized": "view"},
        },
        "model.dbt_stripe.stg_stripe__subscription": {
            "unique_id": "model.dbt_stripe.stg_stripe__subscription",
            "resource_type": "model",
            "name": "stg_stripe__subscription",
            "original_file_path": "models/staging/stg_stripe__subscription.sql",
            "raw_code": "select id as subscription_id, customer_id, status, current_period_start, current_period_end from {{ source('stripe', 'subscription') }}",
            "compiled_code": "select id as subscription_id, customer_id, status, current_period_start, current_period_end from raw_subscription",
            "depends_on": {"nodes": ["source.dbt_stripe.subscription"]},
            "config": {"materialized": "view"},
        },
        # Intermediate / Complex Joins
        "model.dbt_stripe.stripe__balance_transactions": {
            "unique_id": "model.dbt_stripe.stripe__balance_transactions",
            "resource_type": "model",
            "name": "stripe__balance_transactions",
            "original_file_path": "models/stripe__balance_transactions.sql",
            "raw_code": "with bt as (select * from {{ ref('stg_stripe__balance_transaction') }}), c as (select * from {{ ref('stg_stripe__customer') }}), inv as (select * from {{ ref('stg_stripe__invoice') }}) select bt.balance_transaction_id, bt.amount, bt.currency, c.email, inv.status from bt left join c on bt.customer_id = c.customer_id left join inv on bt.invoice_id = inv.invoice_id",
            "compiled_code": "with bt as (select * from stg_stripe__balance_transaction), c as (select * from stg_stripe__customer), inv as (select * from stg_stripe__invoice) select bt.balance_transaction_id, bt.amount, bt.currency, c.email, inv.status from bt left join c on bt.customer_id = c.customer_id left join inv on bt.invoice_id = inv.invoice_id",
            "depends_on": {"nodes": ["model.dbt_stripe.stg_stripe__balance_transaction", "model.dbt_stripe.stg_stripe__customer", "model.dbt_stripe.stg_stripe__invoice"]},
            "config": {"materialized": "table"},
        },
        "model.dbt_stripe.stripe__daily_overview": {
            "unique_id": "model.dbt_stripe.stripe__daily_overview",
            "resource_type": "model",
            "name": "stripe__daily_overview",
            "original_file_path": "models/stripe__daily_overview.sql",
            "raw_code": "with daily as (select date_trunc('day', created_at) as date_day, sum(amount) as daily_amount from {{ ref('stg_stripe__balance_transaction') }} group by 1) select date_day, daily_amount, sum(daily_amount) over (order by date_day rows between 29 preceding and current row) as rolling_30d_amount from daily",
            "compiled_code": "with daily as (select date_trunc('day', created_at) as date_day, sum(amount) as daily_amount from stg_stripe__balance_transaction group by 1) select date_day, daily_amount, sum(daily_amount) over (order by date_day rows between 29 preceding and current row) as rolling_30d_amount from daily",
            "depends_on": {"nodes": ["model.dbt_stripe.stg_stripe__balance_transaction"]},
            "config": {"materialized": "table"},
        },
        "model.dbt_stripe.stripe__customer_overview": {
            "unique_id": "model.dbt_stripe.stripe__customer_overview",
            "resource_type": "model",
            "name": "stripe__customer_overview",
            "original_file_path": "models/stripe__customer_overview.sql",
            "raw_code": "with bt as (select * from {{ ref('stg_stripe__balance_transaction') }}), cust as (select * from {{ ref('stg_stripe__customer') }}) select cust.customer_id, count(bt.balance_transaction_id) as total_transactions, sum(bt.amount) as lifetime_spend from cust left join bt on cust.customer_id = bt.customer_id group by cust.customer_id",
            "compiled_code": "with bt as (select * from stg_stripe__balance_transaction), cust as (select * from stg_stripe__customer) select cust.customer_id, count(bt.balance_transaction_id) as total_transactions, sum(bt.amount) as lifetime_spend from cust left join bt on cust.customer_id = bt.customer_id group by cust.customer_id",
            "depends_on": {"nodes": ["model.dbt_stripe.stg_stripe__balance_transaction", "model.dbt_stripe.stg_stripe__customer"]},
            "config": {"materialized": "table"},
        },
        "model.dbt_stripe.stripe__subscription_item_mrr_report": {
            "unique_id": "model.dbt_stripe.stripe__subscription_item_mrr_report",
            "resource_type": "model",
            "name": "stripe__subscription_item_mrr_report",
            "original_file_path": "models/stripe__subscription_item_mrr_report.sql",
            "raw_code": "with subs as (select * from {{ ref('stg_stripe__subscription') }} where status = 'active') select date_trunc('month', current_period_start) as mrr_month, sum(monthly_amount) as total_active_mrr from subs group by 1",
            "compiled_code": "with subs as (select * from stg_stripe__subscription where status = 'active') select date_trunc('month', current_period_start) as mrr_month, sum(monthly_amount) as total_active_mrr from subs group by 1",
            "depends_on": {"nodes": ["model.dbt_stripe.stg_stripe__subscription"]},
            "config": {"materialized": "table"},
        },
        "model.dbt_stripe.stripe__ending_balance_reconciliation": {
            "unique_id": "model.dbt_stripe.stripe__ending_balance_reconciliation",
            "resource_type": "model",
            "name": "stripe__ending_balance_reconciliation",
            "original_file_path": "models/stripe_financial_reports/stripe__ending_balance_reconciliation.sql",
            "raw_code": "with transactions as (select * from {{ ref('stg_stripe__balance_transaction') }}) select date_trunc('month', created_at) as balance_month, sum(amount) as ending_balance from transactions group by 1",
            # Potential Level-1 Normalization Candidate: Single CTE wrapper around table with group by sum
            "compiled_code": "with transactions as (select * from stg_stripe__balance_transaction) select date_trunc('month', created_at) as balance_month, sum(amount) as ending_balance from transactions group by 1",
            "depends_on": {"nodes": ["model.dbt_stripe.stg_stripe__balance_transaction"]},
            "config": {"materialized": "table"},
        },
    }

    exposures = {
        "exposure.dbt_stripe.cfo_financial_report": {
            "unique_id": "exposure.dbt_stripe.cfo_financial_report",
            "name": "cfo_financial_report",
            "type": "dashboard",
            "depends_on": {
                "nodes": [
                    "model.dbt_stripe.stripe__daily_overview",
                    "model.dbt_stripe.stripe__ending_balance_reconciliation",
                    "model.dbt_stripe.stripe__subscription_item_mrr_report",
                ]
            },
        }
    }

    child_map = {
        "model.dbt_stripe.stg_stripe__balance_transaction": [
            "model.dbt_stripe.stripe__balance_transactions",
            "model.dbt_stripe.stripe__daily_overview",
            "model.dbt_stripe.stripe__customer_overview",
            "model.dbt_stripe.stripe__ending_balance_reconciliation",
        ],
        "model.dbt_stripe.stg_stripe__customer": [
            "model.dbt_stripe.stripe__balance_transactions",
            "model.dbt_stripe.stripe__customer_overview",
        ],
        "model.dbt_stripe.stg_stripe__invoice": ["model.dbt_stripe.stripe__balance_transactions"],
        "model.dbt_stripe.stg_stripe__subscription": ["model.dbt_stripe.stripe__subscription_item_mrr_report"],
        "model.dbt_stripe.stripe__balance_transactions": [],
        "model.dbt_stripe.stripe__daily_overview": [],
        "model.dbt_stripe.stripe__customer_overview": [],
        "model.dbt_stripe.stripe__subscription_item_mrr_report": [],
        "model.dbt_stripe.stripe__ending_balance_reconciliation": [],
    }

    return _build_manifest("dbt_stripe", nodes, exposures=exposures, child_map=child_map)


def write_manifests() -> dict[str, Path]:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    generators = {
        "jaffle_shop_metrics": generate_jaffle_shop_metrics,
        "jaffle_shop_classic": generate_jaffle_shop_classic,
        "mrr_playbook": generate_mrr_playbook,
        "attribution_playbook": generate_attribution_playbook,
        "dbt_stripe": generate_dbt_stripe,
    }
    paths = {}
    for name, gen in generators.items():
        data = gen()
        path = MANIFEST_DIR / f"{name}.manifest.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        paths[name] = path
    return paths


if __name__ == "__main__":
    written = write_manifests()
    for name, path in written.items():
        print(f"Wrote {name} -> {path}")
