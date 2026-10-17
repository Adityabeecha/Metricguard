# MetricGuard V8 Final Acceptance Report (V8.1, V8.2, V8.3)

**Date:** 2026-10-05  
**Execution Mode:** Local Isolated Container Execution (`METRICGUARD_MODE=isolated`)  
**Runner Image:** `metricguard-runner:1.0`  
- Active Inspected Image ID: `sha256:1ed2112cd9d1f524b9c13e26b1b13c18c1a4c8b2ca2e15cf99e2e2d271c29b28` (Created: `2026-10-05T07:49:09.401199024Z`)  
- Historical Initial Build ID: `sha256:15cbd74c34794fec24a372df6cb46ce451e64626f977184c8943d17bb69b12ac` (used during earlier `v8_live_retest`)  
- Replay Reproducibility across both builds: 100% bit-for-bit identical evidence SHA-256 (`36c87d2315eeae7cabf28709e4971cdf461909d8ca9b76a57813aaf295aa2365`)  
**Authentication Policy:** Verified API Key / Bearer Token Required (`METRICGUARD_ALLOW_UNVERIFIED_LOCAL=false`)  
**Acceptance Store:** `data/v8_final_acceptance/metricguard.db` (clean database, zero fixture-injected runs)  
**Evidence Receipts Directory:** `data/v8_final_acceptance/receipts/`  
**Browser Screenshots Directory:** `data/v8_final_acceptance/screenshots/`  
**Status:** **GATED — 2 Application Defects Identified (Restore Relocation & 375px Responsive Overflow)**

> [!WARNING]
> While core relational comparisons, independent integer oracle boundaries, join evidence truth table evaluations, bit-for-bit check reopen invariance, and full regression suites passed, final V8 acceptance is strictly gated on two concrete application defects:
> 1. Un-relocated snapshot artifact paths during project restore (`services/api/backup.py:223-228` and `services/supervisor/worker.py:303`), which blocks isolated restored replay.
> 2. Horizontal page blowout (+159px; scrollWidth 534px vs clientWidth 375px) on 375px mobile viewports caused by `.v2-grid.two`.
> Pilot gates V2-06 and V3-06 remain open; no external adoption claim is made.

---

## 1. Executive Summary

This report documents the synthetic engineering acceptance evaluation of MetricGuard V8, encompassing:
1. **V8.1 Relational Execution:** Contract policy `relational-snapshot-v1` and execution policy `relational_snapshot_v1`. One fact table, one unique dimension, qualified exact-decimal `SUM(f.measure)` declared with `DECIMAL(38, scale)`, declared `INNER` and `LEFT` equality joins, isolated Docker container execution, row-grain evidence artifacts, independent integer oracle boundary verification, and pinned replay reproducibility.
2. **V8.2 Governed Relational Contracts:** `metric-governance-v2` policy, Schema-2 relational expectations, multi-role authorization (owner/analyst/reader), optimistic concurrency conflict detection (`STALE_METRIC_BASELINE` and `STALE_METRIC_CONTRACT`, HTTP 409), metadata match checks, and bit-for-bit check reopen invariance across approved baseline changes.
3. **V8.3 Relational Join Evidence:** `relational-join-evidence-v1` explanation policy, side-by-side outcome explanation, SQL three-valued logic truth table evaluation (`TRUE`, `FALSE`, `UNKNOWN`), pagination with immutable `summary_hash`, upfront whole-artifact verification preventing partial delivery, unchanged queue record counts and point-in-time container sampling during evidence reads, and snapshot unavailability replay safety.
4. **Ordinary Browser UI Verification:** Automated headless Chrome CDP verification across all four evidence views (*What changed*, *Join proof*, *Supporting records*, *Provenance*), CDP Fetch interception for loading and controlled error states, exact SQL refusal retention, dual predicate state expansion (`TRUE`/`FALSE` on INNER JOIN, `UNKNOWN` on LEFT JOIN filter), and honest responsive measurement revealing a +159px mobile overflow defect.
5. **Full System Regressions:** Zero Docker skips, 541 pytest passes (all 45 skips qualified as PostgreSQL) on Python 3.12.5, 43 npm test passes across 6 Node `.mjs` suites, clean production frontend build, valid Docker Compose configuration, and live Docker supervisor verification.

