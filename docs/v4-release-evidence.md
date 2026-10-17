# MetricGuard V4: Connected SQL Workflow — Release Evidence

**Date:** 2026-09-25  
**Version:** 4.0.0  
**Scope:** V4-01, V4-02, V4-03, and V4-04 Complete — All V4 Milestones Frozen & Verified

## 1. Engineering Gate Status

| Gate | Work Item | Status | Verification Evidence |
| :--- | :--- | :--- | :--- |
| **V4-01** | **dbt Artifact Import, Stable IDs & Lineage** | **VERIFIED** | 18 passing tests in `tests/test_v4_dbt_adapter.py`. Manifest v7–v12 ingestion, stable `unique_id` anchors, separate `DbtImportRevision` and `DbtResourceRevision` models. `DefinitionVersion` reuse when compiled SQL is unchanged; sequential version creation when SQL changes. Revision-aware DAG lineage (`observed_lineage`). Companion `run_results.json` correlation. Graceful classification of uncompiled Jinja, CTEs, and JOINs as `metadata_only`. Idempotent re-import. Project RBAC and isolation. |
| **V4-02** | **Read-Only PostgreSQL Collector & Cutoff Consistency** | **VERIFIED** | 38 passing tests in `tests/test_v4_collector.py`. Single PostgreSQL source collector placed strictly in trusted supervisor. `REPEATABLE READ READ ONLY` single transaction snapshot isolation. Parameterized cutoff queries (`WHERE ... <= %s`) and quoted identifier validation (`^[a-zA-Z_][a-zA-Z0-9_]{0,127}$`). In-stream contract validation (Retail V2 and Flat V3), decimal scale/precision, duplicate key checks, and row limits. Atomic staging cleanup on failure across all lifecycle stages. Retry invariance requiring identical logical cutoff. Frozen DuckDB snapshot with companion integrity-verified `.manifest.json`. Source mutation immutability proof and adversarial credential protection. |
| **V4-03** | **Headless CLI / CI Automation Workflow** | **VERIFIED** | 96 passing tests in `tests/test_v4_cli.py`. Headless deterministic comparison executor, `services.cli compare` command, project-scoped environment variable authentication (`METRICGUARD_API_KEY`), strict exit codes (0: supported agreement or advisory drift; 1: enforcing material drift; 2: domain refusal; 3: operational failure), `metricguard_ci_report_v1` versioned JSON report schema, exact decimal serialization, materiality boundary correctness, ambiguous selector rejection, dbt-aware `metadata_only` refusal, API key and URI credential redaction, pinned snapshot replay determinism, 80-column ASCII terminal summary, deterministic Markdown report for CI artifacts/PR comments, full CLI subprocess test matrix, and V4-01/V4-02 regression preservation. |
| **V4-04** | **Integration, Exit Gates & Decoupled Benchmarks** | **VERIFIED** | 15 passing tests in `tests/test_v4_integration_and_exit_gates.py`. Complete end-to-end integration verified: dbt manifest import → dbt resource revision linkage → PostgreSQL collection under REPEATABLE READ → frozen DuckDB snapshot + manifest hash validation → headless CLI execution → deterministic JSON and Markdown export → byte-for-byte replay determinism → analyst review request and owner decision lifecycle. Gate 4.1 provenance chain verified. Gate 4.2 visible failure/refusal verified (unrendered Jinja, CTE, JOIN, missing SQL, failed extraction). Gate 4.3 cutoff/retry isolation verified (`CUTOFF_CHANGED` domain error on altered cutoff; zero partial snapshots on failure). Gate 4.4 snapshot determinism verified (5-run replay identity). Gate 4.5 no live-source dependency verified (200 PostgreSQL table mutations post-snapshot produce identical snapshot comparison results and file hashes). Decoupled benchmarks script verified (`scripts/evaluate_v4_benchmarks.py`) measuring 7 discrete stages and failure boundary limits (`data/evaluation/v4_benchmarks_report.json`). |

---

## 2. Milestone V4-01 Verification Summary

