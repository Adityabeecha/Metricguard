# MetricGuard: roadmap after v1

Drafted 2026-09-23. The original planning pack remains **one v1**, containing M0–M5 and MG01–MG26. V2–v4 are three later releases proposed here. The v2 local workflow is implemented and verified with synthetic development cases; its two-real-case pilot acceptance gate remains open. V3 and v4 are still proposals, without delivery dates. Track implementation and verification in [v2 release evidence](v2-release-evidence.md).

## Direction

Build toward this outcome: **an analyst can explain a KPI disagreement, a metric owner can review the evidence, and the team can detect when the accepted logic changes again.**

The working assumption is a small analyst pilot, beginning with flat retail extracts and SQL. The existing engine, immutable evidence, replay, and metadata adapters make that a more direct next step than attempting every BI language. If the goal is primarily a portfolio, stop after a reproducible v1 and a convincing real-data v2 case; shared deployment and connectors are optional investments.

| Approach | Benefit | Cost or risk | Decision |
| --- | --- | --- | --- |
| Deepen the investigation workflow | Tests usefulness with the existing reconciliation engine | Requires real cases and careful data onboarding | Recommended |
| Add many tools and languages first | Expands potential import coverage | Each execution language brings new context and correctness requirements | Defer until a pilot identifies one priority |
| Add AI matching and explanations first | May make discovery easier | Synthetic top-ranked known precision meets the proposed target, while all-pair precision remains 0.50 and three top picks are unknown; generated explanations need grounded evaluation | Keep conditional on a measured gain |

## What the current project tells us

The [v1 evidence matrix](v1-release-evidence.md) records 102 passing local tests, a successful frontend build, exact synthetic reconciliation, a 100,000-row in-process benchmark, and golden CSV/Parquet workflows through a live Docker runner. V2 now adds explicit mapped input and bounded evidence artifacts; neither set of synthetic checks establishes analyst adoption.

The main constraints are concrete:

- The v1 parser and ingesters retain the fixed `orders` schema, INR and Asia/Kolkata. V2 maps renamed source columns onto the same nine analytical roles and pins a declared business context; it does not yet support arbitrary source semantics or SQL joins. See `packages/metricguard_core/v2_contract.py` and `v2_binding.py`.
- V1 accepts exact fixed-schema CSV and Parquet layouts. V2 accepts mapped CSV and Parquet after explicit approval, preserving both paths separately.
- Matching has measured holdout pair precision of 0.50 and 9/9 precision among known-label top-ranked picks, with three unknown top picks. Both denominators and the synthetic nature of the corpus matter.
- Owner decisions are self-declared local records. They cannot yet support authenticated team review.
- The v1 100,000-row comparison produces approximately 22.8 MB of compact JSON. V2 separates row evidence into a bounded artifact and pages it to the UI; total artifact size, verification time and retention remain operational measurements.
- dbt and TMDL imports already exist as metadata-only capabilities. Bounded Shapley already exists. Repackaging these as new-version features would misstate the baseline.

## Close v1 before calling it released

These remain v1 work; they are not reasons to rename part of the original plan v2.

1. The real container path now passes golden CSV/Parquet ingestion, comparison, malformed-file rejection and core isolation-limit probes. Before deployment, exercise live cancellation, timeout and worker recovery, and record deployment-specific image and dependency versions.
2. Run the planned formative analyst exercise and clean-machine setup. Record incorrect conclusions as well as completion time. Close the original keyboard/small-screen and operational checklist items with evidence.
3. Publish the reproducible walkthrough and record the synthetic demo if required. Keep externally unverified claims visibly unverified.

## Proposed releases

| Release | User outcome | Main scope | Exit evidence |
| --- | --- | --- | --- |
| **v2 — Real-data investigations** | An analyst can investigate their own supported extract without editing application code | Versioned dataset contracts, explicit column mapping, guided context, bounded evidence artifacts, saved case replay | Local checks and synthetic rehearsal pass; exit still requires two independently sourced sanitized real cases with external oracles and a second analyst repeating a saved case |
| **v3 — Shared review and recurring checks** | A small team can review a baseline and detect subsequent definition changes | Identity and project access, review records, restricted hosted job service, monitoring policies, operational recovery | Access checks, restore drill, scheduler recovery, and a controlled drift exercise pass |
| **v4 — Connected SQL workflow** | The team spends less time manually collecting definitions and snapshots | Deepen dbt import, repository checks, one read-only snapshot connector selected by pilot demand | Source-to-case provenance is complete, unsupported coverage is explicit, and collected snapshots reproduce the investigation |

