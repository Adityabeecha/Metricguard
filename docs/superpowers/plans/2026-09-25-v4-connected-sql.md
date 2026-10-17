# MetricGuard V4 Execution Plan: Connected SQL Workflow

**Date:** 2026-09-25  
**Target:** Implementation of V4 engineering gates (V4-01 through V4-04) with strict adherence to architectural corrections.

## Milestone Breakdown

### Milestone V4-01: dbt Artifact Import, Stable IDs & DAG Lineage (COMPLETED & VERIFIED)
1. **Schema & Models (`services/api/models.py`, `services/api/store.py`):**
   - Define `dbt_import_revisions` table:
     - `id`, `project_id`, `manifest_hash`, `source_revision`, `invocation_id`, `dbt_version`, `generated_at`, `created_at`.
   - Define `dbt_resource_revisions` table:
     - `id`, `import_revision_id`, `unique_id`, `resource_type`, `name`, `definition_version_id`, `compiled_sql_present`, `support_state`, `diagnostics`, `observed_lineage`, `run_results_status`.
   - Add store methods for importing dbt revisions, recording resource revisions, looking up definitions by `dbt_unique_id`, and retrieving revision-aware lineage.
2. **Adapter Enhancements (`services/api/metadata_adapters.py`):**
   - Support dbt manifest schema versions 7 through 12.
   - Parse companion `run_results.json` bytes when provided.
   - Detect uncompiled Jinja macros (`{{`, `}}`, `{%`, `%}`).
   - Detect multi-table JOINs and CTEs via `sqlglot` AST traversal; flag as `support_state: 'metadata_only'` with explicit diagnostics.
   - Produce structured import payload separating import revision metadata from resource revisions.
3. **API Endpoint (`services/api/app.py`):**
   - Add endpoint `POST /api/v1/projects/{pid}/dbt/imports` accepting `manifest.json`, optional `run_results.json`, and optional source revision metadata (git SHA, branch).
   - Ensure idempotent re-import based on `manifest_hash`.
   - Re-use existing `DefinitionVersion` if compiled SQL is unchanged; create new `DefinitionVersion` only when executable SQL changes.
   - Query endpoint `GET /api/v1/projects/{pid}/dbt/revisions` and `GET /api/v1/projects/{pid}/dbt/revisions/{id}`.
4. **Verification Suite (`tests/test_v4_dbt_adapter.py`):**
   - 18 passing tests covering manifest v7–v12 ingestion, unrendered Jinja, CTE/JOIN degradation, DAG lineage, version reuse, idempotency, and project RBAC.

---

### Milestone V4-02: Read-Only PostgreSQL Collector & Cutoff Consistency (COMPLETED & VERIFIED)
1. Implement `PostgresCollector` in `services/supervisor/collector.py`:
   - Single PostgreSQL connector (no SQLite/DuckDB presented as external source connectors).
   - Trusted supervisor placement; zero credential leakage into logs, manifests, API errors, or exceptions.
2. Safe parameterized cutoff queries (`WHERE ... <= %s`), allowlisted quoted identifiers (`^[a-zA-Z_][a-zA-Z0-9_]{0,127}$`).
3. Strict transaction isolation: `REPEATABLE READ READ ONLY` via psycopg 3.
4. Bounded streaming (`fetchmany`), contract validation (Retail V2 and Flat V3), decimal precision/scale, required column, and duplicate key checks.
5. Atomic staging & publication (`.duckdb` + companion `.manifest.json`); cleanup on failure across all lifecycle stages.
6. Cutoff invariance on retries: identical cutoff required, retry attempts tracked, fresh staging artifact per attempt.
7. Verification suite in `tests/test_v4_collector.py` (38 passing tests) including source mutation immutability proof, adversarial credential protection, and API RBAC.

---

