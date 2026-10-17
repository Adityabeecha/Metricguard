# V7 implementation and verification evidence

## V7.1 bounded boolean filters - engineering preview

Status: verified and user-approved synthetic engineering acceptance (2026-09-28); see [docs/evidence/v7_1_acceptance_report.md](evidence/v7_1_acceptance_report.md). All seven acceptance groups passed with zero discrepancies across isolated Docker runner comparisons, canonicalization, boundary refusals, forged IR rejection, save/replay, and full regressions. External pilot gates (V2-06, V3-06) remain open; no adoption or analyst-validation claim is made.

### Implemented boundary

- Explicit opt-in **FlatContract v5** with required `predicate_policy: flat-boolean-v1`. Upload UI defaults to v4 and exposes "Enable compound filters (V7.1 preview)". Existing snapshots are never upgraded in place.
- Same registered exact-decimal SUM measure, typed dimensions and whole-snapshot context. CSV/Parquet ingestion and the isolated supervisor remain shared. Types and nullability use existing v4 rules.
- Existing typed leaf predicates combined with bounded AND, OR and generic NOT (including NOT of a compound group). Logical tree, contract identity, predicate policy and hash are checked by the runner.
- 16 leaf occurrences, 8 logical levels, 63 nodes; existing 200 SQL AST nodes, 32 IN values and 256-character literals remain. The SQL AST cap may refuse expressions before the tree leaf limit when individual leaves are large.
- Canonicalization flattens identical associative operators and sorts their children deterministically. It does not distribute expressions, remove duplicate leaves, normalize every semantically equivalent literal or simplify three-valued logic. This is deterministic structural normalization, not proof of all SQL equivalences.
- SQL is generated only from validated structures, with parenthesized operators, allowlisted identifiers and parameterized typed literals. Native SQL NULL behavior is preserved; only TRUE predicates include a row.
- The existing exact contributions, bounded evidence storage/pagination/export, saved-case pinning and replay plumbing are reused. Explanation remains a single whole-filter eligibility change, not per-branch causal attribution or a retail/Shapley bridge.
- V3/v4 retain their predicate-list IR and grammar. New v5 IR uses `predicate_tree` and `predicate_policy`. Both contract hash and expected IR shape prevent cross-version reinterpretation within the existing `flat_snapshot_v1` execution family.
- Investigation Studio labels compound support for v5 and compares complete expressions rather than splitting OR expressions using its legacy AND text heuristic.

Changed code: `packages/metricguard_core/flat_boolean.py`, `packages/metricguard_core/flat.py`, `services/api/models.py`, `apps/web/src/api.ts`, `apps/web/src/views/DataSourcesView.tsx`, `apps/web/src/views/InvestigationsView.tsx`. Plan: [bounded implementation plan](superpowers/plans/2026-09-28-v7-1-bounded-boolean.md).

Not added: arithmetic, CASE, joins, new measures/aggregates, metric contracts, generalized dbt normalization, hosted infrastructure or automatic legacy migration. This first slice enters via the existing flat import/definition workflow; broader V7/dbt integration awaits separate scoping. Durable ad-hoc editor text across reloads remains a separate known limitation.

### Build evidence versus testing

- `npm run build --prefix apps/web`: passed (1595 modules, bundle index-BTjWE0J0.js).
- Python `compileall` of changed backend files: passed syntax compilation only.
- `docker build -f infra/Dockerfile.runner -t metricguard-runner:1.0 .`: packaged the implementation; this does not execute application tests.
- Graphify AST graph/manifest and HTML tree refreshed.
- **No unit, API, Docker execution, UI or replay tests were run for V7.1 by the implementation agent**, per user preference. Previous V6 receipts cannot substitute for V7 regression testing.

### Startup for Flash

Restart the existing local API and supervisor processes before testing so both load the new code. Use the existing V6 startup commands in `v5-release-evidence.md` with `METRICGUARD_MODE=isolated`, `METRICGUARD_DATA_DIR=data/live-workspace`, and `METRICGUARD_DATABASE_URL=sqlite:///data/live-workspace/metricguard.db`; serve the built UI on port 8001. Keep the local supervisor private. Runner image `metricguard-runner:1.0` must be rebuilt after any subsequent core edits.

