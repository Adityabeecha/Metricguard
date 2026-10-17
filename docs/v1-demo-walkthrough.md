# MetricGuard v1 five-minute walkthrough

Run `python -m scripts.seed_demo`, build the web app, and start the local server as shown in the README. The seed command creates four synthetic definitions, including two versions of Dashboard A, and 25 fixed-snapshot comparisons. Use the **Compare** and **History** screens with the August 2026 snapshot. Demo mode uses precomputed evidence; it does not execute a browser-uploaded definition.

| Time | Action | Expected evidence |
| --- | --- | --- |
| 0:00–0:35 | Open Compare. Select Dashboard A v1 as baseline, Finance v1 as target, and run. | INR 104,000,000.00 versus INR 98,000,000.00; target-minus-baseline INR -6,000,000.00. |
| 0:35–1:20 | Open Definition differences. | Refund subtraction, paid eligibility and payment-date attribution are three distinct changes. |
| 1:20–2:05 | Open Value bridge. | Ordered contributions INR -4,320,000.00, -420,000.00 and -1,260,000.00; residual zero. The optional exact Shapley view allocates across all six rule orders and is labeled non-causal. |
| 2:05–2:35 | Open Evidence rows. | Four nonzero rows O01, O02, O04 and O05 reconcile the gap. O03 appears with zero delta. |
| 2:35–3:05 | Select Dashboard B v1 as target and run. | INR +700,000.00 from the pending order included by `status <> 'Cancelled'`. |
| 3:05–3:35 | Select Dashboard A (equal-value variant) v1 as target and run. | Different definition, equal value on this snapshot, zero gap. Equality is not a business-equivalence decision. |
| 3:35–4:20 | Open History, choose Dashboard A, replay v1 against v2 on the same snapshot. | Payment-date rule change alone yields INR -1,680,000.00 with `definition_replay` purpose. |
| 4:20–5:00 | Open Evidence & baselines; inspect the saved run and export JSON or HTML. | The report retains source/IR/snapshot hashes, context, limitations and evidence. A human can record a scoped baseline decision separately. |

The **Metadata adapters** screen demonstrates dbt and TMDL import capability only; it does not run their SQL or DAX. The **Evaluation** screen shows the 100,000-row independent-oracle benchmark and the 132-pair matching corpus. Those results are synthetic. The golden workflows have passed a live Docker runner check; a real analyst pilot remains a separate release gate documented in [v1 release evidence](v1-release-evidence.md).