```
tests/test_v4_dbt_adapter.py:
  test_manifest_schema_versions_7_to_12[7] ................................ PASSED
  test_manifest_schema_versions_7_to_12[8] ................................ PASSED
  test_manifest_schema_versions_7_to_12[9] ................................ PASSED
  test_manifest_schema_versions_7_to_12[10] ............................... PASSED
  test_manifest_schema_versions_7_to_12[11] ............................... PASSED
  test_manifest_schema_versions_7_to_12[12] ............................... PASSED
  test_missing_compiled_sql_classifies_as_metadata_only .................... PASSED
  test_unrendered_jinja_classifies_as_metadata_only ........................ PASSED
  test_cte_detection_classifies_as_metadata_only ........................... PASSED
  test_join_detection_classifies_as_metadata_only .......................... PASSED
  test_multiple_tables_in_from_classifies_as_metadata_only ................. PASSED
  test_companion_run_results_correlation ................................... PASSED
  test_dbt_import_reuses_definition_version_when_sql_unchanged ............. PASSED
  test_dbt_import_creates_new_definition_version_when_sql_changes .......... PASSED
  test_revision_aware_dag_lineage .......................................... PASSED
  test_idempotent_reimport ................................................. PASSED
  test_guardrails_manifest_size_and_schema_version ......................... PASSED
  test_api_dbt_import_rbac_and_tenancy ..................................... PASSED
```

---

## 3. Milestone V4-02 Verification Summary

```
tests/test_v4_collector.py:
  test_sanitize_source_uri_redacts_credentials ............................. PASSED
  test_sanitize_source_uri_with_special_chars .............................. PASSED
  test_redact_credentials_string ........................................... PASSED
  test_validate_identifier_accepts_clean ................................... PASSED
  test_validate_identifier_rejects_malicious[-- comment] ................... PASSED
  test_validate_identifier_rejects_malicious[tbl; DROP TABLE] .............. PASSED
  test_validate_identifier_rejects_malicious[tbl"quotes] ................... PASSED
  test_validate_identifier_rejects_malicious[schema.tbl] ................... PASSED
  test_validate_identifier_rejects_malicious[tbl 123] ...................... PASSED
  test_validate_identifier_rejects_malicious[] ............................. PASSED
  test_validate_identifier_rejects_malicious[1leading_digit] ............... PASSED
  test_validate_identifier_rejects_malicious[Robert'); DROP TABLE Students;--] PASSED
  test_validate_cutoff_value_types ......................................... PASSED
  test_postgres_collector_retail_success ................................... PASSED
  test_postgres_collector_flat_success ..................................... PASSED
  test_postgres_collector_repeatable_read_read_only_isolation .............. PASSED
  test_postgres_collector_cutoff_filtering ................................. PASSED
  test_postgres_collector_row_limit_enforced ............................... PASSED
  test_postgres_collector_missing_contract_column .......................... PASSED
  test_postgres_collector_duplicate_key_rejection .......................... PASSED
  test_postgres_collector_decimal_scale_enforcement ........................ PASSED
  test_postgres_collector_currency_enforcement ............................. PASSED
  test_postgres_collector_malicious_source_selection_rejected .............. PASSED
  test_staging_cleanup_on_connection_failure ............................... PASSED
  test_staging_cleanup_on_mid_stream_failure ............................... PASSED
  test_staging_cleanup_on_duckdb_write_failure ............................. PASSED
  test_no_partial_store_registration_on_failure ............................ PASSED
  test_retry_preserves_cutoff_and_increments_attempt ....................... PASSED
  test_retry_rejects_altered_cutoff ........................................ PASSED
  test_source_mutation_does_not_affect_frozen_snapshot ..................... PASSED
  test_adversarial_credential_protection_in_manifest ....................... PASSED
  test_adversarial_credential_protection_in_logs_and_errors ................ PASSED
  test_api_extract_postgres_endpoint_success ............................... PASSED
  test_api_extract_postgres_endpoint_rbac_reader_forbidden ................. PASSED
  test_api_extract_postgres_endpoint_analyst_permitted ..................... PASSED
  test_api_get_extraction_status ........................................... PASSED
  test_api_cross_project_idor_prevention ................................... PASSED
  test_analytical_replay_on_postgres_snapshot .............................. PASSED
```

---

## 4. Milestone V4-03 Verification Summary

