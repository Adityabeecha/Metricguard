-- Independent DuckDB cross-check. Paths are supplied by the Python evaluator.
CREATE TEMP VIEW source_orders AS
SELECT order_id, order_status FROM read_csv('{orders_path}', header=true, all_varchar=true);
CREATE TEMP VIEW source_payments AS
SELECT order_id, CAST(payment_value AS DECIMAL(18,2)) AS payment_value
FROM read_csv('{payments_path}', header=true, all_varchar=true);

-- Payment rows remain separate from payment_installments.
SELECT COALESCE(o.order_status, '<unmatched order>') AS status,
       COUNT(*) AS payment_rows,
       COUNT(DISTINCT p.order_id) AS orders_with_payments,
       SUM(p.payment_value) AS payment_value
FROM source_payments p LEFT JOIN source_orders o USING(order_id)
GROUP BY 1 ORDER BY 1;
