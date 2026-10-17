# 4 Reconciliation and semantic drift method

## 4.1 What the engine can establish

The engine can establish structural differences within its supported grammar and observed numeric differences on a specified snapshot. It cannot prove that arbitrary SQL programs are equivalent, infer a company's accounting policy from a name, or establish real-world causation from a data comparison.

Keep three kinds of evidence distinct: definition evidence, execution evidence and review evidence. Definition evidence says which rules differ. Execution evidence says what happens when the rules are run on frozen data. Review evidence records whether a knowledgeable person considers the differences intentional.

## 4.2 Pipeline

1. Preserve the original definition and source metadata.
2. Parse with the declared dialect and bind column names to the known schema.
3. Validate the entire syntax tree against the supported grammar.
4. Lower the definition into the typed metric IR and record unsupported features.
5. Suggest related definitions using metadata and structural features.
6. Ask the analyst to confirm expected agreement or intentional variation.
7. Check that the comparison context and snapshot are suitable.
8. Run the original supported endpoints on that snapshot using the validated execution form.
9. Calculate the structured rule diff and, where supported, the value bridge and row evidence.
10. Persist immutable results, limitations and provenance before rendering an explanation.

The engine must revalidate the IR in the runner. An API validation pass does not justify trusting an arbitrary stored job payload. Results become complete only after endpoint and reconciliation invariants pass.

## 4.3 Candidate matching

Start with a transparent heuristic. Normalize names conservatively, use a small reviewed alias dictionary, and rank pairs using shared source columns, table, aggregate family, date concept, unit and purpose description. Name similarity alone is insufficient. Names containing gross, net, booked or recognized should preserve those qualifiers.

The ranking score is a prioritization heuristic, not a probability. Display reasons such as "same amount column and currency; different status predicate." Persist confirmed positive and negative pair labels so the same suggestion is not repeatedly pushed. Do not merge metric families transitively just because A resembles B and B resembles C.

If embeddings are added later, send only approved metadata, include negative examples, and measure precision and recall against a frozen held-out pair set. The model may suggest a relationship; it cannot approve it, supply execution SQL or invent impact numbers. A metadata-only model outage must not stop a supported comparison.

## 4.4 Structural comparison

Compare aggregate, expression, eligibility predicate, time field, time boundaries, NULL treatment, units and scope. Report a known difference even if its financial effect is zero on the selected data. Whitespace, comments and an output alias should not create a business-meaning alert when their normalization is safely established.

Boolean expressions require SQL's three-valued NULL semantics. Do not simplify `status <> 'Cancelled'` into an assumed list of allowed statuses. Do not sort or rewrite every expression indiscriminately; numeric types, floating-point operations, collation and NULL behavior can affect results.

The output Same supported representation means identical validated IR under the stored normalization policy. Equal on snapshot means two execution results agree in that context. Neither label is a universal mathematical proof for all possible databases.

## 4.5 Endpoint calculation and row contributions

For a supported additive SUM, define each row's contribution as its amount expression if the row passes that definition's predicate and period, and zero if it does not. Preserve NULL-term flags separately and require valid non-NULL aggregate endpoints before computing a bridge.

For each order, the signed contribution is target contribution minus baseline contribution. On a unique order_id grain, the sum of these contributions must equal target total minus baseline total. Display baseline-only, target-only, included-in-both and included-in-neither membership, and the amount difference for rows included in both.

This membership partition is mutually exclusive, which prevents double counting. It is a record-level reconciliation, not a unique assignment to business causes. The same order can be affected by more than one changed rule. A contribution table and a rule bridge therefore answer different questions.

For COUNT star, report membership changes and exact count differences. For COUNT DISTINCT customer_id, compare distinct membership sets and use the distinct entity as the evidence unit. Do not distribute a customer's count contribution over its orders or sum distinct counts across segments. The MVP may show ordered rule effects for count metrics, but full money row-contribution charts apply only to SUM.

## 4.6 Ordered rule bridge for the MVP

Use a fixed documented sequence for the retail case: amount expression, then eligibility predicate, then time attribution. The start uses all baseline rules. Each intermediate definition replaces exactly one supported rule group with its target counterpart; the endpoint must reconstruct the target IR exactly.

Each contribution is the new intermediate total minus the preceding total. The telescoping sum then equals the endpoint difference if every intermediate is valid and evaluated. Store the intermediate definition, value, query hash and rule name. The UI must show the order because interactions can move amounts between steps.

