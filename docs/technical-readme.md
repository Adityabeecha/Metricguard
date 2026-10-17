> Moved from the top-level README during the V8 closeout (2026-10-17). The short [README](../README.md) is now the entry point; this page keeps the detailed v1/v2 setup and support notes.

# MetricGuard

MetricGuard is a local KPI reconciliation workbench. The original [`MetricGuard_Planning_Pack`](../MetricGuard_Planning_Pack/README.md) describes **one v1**: its six milestones and 26 backlog items are not separate versions. The seeded v1 demo compares synthetic retail SQL definitions on a frozen August 2026 orders snapshot. The v2 local investigation workflow adds explicit contracts and column mappings for supported CSV or Parquet extracts, bound source SQL, saved cases, and bounded evidence artifacts. It is being rehearsed with synthetic cases; the real-case pilot gate remains open. See the [v1 release matrix](v1-release-evidence.md), [v2 evidence matrix](v2-release-evidence.md), [project graph](project-graph.md), [Graphify code graph](../graphify-out/graph.json), and [repository instructions](../AGENTS.md).

## Start the local demo on Windows

From this repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
npm ci --prefix apps/web
npm run build --prefix apps/web
.\.venv\Scripts\python -m scripts.seed_demo
.\.venv\Scripts\python -m uvicorn services.api.app:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Start with Dashboard A versus Finance, then Dashboard A versus Dashboard B. The first case reports INR 104000000.00 versus INR 98000000.00 and a target-minus-baseline gap of INR -6000000.00. The bridge attributes -4320000.00 to refund treatment, -420000.00 to payment eligibility and -1260000.00 to payment-date attribution in that declared order. The second case exposes the INR 700000.00 pending order included by `status <> 'Cancelled'`.

The [five-minute v1 walkthrough](v1-demo-walkthrough.md) also covers the different-definition/equal-value example, fixed-snapshot date-rule replay, evidence and export.

The demo uses verified results generated only from the repository's synthetic fixture. Imported definitions are stored and classified, but a definition without precomputed evidence cannot run in demo mode. A changed date field may be compared against the frozen fixture only when it matches a precomputed pair; otherwise the app gives an explicit isolation requirement. The web service never evaluates uploaded SQL or an uploaded CSV in its process. Candidate points describe shared syntax and columns, not metric equivalence; a pair requires human review.

The **Metadata adapters** page accepts a dbt `manifest.json` (schema versions 7–12) or a Power BI `.tmdl` document. It displays models, measures and lineage as metadata only. It never evaluates Jinja, compiled SQL or DAX. The **Evaluation** page displays the local 100,000-row benchmark, exact oracle checks and runtime limitations. The Shapley allocation on the bridge tab uses exact rational fractions over all six rule orders; it does not replace the declared ordered bridge or make a causal claim.

## Try a local v2 investigation

Build the frontend and fixed Docker image, then start the API in `isolated` mode and the separate supervisor as described below. Open the **Guided investigation** page. Approve a versioned contract with the reporting currency, timezone, scope, grain, extraction cutoff and source NULL policy. Choose a CSV or Parquet extract, preview its column names and a bounded sample, then explicitly map each of the nine required analytical roles to a distinct source column. The preview and import run inside the fixed container; no source SQL or source data is evaluated in the API process.

Validate the mapped extract to publish an immutable snapshot. Bind each SQL definition to the approved mapping; the stored version retains both source SQL and canonical bound SQL. Select two bound versions with the same aggregate family, choose a half-open reporting period, and run a comparison on that snapshot. Open paginated row evidence, record a local decision in a saved investigation, and replay it later against the pinned snapshot and versions. Saved actor labels are self-declared, without authenticated review. The [v2 API and input contract](contracts.md) lists the exact fields and limits.

V2 currently supports one flat table, the existing nine analytical roles and restricted SUM, COUNT(*) and COUNT(DISTINCT customer_id) grammar. CSV allows either declared blank or literal `NULL` tokens for nullable fields; mapped Parquet requires native NULLs and exact mapped column types. There is no inferred currency, timezone, timestamp or grain conversion. Source bytes are limited to 25 MiB and ingestion to one million rows. The two synthetic layouts in `scripts/evaluate_v2.py` are development checks, not the required two independent sanitized real cases.

## Isolated execution

Docker Desktop must be running to process new analytical definitions or datasets. Build the fixed image:

```powershell
docker build -f infra/Dockerfile.runner -t metricguard-runner:1.0 .
```

The API and trusted supervisor run as separate processes. Set `METRICGUARD_MODE=isolated` before launching the API, then run the supervisor in a second local PowerShell terminal:

```powershell
$env:METRICGUARD_MODE = 'isolated'
.\.venv\Scripts\python -m uvicorn services.api.app:app --host 127.0.0.1 --port 8000
```

```powershell
.\.venv\Scripts\python -m services.supervisor.worker
```

