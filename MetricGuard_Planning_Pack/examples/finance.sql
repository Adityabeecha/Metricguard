-- Specification fixture only. This is illustrative paid net sales,
-- not a complete accounting revenue recognition policy.
SELECT SUM(order_amount - COALESCE(refund_amount, 0)) AS revenue
FROM orders
WHERE payment_status = 'Paid'
  AND payment_date >= DATE '2026-08-01'
  AND payment_date < DATE '2026-09-01';
