# 8 Demo data and evaluation plan

## 8.1 Dataset design

Use an invented online retailer and a documented order-level dataset. The eight-row golden fixture below establishes the worked example by hand. A separate seeded generator can later create 100000 orders over twelve months for performance and history demonstrations. Synthetic scale data must not replace edge cases or independent expected results.

The golden fixture is stored as `examples/orders_golden.csv`. All amounts are INR. order_amount excludes tax and shipping. refund_amount is the total refund attached to that order as of the declared extraction cutoff, rather than a refund-event ledger. Dates are business dates in Asia/Kolkata. The August window is 1 August inclusive through 1 September exclusive. The extraction cutoff is 2 September 2026.

| ID | Order date | Payment date | Status | Payment | Amount INR | Refund INR |
| --- | --- | --- | --- | --- | --- | --- |
| O01 | 2026-08-05 | 2026-08-06 | Completed | Paid | 60000000 | 2400000 |
| O02 | 2026-08-10 | 2026-08-11 | Completed | Paid | 40000000 | 1920000 |
| O03 | 2026-08-15 | 2026-08-16 | Completed | Paid | 2320000 | 0 |
| O04 | 2026-08-20 | NULL | Completed | Unpaid | 420000 | 0 |
| O05 | 2026-08-31 | 2026-09-01 | Completed | Paid | 1260000 | 0 |
| O06 | 2026-08-17 | NULL | Pending | Unpaid | 700000 | 0 |
| O07 | 2026-08-14 | NULL | Cancelled | Unpaid | 500000 | 0 |
| O08 | 2026-08-14 | NULL | NULL | Unpaid | 200000 | 0 |

The unusually large individual orders keep the dataset small while matching the original crore figures. They are not intended as a realistic retail transaction distribution. The larger synthetic dataset should use a plausible documented distribution and report its own independently calculated totals.

## 8.2 Three explicit definitions

Dashboard A uses `SUM(order_amount)`, `status = 'Completed'`, and the August period on order_date. Dashboard B uses `SUM(order_amount)`, `status <> 'Cancelled'`, and that same order-date period. Finance uses `SUM(order_amount - COALESCE(refund_amount, 0))`, `payment_status = 'Paid'`, and the August period on payment_date. These queries are included in the examples folder with explicit date literals.

Finance's COALESCE is an explicit policy choice that treats a NULL refund as zero. The golden rows do not contain a NULL refund, so a separate test is needed to expose the effect. The NULL status in O08 passes neither equality nor inequality predicates because SQL uses unknown rather than true for those comparisons.

## 8.3 Expected results

| Assertion | Expected result |
| --- | --- |
| Dashboard A total | INR 104000000.00 |
| Dashboard B total | INR 104700000.00 |
| Finance total | INR 98000000.00 |
| Finance minus A | INR -6000000.00 |
| Finance versus A relative change | Approximately -5.769230769 percent |
| B minus A | INR 700000.00 |
| Refund bridge contribution | INR -4320000.00 |
| Eligibility bridge contribution | INR -420000.00 |
| Time bridge contribution | INR -1260000.00 |
| Full bridge residual | INR 0.00 |

The four nonzero order contributions for Finance minus A are O01 at -2400000, O02 at -1920000, O04 at -420000 and O05 at -1260000. O03 contributes zero difference. O06 through O08 are excluded from both A and Finance. Dashboard B's additional amount comes from O06 alone.

The bridge deliberately changes eligibility before time attribution. If time is changed first, the unpaid O04 with a NULL payment date leaves during that step; some of the attribution moves from eligibility to time. Endpoint totals do not change. Include this as a demo of why an explanation method must be named.

## 8.4 Schema dictionary

| Field | Type | Meaning and validation |
| --- | --- | --- |
| order_id | String | Unique non-NULL row key |
| customer_id | String | Synthetic entity ID; distinct-count fixtures may contain repeated customers |
| order_date | Date | Business order date; non-NULL in the golden fixture |
| payment_date | Nullable date | Business payment date; NULL means not recorded |
| status | Nullable string | Completed, Pending, Cancelled or explicitly unknown |
| payment_status | String | Paid or Unpaid in the golden fixture |
| order_amount | Decimal 18 2 | Single-currency amount excluding tax and shipping |
| refund_amount | Nullable decimal 18 2 | Order-level accumulated refund as of cutoff |
| currency | String | INR only for the golden snapshot |