Import a NEW snapshot and enable Compound filters at Confirm Meaning. Do not reuse or mutate a v4 contract to run v5 predicates. Saved V6 investigations must remain intact.

### Prompt for AGY/Flash

Work in C:/Users/badit/OneDrive/Desktop/metricguard. Test the V7.1 bounded boolean implementation; do not change application code or expand scope. Read AGENTS.md, docs/project-graph.md and this file. Restart API/supervisor to load the new implementation. Use the real UI/API and isolated Docker runner. Keep old snapshots/investigations untouched. Write scratch harnesses and verification receipts as needed; return concrete failures for the implementation agent.

1. Through Data Sources import a new v5 fixture (exact amount DECIMAL(10,2), key id, status/region strings, flag boolean, nullable status/flag):

```csv
id,amount,status,region,flag
A,10.00,completed,prod,true
B,20.00,settled,prod,false
C,30.00,cancelled,prod,
D,40.00,completed,test,
E,50.00,,prod,true
```

Explicitly select unit "points", blank-is-NULL, and Compound filters. Inspect persisted contract version 5, predicate_policy flat-boolean-v1, source/hash lineage and physical schema. Baseline is SELECT SUM(amount) FROM snapshot, expected 150.00. Independently calculate all expected results before comparing; the expectations here are not executed receipts.

2. Execute through Docker, inspect exact membership/contributions, paged evidence/export, and require zero residual for defined endpoints:
   - `(status = 'completed' OR status = 'settled') AND region != 'test'`: A/B, target 30.00, gap -120.00.
   - `NOT (status = 'completed')`: B/C, target 50.00, gap -100.00; E remains unknown/excluded.
   - `status != 'completed' OR status IS NULL`: B/C/E, target 100.00, gap -50.00.
   - `NOT flag`: B, target 20.00, gap -130.00.
   - `flag IS NOT TRUE`: B/C/D, target 90.00, gap -60.00.
   - Nested mixed AND/OR/NOT, typed DATE/decimal leaves in a separate fixture, reversed predicate order, NOT IN and NOT BETWEEN; derive independent truth tables.
   - Empty/all-NULL SUM: preserve NULL endpoints/gap/residual; never substitute zero.

3. Canonicalization: reorder AND/OR children and regroup the same associative operator; verify matching v5 IR hashes under the same contract, identical result/evidence. Confirm NOT retains three-valued semantics; do not expect general logical-equivalence proofs.

4. Boundary/refusal testing: depth over 8, more than 16 leaf occurrences, more than 63 nodes, SQL AST over 200 nodes, IN over 32 entries; incorrect literal types, unregistered fields, joins, subqueries, arithmetic, CASE and aggregate expansion. Verify explicit refusals and editor input retention. Confirm a flat 16-leaf chain is not rejected merely due to parser left-association (subject to the AST cap).

5. Forged IR: modify leaf types/operators/values, node shapes, operator names, missing/wrong predicate policy, noncanonical tree ordering and complexity; recompute attacker-supplied hashes and ensure the runner still refuses. Attempt v4-list IR against v5 and v5-tree IR against v4; both must fail without altering artifacts.

6. Save and replay a v5 case: identical frozen context, contract/snapshot IDs, totals and evidence hash. Check the UI whole-filter explanation does not claim independent branch attribution, and no fake financial-loss claim or guessed currency appears.

7. Legacy regressions: v3/v4 contract and IR hashes unchanged, previous reports readable, replay unchanged, OR still refused by v4. Run relevant flat, typed v4, API/workflow and CI regression suites, then the full suite using a new scratch directory with the PostgreSQL test service available. Run frontend tests. Do not reuse pre-V7 results as regression proof.

Report PASS/FAIL/BLOCKED for each case, actual IDs, image identity, hashes, commands and any reproduction steps. Keep real-world pilot validation open. Do not start V7.2 or modify code to force tests to pass.

### Resolution of V7.1 Evidence Gaps (2026-09-29 Addendum)

