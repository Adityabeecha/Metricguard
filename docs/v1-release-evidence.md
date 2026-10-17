# MetricGuard v1 plan and release evidence

The planning pack defines **one product version, v1**. M0–M5 are six milestones within it; MG01–MG26 are work items, not versions. This file maps the entire pack to code and checks. “Implemented” means the local code path exists and passes its stated tests; it does not imply a production pilot.

| Item | V1 evidence | Current state |
| --- | --- | --- |
| MG01 Golden fixture and data dictionary | `MetricGuard_Planning_Pack/examples`, independent Decimal assertions in `tests/test_core.py` | Implemented |
| MG02 Typed IR and support policy | `packages/metricguard_core/parser.py`, `docs/contracts.md` | Implemented |
| MG03 Parser and schema binding | Negative AST tests in `tests/test_core.py` | Implemented |
| MG04 Frozen snapshot | `snapshot.py`, hashes, key/schema validation and tamper tests | Implemented |
| MG05 Restricted runner | `infra/Dockerfile.runner`, supervisor protocol and isolation tests; `data/evaluation/live_runner_report.json` | Live Docker ingestion/comparison and isolation probe passed |
| MG06 Comparison context | Core and API validation tests | Implemented |
| MG07 Exact endpoints | Golden and 100,000-row independent oracles | Implemented |
| MG08 Additive row evidence | Reconciliation invariant tests and paginated evidence API | Implemented |
| MG09 Ordered bridge | Intermediate values, residual checks, golden truth table | Implemented |
| MG10 Version differences | Immutable IR/text classification and history tests | Implemented |
| MG11 Immutable versions and runs | SQLAlchemy store and historical-result tests | Implemented |
| MG12 Jobs and recovery | Lease, retry, cancellation and idempotency tests | Implemented |
| MG13 Typed API | Pydantic input models, bounded errors and API tests | Implemented |
| MG14 Comparison screen | React comparison, bridge and evidence views | Implemented |
| MG15 Import and inventory | SQL inventory and queued isolated CSV/Parquet import, exact schema and native NULL validation, tests | Live Docker CSV/Parquet imports passed; malformed Parquet rejected |
| MG16 History and replay | Fixed-snapshot replay API/UI and tests | Implemented |
| MG17 Candidate ranking | Deterministic heuristic, manual review, 132-pair split evaluation | Implemented; holdout top-ranked known precision 9/9, three unknown top picks; all-pair precision 0.50 |
| MG18 Scoped baselines | Immutable version and scope records | Implemented as local self-declared decisions |
| MG19 Report dependencies | Manually registered consumers with explicit coverage | Implemented; automatic discovery is not claimed |
| MG20 Evidence exports | JSON/HTML provenance and bounded export tests | Implemented |
| MG21 Regression and end-to-end tests | 102 local tests passed on 2026-09-23; production frontend build and Compose config passed; live Docker golden workflows passed | Local and live-container happy-path gates passed; live cancellation/timeout/recovery not exercised |
| MG22 Demo and docs | Golden demo, equal-value and date-replay cases, five-minute walkthrough, contracts, benchmark and this matrix | Reproducible local demo ready; external pilot and recording not performed |
| MG23 dbt manifest adapter | Version-aware v7–v12 metadata extraction, lineage and compiled SQL presence tests | Implemented as metadata-only; no Jinja or SQL execution |
| MG24 Power BI TMDL adapter | Bounded table/measure/column/relationship extraction tests | Implemented as subset metadata-only; no DAX execution |
| MG25 Bounded Shapley | Six-order exact rational allocation, undefined-hybrid behavior and integration tests | Implemented as optional companion to ordered bridge |
| MG26 Optional embeddings | Frozen holdout report exists; no local model has demonstrated a gain | Conditional feature not enabled |

## Measured M5 evidence

`python -m scripts.verify_live_runner --output data/evaluation/live_runner_report.json` ran the golden CSV and Parquet through the real API → durable queue → supervisor → fixed Docker runner → immutable snapshot → comparison path on Docker Desktop 29.1.3. Both imports produced eight rows with matching source hashes, and both A→Finance comparisons returned -6,000,000.00 with zero residual. A malformed Parquet import failed with `INVALID_PARQUET` and published no snapshot. A separate probe using the same fixed image and launcher restrictions observed UID 10001, a read-only root, no outbound network, no effective capabilities, `NoNewPrivs=1`, 1 GiB memory, one CPU and 64 PIDs. The built image ID was `sha256:953f6239de21f8c1d6b9e9b56fa9d7e5e27d2fe76aeb6680fc27c45d6d05b144`. This does not test live cancellation, timeout, lease recovery or adversarial escape attempts.

`python -m scripts.evaluate_release --output-dir data/evaluation` generated 100,000 deterministic orders over twelve months (seed `20260923`, source SHA-256 `5d5f47944f3253f51419958f8a1acdb932e57d89bfa70b71d7c0ed0f1e843bc9`). A second CSV/Decimal pass independently matched the generator oracle. On this Windows host with Python 3.12.5, DuckDB 1.5.5 and SQLGlot 30.19.0, snapshot ingestion took 1.0672 seconds; first A→Finance comparison 1.5502 seconds; repeat 1.4651 seconds. The repeat’s numeric values, ordered steps, differences and 100,000 evidence rows matched. Both comparison results were under the 25 MiB runner output cap (22,777,277 and 22,782,350 compact JSON bytes). The 30-second **in-process** target was met. The exact machine-local report is `data/evaluation/release_report.json`.

The independent August INR totals were Dashboard A 81,712,636.58, Dashboard B 94,256,960.30 and Finance 72,217,099.74. A→Finance was -9,495,536.84; A→B was +12,544,323.72. Both deltas and zero residuals matched the independent oracle.

`python -m scripts.evaluate_candidates --output data/evaluation/candidate_report.json` evaluated 132 labeled pairs, 66 per concept-disjoint split. Development pair precision/recall was 1.00/1.00; holdout pair precision/recall was 0.50/1.00. The planning pack's **top-ranked held-out** target is a different denominator: precision was 1.00 among nine known-label top picks, above the proposed 0.90 threshold, with three additional top picks labeled unknown and zero abstentions. Holdout had 16 suggestions involving unknown labels and nine recorded unrelated false positives across all suggested pairs. These synthetic labels are not production intent or a real-user precision claim. Because no private, local embedding matcher has shown held-out improvement over the deterministic baseline, MG26 remains off.

## Open v1 release work

- Exercise live cancellation, timeout and worker lease recovery if those operational gates are required before deployment. Their state-machine behavior is covered by local tests, while the live Docker run above covers golden ingestion/comparison and the core isolation limits.
- Run a real analyst pilot with comparable extracts and collect the decisions described in the planning pack. The synthetic corpus cannot establish product fit.
- Record the five-minute golden walkthrough if a shareable demo video is required. The steps are reproducible from the README and seeded app, but no recording is claimed.

The dbt adapter follows the official [manifest schema reference](https://docs.getdbt.com/reference/artifacts/manifest-json), which states compiled SQL is conditional. The TMDL adapter follows the official [TMDL syntax overview](https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview?view=sql-analysis-services-2025). Both are intentionally import-only capabilities; no model-context or native execution claim follows from reading metadata.
