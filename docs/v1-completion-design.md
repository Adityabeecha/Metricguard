# MetricGuard v1 completion design

The planning pack is one v1 product plan. M0–M5 are delivery milestones, and MG01–MG26 are backlog identifiers; neither is a product-version count. The existing local workbench covers most of MG01–MG22. This completion track keeps a single v1 label and implements the remaining evaluation and bounded later backlog work without claiming unsupported BI execution.

## M5 evaluation and handoff

`scripts/scale_fixture.py` generates a deterministic 100,000-order CSV over September 2025–August 2026 with the fixed orders schema and a direct `Decimal` oracle for the August Dashboard A, Dashboard B and Finance definitions. `scripts/evaluate_release.py` validates the fixture through the core ingestion path, checks exact comparison results, measures generation/ingestion/first/repeat comparisons, records versions/hardware/hashes/output size, and writes bounded JSON and Markdown reports. The proposed 30-second target is displayed alongside the measured value. A failure is recorded as a failure; an in-process result is not described as a live isolated-container result.

A semantic corpus with at least 100 explicitly labeled pairs has disjoint concept families across development and holdout sets. The evaluator reports top-ranked precision, recall, abstention and confusion cases for deterministic candidates. The optional metadata embedding item is conditional on a measured holdout improvement and privacy approval; it cannot be represented as shipped merely because it appears in the backlog. The app's read-only Evaluation page shows reports when present and an explicit not-run state otherwise. The README carries a five-minute synthetic demo, support boundary, reproduction commands and measured limitations.

## Bounded metadata adapters

The dbt adapter accepts version-aware manifest metadata, preserves node keys, lineage and compiled SQL when supplied, and imports a definition only through the existing parser/support checker. It never evaluates Jinja or macros. Nodes without compiled SQL remain metadata-only. The Power BI TMDL adapter extracts conservative model/measure metadata, source text and relationships. DAX is metadata-only: the app does not claim numeric equivalence or execute it in DuckDB. Both adapters have size bounds, immutable import records, failure states and UI coverage labels. No arbitrary file paths or network fetches are accepted from a browser request.

## Exact bounded attribution

For the existing three supported rule groups—amount, eligibility and time—the core can calculate exact Shapley allocations from all eight valid hybrid endpoints. The method is an optional second explanation beside the existing ordered bridge. Every hybrid must pass typed-IR/context checks and have a defined endpoint; otherwise the Shapley explanation is unavailable and the ordered result remains intact. Decimal contributions must sum exactly to target minus baseline; percentages and causal language are avoided.

## Boundaries and release evidence

The fixed DuckDB SQL subset, single flat orders snapshot and local-only security model remain explicit. Planned future capabilities outside MG01–MG26 (arbitrary DAX/Excel execution, hosted multitenancy, warehouse connectivity, automatic source edits) are not silently represented as implemented. The final v1 checklist maps each backlog item to a test, artifact, or a clear environment/conditional limitation. Docker's unavailable daemon on this machine remains a live-container verification gate rather than a passing test.