### Milestone V4-03: Headless CLI & CI Automation Workflow (COMPLETED & VERIFIED)
1. Implemented `services/cli.py` with deterministic comparison execution contract and `compare` command.
2. Project-scoped environment variable authentication (`METRICGUARD_API_KEY`) with full credential redaction across reports, exceptions, and logs.
3. Strict exit code semantics (`0` agreed or advisory drift, `1` enforcing material drift, `2` refusal/unsupported in all modes including advisory, `3` operational failure). Custom `MetricGuardArgumentParser` prevents default argparse exit code collisions.
4. Machine-readable versioned JSON report (`metricguard_ci_report_v1`) with exact decimal serialization, input provenance anchors, top bridge contributions, and evidence summary.
5. Clean 80-column ASCII terminal summary for CLI invocations (`generate_terminal_summary`).
6. Human-readable structured Markdown report for CI artifacts and PR comments (`generate_markdown_report`) with zero AI narrative.
7. Materiality boundary checks (exact boundary, zero baseline, unconfigured thresholds).
8. Ambiguous selector rejection and dbt unique_id resolution with `metadata_only` refusal, preserving `observed_lineage` (both upstream and downstream nodes) and Git source revision.
9. Verification suite in `tests/test_v4_cli.py` (96 passing tests across 26 test classes), including full CLI subprocess execution matrix and V4-01/V4-02 regression preservation.

---

### Milestone V4-04: Integration, Exit-Gate Verification & Benchmarks (COMPLETED & VERIFIED)
1. **End-to-End Workflow Verification (`tests/test_v4_integration_and_exit_gates.py`):**
   - Single realistic deterministic end-to-end integration test (`TestV4EndToEndWorkflow`):
     - Ingests dbt Core manifest (`model.retail.monthly_completed_revenue` and `model.retail.monthly_paid_revenue`).
     - Preserves stable dbt unique_id anchors and links source revision (Git commit SHA) to DefinitionVersion.
     - Performs PostgreSQL collection under `REPEATABLE READ READ ONLY` into a frozen DuckDB snapshot with companion integrity-verified `.manifest.json`.
     - Invokes headless CLI subprocess to compare supported baseline and target definitions on that frozen snapshot.
     - Generates machine-readable `metricguard_ci_report_v1` JSON and human-readable Markdown reports identifying dbt revision and snapshot hashes.
     - Replays comparison to prove byte-for-byte report reproducibility.
     - Analyst submits review request for intentional variant and Owner records accepted decision.
2. **V4 Exit Gate Verification:**
   - **Gate 4.1 (Provenance Chain):** Source Git commit SHA links cleanly through DbtImportRevision → DbtResourceRevision → DefinitionVersion → DatasetContract → Snapshot → Comparison Run → Review Decision (`TestGate41ProvenanceChain`).
   - **Gate 4.2 (Visible Failure/Refusal):** Explicit refusals (exit code 2) and structured diagnostics verified for unrendered Jinja, CTEs, JOINs, missing compiled SQL, and failed extraction; never silent passes (`TestGate42VisibleFailureRefusal`).
   - **Gate 4.3 (Cutoff/Retry Isolation):** Interrupted collector attempts clean staging atomically; retry invariance requires identical logical cutoff (`DomainError: CUTOFF_CHANGED` on altered cutoff) (`TestGate43CutoffRetryIsolation`).
   - **Gate 4.4 (Snapshot Determinism):** 5-run replay identity test on frozen DuckDB snapshot confirms byte-for-byte deterministic output (`TestGate44SnapshotDeterminism`).
   - **Gate 4.5 (No Live-Source Dependency):** Post-snapshot mutations to PostgreSQL table (200 inserts, updates, deletes) produce zero change in snapshot comparison results or snapshot file hashes (`TestGate45NoLiveSourceDependency`).
3. **Decoupled Benchmarks Script (`scripts/evaluate_v4_benchmarks.py`):**
   - Benchmarks 7 discrete stages: dbt manifest ingestion (223.9 models/sec), PostgreSQL extraction (175.5 rows/sec), snapshot validation (0.010s), runner container startup (1.114s), comparison execution (0.201s), evidence generation (<0.001s), and report export (0.101s).
   - Validates failure boundary limits: manifest size limit (10 MiB rejected in 0.000024s), resource count limit (500 nodes rejected in 0.001130s), and SQL refusal detection (CTE rejected as `metadata_only` in 0.000509s).
   - Generates machine-readable evaluation report in `data/evaluation/v4_benchmarks_report.json`.
4. **Release Evidence:**
   - Documented in `docs/v4-release-evidence.md`. All 353 tests passing across the repository.
