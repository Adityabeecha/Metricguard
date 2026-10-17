# 6 Data model and API contracts

## 6.1 Relational model

Use UUID identifiers in the implementation; fixture labels in this pack are readable examples. All primary objects belong to a project even in the single-user MVP. Project scoping does not itself make the application a secure multitenant service.

| Entity | Important fields | Key constraints |
| --- | --- | --- |
| project | id, name, timezone, settings_version, created_at | Owns every import, run and decision |
| source_asset | id, project_id, kind, label, locator, content_hash, imported_at | Locator is display metadata, not an executable path |
| dataset_snapshot | id, project_id, manifest_hash, schema_json, key_columns, cutoff_at, artifact_ref, status | Immutable after validation; unique manifest identity |
| metric_family | id, project_id, name, purpose, owner_label | Family membership is reviewed, not solely inferred |
| metric_definition | id, project_id, family_id nullable, source_asset_id, stable_source_key | Stable implementation identity across versions |
| definition_version | id, definition_id, version_number, raw_sql, dialect, raw_hash, ir_json, ir_hash, support_state, observed_at | Unique definition and version number; immutable |
| pair_review | id, left_definition_id, right_definition_id, relation, reason, actor_label, created_at | Canonical pair ordering; decisions append or supersede |
| comparison_run | id, project_id, version_a, version_b, snapshot_id, context_json, method, state, idempotency_key, lease fields | Inputs immutable after queueing; unique idempotency key per project |
| run_result | id, run_id, endpoint_values, delta, status_fields, residual, engine_versions, evidence_ref | One accepted terminal result per attempt; decimal values stored exactly |
| bridge_step | id, run_result_id, position, rule_group, ir_hash, previous_value, next_value, contribution | Unique result and position; ordered contributions |
| baseline_approval | id, family_id, version_id, scope_json, effective_from, reason, actor_label, supersedes_id | No conflicting active approval for the same declared scope |
| dependency_edge | id, project_id, upstream_asset, downstream_asset, origin, observed_at, confidence_label | Known, declared or inferred origin retained |
| drift_event | id, definition_id, old_version, new_version, change_kind, approval_state, replay_run_id | Old and new versions fixed |
| audit_event | id, project_id, event_type, actor_label, target_type, target_id, before_ref, after_ref, created_at | Append-only through the application interface |

Use foreign keys and transactional state transitions. Large row evidence stays in a bounded Parquet or JSON artifact, with its hash and schema referenced from the database. A result should never rely on a mutable filesystem path without an integrity record.

## 6.2 Versioning and retention

Importing identical source bytes creates a new import event if useful but should not create a fake semantic version change. Importing a changed definition produces a new immutable version. A changed parser or normalization policy may generate a new derived analysis; do not overwrite the old IR used by completed runs.

A baseline approval points at one immutable definition version. Editing that definition creates a new version and requires a new decision for the applicable scope. Approvals are historical records even if superseded. Reports list whether their baseline was approved at run time.

Snapshot deletion should explicitly mark dependent runs as no longer replayable while preserving their recorded results and hashes, unless the user chooses to delete those results too. Do not silently leave a reproducibility badge after source evidence is removed.

## 6.3 API conventions

Proposed base path is `/api/v1`. The API uses JSON except file upload and downloads. Monetary values are decimal strings. Dates are ISO 8601; timestamps include offsets. List endpoints use cursor pagination. Errors include a stable code, readable message, affected field and correlation ID. Never expose local paths, database secrets or raw stack traces in the browser.

