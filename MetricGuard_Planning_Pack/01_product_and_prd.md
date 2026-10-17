# 1 Product refinement and PRD

## 1.1 Recommended product

Build MetricGuard as a KPI reconciliation workbench with definition history. Its main job is to help an analyst answer four questions: are these numbers intended to mean the same thing, how do their definitions differ, how much does that difference matter on a controlled dataset, and what evidence supports the explanation?

The first deliverable should be a working investigation from imported SQL to a reproducible reconciliation report. This suits a data analyst portfolio because the substance is SQL interpretation, metric design, variance analysis, validation and communicating evidence. A polished interface supports that work; it does not replace it.

**Recommended positioning:** MetricGuard compares existing SQL KPI definitions, quantifies their differences on the same data snapshot, and tracks changes against an approved definition.

**Longer term positioning:** Discover related metrics across analytics tools and help teams reconcile their implementations before or alongside adopting a semantic layer. This is a product hypothesis, not a verified unserved market.

## 1.2 Corrections to the original concept

| Original assumption | Correction | Product consequence |
| --- | --- | --- |
| Similar names imply the same KPI | Net sales, booked sales and recognized revenue may be intentionally different | Suggest related concepts and ask an analyst to confirm the intended relationship |
| Completed equals not Cancelled | Not Cancelled can include Pending and other statuses; SQL NULL also needs explicit treatment | Show the exact predicate and affected statuses |
| Date attribution can be inferred from the example | The original snippets contain no date predicate | Require date field, period and timezone metadata or mark them unknown |
| Different totals imply a definition problem | Different snapshots, permissions, scope or source transformations can also change totals | Check comparability before attributing an impact |
| Matching totals prove matching definitions | Different formulas can agree on a dataset with no refunds or pending orders | Show definition status separately from observed value status |
| An LLM can explain the gap | It can invent causes and percentages | Compute evidence first; use templates for the initial release |
| Contributions are inherently unique | Refunds, filters and date rules can interact | Declare the decomposition method and its order |
| All BI formulas are easy to translate | DAX depends on model context; spreadsheets have their own evaluation behavior | Start with SQL and add explicitly bounded adapters |
| There should be one canonical Revenue | Several definitions can be valid for different business purposes | Approve definitions within a stated scope and allow intentional variants |
| A changed definition tells when meaning changed | Import timestamps only show when MetricGuard observed a change | Separate observed time, source change time and business effective time |

Use single quotes for SQL string literals. Label the finance example as an illustrative paid net sales definition; payment alone does not establish an accounting revenue recognition policy.

## 1.3 Problem and users

The immediate problem is repeated manual investigation when reports with apparently related KPI names disagree. The work includes finding the queries, resolving hidden filters, checking refresh cutoffs and identifying the rows that contribute to the gap. Anomaly detection can signal a change without establishing why it happened.

The primary user is a data or BI analyst investigating a specific discrepancy. A second role, the metric owner, decides what the KPI should mean and whether a difference is intentional. A reviewer consumes the evidence and checks the proposed resolution. In the local portfolio release one person may act in all three roles; the interface must not imply independent approval occurred.

The best initial setting is one online retail business with an order-level extract and several SQL reports. This keeps the business meaning understandable while exposing realistic refunds, order statuses and date attribution issues. Avoid presenting that setting as representative of all industries.

**Job to be done:** When two revenue reports disagree, help me isolate the definition differences on comparable data, show the contributing records, and document the accepted interpretation so that the same dispute is easier to resolve next time.

## 1.4 Existing alternatives and differentiation

| Approach | Established purpose | MetricGuard design implication |
| --- | --- | --- |
| dbt Semantic Layer | Central definition and querying of metrics using MetricFlow [S1] | Complement adoption by investigating existing definitions and exporting an approved specification |
| Soda observability and contracts | Monitor data behavior and verify declared expectations [S2] | Reuse the expectation concept but make the investigation begin with existing logic |
| Datafold data diff and monitors | Compare datasets and monitor changes [S3] | A data diff is adjacent; add metric intent, rule differences and evidence connecting rules to values |
| SQLGlot | Parse, inspect, transform and compare SQL structures [S4] | Use it as a component, not as evidence that all business meanings are understood |
| Manual SQL and spreadsheet reconciliation | Flexible investigation by an analyst | Reduce repeated setup while preserving the analyst's ability to inspect every calculation |

These sources establish overlap. They do not establish that competing products cannot perform a particular investigation. The defensible portfolio contribution is the end-to-end workflow and its evaluated limits, not a claim to have invented metric governance.

## 1.5 Outcomes and success measures

The primary outcome is a completed, reproducible reconciliation case with a reviewed relationship between definitions. A successful investigation can conclude that the metrics are intentionally different or that there is insufficient evidence; it need not conclude that a report is wrong.