```
tests/test_v4_cli.py:
  TestExitCodeSemantics (9 tests) .......................................... PASSED
  TestAdvisoryAgreement (1 test) ........................................... PASSED
  TestAdvisoryMaterialDrift (1 test) ....................................... PASSED
  TestEnforcingAgreement (1 test) .......................................... PASSED
  TestEnforcingDrift (1 test) .............................................. PASSED
  TestUnsupportedSQL (1 test) .............................................. PASSED
  TestMetadataOnlyDbt (2 tests) ............................................ PASSED
  TestOperationalMissingSnapshot (1 test) ................................... PASSED
  TestCorruptedSnapshot (1 test) ........................................... PASSED
  TestCrossProjectDenied (2 tests) ......................................... PASSED
  TestRBAC (7 tests) ....................................................... PASSED
  TestAPIKeyRedaction (6 tests) ............................................ PASSED
  TestDeterministicReport (8 tests) ........................................ PASSED
  TestExactDecimalSerialization (2 tests) .................................. PASSED
  TestAmbiguousSelectors (3 tests) ......................................... PASSED
  TestReplayDeterminism (2 tests) .......................................... PASSED
  TestMaterialityCorrectness (4 tests) ..................................... PASSED
  TestSupportStateGating (4 tests) ......................................... PASSED
  TestDbtIntegration (3 tests) ............................................. PASSED
  TestCLIEntryPoint (3 tests) .............................................. PASSED
  TestOutcomeModel (3 tests) ............................................... PASSED
  TestTerminalSummary (5 tests) ............................................ PASSED
  TestMarkdownReport (5 tests) ............................................. PASSED
  TestCLISubprocessMatrix (17 tests) ....................................... PASSED
  TestV401IntegrationPreservation (1 test) ................................. PASSED
  TestV402IntegrationPreservation (3 tests) ................................. PASSED
  Total: 96 passed in 143.94s
```

---

## 5. Milestone V4-04 Verification Summary & Exit Gates

```
tests/test_v4_integration_and_exit_gates.py:
  TestV4EndToEndWorkflow:
    test_complete_end_to_end_v4_scenario ................................... PASSED
  TestGate41ProvenanceChain:
    test_unbroken_provenance_chain ......................................... PASSED
  TestGate42VisibleFailureRefusal:
    test_unrendered_jinja_refusal_exits_2 .................................. PASSED
    test_cte_refusal_exits_2 ............................................... PASSED
    test_join_refusal_exits_2 .............................................. PASSED
    test_missing_compiled_sql_refusal_exits_2 .............................. PASSED
    test_unsupported_sql_refusal_exits_2_in_all_modes ...................... PASSED
    test_failed_extraction_raises_explicit_collector_error ................. PASSED
  TestGate43CutoffRetryIsolation:
    test_interrupted_attempt_cleans_up_staging ............................. PASSED
    test_retry_strictly_requires_identical_logical_cutoff .................. PASSED
  TestGate44SnapshotDeterminism:
    test_multiple_comparisons_produce_identical_results .................... PASSED
  TestGate45NoLiveSourceDependency:
    test_post_snapshot_mutation_does_not_affect_comparison ................. PASSED
  TestDecoupledBenchmarksAndBoundaries:
    test_benchmark_script_runs_cleanly ..................................... PASSED
    test_benchmark_manifest_size_boundary .................................. PASSED
    test_benchmark_manifest_resource_count_boundary ........................ PASSED
  Total: 15 passed in 52.56s
```

### Exit Gate Results Matrix

| Exit Gate | Contract Requirement | Result | Evidence |
| :--- | :--- | :--- | :--- |
| **Gate 4.1: Provenance Chain** | Source/dbt revision links cleanly: Git commit → DbtImportRevision → DbtResourceRevision → DefinitionVersion → DatasetContract → Snapshot → Comparison Run → Review Decision. | **PASS** | `test_unbroken_provenance_chain` verifies all foreign keys and SHA-256 integrity hashes across the full relational chain. |
| **Gate 4.2: Visible Failure / Refusal** | Missing compiled SQL, unrendered Jinja, CTE, JOIN, unsupported SQL, failed extraction remain explicit, never silent passes. | **PASS** | `TestGate42VisibleFailureRefusal` (6 tests) verifies exit code 2 and explicit diagnostic codes across both advisory and enforcing modes. |
| **Gate 4.3: Cutoff / Retry Isolation** | Interrupted collector attempts leave no valid partial snapshot; retry strictly requires identical logical cutoff; no mixed attempts. | **PASS** | `test_interrupted_attempt_cleans_up_staging` confirms 0 residual staging files; `test_retry_strictly_requires_identical_logical_cutoff` confirms `DomainError: CUTOFF_CHANGED`. |
| **Gate 4.4: Snapshot Determinism** | Run same supported comparison multiple times against same frozen snapshot and verify identical analytical results. | **PASS** | `test_multiple_comparisons_produce_identical_results` executes 5 consecutive runs with byte-for-byte delta and residual identity. |
| **Gate 4.5: No Live-Source Dependency** | Modifying PostgreSQL source data post-snapshot leaves snapshot comparison and report completely unchanged. | **PASS** | `test_post_snapshot_mutation_does_not_affect_comparison` executes 200 post-snapshot inserts, updates, and deletes with zero change in snapshot hash or comparison output. |