The production-like test set should add duplicates, missing IDs, invalid dates and currency mismatches as separate invalid snapshots. Do not mix those into the validated golden fixture and silently clean them.

## 8.5 Test matrix

| ID | Scenario | Required result |
| --- | --- | --- |
| T01 | Golden A versus Finance | Correct endpoints, four nonzero rows and complete ordered bridge |
| T02 | A versus B with pending order | Predicate difference and INR 700000 extra |
| T03 | Different predicates with no pending rows | Values equal, definition still different |
| T04 | Whitespace and output alias change | Cosmetic version event without meaning alert |
| T05 | NULL status with inequality | Row excluded according to SQL semantics |
| T06 | NULL refund with and without COALESCE | Different term behavior retained; no implicit cleanup |
| T07 | No matching rows | SUM is undefined, COUNT is zero; no forced money gap |
| T08 | Zero baseline and nonzero target | Absolute gap shown; relative percentage undefined |
| T09 | Opposing rule effects | Signed contributions shown without forcing 0 to 100 shares |
| T10 | Refund and date interaction on one row | Bridge changes by order; endpoint and row sums remain stable |
| T11 | Duplicate order_id | Row evidence blocked and invalid snapshot reported |
| T12 | Mixed currencies | Impact blocked until a supported mapping exists |
| T13 | Incompatible period or timezone | Needs context or not comparable |
| T14 | Join, CTE, window or division expression | Unsupported status rather than partial silent parsing |
| T15 | File scan or second SQL statement | Rejected before execution |
| T16 | Oversized file or resource exhaustion | Bounded failure and honest job state |
| T17 | Worker dies mid-run | Lease recovery creates a new attempt without two accepted results |
| T18 | Repeated request with same idempotency key | One run returned; changed payload receives conflict |
| T19 | Same inputs and versions repeated | Numeric results and stable evidence reproduce |
| T20 | Snapshot or engine version changes | New run identity; no stale cache reuse |
| T21 | Changed definition with unchanged data | Fixed-data drift effect correctly isolated |
| T22 | Same definition with changed data | Data change not labeled pure semantic drift |
| T23 | Duplicate customer across orders | COUNT DISTINCT uses entity membership, not sum of order counts |
| T24 | Missing snapshot after deletion | Historical result visible, replay unavailable |
| T25 | Partial attribution or invalid hybrid | Endpoint remains visible; explanation cannot be complete |
| T26 | SQL or description contains HTML or instructions | Escaped text; no model or runner instruction following |

## 8.6 Independent oracles and evaluation

Verify the golden totals using a separate decimal calculation and explicit membership lists, not by calling the same parser and reconciler under test. For generated datasets, independently calculate simple expected aggregates and keep fixed seeds. Use property tests for sign reversal of endpoint deltas, identity comparisons, row partition coverage and bridge telescoping.

Do not require each ordered rule contribution to reverse exactly when the direction changes; interactions and the declared sequence can change attribution. The endpoint gap must reverse. Identity comparison must produce a zero gap for defined endpoints and preserve undefined endpoint semantics for empty SUM cases.

Create a matching corpus with at least 100 labeled pairs covering expected agreement, intentional variants, unrelated names and unknown cases. Split it by business concept or template family so near-duplicate definitions do not leak between tuning and evaluation. Report precision, recall, confusion cases and the abstention rate; a high precision score with almost no suggestions can be unhelpful.

## 8.7 Five minute demonstration

1. Show the three revenue totals and ask which number should be used for which purpose.
2. Open the definition matrix and show status, refund and date differences.
3. Run A against Finance on the frozen August snapshot and show the INR 60 lakh gap.
4. Open the bridge and click through to the four contributing orders.
5. Compare A with B to show that not Cancelled includes a pending order.
6. Show a different-definition but equal-value case to demonstrate that equality is not proof.
7. Import a changed date rule, replay it on the same snapshot, and record a scoped baseline decision.
8. Export evidence with the limitations and reproducibility identifiers intact.

The strongest portfolio proof is this reproducible investigation plus tests and a clear explanation of limits. A screenshot of a dashboard alone does not demonstrate the reconciliation engine.
