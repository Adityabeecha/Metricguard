# V8 implementation and verification evidence

## V8 Relational Execution, Governed Contracts, and Join Evidence

Status: **GATED — 2 Application Defects Identified (Restore Relocation & 375px Responsive Overflow)**  
See the full acceptance report in [docs/evidence/v8_final_acceptance_report.md](evidence/v8_final_acceptance_report.md). Core execution gates passed across isolated Docker runner comparisons, independent integer oracle calculations, SQL 3-valued logic truth table evaluation, browser CDP verification, bit-for-bit check reopen invariance, and full regression suites. However, acceptance is gated by two concrete application defects: (1) un-relocated snapshot artifact paths upon project restore blocking isolated restored replay, and (2) 159px horizontal document blowout on 375px mobile viewports. External pilot gates (V2-06, V3-06) remain open; no external adoption or analyst-validation claim is made.

---

### 1. Implemented Boundaries

### V8.1 Relational Execution (`relational-snapshot-v1` / `relational_snapshot_v1`)
- **Contract & Execution Policy Distinction:** Governed under contract policy `relational-snapshot-v1`; comparisons, snapshot metadata, and run records execute under execution policy `relational_snapshot_v1`.
- **Model:** One fact table, one unique dimension table, qualified exact-decimal `SUM(f.measure)` measure declared with `DECIMAL(38, scale)`.
- **Joins:** Explicit `INNER JOIN` and `LEFT JOIN` on equality key `f.foreign_key = d.dimension_key`.
- **Proof of Grain:** Whole-table key uniqueness, dimension uniqueness, match counts, and zero-fanout verification before snapshot publication.
- **Container Isolation:** Executed exclusively via fixed Docker runner `metricguard-runner:1.0` in `METRICGUARD_MODE=isolated` (active verified image `sha256:1ed2112cd9d1f524b9c13e26b1b13c18c1a4c8b2ca2e15cf99e2e2d271c29b28`; historical initial build `sha256:15cbd74c...`).
- **Row Evidence:** Fact-grain evidence artifact with exact row deltas, signed gap, and zero residual for defined comparisons.
- **Arithmetic Boundaries (Isolated Application Runner):**
  - Wide valid gap: Baseline $-M$, Target $+M$ ($M = 10^{38} - 1$). Both endpoints valid; expected gap $2M = 19999999999999999999999999999999999998$, residual $0.00$, exact row reconciliation in evidence artifact.
  - Intermediate cancellation: Facts $+M, +1, -M$. Complete SUM = $1$ despite intermediate total exceeding endpoint capacity ($10^{38}$). Compared against supported filter selecting $+1$ fact; gap $0.00$, residual $0.00$.
  - Endpoint overflow refusal: Facts $+M, +1$. Application runner strictly refused endpoint SUM of $10^{38}$ with terminal state `failed` and structured error `ARITHMETIC_OVERFLOW` (*"Endpoint SUM exceeds the declared DECIMAL(38, scale) capacity."*).

### V8.2 Governed Relational Contracts (`metric-governance-v2`)
- **Schema-2 Expectations:** Governs business metrics bound to relational implementations (`fact`, `dimension`, `relationship`, `expected_fields`, `source_expectations`, `materiality_policy`).
- **Lifecycle & Multi-Role RBAC:** Proposal by analyst, explicit approval by owner with rationale, immutable audit trail.
- **Optimistic Concurrency:** Detects stale baseline updates (`STALE_METRIC_BASELINE`, HTTP 409) and stale contract head tokens (`STALE_METRIC_CONTRACT`, HTTP 409).
- **Check Invariance:** Metadata match check reports remain bit-for-bit identical when reopened across subsequent approved baseline promotions (`report_hash: 8816fc35...`).

### V8.3 Relational Join Evidence (`relational-join-evidence-v1`)
- **Route:** `GET /api/v1/comparisons/{run_id}/join-evidence?offset=0&limit=100`.
- **Summary & Independent Hash:** Independent SHA-256 over summary without `summary_hash` using sorted keys, compact separators, and UTF-8 encoding.
- **Side-by-Side Explanation:** Categorizes row outcomes into:
  - `included_value`
  - `included_null_measure`
  - `excluded_join`
  - `excluded_filter_false`
  - `excluded_filter_unknown`
- **SQL Three-Valued Logic:** Evaluates outer filters under `LEFT JOIN` where dimension attributes are NULL as `UNKNOWN`. `FALSE AND UNKNOWN` correctly resolves to `FALSE`. Observed `UNKNOWN` on rows F3/F4 in the LEFT JOIN filter case; INNER JOIN fixture proves `TRUE`/`FALSE` and `excluded_join` without claiming `UNKNOWN`.
- **Artifact Security & Upfront Verification:** Scans the full evidence artifact before returning any paged data. Refuses corrupted records outside the requested page with HTTP 422 `EVIDENCE_INTEGRITY`. Semantic validation rejects forged byte hashes.
- **Zero-Enqueue & Process Sampling:** Direct database inspection confirmed zero queue records were added during `/join-evidence` reads (43 runs, 4 imports before and after). Point-in-time container sampling observed zero running containers at observation points; these snapshots alone do not establish that no short-lived container existed between observations.
- **Snapshot Missing Safety:** If the underlying snapshot data file is deleted or moved, stored join evidence remains readable (`200 OK`), but replay is refused with HTTP 422 `SNAPSHOT_MISSING` (*"Stored results remain readable, but this snapshot is unavailable for replay."*).