The supervisor owns the Docker CLI. The fixed container has no network, a read-only root, no capabilities, a non-root UID, bounded memory/CPU/PIDs and a 60-second run deadline. The API has no container socket or launch privilege. The worker rechecks snapshot hashes and the typed IR before accepting a result. Golden CSV/Parquet workflows and the core container restrictions have now been checked on a live Docker daemon; see [the v1 evidence](v1-release-evidence.md).

In isolated mode, the browser can upload a v1 orders CSV or Parquet file or use the v2 guided mapped-extract workflow. The API stages bounded bytes; the supervisor claims a durable preview or import, runs validation in the fixed isolated container, and publishes a selectable immutable snapshot only after validation. The Datasets screen shows v1 imports; the guided page shows v2 previews and mapped imports. Start the supervisor with the same `METRICGUARD_DATABASE_URL` and `METRICGUARD_DATA_DIR` as the API. The trusted administrator command remains available:

```powershell
.\.venv\Scripts\python -m services.supervisor.admin ingest .\path\to\orders.csv .\data\snapshots\imported.duckdb --name "Imported orders"
```

The administrator command and v1 browser import require [the fixed orders schema](../MetricGuard_Planning_Pack/08_demo_and_evaluation.md). V1 Parquet requires the same ordered columns and DuckDB types; it preserves native NULL values. The v2 guided workflow accepts renamed and extra source columns after explicit mapping. Keep source extracts local. After validation, choose the new snapshot. Its business metadata and, for v2, contract and mapping hashes must match the reporting context before a comparison can queue. If the supervisor is not running, uploads stay queued and resume when it starts. In demo mode, new file bytes return `ISOLATION_REQUIRED`.

Alternatively, `docker compose -f infra/compose.yaml up --build` packages the seeded demo and PostgreSQL for a loopback-only setup. Build the frontend first (`npm run build --prefix apps/web`). Compose uses demo mode; the separate supervisor is required for isolated jobs.

## Tests

```powershell
.\.venv\Scripts\python -m pytest -q --basetemp .test-tmp/full
npm run build --prefix apps/web
docker compose -f infra/compose.yaml config --quiet
.\.venv\Scripts\python -m scripts.evaluate_release --output-dir data/evaluation
.\.venv\Scripts\python -m scripts.evaluate_candidates --output data/evaluation/candidate_report.json
.\.venv\Scripts\python -m scripts.evaluate_v2 --output data/evaluation/v2_report.json
.\.venv\Scripts\python -m scripts.verify_live_runner --output data/evaluation/live_runner_report.json
.\.venv\Scripts\python -m scripts.verify_v2_scale
```

The temporary test directory is inside the workspace because this environment restricts the default system temp path. The full v2 suite passes 140 tests, alongside the frontend build and live Docker checks. The mapped 100,000-row Docker check matched independent totals with zero residual, stored a 6,863-byte summary, and verified paged evidence and bounded export. See [v2 release evidence](v2-release-evidence.md) for timings and limits. The 102-test count and in-process benchmark in [v1 evidence](v1-release-evidence.md) remain the earlier v1 baseline; external analyst gates are still open.

## Declared core support

| Area | Included | Boundary |
| --- | --- | --- |
| SQL dialect | DuckDB, one SELECT and one scalar aggregate | Joins, CTEs, groups, windows, file scans, table functions, DDL/DML and multiple statements are rejected |
| Aggregates | SUM numeric expression, COUNT(*), COUNT(DISTINCT customer_id) | No ratios, averages or arbitrary DISTINCT expressions |
| Rules | Decimal columns/literals, `+`, `-`, `*`, numeric COALESCE, boolean predicates, comparisons, IN, IS NULL | No division, CASE, UDFs or volatility |
| Dates | Confirmed `order_date` or `payment_date`, half-open business DATE interval | No timestamp timezone conversion; SQL literal dates must agree with selected context |
| Data | V1 fixed-schema CSV/Parquet; v2 explicit mapping from CSV/Parquet into one immutable flat analytical snapshot | Same nine roles and declared business context; no currency conversion, joins, or arbitrary schema semantics |
| Evidence | Exact signed row deltas and ordered bridge; entity membership for COUNT DISTINCT; v2 isolated runs store bounded JSONL row evidence separately | Order-dependent attribution is an accounting convention, not a causal or policy judgment; exports include bounded row detail and coverage counts |
| Governance | Local pair review, scope-specific baseline record, manually registered consumers | Self-declared labels; no authenticated approval or production report discovery |
| Adapters | dbt manifest v7–v12 and a bounded TMDL metadata subset | Metadata import only; no Jinja, DAX or compiled SQL execution |
| Evaluation | Seeded 100,000-row fixture, independent totals, 132 labeled candidate pairs, two mapped v2 synthetic layouts | Synthetic results are not a real-case pilot; optional embeddings remain off without a held-out gain |

Definition difference and observed value equality are separate statuses. An empty SUM remains undefined and cannot produce a numeric gap. A known unsupported construct, missing date, incompatible context or missing precomputed demo result cannot appear as a successful comparison. Review the [original planning pack](../MetricGuard_Planning_Pack/README.md) and the [v1 release matrix](v1-release-evidence.md) for the full scope and evidence limits.