These are three versions after the original v1. Dates for v3 and v4 should follow pilot evidence and available developer time. Each version has its own acceptance gate; its internal tasks are not additional product versions.

### v2: Real-data investigations

Keep one flat table and the current supported aggregate families initially. Generalize the inputs without simultaneously adding arbitrary joins or a new execution language.

| ID | Work | Acceptance condition |
| --- | --- | --- |
| V2-01 | Introduce a versioned dataset contract: row key, eligible entity key, types, decimal scale, date fields, null policy, unit/currency, business timezone, grain, scope and cutoff | Unsupported contracts fail before execution; v1 snapshots and reports retain their original policy |
| V2-02 | Add preview, explicit column mapping and reusable mapping versions | User approves transformations; source and mapping hashes appear in every run; no guessed currency conversion, timestamp conversion, or grain change |
| V2-03 | Bind the parser and runner to the validated contract | Identifiers remain allowlisted; schema changes invalidate incompatible runs; original SQL is preserved alongside any explicit mapping |
| V2-04 | Guide users through context and support errors; group versions, runs, evidence and decisions into a saved investigation | A second analyst can reopen the exact case, identify missing context and reproduce a supported result |
| V2-05 | Separate summary results from bounded row-evidence artifacts | Supervisor validates artifact paths, schema, size and hashes; UI pages evidence without loading the entire result; limited exports remain explicit |
| V2-06 | Run pilot cases and measure completion time, wrong conclusions, support coverage and candidate usefulness | At least two independently sourced sanitized cases match separately calculated expectations; publish limitations and raw sample counts |

Start v2 locally so data onboarding and usability can be tested without making hosted deployment a prerequisite. Any remote upload deployment must first satisfy the identity, authorization and job-isolation requirements listed in v3. Do not expose the existing local supervisor as a public execution service.

The current local v2 implementation covers V2-01 through V2-05 in code: versioned contracts and mapping hashes, bounded CSV/Parquet preview and import in the fixed runner, source SQL binding, guided context and saved cases, plus supervisor-validated evidence artifacts with paged API reads and bounded exports. [The evidence matrix](v2-release-evidence.md) distinguishes code paths, local verification, and the still-open V2-06 pilot. The supported schema remains the nine roles of the existing flat engine; a contract cannot introduce a tenth executable role.

Migrate policy explicitly. A schema-mapping change creates a new identity; it must not reinterpret stored v1 evidence. Keep old reports readable and keep old replay behavior available through its pinned policy, or mark replay unavailable with the reason.

### v3: Shared review and recurring checks

Build this when pilot users have repeated investigations or need another person to review a decision.

- Add authenticated reader, analyst and owner roles with server-side authorization for every project object, run, export, snapshot and download. Preserve historical local labels as unverified labels.
- Support review requests, rationale, intentional variants, effective time, and superseding decisions tied to immutable versions. Recording a new decision cannot rewrite old evidence.
- Move remote analytical jobs behind a restricted job service; keep credentials and networked collectors outside the analytical runner. Retain the existing restricted IR and immutable snapshot boundaries.
- Add scheduled or event-triggered rechecks, fixed-snapshot definition replay, and explicit monitoring scope. Separate a changed definition from a changed dataset. When both change, label the combined observation and use controlled replays only where valid.
- Add absolute and relative materiality policies, undefined-result handling, deduplication, acknowledgement, and intentional-variant suppression. Start with an in-app inbox; add one notification channel only when requested by the team.
- Define retention, storage quotas, redacted operational logs, backup plus artifact restore, and recovery from expired leases or missed schedules.

Exit gates: cross-project access attempts fail; revoked access applies to downloads; repeated events produce one accepted alert; scheduler restarts do not duplicate accepted results; an induced definition change produces the expected fixed-data impact; metadata and artifacts restore together. Run a small observed team trial before expanding concurrency.