---

### 2. Evidence Receipts

All formal receipts and test transcripts are preserved under `data/v8_final_acceptance/receipts/`:

| Receipt File | Gate | Status | Key Evidence |
| --- | --- | --- | --- |
| `receipt_01_startup_and_identities.json` | Startup & Identities | **PASS** | Isolated API (port 8001), supervisor worker, Vite frontend (port 5173), 4 provisioned synthetic identities, verified auth enforced |
| `receipt_02_relational_execution.json` | 5-Row Execution | **PASS** | Baseline 65.00, Target 10.00, Gap −55.00, Residual 0.00, 5 row deltas reconciled against standard-library oracle; replay identical SHA-256 (`36c87d23...`) |
| `receipt_03_relational_join_evidence.json` | Join Evidence & Edge Cases | **PASS** | `relational-join-evidence-v1`, independent `summary_hash` (`259529a4...`), 10 edge cases verified |
| `receipt_04_exports_browser_and_replay.json` | Exports, Browser CDP & Replay | **PASS** | JSON/HTML exports, snapshot missing safety, CDP browser verification of all 4 views |
| `receipt_05_boundaries_and_governance.json` | Boundaries & Governance | **PASS** | Scale/SQL/cross-workspace refusals, Schema-2 lifecycle, bit-for-bit check reopen (`8816fc35...`), 409 conflicts, project backup tar.gz |
| `receipt_runner_identity_and_replay.json` | Runner Image Identity & Fresh Replay | **PASS** | Inspected image `sha256:1ed2112c...` (built 2026-10-05T07:49:09Z); fresh comparison and replay bit-for-bit identical SHA-256 (`36c87d23...`) |
| `receipt_zero_enqueue_verification.json` | Queue Records & State Sampling | **PASS** | Direct DB counts before/after: 43 comparison runs (5 partial, 38 succeeded), 4 snapshot imports (4 validated). Zero analytical queries; point-in-time container sampling observed 0 containers |
| `receipt_browser_strengthened.json` | Corrected Browser CDP Observations | **GATED** | CDP Fetch interception: loading held pending (absence of "Complete artifact checked"), controlled 500 error banner with retry recovery; dual expanded predicate inspections (INNER JOIN: TRUE/FALSE; LEFT JOIN: UNKNOWN); exact SQL refusal (`UNSUPPORTED_SQL: Use one fact table, one dimension join and one SUM.`); **honest 375px overflow observed (+159px blowout, scrollWidth 534px vs clientWidth 375px)** |
| `receipt_export_and_restore.json` | Escaping, Exports & Restored Store Replay | **GATED** | HTML entity escaping verified for `<script>`, `<img>`, `<b>`, `"`, `'`, `&`; 120-row export capping; restored store on port 8002 reopens check `mcc-944f...` (`8816fc35...`) and investigation `21d7e74d...`; **replay blocked by snapshot path relocation defect in `services/api/backup.py:223-228` and `services/supervisor/worker.py:303`** |
| `receipt_uncovered_audit_claims.json` | Boundary Arithmetic & Coverage Audit | **PASS** | Actual isolated container runs: wide gap ($\pm M \to 2M$, residual 0), intermediate cancellation ($+M, +1, -M \to 1$, residual 0), endpoint overflow ($+M + 1 \to 10^{38}$ refused as `ARITHMETIC_OVERFLOW`); direct core checks: empty vs all-NULL aggregates, contract validation, tamper defenses |
| `pytest_transcript.txt` | Raw Pytest Regression Transcript | **PASS** | Complete console output on Python 3.12.5: 541 passed, 45 skipped (PostgreSQL unavailable), 8 warnings in 649.33s |
| `frontend_test_transcript.txt` | Raw Frontend Test Transcript | **PASS** | Node native runner output: 43 passed across 6 `.mjs` suites, 0 failed, 0 skipped |
| `frontend_build_transcript.txt` | Raw Frontend Build Transcript | **PASS** | Production build output: `tsc -b && vite build`, dist bundle generated in 13.77s |

---

### 3. Regression Evidence

- **Backend Pytest:** 541 passed, 45 skipped, 0 failed (`.venv\Scripts\python -m pytest -q -rs --basetemp .test-tmp/v8-evidence-correction` on Python 3.12.5).
  - *Skip Qualification:* Exactly 45 skips due to optional external PostgreSQL database at `postgresql://metricguard:***@127.0.0.1:5433/metricguard`.
  - *Docker Skips:* 0 skips.
