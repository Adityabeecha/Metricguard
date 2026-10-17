-- Synthetic demonstration of definition difference without value difference.
-- IN ('Completed') is structurally distinct from Dashboard A's equality test.
SELECT SUM(order_amount) AS revenue
FROM orders
WHERE status IN ('Completed')
  AND order_date >= DATE '2026-08-01'
  AND order_date < DATE '2026-09-01';