### Decoupled Benchmarks (`data/evaluation/v4_benchmarks_report.json`)

Measured across 7 discrete stages under isolated conditions:

| Stage | Resource / Data Scale | Elapsed Time | Throughput / Metric | Status |
| :--- | :--- | :--- | :--- | :--- |
| **1. dbt Manifest Ingestion** | 50 models, 20.6 KB manifest | 0.223s | 223.9 models/sec | PASSED |
| **2. PostgreSQL Collection** | 2,500 rows, 10 columns | 14.248s | 175.5 rows/sec (`REPEATABLE READ`) | PASSED |
| **3. Snapshot Validation & Publication** | 536.5 KB DuckDB + SHA-256 | 0.010s | 51.4 MB/sec hash verification | PASSED |
| **4. Runner Container Startup** | `metricguard-runner:1.0` | 1.114s | `--network none --read-only` | PASSED |
| **5. Comparison Execution** | 2,500 rows baseline & target | 0.201s | Exact delta `0.00`, residual `0.00` | PASSED |
| **6. Evidence & Bridge Generation** | 2,500 evaluated rows | < 0.001s | Deterministic classification | PASSED |
| **7. Report Generation & Export** | JSON (3.3 KB) + Markdown (2.1 KB) | 0.101s | `metricguard_ci_report_v1` format | PASSED |

#### Failure Boundary Benchmarks

| Boundary Test | Enforced Limit | Rejection Time | Observed Diagnostic | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Manifest Size Limit** | 10 MiB cap | 0.000024s | `AdapterError: DBT_MANIFEST_TOO_LARGE` | PASSED |
| **Resource Count Limit** | 500 nodes cap | 0.001130s | `AdapterError: DBT_TOO_MANY_RESOURCES` | PASSED |
| **SQL Refusal Detection** | CTE / multi-table detection | 0.000509s | `support_state: metadata_only` (`CTE_DETECTED`) | PASSED |

---

## 6. Full Repository Regression Status (Post V4-04)

- **Pytest:** 353 passed, 0 failed in 427.93s (`.venv\Scripts\python -m pytest -q -p no:cacheprovider --basetemp .test-tmp/full_run_v404`)
- **V4-04 Focused Tests:** 15 passed in 52.56s (`tests/test_v4_integration_and_exit_gates.py`)
- **V4-03 Focused Tests:** 96 passed in 143.94s (`tests/test_v4_cli.py`)
- **V4-02 Focused Tests:** 38 passed in 140.37s (`tests/test_v4_collector.py`)
- **V4-01 Focused Tests:** 18 passed in 4.01s (`tests/test_v4_dbt_adapter.py`)
- **Web Unit Tests:** 5 passed (`npm test --prefix apps/web`)
- **Web Production Build:** Clean, 0 errors, 1585 modules transformed in 4.40s (`npm run build --prefix apps/web`)
- **Docker Compose:** Clean, 0 errors (`docker compose -f infra/compose.yaml config --quiet`)
- **Live Isolated Runner Verification:** Passed (`.venv\Scripts\python -m scripts.verify_live_runner --output data/evaluation/live_runner_report.json`), verifying `--network none`, root read-only, 0 capabilities, and zero source credentials in the runner.
- **Decoupled Benchmarks Script:** Passed (`.venv\Scripts\python -m scripts.evaluate_v4_benchmarks --output data/evaluation/v4_benchmarks_report.json`).
