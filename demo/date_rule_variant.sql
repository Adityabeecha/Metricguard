-- Synthetic changed version of Dashboard A for fixed-snapshot replay.
SELECT SUM(order_amount) AS revenue
FROM orders
WHERE status = 'Completed'
  AND payment_date >= DATE '2026-08-01'
  AND payment_date < DATE '2026-09-01';