| Method and path | Purpose | Important behavior |
| --- | --- | --- |
| POST /projects | Create a local project | Returns its configuration version |
| POST /projects/{id}/snapshots | Upload a dataset and ingestion metadata | Returns validation job; rejects size or format violations |
| GET /snapshots/{id} | Read schema and validation result | No accepted snapshot before validation succeeds |
| POST /projects/{id}/definitions | Import SQL and provenance | Returns support status and structured errors |
| POST /definitions/{id}/versions | Add a new immutable version | Does not update completed runs |
| GET /projects/{id}/definitions | List inventory and support coverage | Paginated with server-side filtering |
| GET /projects/{id}/candidates | List ranked potential pairs | Includes heuristic reasons and review state |
| POST /projects/{id}/pair-reviews | Record intended relationship | Requires rationale for intentional variant or rejection |
| POST /comparisons/validate | Check proposed pair and context | Does not execute SQL |
| POST /comparisons | Queue a validated comparison | Requires Idempotency-Key; returns 202 with run ID |
| GET /comparisons/{id} | Read state and results | Partial results retain limitations |
| GET /comparisons/{id}/evidence | Page or download evidence | Applies export limit and safe filename rules |
| POST /comparisons/{id}/cancel | Request cancellation | Idempotent for a terminal run |
| GET /definitions/{id}/history | Read versions and drift events | Distinguishes source time from observation time |
| POST /families/{id}/baselines | Record a scoped baseline | Expected current approval version prevents stale overwrite |
| POST /projects/{id}/dependencies | Register a known consumer link | Records manual origin and coverage limits |
| POST /comparisons/{id}/exports | Generate report and JSON bundle | Preserves incomplete and unknown result statuses |

The path list is a proposed interface specification, not a generated production OpenAPI document. During implementation, generate OpenAPI from validated server models and check that the UI uses those same contracts.

## 6.4 Comparison request

The illustrative request in `examples/comparison_request.json` references two immutable versions, one snapshot and an August 2026 half-open interval. It names the ordered bridge method and fixes the rule order. Date parameters are bound values; no client-supplied SQL fragments are appended to queries.

Required context fields are snapshot_id, start, end_exclusive, timezone, unit, currency, grain and scope. Each definition's time field remains in that definition. The shared reporting window stays the same while attribution fields may differ.

The server checks that both versions and the snapshot belong to the same project. It rejects unknown or unsupported versions, contradictory context and incompatible scopes. A currency string on the request cannot override a snapshot's currency without an explicit supported mapping.

## 6.5 Result contract

The result separates relationship_status, comparability_status, definition_status, value_status and explanation_status. It includes baseline and target decimal values, signed delta, absolute gap, baseline-relative change when defined, tolerance, ordered contributions, residual and artifact references.

Provenance includes source text hashes, IR hashes, snapshot manifest hash, context fingerprint, mapping version, parser version, DuckDB version, application revision and decomposition method version. A display summary is derived from these fields. The JSON example contains readable identifiers and fixture results; actual hashes are generated from real bytes during implementation.

## 6.6 Error taxonomy

| Code | Meaning | User action |
| --- | --- | --- |
| PARSE_ERROR | SQL cannot be parsed with the declared dialect | Correct SQL or dialect |
| UNSUPPORTED_CONSTRUCT | Query uses a feature outside the declared subset | Use a supported extract or defer the case |
| UNKNOWN_COLUMN | Definition cannot bind to the snapshot schema | Correct mapping or select another dataset |
| CONTEXT_REQUIRED | Period, timezone, grain or scope is missing | Supply the missing context |
| NOT_COMPARABLE | Known scopes, units or data are incompatible | Resolve mismatch before interpreting impact |
| SNAPSHOT_INVALID | Data fails declared structural rules | Inspect ingestion report |
| UNDEFINED_ENDPOINT | An aggregate result is NULL or otherwise undefined | Inspect empty population or missing terms |
| EXECUTION_LIMIT | Time, memory, disk or row output limit reached | Narrow the case or revise configured limits |
| RECONCILIATION_MISMATCH | An invariant failed | Inspect evidence; report cannot claim full explanation |
| STALE_REVIEW | Baseline changed since the review was opened | Reload and review the current state |

Use HTTP 400 for malformed requests, 413 for size violations, 422 for typed semantic validation failures, 404 for unavailable objects and 409 for idempotency or version conflicts. A queued job can later fail with a structured domain error without turning its status endpoint into an HTTP server failure.