### v4: Connected SQL workflow

The default integration direction is dbt plus one read-only source connector chosen from pilot evidence. This extends the existing SQL and metadata investment. It does not make imported dbt projects executable automatically.

- Import versioned dbt artifacts repeatedly, preserve stable resource IDs and source revision, and distinguish observed lineage from manually registered report dependencies. Display coverage and missing compiled SQL. The [official manifest reference](https://docs.getdbt.com/reference/artifacts/manifest-json) documents artifact versions and conditional compiled SQL availability.
- Add a local CLI or CI entry point that compares supported changed definitions with a reviewed baseline on the same approved snapshot. Produce a machine-readable check and a human-readable report. Start as advisory; blocking policy requires an explicit team decision and does not turn unsupported analysis into a pass.
- Implement one collector that reads a declared source selection into a frozen snapshot with an extraction cutoff, row-count checks, hashes and a recorded consistency policy. Connection credentials stay in the collector; the runner receives only validated artifacts. A live source response is not automatically a reproducible snapshot.
- Treat SQL dialect and grammar expansion as separate support-policy work. Accept only constructs with independent fixtures, failure cases, NULL/decimal checks and valid explanation methods. Compiled SQL containing unsupported joins or CTEs stays metadata-only until that support is implemented and tested.

Exit gates: a source revision links to the imported definition, dataset contract, snapshot, run and review; partial imports and extraction failures remain visible; a connector retry does not silently mix cutoffs; the stored snapshot reproduces expected results. Benchmark collection, queueing, execution, storage and export separately.

If the pilot is predominantly Power BI, replace the v4 integration focus with a bounded native-model feasibility track. First prove that the same measure and filters, identity/RLS, model version and refresh state can be reproduced. TMDL import alone does not establish that. Microsoft documents different constraints for [JSON Execute Queries](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries) and the newer [Arrow Execute DAX Queries](https://learn.microsoft.com/en-us/power-bi/developer/execute-dax-queries-arrow/overview), so endpoint choice and tenant/capacity access need verification. Do not promise SQL-style row attribution for arbitrary DAX.

## Extensions that need evidence before a release slot

| Extension | Evidence needed to prioritize it | Correctness requirement |
| --- | --- | --- |
| Ratios and averages | Recurring pilot cases cannot be expressed using current aggregates | Store numerator/denominator evidence, define zero/NULL handling and attribution; no additive row-sum claim for a ratio |
| Joins or CTEs | Specific rejected query shapes block otherwise valid cases | Prove row grain, cardinality and bounded transformations; reject duplication that invalidates evidence |
| Metadata embeddings | Held-out improvement over the deterministic matcher | Report precision, recall, abstention, privacy and runtime cost; preserve manual relationship review |
| AI narrative assistance | Analysts demonstrably misunderstand computed reports | Ground each claim in stored evidence; evaluate factual and numeric errors; retain deterministic reports |
| Multi-tenancy or broad warehouse support | Multiple independent teams require it | Establish tenancy boundaries and operational costs before expanding the deployment model |

Graphify remains a development-navigation aid. Building a graph database into the product is not required by this roadmap. Keep the present React, FastAPI, core and supervisor boundaries; refactor only where a versioned contract or measured workload requires it.

## Immediate sequence

1. Obtain two independently sourced sanitized real cases and their separately calculated expected totals. Run the guided workflow, record support/refusal coverage, completion times and wrong conclusions, and have another analyst reopen and replay a pinned case.
2. Compare pilot workload sizes with the measured 100,000-row synthetic Docker workflow; repeat scale and operational checks if the pilot needs different shapes or concurrency.
3. Decide whether v2 meets its real-data acceptance gate. Use the cases to choose or change v3/v4 priorities; unsupported query shapes remain explicit failures rather than hidden workarounds. Keep outstanding v1 operational and analyst gates visible in [v1 release evidence](v1-release-evidence.md).

For future edits, keep [v1 status](v1-release-evidence.md) and [v2 status](v2-release-evidence.md) separate. Update the [project map](project-graph.md) when boundaries change. Refresh Graphify after code changes according to [AGENTS.md](../AGENTS.md).
