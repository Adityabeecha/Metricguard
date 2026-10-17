-- Specification fixture only. DuckDB dialect. Synthetic data.
-- SQL NULL status does not pass the inequality predicate.
SELECT SUM(order_amount) AS revenue
FROM orders
WHERE status <> 'Cancelled'
  AND order_date >= DATE '2026-08-01'
  AND order_date < DATE '2026-09-01';
