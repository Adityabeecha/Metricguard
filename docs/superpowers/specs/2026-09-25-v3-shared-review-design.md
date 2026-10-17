# MetricGuard V3: Shared Review and Recurring Checks — Specification

**Date:** 2026-09-25  
**Version:** 3.0.0  
**Status:** In Progress (Engineering implementation active; external human team trial deferred)

## 1. Scope and User Outcome

Build toward the V3 outcome defined in [the roadmap](../../next-versions-roadmap.md):
> **A small team can review a baseline and detect subsequent definition changes.**

V3 introduces shared governance, authenticated multi-role authorization, review workflows with intentional variants, recurring monitoring with change classification, materiality alerting, and operational disaster recovery (backup/restore, quotas, lease recovery).

### Reused V1 & V2 Components
1. **Restricted Metric Core (`packages/metricguard_core`):**
   - Exact numeric calculations, Decimal precision, IR parsing, DuckDB snapshot engine, waterfall bridges, exact Shapley attribution.
   - Retail 9-role policy and Flat-snapshot v1 policy are preserved without modification.
2. **Supervisor & Runner Sandboxing (`services/supervisor`):**
   - Fixed container runner with read-only root, non-root user (10001), no network access, memory/CPU/pid limits.
   - Summary JSON + separate streaming JSONL row-evidence artifacts (`evidence_artifact.py`).
   - `InProcessLauncher` and `DockerLauncher` patterns.
3. **Storage Engine (`services/api/store.py`):**
   - SQLite/PostgreSQL transactional records, immutable version records, DuckDB snapshot manifests, lease acquisition and heartbeats.

---

## 2. Engineering Gates & Architecture

### Gate V3-01: Authentication, Project Tenancy & RBAC
- **Identity Model:**
  - `users` table: `id`, `email`, `name`, `api_key_hash`, `created_at`, `is_active`.
  - `project_members` table: `id`, `project_id`, `user_id`, `role` (`owner`, `analyst`, `reader`), `created_at`, `revoked_at`.
- **Role Permissions:**
  - `owner`: full project administration, member management, revoke access, set retention/quotas, approve review requests, record superseding decisions, run comparisons, export evidence.
  - `analyst`: create definitions, upload snapshots, queue comparisons, request reviews, save investigations, trigger replays, view evidence, download exports.
  - `reader`: read-only access (definitions, runs, snapshots, evidence, exports). Blocked from creating definitions, uploading, running comparisons, or reviewing.
- **Server-Side Enforcement:**
  - Every API endpoint checks authentication (`Authorization: Bearer <api_key>` or `X-API-Key`) and project membership.
  - Direct download links (`/api/v1/comparisons/{id}/export`, `/api/v1/comparisons/{id}/evidence`) require authenticated authorization.
  - Cross-project access is strictly blocked (HTTP 403 / 404).
  - Immediate revocation: When membership is revoked (`revoked_at` is set), all subsequent requests (including for previously known object IDs) are denied immediately.
- **Legacy Compatibility:**
  - Requests without credentials operate in local unauthenticated mode, mapped to a default `unverified` analyst identity on `local-retail` to preserve all existing V1/V2 automated tests.

### Gate V3-02: Review Workflow, Review Rationale & Superseding Decisions
- **Review Requests:**
  - Analysts submit review requests on comparison runs or definition versions with explicit `rationale`.
  - Intentional variants: Definition pairs can be designated as intentional variants (`is_intentional_variant = True`, `variant_rationale`, `effective_time`), distinguishing intentional discrepancies from bugs/drift.
- **Superseding Decisions:**
  - Owners approve reviews and record superseding decisions (`decision`, `supersedes_version_id`, `approved_version_id`, `rationale`).
  - Decisions are immutable records tied to frozen definition versions and snapshot IDs.
  - **Zero Historical Mutation:** Recording a decision never mutates historical runs, evidence JSONL files, or past reviews.

### Gate V3-03: Recurring Checks, Change Classification, Materiality & Alerts
- **Monitoring Policies:**
  - Registered under a project with scope (baseline definition, target definition, snapshot target).
  - Triggered on schedule or event (new snapshot import).
- **3-Way Change Classification:**
  1. `definition_change`: baseline version vs. new definition version evaluated on the *same* fixed snapshot.
  2. `dataset_change`: identical definition versions evaluated against a *new* snapshot.
  3. `combined_change`: both definition and snapshot have changed simultaneously. Explicitly labeled to prevent false single-cause attribution.
- **Materiality Evaluation:**
  - Absolute threshold: $|\Delta| \ge \text{threshold}$.
  - Relative threshold: $|\Delta / \text{baseline}| \ge \text{threshold}$.
  - Zero-denominator guard: When baseline is 0, relative materiality handles division safely without error.
  - Undefined-result guard: When comparisons have undefined/null endpoints, an alert is generated with status `undefined_endpoint`.
  - Intentional-variant suppression: Comparisons reviewed as intentional variants suppress alert generation.
- **Alert Deduplication & Inbox:**
  - Duplicate recurring executions on the same inputs emit at most one active alert.
  - In-app inbox: `GET /api/v1/projects/{id}/alerts` with states `unacknowledged`, `acknowledged`, `resolved`.

### Gate V3-04: Restricted Job Boundary, Leases & Scheduler Recovery
- **Security Boundary:**
  - The isolated runner remains network-isolated (`--network none`) without external credentials.
  - Remote connectors and external credentials live strictly in orchestrator services outside the runner container.
- **Lease and Scheduler Recovery:**
  - Worker crash or expired lease is automatically recovered on next supervisor poll.
  - Scheduler restart re-checks missed periods without creating duplicate runs or alerts.

### Gate V3-05: Operational Governance, Quotas, Redacted Logs & Complete Backup/Restore
- **Storage Quotas:**
  - Project-level storage limits on snapshots and evidence artifacts.
- **Redacted Operational Logs:**
  - Sensitive API keys, tokens, and credentials are redacted (`[REDACTED]`) in all audit log endpoints.
- **Atomic Backup & Restore:**
  - `backup_project` and `restore_project` utilities.
  - Bundles database records (JSON), DuckDB snapshot files, and JSONL row-evidence artifacts together into an archive with SHA-256 manifests.
  - Restore validates that database records and all referenced physical artifacts exist and match cryptographic hashes. Partial restore without artifacts is rejected.

### Gate V3-06: Human / Team Validation Gate (External)
- Requires an observed multi-analyst team trial with real users.
- Kept **OPEN** until real external teams are available.