Do not break a logically indivisible rule into arbitrary fragments. If a new amount expression requires a join absent from the old definition, or a hybrid rule creates an invalid scope, stop that bridge. Report the valid endpoints and supported row evidence, with a reason that rule attribution is unavailable. Never describe a partial bridge as complete.

When every supported rule has been applied but the reconstructed target differs, classify this as an internal reconciliation failure. Suppress a complete explanation and retain the endpoint gap as unexplained. Residual equals observed gap minus the sum of reported bridge contributions. A residual is a diagnostic, not a cause named Other.

## 4.7 The corrected revenue example

The synthetic fixture in chapter 8 was constructed to produce the original headline values. It does not represent an actual company. The order_amount field excludes tax and shipping, and refunds are already expressed at order grain as of a fixed cutoff. This simplified refund model is not a financial ledger.

| Bridge step | Total INR | Change INR | Interpretation |
| --- | --- | --- | --- |
| Dashboard A | 104000000.00 | Start | Completed orders by order date |
| Apply refunds | 99680000.00 | -4320000.00 | Use order amount minus refund amount |
| Apply Paid eligibility | 99260000.00 | -420000.00 | Replace Completed with Paid eligibility |
| Apply payment date | 98000000.00 | -1260000.00 | Attribute the period using payment date |
| Finance endpoint | 98000000.00 | -6000000.00 total | Illustrative paid net sales |

In Indian units the totals are 10.4 crore and 9.8 crore; the gap is 60 lakh. For this declared order, the contributions are 43.2 lakh, 4.2 lakh and 12.6 lakh, corresponding to 72 percent, 7 percent and 21 percent of the signed gap. Another valid order can allocate interactions differently. These percentages must be calculated from the actual run, not stored as an explanation template.

Dashboard B using not Cancelled produces 10.47 crore in this fixture because it additionally includes a pending order. The original assumption that A and B both produce 10.4 crore is not generally valid.

Percentages should be secondary to currency amounts. Opposing contributions can produce negative shares or shares above 100 percent. When the endpoint gap is zero, shares of the gap are undefined even if large offsetting contributions exist. Do not force a pie chart in either case.

## 4.8 Optional order-independent allocation

After the ordered bridge is correct, consider exact Shapley allocation for at most five independently switchable rule groups. Evaluate every valid combination of baseline and target rules, at most 32 combinations, and average each rule's marginal effect across all possible orders. Cache each unique combination result.

This allocation is independent of the order chosen for presentation, but depends on the chosen groups and admissible counterfactuals. It is an attribution convention, not causal proof. Require every needed hybrid to be valid; do not omit invalid combinations and still label the result exact Shapley.

If there are more groups, use the ordered method or decline this feature. A later sampling approximation would need a sampling seed, error assessment and a clear approximate label. It is not part of the MVP.

## 4.9 Semantic drift

Create a new definition version whenever source text changes. Compare both raw and normalized fingerprints. A raw-only cosmetic change remains in history without a meaning-change alert. A supported change to the expression, business predicate or time field creates a definition-change event even if the observed amount does not change.

A meaning-change alert depends on the reviewed intent. If the owner intentionally changed the definition and approved its effective date, classify it as an approved change. An unapproved change against an expected baseline is suspected semantic drift. Without enough source context, use Unknown meaning impact.

Record observed_at, optional source_changed_at and optional effective_from separately. "Changed 12 days ago" is permissible only when source history supports that timestamp. Otherwise say "First observed 12 days ago." Compare old and new definitions on the same snapshot to isolate the definition effect.

For a historical replay, freeze a full extraction with an as-of cutoff and run both versions for each chosen month. Label results as restated under that cutoff. They are not the original figures published at the time unless dated historical snapshots are available. Require both definitions to be evaluable for each period; expose gaps rather than joining missing periods into a continuous line.

## 4.10 Separating definition and data changes

When both the definition and data change, use a two-by-two experiment if both data snapshots exist: old definition on old data, new definition on old data, old definition on new data, and new definition on new data. A selected path can separate definition and data contributions, while a Shapley-style average can share the interaction. Label the chosen method.

The MVP fixes the data and changes the definition. It must not call a difference between an old reported number and a newly calculated number a pure semantic drift impact. Changes to late-arriving records, refund cutoffs or mappings are data changes even when the SQL is identical.
