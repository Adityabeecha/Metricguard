# Case intake and support check

Complete one copy **before** running MetricGuard. Leave unknowns explicit. Proposed sequence: **Case 1** previously resolved, known explanation withheld where practical; **Case 2** independently sourced with different assumptions; **Case 3** current discrepancy observed through its normal handoff. These are planned slots, not completed cases.

**Case ID / slot:** ___  **Business question and significance:** ___  **Case owner/contact role:** ___  **Observation date:** ___

| Item | Record |
| --- | --- |
| Original baseline report, value, unit, period, filters, refresh/cutoff, version |  |
| Original target report, value, unit, period, filters, refresh/cutoff, version |  |
| Original reported gap and sign convention |  |
| Known explanation/resolution and who may see it (resolved case only) |  |
| Exact original definitions and source locations/revisions |  |
| Extract source, owner, format, size, row count, and availability date |  |
| Permission to use sanitized data locally, retention/deletion instructions, restrictions |  |
| Independent endpoint totals/oracle, method, calculator, and date calculated |  |
| Expected result **before the run**; known uncertainty |  |
| Essential business conditions: currency, business timezone, grain, scope, status/refund rules, date role, period, cutoff |  |
| Specific benefit being tested: discovery, calculation, explanation, review, or replay |  |
| Recipient/reviewer and evidence they need |  |

## Fit check

- [ ] One flat extract can represent the case without changing its business meaning.
- [ ] Nine distinct source columns can map to `order_id`, `customer_id`, `order_date`, `payment_date`, `status`, `payment_status`, `currency`, `order_amount`, and `refund_amount` with declared types.
- [ ] Unique row key, consistent currency, supported NULL policy, explicit cutoff and date semantics are known.
- [ ] Source is CSV or Parquet, at most 25 MiB and one million rows; permitted to keep locally.
- [ ] Each definition fits the restricted single-aggregate SQL policy, with the same aggregate family and compatible unit.
- [ ] Both original endpoint values have compatible context, or any missing/incompatible context is explicitly marked.

**Support decision:** supported / supported with documented transformation / unsupported / unknown. **Reason and evidence:** ___

**Transformations needed and who approved them:** ___  **Does any change alter the business question?** ___

**If unsupported:** retain the original definition and refusal; record preparation already spent and the proposed manual path. Do not claim a successful reconciliation through a semantically different query. An independent expected result may be unavailable for a current case; mark that limitation before seeing MetricGuard's output.
