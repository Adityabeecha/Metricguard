# MetricGuard v3 implementation and release evidence

The original planning pack describes one v1; the later v2 covers real-data investigations. This matrix tracks **v3 — Shared review and recurring checks** as defined in [the roadmap](next-versions-roadmap.md).

V3 is partitioned into:
1. **V3 engineering/product requirements** that are implemented, hardened, and objectively verified locally.
2. **V3 external team-validation gate** that requires an observed multi-analyst team trial. Real external teams are not currently available in this local environment, so this gate remains separately open.

**Current Status:**
`V3 engineering/product requirements complete and frozen; observed team trial remains open.`

---

## V3 Engineering Requirements & Gate Status

| ID | Capability & Acceptance Criteria | Local Implementation | Evidence & Test Suite | State |
| --- | --- | --- | --- | --- |
| **V3-01** | **Multi-role Auth & Access Isolation**<br>- Authenticated `owner`, `analyst`, and `reader` roles.<br>- Server-side authorization on all project objects, runs, snapshots, evidence artifacts, and download routes.<br>- Immediate access revocation without token caching bypass.<br>- Strict cross-project isolation and IDOR rejection.<br>- Bounded legacy unauthenticated compatibility path with deployment configuration boundary (`METRICGUARD_ALLOW_UNVERIFIED_LOCAL`). | `services/api/models.py`<br>`services/api/auth.py`<br>`services/api/store.py`<br>`services/api/app.py` | `tests/test_v3_auth_isolation.py`<br>`tests/test_v3_adversarial_auth.py`<br>- `test_cross_project_isolation`<br>- `test_reader_cannot_mutate`<br>- `test_analyst_cannot_manage_decisions_or_members`<br>- `test_immediate_access_revocation`<br>- `test_unverified_legacy_backward_compatibility`<br>- `test_authorization_matrix_and_idor_isolation`<br>- `test_legacy_local_retail_compatibility_bounds`<br>- `test_revocation_blocks_all_access_immediately`<br>*(8 passed)* | **Complete, Hardened & Verified** |
| **V3-02** | **Review Workflow & Superseding Decisions**<br>- Formal review requests with rationale.<br>- Intentional variant declarations with effective time.<br>- Owner superseding decisions tied to immutable versions.<br>- Historical runs, evidence JSONL, snapshots, and reviews are never rewritten.<br>- Tested immutability under attempted overwrite. | `services/api/models.py`<br>`services/api/store.py`<br>`services/api/app.py` | `tests/test_v3_review_workflow.py`<br>`tests/test_v3_adversarial_immutability_and_scheduler.py`<br>- `test_review_request_lifecycle_and_intentional_variant`<br>- `test_superseding_decisions_and_evidence_immutability`<br>- `test_immutable_review_and_evidence_history`<br>*(3 passed)* | **Complete, Hardened & Verified** |
| **V3-03** | **Recurring Checks, Materiality & Alert Inbox**<br>- Scheduled & event-triggered rechecks.<br>- 4-way change classification (`definition_change`, `dataset_change`, `combined_change`, `unchanged`).<br>- Absolute and relative materiality thresholds.<br>- Boundary edge cases, zero-denominator, zero-target, and undefined-endpoint handling.<br>- Intentional-variant suppression.<br>- Deterministic event deduplication across unacknowledged and acknowledged states.<br>- In-app alert inbox and acknowledgement. | `services/api/models.py`<br>`services/api/monitoring.py`<br>`services/api/store.py`<br>`services/api/app.py` | `tests/test_v3_monitoring.py`<br>`tests/test_v3_adversarial_monitoring_and_materiality.py`<br>- `test_change_classification`<br>- `test_materiality_evaluation_and_variant_suppression`<br>- `test_monitoring_check_execution_dedup_and_inbox`<br>- `test_four_way_change_classification_independent`<br>- `test_materiality_threshold_boundaries_and_negatives`<br>- `test_materiality_relative_threshold_and_denominator_edge_cases`<br>- `test_materiality_undefined_and_intentional_variant_suppression`<br>*(7 passed)* | **Complete, Hardened & Verified** |
| **V3-04** | **Restricted Job Boundary & Recovery**<br>- Analytical runner remains sandboxed without credentials or network access.<br>- Fixed-snapshot definition replay and controlled rechecks.<br>- Supervisor lease recovery for interrupted or expired runs and imports.<br>- Idempotent retry without duplicate runs.<br>- Stale worker attempts rejected after lease expiration. | `services/supervisor/worker.py`<br>`services/supervisor/runner.py`<br>`services/api/store.py` | `tests/test_supervisor.py`<br>`tests/test_end_to_end_isolated.py`<br>`tests/test_workflows.py`<br>`tests/test_v3_adversarial_immutability_and_scheduler.py`<br>- `test_scheduler_lease_recovery_and_stale_worker_rejection`<br>- `test_alert_event_deduplication_including_acknowledged`<br>*(All runner isolation and lease tests passed)* | **Complete, Hardened & Verified** |
| **V3-05** | **Governance, Quotas, Logs, Backup & Restore**<br>- Storage quota enforcement (DuckDB + JSONL evidence) and restore quota check.<br>- Redacted operational audit logs (scrubbing keys/tokens, Bearer tokens, DB connection strings, query parameters).<br>- Path-traversal rejection on restore (`../evil.txt`).<br>- Atomic backup and restore bundling database records, DuckDB snapshots, and JSONL row-evidence files with SHA-256 verification.<br>- Rejection of corrupted, incomplete, or tampered archives without state modification. | `services/api/backup.py`<br>`services/api/store.py`<br>`services/api/app.py` | `tests/test_v3_backup_restore.py`<br>`tests/test_v3_adversarial_backup_quotas_and_logs.py`<br>- `test_complete_project_backup_and_atomic_restore`<br>- `test_atomic_rejection_of_corrupted_backup`<br>- `test_storage_quota_enforcement`<br>- `test_redacted_audit_logs`<br>- `test_storage_quota_accounting_and_restore_enforcement`<br>- `test_backup_restore_adversarial_tampering`<br>- `test_comprehensive_log_redaction`<br>*(7 passed)* | **Complete, Hardened & Verified** |
| **V3-06** | **Observed Team Trial**<br>- Multi-analyst collaborative workflows with real teams.<br>- Real adoption, usability, and review efficiency metrics.<br>- Not simulated; no synthetic human data fabricated. | External human/team environment | External multi-user team trial required | **Not evaluated; external team gate open** |

---

## Automated Verification Summary

| Check Suite | Command | Result | Pass Rate |
| --- | --- | --- | --- |
| **Full Python Test Suite** | `.venv\Scripts\python -m pytest -q --basetemp .test-tmp/v3_adv_full` | **186 passed** in 83.31s | 100% |
| **V3 Hardening & Adversarial Tests** | `.venv\Scripts\python -m pytest tests/test_v3_adversarial_*.py -q` | **13 passed** in 10.49s | 100% |
| **Web Unit Tests** | `npm test --prefix apps/web` | **5 passed** in 24ms | 100% |
| **Web Production Build** | `npm run build --prefix apps/web` | **0 errors** (built in 2.70s) | 100% |
| **Docker Compose Config** | `docker compose -f infra/compose.yaml config --quiet` | **0 errors** (valid) | 100% |

All 159 legacy V1 and V2 tests remain 100% passing, ensuring strict backward compatibility for single-user local workflows while guaranteeing full adversarial security, multi-role isolation, atomic restore, and recurring monitoring.