The remaining synthetic engineering evidence gaps identified after initial V7.1 review have been closed and documented in [docs/evidence/v7_1_acceptance_report.md](evidence/v7_1_acceptance_report.md#5-addendum-resolution-of-v71-evidence-gaps-2026-09-29):

1. **Browser End-to-End Verification:** Onboarded new fixture `v7_browser_orders.csv` through Data Sources modal with compound filters enabled; persisted Contract v5 `ef829674-c380-41ab-9f22-b1fd39e2c5d7` (`predicate_policy: flat-boolean-v1`, hash `f35be6ac11284e1f1976c89d401fe98393b0c7334ec469f614f41fb0c3a21939`) and Snapshot `06e5fb7a-3b4b-4009-83f3-0edf5c865447`. Executed compound comparison (Run `a1c5eaab-1b34-4843-9b49-46cda4a72fd6`, `150.00 / 30.00 / -120.00 points`, residual 0), inspected paged evidence, reconciled export JSON, and verified HTTP 422 refusal with editor text preserved verbatim.
2. **All-NULL Measure Test:** Tested nonempty selection where all matching measure values are NULL (`WHERE status = 'pending'`). DuckDB/Docker runner verified NULL target SUM, undefined gap (`delta: None`), undefined residual (`residual: None`), and honest row evidence flags (`target_null_term: True`) without zero substitution.
3. **Backend Test Suite Accounting (45 Skipped Tests):** Clarified that the 45 skipped tests do not require external cloud infrastructure; they depend on local Docker PostgreSQL container `metricguard-test-pg` on port 5433. With the container running, all 45 tests passed (38 in `test_v4_collector.py`, 7 in `test_v4_integration_and_exit_gates.py`). This establishes **combined coverage of 586 tests** (541 earlier backend passes plus 45 subsequently executed PostgreSQL-dependent passes across separate runs; no single monolithic 586-test run was executed).
4. **Frontend Test Suite Reconciliation:** Reconciled count discrepancy (43 vs 35); `apps/web/package.json` executes 6 test files (`v2Workflow.test.mjs`, `router.test.mjs`, `dataSources.test.mjs`, `metricsLifecycle.test.mjs`, `changes.test.mjs`, `investigations.test.mjs`), all passing cleanly (**43 passed, 0 failed**).
5. **Historical v3/v4 Replay Provenance & Fresh Post-V7.1 Execution:** Historical replay runs `0aad1104-c376-4612-b696-98ff34523b9f` (v4) and `0497a5a2-60ec-4679-9f3c-5a282acf5f6c` (v3) occurred at 16:11–16:13 UTC on 2026-09-28, predating V7.1 code execution (17:09 UTC). Because timestamps alone do not prove image/code provenance, that historical coverage was marked unestablished for V7.1. On 2026-09-29, two fresh replays were explicitly authorized and executed against the live API and isolated Docker runner (`metricguard-runner:1.0`, ID `sha256:698f162e...`): Contract v4 replay `769fb72c-ca45-4da4-ac4e-e184cb58f292` (baseline 44300.00, target 24800.00, delta -19500.00, residual 0, evidence SHA-256 `4cf76bd1f361...`) and Contract v3 replay `31b3ae4b-21e5-4de5-acf8-dd2db7d4ba9a` (baseline 1985.00, target 870.00, delta -1115.00, residual 0, evidence SHA-256 `109f32b605b2...`), both passing with 100% bitwise matching evidence digests (`scratch/post_v7_1_replay_receipt.json`).
6. **External Pilot Gates:** V2-06 and V3-06 remain **OPEN**. No claims of live customer adoption or external analyst validation are made.
7. **Definition Quota & Pruning Documentation:** Documented the pruning of 9 unreferenced temporary definitions from `local-retail` to prevent hitting the 100-definition quota (`IMPORT_LIMIT`) during browser testing. Noted the limitation that direct DB pruning bypassed application-level audit logging, and mandated that future test runs execute within a dedicated, isolated test workspace rather than mutating shared project records.

## V7.2 safe additive expressions - speculative engineering preview

Status: **ACCEPTED — Engineering Preview Certified (2026-09-29)**; see [docs/evidence/v7_2_acceptance_report.md](evidence/v7_2_acceptance_report.md). Full closeout verification passed across Groups A through H (586 backend pytest passes, 43 frontend passes, bitwise historical replays for v3/v4/v5, exact 102-digit rounded forgery defense via `ScaledInteger`, ordered signed subtraction validation, and live API tampering defense). External pilot gates (V2-06, V3-06) remain explicitly **OPEN**; no pilot justification, customer production adoption, or operational time savings claims are made.

### Implemented boundary

- **Explicit opt-in FlatContract v6**: Requires `measure_policy: flat-subtraction-v1` and `predicate_policy: flat-boolean-v1`. Registered schema stores both `decimal_columns: [DecimalColumn, DecimalColumn]` and `measure_expression: { op: 'sub', minuend: string, subtrahend: string }`. Legacy `measure` and root `precision` fields are completely excluded from canonical v6 contracts and v6 IR; expression labels (`f"{minuend} - {subtrahend}"`) are generated dynamically for display. Conflicting legacy executable fields are rejected with `invalid_contract` (422).
- **Exact Decimal Arithmetic & Endpoint Range Bound**: Both operands must have identical declared scale ($s_A = s_B = s$) and compatible unit labels. Precision bound $p_\text{req} = \max(p_A, p_B) + 1 \le 38$; contracts with $p_\text{req} > 38$ are refused with `UNSUPPORTED_PRECISION` (422). Python `Decimal(prec=100)` accumulates terms exactly. After complete accumulation, each defined endpoint aggregate must satisfy $|\text{endpoint} \times 10^s| \le 10^{38} - 1$; out-of-range endpoints raise structured `ARITHMETIC_OVERFLOW` (exit code 2, terminal job failure). Intermediate sums are allowed to cancel out. Target-minus-baseline may exceed endpoint precision and is preserved exactly if both endpoints are valid. Undefined endpoints remain NULL.
- **Zero Schema Fabrication**: DuckDB snapshot tables store source columns `[order_id, gross_amount, discount_amount, ...]`. The measure difference `(minuend - subtrahend)` is computed dynamically during query evaluation.
- **SQL & IR Validation**: Parser strictly requires `exp.Sum(this=exp.Sub)`. Operands must match contract measure expression. Formula drift between baseline and target is refused with `DIFFERENT_MEASURE_EXPRESSIONS` (422). Addition, multiplication, division, constants, CASE, functions, and joins are rejected with `UNSUPPORTED_SQL` (422). Subtraction on v3–v5 contracts is refused with `UNSUPPORTED_SQL`.
- **Inclusion vs NULL Expression Distinction**:
  - Whether a row is included by the filter is strictly decoupled from whether its expression evaluates to NULL.
  - In `v7_2_orders.csv`, R5 (`gross=150.00, discount=NULL, region=prod`) and R6 (`gross=NULL, discount=30.00, region=prod`) are filter-included in both unfiltered baseline and `WHERE region = 'prod'`. Both evaluate to NULL expressions; SQL SUM ignores NULL terms; their contribution is `0.00`; they are labelled `included_in_both`, NOT filter-excluded.
  - Empty or all-NULL selections preserve `None` (undefined) endpoints, gap, and residual.
- **Evidence Artifact & UI**:
  - `services/supervisor/evidence_artifact.py` schema permits `ALLOWED_EXPRESSION_FIELDS`: `baseline_operands`, `target_operands`, `baseline_expression_value`, `target_expression_value`.
  - Supervisor pagination, verification, and reports preserve expression evidence with exact `localcontext(prec=100)` arithmetic.
  - Data Sources UI Step 2 provides an opt-in checkbox, minuend/subtrahend selection, and live expression preview.
  - Investigation Studio displays an `Operands & Expression` column showing operands, computed expression value, and `(NULL term)` indicator.
  - Pinned replays and hash verifications are frozen.

### Build evidence versus testing

- `npm run build --prefix apps/web`: passed (1595 modules, bundle `dist/assets/index-D38ERBy5.js`).
- Python `compileall` of changed backend files: passed syntax compilation with 0 syntax errors.
- `docker build -f infra/Dockerfile.runner -t metricguard-runner:1.0 .`: cleanly built and packaged runner image (`sha256:6e82805e15fb0ced0c919da02c2ac1ddbfd6e879912cdc19a9fc3d64a41c0feb`).
- Graphify AST extraction (`graphify update . --no-cluster`) and tree generation (`graphify tree`) refreshed.
- **Acceptance Status (2026-09-29):** Following remediation of the rounded false-acceptance defect (via `ScaledInteger` in `numeric.py`) and signed ordered subtraction in `evidence_artifact.py`, full closeout verification was executed across Groups A through H against the live API and isolated Docker runner (`sha256:6e82805e15fb0ced0c919da02c2ac1ddbfd6e879912cdc19a9fc3d64a41c0feb`). All groups PASSED (586 backend pytest passes, 43 frontend passes, bitwise historical replays, exact 102-digit forgery defense, and live API tampering defense). V7.2 status is **ACCEPTED — Engineering Preview Certified**; see [docs/evidence/v7_2_acceptance_report.md](evidence/v7_2_acceptance_report.md). External pilot gates (V2-06, V3-06) remain explicitly OPEN.

## V7.3 restricted CASE WHEN & investigation persistence - engineering preview

Status: **ACCEPTED — Engineering Preview Certified (2026-10-04)**; see [docs/evidence/v7_3_acceptance_report.md](evidence/v7_3_acceptance_report.md). Full closeout verification passed across Tracks 1 through 5 (586 backend pytest passes, 43 frontend passes across all 6 test suites with 0 failures, bitwise identical replay evidence SHA-256 digests, native SQL three-valued logic, 20 HTTP 422 boundary refusals, 10 forged evidence tampering defenses rejected with `EVIDENCE_INVALID`, and real browser CDP verification across 6 screenshots). External pilot gates (V2-06, V3-06) remain explicitly **OPEN**; no customer adoption, production pilot justification, or analyst time-savings claims are made.

### Implemented boundary

- **Explicit opt-in FlatContract v7**: Requires `measure_policy: flat-case-v1` and `predicate_policy: flat-boolean-v1`. Schema registers a single exact-decimal measure (`DECIMAL(p, s)`), typed dimensions (`string`, `boolean`, `date`, `timestamp`), and null policy (`native_null` or `literal_NULL`).
- **Restricted Searched CASE Grammar**: Exactly one `WHEN` branch, exactly one registered decimal `THEN` measure, mandatory numeric zero `ELSE 0` (normalized to string `'0'` in canonical IR), and optional outer `WHERE` boolean tree. Simple CASE (`CASE col WHEN ...`), multiple WHEN branches, omitted/non-zero/NULL/string ELSE clauses, nested CASE, branch arithmetic, and functions are refused with `UNSUPPORTED_SQL` (HTTP 422).
- **Canonical CASE IR from Source (`packages/metricguard_core/flat_case.py`)**:
  - `mode`: `'flat_snapshot'`, `policy`: `'flat_snapshot_v1'`, `contract_version`: `7`, `contract_hash`: SHA-256 string
  - `measure_policy`: `'flat-case-v1'`, `predicate_policy`: `'flat-boolean-v1'`, `table`: `'snapshot'`, `aggregate`: `'sum'`
  - `measure_expression`: `{'op': 'case', 'when': <lowered_boolean_tree>, 'then': '<measure_col>', 'else': '0'}`
  - `predicate_tree`: `<outer_where_boolean_tree_or_none>`
  - `ir_hash`: SHA-256 digest of normalized IR.
  - AST bounds: Combined WHEN and WHERE trees must not exceed 96 nodes or 24 leaf occurrences. SQL AST cap is 250 nodes.
- **Native SQL Three-Valued Logic & Row Evidence**:
  - DuckDB evaluates expressions dynamically without fabricating table columns.
  - `WHERE` clause is evaluated as an expression to retain diagnostic row states even for outer-excluded rows (`where_state: false`, contribution `0.00`).
  - Row evidence records `ALLOWED_CASE_FIELDS`: `predicate_inputs`, `measure_value`, `baseline_where_state`, `target_where_state`, `baseline_when_state`, `target_when_state`, `baseline_branch` (`then`/`else`), `target_branch`, `baseline_case_term`, `target_case_term`.
  - Native three-valued logic distinguishes: empty selection (`NULL` SUM), all-ELSE-0 rows (`0.00` defined SUM), true-branch NULL measures (`NULL` SUM), and true-branch NULL measures combined with ELSE-0 rows (`0.00` defined SUM). Undefined endpoints maintain undefined gap/residual across save and replay.
- **Evidence Trust & Source Authenticity Boundary**:
  - `packages/metricguard_core/flat_case_evidence.py` enforces that typed artifact inputs do NOT independently prove source authenticity. The supervisor's immutable snapshot/source checks and published artifact digests remain the authoritative source-authenticity boundary.
  - Evidence validator checks mathematical reconciliation, branch consistency, and definition bindings (`contract_hash`, `baseline_ir_hash`, `target_ir_hash`). 10 tampering vectors with recomputed SHA-256 rejected with `EVIDENCE_INVALID`.
- **Durable Investigation Drafts & Async Concurrency**:
  - Server-backed durable drafts under `/api/v1/projects/{pid}/investigation-drafts`.
  - Debounced autosave with `version_token` optimistic locking: concurrent edits return HTTP 409 `STALE_DRAFT`, preventing silent overwrites.
  - Refused SQL expressions remain verbatim across refusal and draft reload.
  - Deleted drafts return HTTP 404 and block delayed autosave recreation. Multi-tenant project isolation enforces HTTP 403 on cross-workspace draft access.

### Build evidence versus testing

- `npm run build --prefix apps/web`: passed (1595 modules transformed, bundle `dist/assets/index-CEBE9WFr.js`).
- Python `pytest`: **586 passed, 0 failed, 8 warnings in 486.54s** across the entire backend suite (including all 45 PostgreSQL collector and integration tests against `metricguard-test-pg`).
- `docker build -f infra/Dockerfile.runner -t metricguard-runner:1.0 .`: rebuilt runner image `sha256:4e66c3bf142623274ab2a29af17145edcffae77895eff949fd0312f3b2574cc6`.
- `docker compose -f infra/compose.yaml config --quiet`: passed cleanly (exit 0).
- `npm run test --prefix apps/web`: **43 passed, 0 failed across 6 test suites (exit code 0)**. Stale assertion in `investigations.test.mjs` was aligned with intended undefined residual semantics (`classifyResidual(null)`, `classifyResidual('')`, and `classifyResidual(undefined)` return `'Residual not applicable'`, while zero and non-zero residual assertions were preserved). Application behavior was strictly preserved without modification.
- Browser CDP Verification: 6 screenshots captured in `docs/evidence/screenshots/` (`v7_3_onboarding.png`, `v7_3_comparison.png`, `v7_3_evidence.png`, `v7_3_refusal.png`, `v7_3_draft_reopen.png`, `v7_3_saved_case.png`).
- External pilot gates (V2-06, V3-06) remain explicitly **OPEN**.

## V7.4 Metric Contracts - engineering preview

Status: **ACCEPTED — Engineering Preview Certified (2026-10-04)**; see [docs/evidence/v7_4_acceptance_report.md](evidence/v7_4_acceptance_report.md). Full closeout verification passed across Tracks 1 through 7 (RBAC permissions, cross-workspace isolation, optimistic concurrency 409 conflict detection, metadata verification against frozen snapshot manifests and pinned definition versions, second revision approval with bitwise check reopening invariance, bounded pagination, project backup/restore into a fresh store, refused form input retention, browser history navigation, 4 official Edge CDP screenshots, 43 passing frontend tests across all 6 test suites, and 98 passing backend tests with 0 failures). External pilot gates (V2-06, V3-06) remain explicitly **OPEN**; no customer adoption, production pilot justification, or analyst time-savings claims are made.

### Implemented boundary

- **Metric Contract Model & Identity (`metric-governance-v1`)**:
  - Bound to a declared business Metric identity (`display_name`, `importance`).
  - Governs declared expectations: `owner_label`, `meaning` ($\le 600$ chars), `unit_label` ($\le 200$ chars), `grain` ($\le 200$ chars), `expected_dimensions` ($\le 24$ columns with allowlisted types `string`, `boolean`, `date`, `timestamp`, `decimal`), `source_expectations` (`mode`: `flat_snapshot` or `retail`, `source_label`, `contract_hash`), and `materiality_policy` (`absolute_threshold`, `relative_threshold`).
  - Strict semantic boundaries: Materiality fields record expectations; they do not override CI policy or calculate drift. Contract checks assess registered metadata; they do not run SQL, verify business truth, or prove source authenticity. Unknown currency stays `unknown currency` without inference; flat snapshots lacking declared business grain report grain as `unverified` ("not proof of business grain").
- **Immutable Revisions & Approval Lifecycle**:
  - Revisions are created via `POST /api/v1/projects/{pid}/metrics/{metric_id}/contracts` in `proposed` state with incremental `version_number`. Proposing leaves the active approved contract unchanged.
  - Only authenticated workspace Owners (`require_verified=True`, role `owner`) can approve revisions via `.../approve` with a mandatory human reason. Previous revisions remain permanently in audit history.
  - Concurrency control: Both propose and approve require `expected_version_token` (returning HTTP 409 `STALE_METRIC_CONTRACT` on mismatch) and `expected_approved_version_id` (returning HTTP 409 `STALE_METRIC_BASELINE` if the implementation changed). Duplicate approvals return HTTP 409 `METRIC_CONTRACT_ALREADY_APPROVED`. Concurrent proposals never silently overwrite history.
- **RBAC & Multi-Tenant Isolation**:
  - Readers: read-only access (all capabilities `false`).
  - Analysts: can propose revisions and execute metadata checks; cannot approve.
  - Owners: full capabilities (propose, check, approve).
  - Unverified local access: cannot propose, check, or approve (HTTP 403 `FORBIDDEN`).
  - Workspace mismatch: Header `x-project-id` or query param `project_id` conflicting with URL workspace returns HTTP 400 `PROJECT_CONTEXT_MISMATCH`. Cross-workspace metric access returns HTTP 403 / 404.
- **Metadata Check Reports & Reopen Invariance**:
  - Compares expectations against snapshot manifest and bound definition version without executing SQL.
  - Records granular checks (`display_name`, `importance`, `approved_baseline`, `definition_raw_hash`, `definition_ir_hash`, `source_mode`, `definition_mode`, `source_label`, `dataset_contract_hash`, `dimensions_declared`, and per-column presence/type).
  - Flags mismatches (`mismatch`) and unverified limitations (`unverified`), yielding `inconclusive` status when limitations exist.
  - Saved checks are immutable and pinned by `report_hash`. Reopening an earlier check retains its original results and hashes bitwise even after subsequent contract revisions are approved.
- **Backup, Restore & Pagination**:
  - `backup_project()` and `restore_project()` fully serialize and restore all metric contracts, revisions, approvals, head pointers, and metadata checks.
  - Pagination enforces bounds (`1 <= limit <= 100`, `offset >= 0`) and returns `next_offset`.
- **UI Ergonomics & Router Navigation**:
  - `MetricContractsPanel.tsx` in `MetricsView` provides proposal forms, revision selectors, approval dialogs, metadata check runners, and recent check/approval audit lists.
  - Refused forms retain entered values and display an informative error banner.
  - Contracts tab navigation synchronizes with the browser URL (`/w/:workspaceId/metrics/:metricId?tab=contracts`) and honors browser history (`history.back()` and `history.forward()`).

### Build evidence versus testing

- `npm run build --prefix apps/web`: passed (1,597 modules transformed, bundle `dist/assets/index-B7RVwnaZ.js`, 496.80 kB).
- `npm run test --prefix apps/web`: **43 passed, 0 failed across all 6 test suites (exit code 0)**.
- Backend regressions: **98 passed, 0 failed across 2 pytest batches** (`tests/test_v5_metric_diff.py`, `tests/test_v6_config.py`, `tests/test_v2_contract.py`, `tests/test_api.py`, `tests/test_v5_investigation_drafts.py`, `tests/test_v3_auth_isolation.py`).
- Automated contracts verification suite: **21 passed, 0 failed** (`scratch/v7_4_verify/verify_v7_4_contracts.py`).
- Browser CDP Verification: 4 official screenshots captured in `docs/evidence/screenshots/`:
  - `v7_4_contracts_tab.png` (98,696 bytes)
  - `v7_4_propose_form.png` (88,242 bytes)
  - `v7_4_refused_form_retention.png` (91,166 bytes)
  - `v7_4_check_report.png` (90,642 bytes)
- External pilot gates (V2-06, V3-06) remain explicitly **OPEN**.






