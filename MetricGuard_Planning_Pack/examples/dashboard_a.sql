-- Specification fixture only. DuckDB dialect. Synthetic data.
-- August 2026 business dates in Asia/Kolkata.
SELECT SUM(order_amount) AS revenue
FROM orders
WHERE status = 'Completed'
  AND order_date >= DATE '2026-08-01'
  AND order_date < DATE '2026-09-01';