- **Frontend Web Tests:** 43 passed, 0 failed, 0 skipped (`npm run test --prefix apps/web`).
  - Executed via Node native test runner (`node --experimental-strip-types`) across 6 `.mjs` suites:
    1. `src/v2Workflow.test.mjs` (5 tests)
    2. `src/router.test.mjs` (3 tests)
    3. `src/dataSources.test.mjs` (12 tests)
    4. `src/metricsLifecycle.test.mjs` (9 tests)
    5. `src/changes.test.mjs` (8 tests)
    6. `src/investigations.test.mjs` (6 tests)
- **Frontend Web Build:** Passed in 13.77s (`npm run build --prefix apps/web`), 0 errors.
- **Docker Compose:** Valid configuration (`docker compose -f infra/compose.yaml config --quiet`), exit code 0.
- **Live Docker Runner:** 7 live checks passed (`scripts.verify_live_runner`), exit code 0.

---

### 4. Remaining Acceptance Gaps & Repair Handoffs

| Gap | Actual Assertion | Result | Receipt Path | Remaining Blocker |
| --- | --- | --- | --- | --- |
| **Arithmetic Boundaries** | Isolated runner executes wide gap ($\pm M$), cancellation ($+M,+1,-M$), and refuses $+M+1=10^{38}$ with `ARITHMETIC_OVERFLOW` | **PASS** | `receipt_uncovered_audit_claims.json` | None. Executed via actual application workflow with declared `DECIMAL(38,0)`. |
| **Restored Store Replay** | Replay executed exclusively from restored files in disposable store without accessing primary store snapshot | **FAIL (Gated)** | `receipt_export_and_restore.json` | **Defect 1:** `services/api/backup.py:223-228` preserves original `artifact_path` instead of rebasing to `data_dir / 'snapshots'`; `services/supervisor/worker.py:303` resolves stored string directly. |
| **Browser CDP Interception & Refusal** | Hold pending (no checked label), controlled 500 error & recovery; assert exact refusal `UNSUPPORTED_SQL: Use one fact table, one dimension join and one SUM.` | **PASS** | `receipt_browser_strengthened.json` | None. All lifecycle and text preservation assertions satisfied. |
| **Expanded Predicate States** | INNER JOIN fixture proves TRUE/FALSE (no UNKNOWN); LEFT JOIN filter case proves UNKNOWN on F3/F4 | **PASS** | `receipt_browser_strengthened.json` | None. UNKNOWN evaluated and observed exclusively on LEFT JOIN filter case. |
| **Mobile 375px Layout** | `document.scrollWidth <= document.clientWidth + 1` at 375px mobile viewport | **FAIL (Gated)** | `receipt_browser_strengthened.json` | **Defect 2:** Horizontal page blowout (+159px; scrollWidth 534px vs clientWidth 375px) caused by non-wrapping grid layout in `.v2-grid`. |

#### Repair Handoff 1: Snapshot Artifact Path Relocation on Restore
- **Locations:** `services/api/backup.py` (lines 223–228) and `services/supervisor/worker.py` (line 303).
- **Problem:** `restore_project` copies physical `.duckdb` files to `data_dir / 'snapshots' / filename`, but writes snapshot records to the database with `item['data']['artifact_path']` still pointing to the original store. In addition, `Supervisor._execute` resolves `snapshot['artifact_path']` directly rather than rebasing relative to `self.data_dir / 'snapshots'`.
- **Narrow Fix:**
  1. In `restore_project`, when inserting snapshot records, rebase `item['data']['artifact_path']` to `str(data_dir / 'snapshots' / Path(item['data']['artifact_path']).name)`.
  2. In `Supervisor._execute`, resolve relative snapshot filenames against `self.data_dir / 'snapshots'`.

#### Repair Handoff 2: Responsive Viewport Horizontal Overflow at 375px
- **Locations:** `apps/web/src/views/RelationalInvestigations.tsx` and related styles in `apps/web/src/views/relationalEvidence.css` / `index.css`.
- **Problem:** `.v2-grid.two` uses rigid grid columns (`minmax` without wrapping or sub-375px media query collapse), pushing container scrollWidth to 534px.
- **Narrow Fix:** Add `@media (max-width: 600px)` breakpoint setting `.v2-grid.two { grid-template-columns: 1fr; }` and ensure `.table-scroll` elements have `max-width: 100%` and `overflow-x: auto` without blowing out parent page containers.

---

## V8 usability and portfolio closeout (2026-10-17)

Status: **Implemented, not yet verified.** Presentation, access-flow and packaging changes only; no change to relational/flat execution, contracts, evidence artifacts or authorization rules. Only the web production build (TypeScript check) and syntax checks were run. The open checks, expected outcomes and remaining gaps are listed in the [V8 closeout record](v8-closeout-record.md). The two gating defects above are not re-verified by this closeout: restored-case replay through Docker remains open (check R), and the 375px overflow should be re-checked against the new overflow rules in `apps/web/src/closeout.css` (check Q).
