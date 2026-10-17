# MetricGuard V3 Implementation Plan: Shared Review and Recurring Checks

**Date:** 2026-09-25  
**Target:** Implementation of V3 engineering requirements (V3-01 to V3-05) and verification test suite.

## Execution Sequence

### Phase 1: Storage Layer & Identity Model (`services/api/store.py`, `models.py`)
- Define tables: `users`, `project_members`, `review_requests`, `monitoring_policies`, `alerts`, `audit_logs`.
- Implement user creation, API key generation/hashing, project membership management, role revocation.
- Implement project-scoped data queries with authorization checks.
- Add methods for reviews, intentional variants, superseding decisions.
- Add methods for monitoring policies, alert generation, deduplication, and acknowledgement.
- Add methods for project storage quota checking.

### Phase 2: API Authentication & Multi-Role Authorization (`services/api/auth.py`, `services/api/app.py`)
- Implement FastAPI authentication dependency extracting API key / Bearer token.
- Implement role-based authorization dependencies (`require_project_role(['owner', 'analyst', 'reader'])`).
- Protect every project object endpoint (definitions, snapshots, runs, investigations, evidence, export, downloads).
- Preserve unauthenticated requests on `local-retail` as `unverified` for V1/V2 backward compatibility.
- Ensure revoked members receive immediate 403 on all object IDs and direct download paths.

### Phase 3: Review Workflow & Superseding Decisions API
- `POST /api/v1/projects/{pid}/review-requests`: Submit review request with rationale, optional intentional-variant flag and effective time.
- `GET /api/v1/projects/{pid}/review-requests`: List review requests with status.
- `POST /api/v1/projects/{pid}/decisions`: Owner approves review and records superseding decision on immutable versions.
- Verify historical runs and evidence artifacts are unchanged.

### Phase 4: Monitoring Engine, Change Classification & Alerts (`services/api/monitoring.py`)
- Implement change classifier:
  - `classify_change(baseline_v, target_v, baseline_snap, target_snap) -> 'definition_change' | 'dataset_change' | 'combined_change'`
- Implement materiality evaluator:
  - Absolute threshold check
  - Relative threshold check with zero-denominator guard
  - Undefined result detection
  - Intentional-variant suppression
- Deduplication: avoid creating duplicate unacknowledged alerts for identical check events.
- Endpoints:
  - `POST /api/v1/projects/{pid}/monitoring-policies`: Register policy.
  - `POST /api/v1/projects/{pid}/monitoring-checks`: Trigger check run.
  - `GET /api/v1/projects/{pid}/alerts`: List alerts.
  - `POST /api/v1/alerts/{id}/acknowledge`: Acknowledge alert.

### Phase 5: Operational Governance & Backup/Restore (`services/api/backup.py`, `scripts/backup_restore.py`)
- Storage quota calculation and enforcement.
- Redacted audit log API endpoint.
- Atomic backup and restore function:
  - Dumps database records for project.
  - Bundles snapshot DuckDB files + manifests.
  - Bundles row-evidence JSONL files.
  - Validates all cryptographic hashes on restore; rejects partial restores where evidence files are missing.

### Phase 6: Multi-Role Engineering Verification Suite (`tests/test_v3_*.py`)
- `tests/test_v3_auth_isolation.py`: Cross-project isolation, role capabilities (`owner`, `analyst`, `reader`), revocation, direct download protection.
- `tests/test_v3_review_workflow.py`: Review requests, rationale, intentional variant suppression, immutable superseding decisions.
- `tests/test_v3_monitoring.py`: Definition change vs. dataset change vs. combined change, materiality thresholds, zero denominator, deduplication, alert inbox.
- `tests/test_v3_backup_restore.py`: Complete backup & restore of DB + snapshots + evidence artifacts, partial restore rejection.

### Phase 7: Documentation & Release Evidence (`docs/v3-release-evidence.md`)
- Document all verified V3 engineering gates.
- Explicitly state: `V3 engineering/product requirements complete; observed team trial remains open.`