| Measure | Proposed release gate | How to measure |
| --- | --- | --- |
| Supported fixture correctness | All declared supported golden cases match expected results | Fixed SQL, data and expected values in version control |
| Unsupported behavior | No unsupported test is marked equivalent or fully explained | Negative and adversarial corpus |
| Additive reconciliation | Endpoint gap equals the sum of row contributions to one paisa | Independent decimal arithmetic oracle |
| Repeatability | Identical versions, data and configuration produce identical numeric evidence | Re-run test with stored hashes |
| Candidate suggestion usefulness | At least 90 percent precision on the top ranked held-out pairs | Report sample size, recall and pair labels; target only |
| Investigation usability | At least four of five pilot users complete the core task without help | Small formative pilot; disclose that it is not a population estimate |
| Analyst effort | Record completion time and wrong conclusions versus a manual baseline | Same task, counterbalanced order where practical; no savings claim before testing |
| Performance | Define an initial 30 second target for the standard 100000-row case | Fixed hardware, warm and cold runs; revise after a measured spike |

Do not combine these into a single opaque trust score. The interface should separately show import coverage, context completeness, definition differences, numeric impact and review status.

## 1.6 Scope by release

| Capability | MVP | Later increment |
| --- | --- | --- |
| SQL definitions | Upload or paste the supported DuckDB SQL subset | Tested PostgreSQL and warehouse dialect adapters |
| Data | One immutable flat snapshot per run, CSV or Parquet input | Controlled warehouse snapshots and cross-source mapping |
| Matching | Deterministic candidate suggestions plus human review | Optional metadata embeddings if evaluation shows value |
| Metric types | SUM, COUNT and single-column COUNT DISTINCT | Ratios, averages, funnels and non-additive metrics |
| Explanation | Row contributions for SUM and ordered rule bridge | Exact Shapley attribution for a bounded set of valid rule switches |
| Drift | Definition versions, explicit comparison and manual replay | Scheduled monitoring and repository integration |
| Governance | Local owner decision and scoped approved baseline | Authenticated roles, review workflow and notifications |
| Power BI | Product roadmap only; screenshots are not executable evidence | TMDL metadata import, then native DAX execution with full context |
| dbt | Compiled SQL may be pasted if it fits the subset | Manifest import with version-aware lineage and SQL handling |
| Excel | CSV export treated as data, without formula evaluation | Bounded formula metadata extraction before any calculation support |
| Deployment | Local application and reproducible synthetic demonstration | Hosted private pilot; public demo can use precomputed results |

**Explicit exclusions for the first release:** arbitrary joins and CTEs, arbitrary DAX or Excel evaluation, automatic production corrections, universal SQL equivalence proofs, financial accounting certification, enterprise multitenancy, complex warehouse connectivity and an LLM-driven SQL execution agent.

## 1.7 Functional requirements

| ID | Requirement | Acceptance condition |
| --- | --- | --- |
| FR01 | Import definitions and record provenance | Every definition retains original text, dialect, source label, version and import time |
| FR02 | Parse and classify support | Every import is executable, metadata only, needs context, or unsupported, with a reason |
| FR03 | Propose related metrics | Suggestions show the name and structural evidence; none is auto-approved |
| FR04 | Confirm comparison context | Currency, grain, snapshot, period and scope checks run before execution |
| FR05 | Compare definitions | Changes to amount expression, predicate and time field remain visible even when totals match |
| FR06 | Execute a comparison | Both definitions run on the same frozen snapshot with a stored context |
| FR07 | Explain supported gaps | Report endpoints, signed contributions, method, samples and any unexplained amount |
| FR08 | Track definition drift | Preserve old and new definitions and compare them on a fixed snapshot |
| FR09 | Record a reviewed baseline | Approval stores scope, rationale, version, effective time and actor |
| FR10 | Show registered report dependencies | Every affected asset has a recorded dependency path and coverage label |
| FR11 | Export the evidence | A readable report and JSON include enough identifiers to reproduce the run |
| FR12 | Handle failure honestly | Parse failures, absent data, timeout and unsupported features cannot become green success states |

## 1.8 Validation before expanding scope

Interview three to five analysts about an actual recent metric disagreement. Ask where the logic lived, which contexts were missing, what evidence resolved the dispute, and whether access to transaction data was possible. Ask for sanitized examples only. Record whether the problem is mostly definition inconsistency, data freshness, accounting policy, or ownership confusion.

Proceed with the SQL workbench if at least two independently described cases fit the supported workflow and users can supply comparable extracts. If all cases require inaccessible DAX models or unrelated warehouses, adjust the adapter strategy before claiming product fit. The portfolio build can still proceed with synthetic evidence while clearly labeling the validation gap.