---

## 2. Environment & Synthetic Identities

All checks were executed in a freshly initialized isolated store under `data/v8_final_acceptance`:

| Identity | Role | Workspace | Verified Capabilities | Key Mask |
| --- | --- | --- | --- | --- |
| **Owner Alpha** | `owner` | `ws-relational-final` | Full administrative, baseline promotion, contract approval, backup/restore | `mg_key_...` |
| **Analyst Beta** | `analyst` | `ws-relational-final` | Comparison execution, definition registration, contract proposal, investigation saving | `mg_key_...` |
| **Reader Gamma** | `reader` | `ws-relational-final` | Read-only inspection; mutations refused with HTTP 403 | `mg_key_...` |
| **Outsider Delta** | `analyst` | `ws-isolation-final` | Tenant-isolated; access to `ws-relational-final` refused with HTTP 403 | `mg_key_...` |
| **Unauthenticated** | none | none | Requests refused with HTTP 401 Unauthorized | N/A |

*Evidence Receipt:* `data/v8_final_acceptance/receipts/receipt_01_startup_and_identities.json`

---

## 3. Real Relational Execution Path (5-Row Fixture)

### 3.1 Independent Oracle vs Executed Results

An independent standard-library calculation (using Python's standard `decimal.Decimal` without referencing MetricGuard core code) served as the oracle:

| Metric | Independent Oracle | Executed Actual | Status |
| --- | --- | --- | --- |
| **Baseline Value** | `65.00` | `65.00` | **EXACT MATCH** |
| **Target Value** | `10.00` | `10.00` | **EXACT MATCH** |
| **Delta (Target − Baseline)** | `−55.00` | `−55.00` | **EXACT MATCH** |
| **Residual** | `0.00` | `0.00` | **EXACT MATCH** |
| **Row F1** | `0.00` | `0.00` | **EXACT MATCH** |
| **Row F2** | `−20.00` | `−20.00` | **EXACT MATCH** |
| **Row F3** | `−30.00` | `−30.00` | **EXACT MATCH** |
| **Row F4** | `−5.00` | `−5.00` | **EXACT MATCH** |
| **Row F5** | `0.00` | `0.00` | **EXACT MATCH** |

*Note on Row F5:* F5 has `amount = NULL`. In both baseline and target, F5 survives join and filter evaluation, but `SUM` ignores NULL. It contributes `0.00` bookkeeping delta with NULL source value, verified as distinct from a `0.00` source value.

### 3.2 Structural Join Proof

The isolated Docker ingestion worker inspected full tables and proved:
- `fact_rows`: 5
- `dimension_rows`: 2
- `matched_fact_rows`: 3 (F1 -> D1, F2 -> D2, F5 -> D1)
- `unmatched_fact_rows`: 2 (F3, F4)
- `null_foreign_key_rows`: 1 (F4 has empty `group_id`)
- `missing_nonnull_foreign_key_rows`: 1 (F3 has foreign key `DX`, which does not exist in dimension table)
- `max_dimension_matches_per_fact`: 1
- `fanout_rows`: 0
- `grain_preserved`: `true`
- `fact_key_unique`: `true`
- `dimension_key_unique`: `true`

### 3.3 Runner Identity & Pinned Replay Reproducibility

Two runner image builds were inspected and verified across historical and fresh runs:

| Run Type | Image ID | Run ID | Baseline | Target | Gap | Residual | Evidence SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Initial Acceptance** | `sha256:15cbd74c...` | `53415818-8c57-48ef-9fc5-c38156c631b8` | `65.00` | `10.00` | `−55.00` | `0.00` | `36c87d2315eeae7cabf28709e4971cdf461909d8ca9b76a57813aaf295aa2365` |
| **Initial Replay** | `sha256:15cbd74c...` | `ec33f48e-e427-403d-a717-5c914481dec9` | `65.00` | `10.00` | `−55.00` | `0.00` | `36c87d2315eeae7cabf28709e4971cdf461909d8ca9b76a57813aaf295aa2365` |
| **Verified Active Run** | `sha256:1ed2112c...` | `303c3a45-b302-47c3-869d-d4fb68c48ee2` | `65.00` | `10.00` | `−55.00` | `0.00` | `36c87d2315eeae7cabf28709e4971cdf461909d8ca9b76a57813aaf295aa2365` |
| **Verified Active Replay** | `sha256:1ed2112c...` | `66d5e260-74d4-40a5-a85c-e79e3c4a8170` | `65.00` | `10.00` | `−55.00` | `0.00` | `36c87d2315eeae7cabf28709e4971cdf461909d8ca9b76a57813aaf295aa2365` |

Both images produce 100% bit-for-bit identical results and hash invariance under isolated supervisor execution.

*Evidence Receipts:*
- `data/v8_final_acceptance/receipts/receipt_02_relational_execution.json`
- `data/v8_final_acceptance/receipts/receipt_runner_identity_and_replay.json`

---

## 4. V8.3 Relational Join Evidence Path & Edge Cases

### 4.1 Specification Verification

- **Route:** `GET /api/v1/comparisons/{run_id}/join-evidence?offset=0&limit=100`
- **Explanation Policy:** `relational-join-evidence-v1` (Schema version 1)
- **Summary Hash (Independent SHA-256):** `259529a46f63045b85d82b701974ec98a3472beaf0599f95611d0ff730c4f0c6`
- **Side Counts:**
  - Baseline: 4 `included_value`, 1 `included_null_measure`, 0 `excluded_join`, 0 `excluded_filter_false`, 0 `excluded_filter_unknown`
  - Target: 1 `included_value`, 1 `included_null_measure`, 2 `excluded_join`, 1 `excluded_filter_false`, 0 `excluded_filter_unknown`

### 4.2 Row-Grain SQL Three-Valued Logic Evaluation

| Fact Key | Baseline Outcome | Baseline WHERE | Target Outcome | Target WHERE | Explanation Reason |
| --- | --- | --- | --- | --- | --- |
| **F1** | `included_value` | `true` | `included_value` | `true` | Matched D1 ('north'), included in both |
| **F2** | `included_value` | `true` | `excluded_filter_false` | `false` | Matched D2 ('south'), excluded by `segment = 'north'` |
| **F3** | `included_value` | `true` | `excluded_join` | `not_evaluated` | Unmatched foreign key `DX` dropped by `INNER JOIN`; WHERE not evaluated |
| **F4** | `included_value` | `true` | `excluded_join` | `not_evaluated` | NULL foreign key dropped by `INNER JOIN`; WHERE not evaluated |
| **F5** | `included_null_measure` | `true` | `included_null_measure` | `true` | Matched D1 ('north'), included with NULL measure term |

### 4.3 Ten Edge Cases & Zero-Enqueue Verification

1. **Target LEFT JOIN with Dimension Filter:** When target is `LEFT JOIN dim_groups d ON ... WHERE d.segment = 'north'`, F3 and F4 survive the join with NULL dimension attributes. The predicate `NULL = 'north'` evaluates to `UNKNOWN`, excluding them with outcome `excluded_filter_unknown` and `where: unknown`. (**PASS**)
2. **Compound Filter with Unknown (`FALSE AND UNKNOWN`):** Adding `f.kind = 'web'` to that LEFT JOIN target makes `kind = 'web'` FALSE for store facts (F3, F4). In SQL three-valued logic, `FALSE AND UNKNOWN` evaluates to `FALSE`. Verified outcome becomes `excluded_filter_false` with `where: false`. (**PASS**)
3. **IN, IS NULL, IS NOT NULL:** Correctly evaluated across NULL categories and NULL foreign keys in exact 3-valued semantics. (**PASS**)
4. **All-NULL Endpoint SUMs:** Evaluated with state `partial`, preserved NULL values, and reconciliation marked `not_applicable`. (**PASS**)
5. **Pagination with 120-Fact Fixture:** 120 rows paginated across Page 1 (100) and Page 2 (20). Summary, counts, reconciliation, and `summary_hash` (`0a788a8b...`) remain 100% identical between pages. (**PASS**)
6. **Corrupted Record Outside Requested Page:** Modifying a record outside the requested page is caught during upfront verification; endpoint refuses partial return with HTTP 422 `EVIDENCE_INTEGRITY`. (**PASS**)
7. **Semantic Discrepancy with Recomputed Byte Hash:** Even when the artifact byte count and SHA-256 are maliciously updated to match corrupted file bytes, semantic validation refuses the artifact (`EVIDENCE_INVALID`). (**PASS**)
8. **Frozen Pin Invariance:** Tampering with `relational_binding` or context pins in the manifest causes upfront refusal (`EVIDENCE_INVALID`). (**PASS**)
9. **Queue Records & State Sampling on Evidence Reads:** Verified database state directly before and after evidence reads:
   - `SELECT state, COUNT(*) FROM comparison_runs GROUP BY state`: exactly 43 runs (5 `partial`, 38 `succeeded` before and after).
   - `SELECT state, COUNT(*) FROM snapshot_imports GROUP BY state`: exactly 4 imports (4 `validated` before and after).
   - Point-in-time container sampling: observed 0 active runner containers at observation points; these snapshots alone do not establish that no short-lived container existed between observations.
   - Execution distinction verified: SQLite metadata reads occur solely for authorization and pinned integrity; zero analytical DuckDB queries and zero supervisor jobs are enqueued. Artifact is streamed and validated directly. (**PASS**)
10. **Historical `/evidence` Endpoint:** Preserved `{items, total}` structure with exact row deltas. (**PASS**)

*Evidence Receipts:*
- `data/v8_final_acceptance/receipts/receipt_03_relational_join_evidence.json`
- `data/v8_final_acceptance/receipts/receipt_zero_enqueue_verification.json`

---

## 5. Exports, Ordinary Browser Behavior & Replay

### 5.1 JSON and HTML Exports & HTML Entity Escaping Verification

- **JSON Export (`/export?format=json`):** Verified presence of `join_proof`, `join_evidence.summary`, and `rows`. For the 120-row case, export capped at 100 rows with exact coverage metadata: `total_rows: 120`, `included_rows: 100`, `omitted_rows: 20`, `detail: 'first_rows'`.
- **HTML Export (`/export?format=html`):** Verified presence of join proof summary table, side-by-side contract/definition/snapshot pins, row table with badges, and escaping of strings.
- **Hostile Payload Escaping Assertions:** Tested with hostile payloads containing `<script>`, `onerror=`, `onload=`, `"`, `'`, and `&`. Verified specific character-by-character escaping to standard HTML entities (`&lt;script&gt;`, `&amp;`, `&quot;`, `&#x27;`). No unescaped executable script tags or attribute event handlers exist in output.

### 5.2 Snapshot Unavailability Replay Safety

- Moved snapshot artifact from disk.
- `GET /api/v1/comparisons/{run_id}/join-evidence`: returned HTTP 200 OK (stored evidence artifact remains readable).
- `POST /api/v1/investigations/{inv_id}/replays`: refused with HTTP 422 `SNAPSHOT_MISSING` and exact message:
  `"Stored results remain readable, but this snapshot is unavailable for replay."`
- Restored snapshot artifact: replay succeeded with HTTP 202.

### 5.3 Strengthened Ordinary Browser CDP Automation

Automated headless Chrome CDP execution verified the full UI workflow without header injection or Javascript bypassing:
1. **Unauthenticated Dashboard & Connect Control:** Initial status confirmed "Not connected". Opened modal, entered Analyst Beta API key, server confirmed role `Analyst Beta · analyst`.
2. **Four Views & Expanded Predicate States:**
   - *What changed:* Verified fact counts, changed inclusions, waterfall outcome table (`step5_01_waterfall.png`).
   - *Join proof:* Verified fact key uniqueness, dimension uniqueness, matches, and zero fanout (`step5_02_join_proof.png`).
   - *Supporting records (INNER JOIN fixture):* Expanded all 5 row explanations with exact observed predicate states (`TRUE`, `FALSE`, and `excluded_join` on F3/F4 without claiming `UNKNOWN`) (`step5_03_inner_join_supporting_records.png`).
   - *Supporting records (LEFT JOIN filter fixture):* Expanded rows proving `TRUE`, `FALSE`, and observed `UNKNOWN` on rows F3 and F4 where `d.segment` is NULL (`where_state: unknown`, `outcome: excluded_filter_unknown`) (`step5_04_left_join_filter_unknown_observed.png`).
   - *Provenance:* Verified immutable contract hash, engine version, snapshot manifest hash, and evidence hash (`step5_04_provenance.png`).
3. **CDP Fetch Interception & Error Recovery:**
   - *Loading state held pending:* Intercepted `/join-evidence` requests via CDP `Fetch.enable`. While held pending, verified heading displays `"Supporting records appear after the integrity check"` and `"Complete artifact checked"` is strictly absent (`step5_02a_loading_evidence_held.png`).
   - *Controlled 500 failure:* Failed request via CDP `Fetch.failRequest`. Verified visible error banner (`"Failed to fetch"`), presence of `"Retry evidence check"` button, and absence of `"Complete artifact checked"` (`step5_02b_controlled_failure.png`).
   - *Recovery:* Clicked retry, allowed request through via `Fetch.continueRequest`, verified heading updated to `"Complete artifact checked · supporting records paged"` (`step5_02c_recovery_success.png`).
4. **Responsive Viewport Measurements (Honest Overflow Observed):** Under simulated 375px mobile viewport, DOM metrics were measured directly without OR conditions:
   - `document.clientWidth`: 375px
   - `document.scrollWidth`: 534px (+159px horizontal blowout)
   - `window.innerWidth`: 534px
   - `visualViewport.width`: 375px
   - **Observed Defect:** Page-level horizontal blowout (+159px) caused by rigid column layout in `.v2-grid.two`. Internal table scrolling (`table_scroll_elements_count`: 9) works, but parent grid pushes document width to 534px (`step5_05_narrow_screen.png`).
5. **Refused SQL & State Retention:** Submitting invalid SQL (`SELECT f.amount FROM fact_items f WHERE non_existent_column_12345 = 1`) produced the exact server refusal banner (`"UNSUPPORTED_SQL: Use one fact table, one dimension join and one SUM."` via `[role="alert"]`) while preserving the exact submitted SQL verbatim in the textarea (`step5_06_refused_sql_preserved.png`).

*Evidence Receipts:*
- `data/v8_final_acceptance/receipts/receipt_04_exports_browser_and_replay.json`
- `data/v8_final_acceptance/receipts/receipt_browser_strengthened.json`

---

## 6. Boundaries, Governance Regressions, Invariance & Coverage Audit

### 6.1 Boundary & Refusal Checks

- Refused scale > precision (`scale: 10, precision: 5`): HTTP 422/400.
- Refused unsupported `RIGHT JOIN`: HTTP 422/400.
- Refused unsupported `AVG` aggregate: HTTP 422/400.
- Refused cross-workspace access by Outsider Delta: HTTP 403 Forbidden.
- Refused definition mutation by Reader Gamma: HTTP 403 Forbidden.

### 6.2 Governed Contract Lifecycle & Bit-for-Bit Reopen Invariance

1. Created business metric `metric-b6155673e2a040e18bc0e4fc4fed8711`.
2. Bound and approved baseline definition version `a67f38a4...`.
3. Analyst proposed Schema-2 relational expectations (`mcr-55a06b06...`).
4. Owner approved revision with rationale.
5. Saved metadata check `mcc-944f4f668f8243e1bf70357501cc9ba4` with report hash:
   `8816fc35ebb03471ae7d3acbfbf44bc28e336a437adb5275cecce609637e1c30`
6. Promoted target definition version `cdce6148...` as **new baseline**. Verified state transition recorded.
7. **Bit-for-Bit Check Reopen:** Reopened original check `mcc-944f4f66...`:
   - Check ID: `mcc-944f4f668f8243e1bf70357501cc9ba4` (identical)
   - Report Hash: `8816fc35ebb03471ae7d3acbfbf44bc28e336a437adb5275cecce609637e1c30` (bit-for-bit identical)
   - Bound Definition Version: `a67f38a4...` (remains pinned to the original version)
8. **Optimistic Concurrency:**
   - Proposing with old baseline version returned HTTP 409 `STALE_METRIC_BASELINE`.
   - Proposing with stale head token returned HTTP 409 `STALE_METRIC_CONTRACT`.

### 6.3 Backup and Restore Integrity

- Owner triggered `POST /api/v1/projects/{pid}/backup`.
- Archive created: 62,568 bytes, 51 archive records.
- Verified valid `tar.gz` format containing manifests, definitions, snapshots, runs, evidence, and investigations.
- **Disposable Restore Verification & Relocation Check:**
  - Restored archive into disposable test store `data/v8_disposable_restore_test`.
  - Started isolated API and supervisor processes bound to port 8002 and `data/v8_disposable_restore_test`.
  - Reopened governance check `mcc-944f4f668f8243e1bf70357501cc9ba4` in the restored environment: HTTP 200 OK, report hash computed to bit-for-bit identical `8816fc35ebb03471ae7d3acbfbf44bc28e336a437adb5275cecce609637e1c30`.
  - Reopened saved investigation `21d7e74d-32be-49c4-9699-06944b0299e1`: HTTP 200 OK.
  - Queued replay run `084e0970-7248-45be-87db-c1f1f9ef9f91`.
  - **Identified Production Relocation Defect:** Verified that snapshot records in SQLite preserve original `artifact_path` (pointing to primary store `data\v8_final_acceptance\snapshots\...`) rather than rebasing to `data\v8_disposable_restore_test\snapshots\...`. In addition, `Supervisor._execute` resolves this stored path directly. Consequently, restored execution cannot execute exclusively from restored files without reading from the primary store. Per instructions, the primary store was untouched, database paths were not manually patched, and the disposable store was retained on disk for verification.

### 6.4 Audit of Boundary Arithmetic & Uncovered Claims

All checks clearly distinguish between **actual isolated application execution** (full asynchronous container workflow with declared SQL schema) and **direct core function checks**:

#### Actual Isolated Application Execution (`METRICGUARD_MODE=isolated`)
- **Schema & Contract Declaration:** Registered contract with `precision: 38, scale: 0` (`DECIMAL(38,0)`).
- **Independent Integer Oracle:** Let $M = 10^{38} - 1 = 99999999999999999999999999999999999999$.
- **Wide Valid Gap:** Baseline $-M$, Target $+M$. Both endpoints are valid integers within `DECIMAL(38,0)`. The isolated container runner computed:
  - Baseline: $-M$
  - Target: $+M$
  - Expected Gap: $2M = 19999999999999999999999999999999999998$
  - Residual: $0.00$
  - Row reconciliation: exact matching across row deltas in the generated evidence artifact. (**PASS**)
- **Intermediate Cancellation:** Facts ordered with measures $+M, +1, -M$. The complete SUM equals $1$ despite an intermediate total exceeding endpoint capacity ($10^{38}$). Compared against a supported filter selecting the $+1$ fact; gap $0.00$, residual $0.00$. (**PASS**)
- **Endpoint Overflow Refusal:** Facts ordered with measures $+M, +1$. The actual application strictly refused the endpoint SUM of $10^{38}$. The test harness captured the terminal job state `failed` and structured error `ARITHMETIC_OVERFLOW` (*"Endpoint SUM exceeds the declared DECIMAL(38, scale) capacity."*) without raising ValueError itself. (**PASS**)

#### Direct Core Function Checks
- **Empty Aggregates vs All-NULL Aggregates:** Direct invocation of `compare_tables`:
  - Empty aggregate: 0 included rows, target value `None`, status `undefined`, explanation `unavailable`.
  - All-NULL aggregate: 2 included rows with `target_null_term=True`, target value `None`, status `undefined`, row-grain explanations available with NULL measure terms.
- **Ingestion & Registration Rejections:**
  - Duplicate fact key rejected.
  - NULL fact key rejected.
  - Duplicate dimension key rejected.
  - Invalid decimal amount string rejected.
  - Missing referenced fields (e.g. `fact.nonexistent_column`) rejected with HTTP 422 `UNSUPPORTED_SQL`.
- **Tampering Defenses:**
  - Modifying frozen contract in artifact rejected by `verify_artifact`.
  - Modifying row deltas in artifact rejected by `verify_artifact`.
  - Modifying definition IR in artifact rejected by `verify_artifact`.
- **Authorization & Historical Endpoints:**
  - Reader mutation attempt refused with HTTP 403 Forbidden.
  - Outsider tenant workspace access refused with HTTP 403 Forbidden.
  - Historical `/evidence` endpoint verified functional and consistent with row deltas.

*Evidence Receipts:*
- `data/v8_final_acceptance/receipts/receipt_05_boundaries_and_governance.json`
- `data/v8_final_acceptance/receipts/receipt_export_and_restore.json`
- `data/v8_final_acceptance/receipts/receipt_uncovered_audit_claims.json`

---

## 7. Full Regression Results

All regression suites executed sequentially with 100% pass rates on Python 3.12.5 and Node native test runner:

| Suite | Command | Result | Notes | Transcript Receipt |
| --- | --- | --- | --- | --- |
| **Backend Pytest** | `.venv\Scripts\python -m pytest -q -rs --basetemp .test-tmp/v8-evidence-correction` | **541 passed, 45 skipped, 8 warnings (649.33s)** | All 45 skips qualified as external PostgreSQL database tests (`postgresql://metricguard:***@127.0.0.1:5433/metricguard`); 0 Docker runner skips | `receipts/pytest_transcript.txt` |
| **Frontend Tests** | `npm run test --prefix apps/web` | **43 passed, 0 failed, 0 skipped** | Node native runner with `--experimental-strip-types` across 6 `.mjs` suites: `v2Workflow.test.mjs` (5), `router.test.mjs` (3), `dataSources.test.mjs` (12), `metricsLifecycle.test.mjs` (9), `changes.test.mjs` (8), `investigations.test.mjs` (6) | `receipts/frontend_test_transcript.txt` |
| **Frontend Build** | `npm run build --prefix apps/web` | **0 errors (13.77s)** | Clean production bundle generated via `tsc -b && vite build` | `receipts/frontend_build_transcript.txt` |
| **Docker Compose** | `docker compose -f infra/compose.yaml config --quiet` | **Exit code 0** | Valid configuration | Preserved in receipts |
| **Live Runner** | `.venv\Scripts\python -m scripts.verify_live_runner --output data/evaluation/v8_final_live_runner_report.json` | **Exit code 0 (7 checks passed)** | Verified live Docker container isolation and execution | `data/evaluation/v8_final_live_runner_report.json` |

---

## 8. Preserved Policies & Handoff to V9

All existing policies and invariants are strictly preserved for V9:
- **Contract Policies:**
  - `flat-snapshot-v1` (V2/V3/V4)
  - `flat-boolean-v1` (V7.1)
  - `flat-subtraction-v1` (V7.2)
  - `flat-case-v1` (V7.3)
  - `relational-snapshot-v1` (V8.1)
- **Execution Policies:**
  - `flat_snapshot_v1` (V2/V3/V4/V7)
  - `relational_snapshot_v1` (V8.1)
- **Governance Policies:**
  - `metric-governance-v1` (V7.4, Schema-1 flat expectations)
  - `metric-governance-v2` (V8.2, Schema-2 relational expectations)
- **Explanation Policies:**
  - `relational-join-evidence-v1` (V8.3)

---

## 9. Remaining Acceptance Gaps & Repair Handoffs

| Gap | Actual Assertion | Result | Receipt Path | Remaining Blocker |
| --- | --- | --- | --- | --- |
| **Arithmetic Boundaries** | Isolated runner executes wide gap ($\pm M$), cancellation ($+M,+1,-M$), and refuses $+M+1=10^{38}$ with `ARITHMETIC_OVERFLOW` | **PASS** | `receipt_uncovered_audit_claims.json` | None. Executed via actual application workflow with declared `DECIMAL(38,0)`. |
| **Restored Store Replay** | Replay executed exclusively from restored files in disposable store without accessing primary store snapshot | **FAIL (Gated)** | `receipt_export_and_restore.json` | **Defect 1:** `services/api/backup.py:223-228` preserves original `artifact_path` instead of rebasing to `data_dir / 'snapshots'`; `services/supervisor/worker.py:303` resolves stored string directly. |
| **Browser CDP Interception & Refusal** | Hold pending (no checked label), controlled 500 error & recovery; assert exact refusal `UNSUPPORTED_SQL: Use one fact table, one dimension join and one SUM.` | **PASS** | `receipt_browser_strengthened.json` | None. All lifecycle and text preservation assertions satisfied. |
| **Expanded Predicate States** | INNER JOIN fixture proves TRUE/FALSE (no UNKNOWN); LEFT JOIN filter case proves UNKNOWN on F3/F4 | **PASS** | `receipt_browser_strengthened.json` | None. UNKNOWN evaluated and observed exclusively on LEFT JOIN filter case. |
| **Mobile 375px Layout** | `document.scrollWidth <= document.clientWidth + 1` at 375px mobile viewport | **FAIL (Gated)** | `receipt_browser_strengthened.json` | **Defect 2:** Horizontal page blowout (+159px; scrollWidth 534px vs clientWidth 375px) caused by non-wrapping grid layout in `.v2-grid`. |

### Repair Handoff 1: Snapshot Artifact Path Relocation on Restore
- **Locations:** `services/api/backup.py` (lines 223–228) and `services/supervisor/worker.py` (line 303).
- **Problem:** `restore_project` copies physical `.duckdb` files to `data_dir / 'snapshots' / filename`, but writes snapshot records to the database with `item['data']['artifact_path']` still pointing to the original store. In addition, `Supervisor._execute` resolves `snapshot['artifact_path']` directly rather than rebasing relative to `self.data_dir / 'snapshots'`.
- **Narrow Fix:**
  1. In `restore_project`, when inserting snapshot records, rebase `item['data']['artifact_path']` to `str(data_dir / 'snapshots' / Path(item['data']['artifact_path']).name)`.
  2. In `Supervisor._execute`, resolve relative snapshot filenames against `self.data_dir / 'snapshots'`.

### Repair Handoff 2: Responsive Viewport Horizontal Overflow at 375px
- **Locations:** `apps/web/src/views/RelationalInvestigations.tsx` and related styles in `apps/web/src/views/relationalEvidence.css` / `index.css`.
- **Problem:** `.v2-grid.two` uses rigid grid columns (`minmax` without wrapping or sub-375px media query collapse), pushing container scrollWidth to 534px.
- **Narrow Fix:** Add `@media (max-width: 600px)` breakpoint setting `.v2-grid.two { grid-template-columns: 1fr; }` and ensure `.table-scroll` elements have `max-width: 100%` and `overflow-x: auto` without blowing out parent page containers.

---

**Final Verdict:** **GATED — Core functionality proven and verified across isolated containers and independent oracles; formal release acceptance is gated by the two concrete application defects above.**
