# MetricGuard V7.3 Restricted CASE WHEN & Investigation Persistence Acceptance Report

**Date:** 2026-10-04  
**Scope:** V7.3 Engineering Preview — Restricted Searched `CASE WHEN` (`flat-case-v1` + `flat-boolean-v1`, FlatContract v7) and Investigation Persistence / Async Robustness  
**Classification:** Speculative engineering preview only (external pilot gates V2-06 and V3-06 remain explicitly **OPEN**; no customer adoption, production pilot justification, or analyst time-savings claims are made).  
**Codebase Policy:** Verification conducted strictly without application code modifications, SQL broadening, or V8 work. Test harnesses, receipts, screenshots, and evidence documentation created in dedicated isolated directories.

---

## 1. Executive Summary & Defect / Finding Catalog

A comprehensive synthetic engineering acceptance evaluation of MetricGuard V7.3 was performed against a dedicated temporary SQLite database (`data/v7_3_verify_run/metricguard.db`), a clean data directory (`data/v7_3_verify_run/`), the live API (`http://127.0.0.1:8001`), the trusted supervisor worker, and the freshly rebuilt Docker container runner (`metricguard-runner:1.0`, image ID `sha256:4e66c3bf142623274ab2a29af17145edcffae77895eff949fd0312f3b2574cc6`).

The evaluation verified all six required verification tracks:
1. **Isolated Workflow:** 5-row primary fixture ingested under FlatContract v7 (`measure_policy: flat-case-v1`, `predicate_policy: flat-boolean-v1`). Executed baseline `SUM(CASE WHEN flag IS TRUE THEN amount ELSE 0 END)` (50.00) vs target `SUM(CASE WHEN flag IS NOT FALSE THEN amount ELSE 0 END)` (80.00) with delta +30.00 and zero residual. Under SQL 3-valued logic, only row `c` (`flag=NULL`) transitioned from the ELSE branch to the THEN branch (+30.00 contribution). Filtered `WHERE region = 'prod'` produced baseline 10.00, target 40.00, delta +30.00, zero residual, with row `e` diagnostic branches/terms retained while `where_state: false`. Saved investigation reopened and replayed with bitwise matching evidence SHA-256.
2. **Semantics & Boundaries:** All 8 semantic boundary cases passed (empty selection produces NULL SUM; all-ELSE-0 produces defined 0.00; TRUE branches with only NULL measures produce NULL SUM; NULL term plus ELSE-0 produces defined 0.00; undefined endpoints maintain undefined gap/residual across save/reopen/replay; large exact integers > $2^{53}$ and exact cancellation; precision/scale bounds enforced without rounding). All 20 forbidden SQL constructs were refused with HTTP 422 (`UNSUPPORTED_SQL`). Tree leaf limits (24 accepted, 26 refused) were verified.
3. **Evidence Trust & Replay:** Baseline artifact verified; 10 forged/tampered evidence vectors with recomputed SHA-256 hashes rejected with `EVIDENCE_INVALID`. Rebinding attacks across contracts and definitions rejected. Bounded pagination and JSON/HTML exports verified. Cross-policy submissions refused.
4. **Browser CDP Execution & Async Persistence Fixes:** Real Chrome/Edge browser workflows exercised via CDP. 6 official screenshots captured. Rapid title/notes edits save latest values; version token optimistic locking returns HTTP 409 `STALE_DRAFT` and prevents silent overwrites; invalid SQL remains verbatim across refusal and draft reopen; cross-workspace draft access blocked (HTTP 403); deleted drafts return 404; saved-case reopen and pinned replay confirmed.
5. **Full Regressions:** 586 backend pytest tests passed in 486.54s (including 45 PostgreSQL collector and integration tests against `metricguard-test-pg`). Web bundle built cleanly (`npm run build`). Docker compose configuration valid.

### Findings & Resolution of Frontend Test Gaps

```
========================================================================================
FINDINGS & RESOLUTION OF FRONTEND TEST GAPS
========================================================================================
1. Stale Frontend Assertion on Undefined Residual (RESOLVED):
   - File: apps/web/src/investigations.test.mjs (line 52)
   - Original Stale Assertion: assert.equal(classifyResidual(null), 'No unexplained residual')
   - Intended Semantics: When aggregate endpoints or the gap are undefined (null), residual is
     mathematically not applicable. Conflating undefined residual with zero ('No unexplained residual')
     contradicts V6, V7.1, V7.2, and V7.3 mathematical specification requirements.
   - Resolution: Corrected null and empty-string expectations to 'Residual not applicable';
     added undefined-input assertion expecting 'Residual not applicable'; preserved all zero
     ('0.00', '0') and nonzero ('12.50', '-5.00') assertions. Application behavior was
     strictly preserved without modification.
   - Re-run Result: `npm run test --prefix apps/web` passed cleanly with exit code 0
     (43 passed, 0 failed across all 6 test suites).

2. Draft Landing List Navigation Isolation:
   - Investigation drafts are project-scoped and isolated under /api/v1/projects/{pid}/investigation-drafts.
   - In browser tests, drafts created in one project were verified to return HTTP 403 / 404
     when accessed from another project, ensuring multi-tenant project isolation.
========================================================================================
```

---

## 2. Acceptance Group Verification Matrix

| Track | Description | Status | Verification Engine | Summary of Results |
| :--- | :--- | :--- | :--- | :--- |
| **Track 1** | Isolated Workflow & Docker Runner | **PASS** | Live API & Docker Runner | Ingested 5-row fixture under FlatContract v7. Unfiltered comparison: 50.00 vs 80.00, Delta +30.00, Residual 0 (only row c contributes). Filtered comparison (`WHERE region = 'prod'`): 10.00 vs 40.00, Delta +30.00, Residual 0. Saved investigation reopened and replayed with bitwise matching evidence SHA-256. |
| **Track 2** | Semantics & Boundary Limits | **PASS** | Live API & DuckDB Core | 8/8 semantic checks passed (empty selection -> NULL SUM; all-ELSE-0 -> defined 0.00; TRUE branches with only NULL measures -> NULL SUM; NULL term + ELSE-0 -> defined 0.00; undefined endpoints maintain undefined gap/residual and replay identically; large exact integers > $2^{53}$ and exact cancellation; precision/scale bounds). 20/20 SQL/IR refusals returned HTTP 422. 24 leaves accepted, 26 leaves refused. |
| **Track 3** | Evidence Trust & Replay | **PASS** | Supervisor Worker & Evidence Validator | Baseline artifact verified. 10 forged/tampered evidence rows with valid recomputed SHA-256 rejected with `EVIDENCE_INVALID`. Rebinding attacks rejected. Bounded pagination (offsets 0, 2, 4) and JSON/HTML export verified. Cross-policy submissions blocked. |
| **Track 4** | Browser CDP & Async Persistence Fixes | **PASS** | Chrome CDP, Live Web UI, API Store | 6 screenshots captured in `docs/evidence/screenshots/`. Rapid edits persist latest values; HTTP 409 `STALE_DRAFT` conflict detection blocks concurrent overwrites; verbatim invalid SQL preserved; draft deletion blocks delayed autosaves; saved cases reopen and replay. |
| **Track 5** | Full Regression Suite | **PASS** | Pytest, Vite Build, Compose Config | Pytest: **586 passed, 0 failed, 8 warnings** in 486.54s (including 45 PostgreSQL tests). Web build: passed (1595 modules). Docker compose config: passed (exit 0). Web unit tests: **43 passed, 0 failed across all 6 test suites (exit code 0)**. |

---

## 3. Environment & Execution Identity

- **Dedicated Test Environment:**
  - Workspace: `data/v7_3_verify_run/`
  - SQLite Database: `sqlite:///data/v7_3_verify_run/metricguard.db`
  - Supervisor API: `http://127.0.0.1:8001` (`METRICGUARD_MODE=isolated`)
  - Dedicated Project: `local-retail` (`87f0b9f9-dbfb-474d-944f-37c2bf760e46`)
  - Test Member API Key: `[REDACTED]`
- **Docker Runner Container Image:**
  - Build Command: `docker build -f infra/Dockerfile.runner -t metricguard-runner:1.0 .`
  - Image Tag: `metricguard-runner:1.0`
  - Image ID: `sha256:4e66c3bf142623274ab2a29af17145edcffae77895eff949fd0312f3b2574cc6`
  - Container Configuration: `4410ced0283281aa06a440156a5dc2782b8813bc30e463a562ef648df483bcf3`
  - Security Profile: `--network none`, `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, user `10001:10001`
- **PostgreSQL Test Container:**
  - Container Name: `metricguard-test-pg`
  - Image: `postgres:16-alpine`
  - Port: `127.0.0.1:5433->5432/tcp`

---

## 4. Canonical CASE IR from Current Source

In the implemented source (`packages/metricguard_core/flat_case.py`), canonical CASE IR is defined and validated by `parse_case()` and `check_case_ir()`. It does not use the earlier speculative shape with multiple branches or root-level measure fields.

### Implemented Canonical CASE IR Schema

```json
{
  "mode": "flat_snapshot",
  "policy": "flat_snapshot_v1",
  "contract_version": 7,
  "contract_hash": "a6043605007f6bf182ae1c8a551f106df60a8285eb4d4ca9a20083d0472a287c",
  "measure_policy": "flat-case-v1",
  "predicate_policy": "flat-boolean-v1",
  "table": "snapshot",
  "aggregate": "sum",
  "measure_expression": {
    "op": "case",
    "when": {
      "op": "predicate",
      "predicate": {
        "field": "flag",
        "op": "is_true",
        "values": [],
        "type": "boolean"
      }
    },
    "then": "amount",
    "else": "0"
  },
  "predicate_tree": null,
  "ir_hash": "be380b29dce29df7570bbf27778a67d60e125bb613e0ea27c69031f615fba1e9"
}
```

### Key Differences from Earlier Proposed Shapes
1. **`measure_expression` Envelope:** The CASE structure is encapsulated inside `measure_expression` with mandatory fields `{'op': 'case', 'when': <tree>, 'then': <measure_col>, 'else': '0'}`. Earlier drafts proposed putting `branches: [...]` or placing `when` at the IR root.
2. **Mandatory String `'0'` in `else`:** The `else` field must strictly be the string `'0'`. Non-zero numeric values, NULL, expressions, or omitted ELSE clauses are refused with `unsupported_sql` (HTTP 422).
3. **Decoupled `predicate_tree` for Outer `WHERE`:** The outer filter lives in `ir['predicate_tree']` (a canonical boolean tree or `null`), completely decoupled from `measure_expression['when']`.
4. **Complexity Caps:** Combined nodes across `measure_expression['when']` and `ir['predicate_tree']` must satisfy $\le 96$ nodes and $\le 24$ leaf occurrences. The overall SQL AST cap is 250 nodes.

---

## 5. Detailed Track Findings & Execution Receipts

### Track 1: Isolated Workflow & Docker Runner Verification (PASS)

- **Receipt:** `scratch/v7_3_fresh_receipts/section1_receipt.json`
- **FlatContract v7 Registration:**
  - Contract ID: `2f48474f-984d-4495-b979-1a137fa94414`
  - Contract Hash: `a6043605007f6bf182ae1c8a551f106df60a8285eb4d4ca9a20083d0472a287c`
  - Key Column: `id`, Measure Column: `amount DECIMAL(10,2)`
  - Measure Policy: `flat-case-v1`, Predicate Policy: `flat-boolean-v1`
  - Unit Label: `"unknown currency"`, Null Policy: `"native_null"`
- **Snapshot Ingestion:**
  - Snapshot ID: `07dd8fb1-c8fd-4639-97e3-75286056fced`
  - Source File: 5-row primary fixture (`a`, `b`, `c`, `d`, `e`)
- **Unfiltered Execution (Run `99e04277-5765-45d1-a3d1-c3ea96807f2f`):**
  - Baseline Definition: `SELECT SUM(CASE WHEN flag IS TRUE THEN amount ELSE 0 END) FROM snapshot`
  - Target Definition: `SELECT SUM(CASE WHEN flag IS NOT FALSE THEN amount ELSE 0 END) FROM snapshot`
  - Baseline Total: `50.00`, Target Total: `80.00`, Delta: `+30.00`, Residual: `0`
  - Exact Row Membership and Contributions:
    - Row `a` (`10.00, true, prod`): Baseline THEN (10.00), Target THEN (10.00), Delta 0.00
    - Row `b` (`20.00, false, prod`): Baseline ELSE (0.00), Target ELSE (0.00), Delta 0.00
    - Row `c` (`30.00, NULL, prod`): `NULL IS TRUE` evaluates to `FALSE` $\to$ Baseline ELSE (0.00); `NULL IS NOT FALSE` evaluates to `TRUE` $\to$ Target THEN (30.00). Delta = **+30.00**. Only row `c` contributes to the gap!
    - Row `d` (`NULL, true, prod`): Baseline THEN (NULL term, contribution 0.00), Target THEN (NULL term, contribution 0.00), Delta 0.00
    - Row `e` (`40.00, true, test`): Baseline THEN (40.00), Target THEN (40.00), Delta 0.00
- **Filtered Execution with `WHERE region = 'prod'` (Run `63c7ba0c-15a0-43b8-80df-5fbfa0fa392d`):**
  - Baseline Total: `10.00`, Target Total: `40.00`, Delta: `+30.00`, Residual: `0`
  - Row `e` outer-excluded (`where_state: false`, contribution 0.00, diagnostic branch and term preserved)
- **Saved Investigation Reopen & Replay:**
  - Saved Investigation ID: `ba986351-6e6e-4467-b28f-10a2a30794cf`
  - Replay Run ID: `1f893e58-f9b7-4c31-97b4-2dfb9d45464a`
  - Evidence Artifact SHA-256: `a4205ea85c890e49578506b8ae975ce6989ea306ab55330107c150882f255fd4` (100% bitwise identical between run and replay)

### Track 2: Semantics & Boundaries (PASS)

- **Receipt:** `scratch/v7_3_fresh_receipts/section2_receipt.json`
- **8 Semantic Validation Cases:**
  1. *Empty selection produces NULL SUM:* `WHERE region = 'nonexistent'` $\to$ `baseline: null`, `target: null`, `delta: null`, `residual: null` (`value_status: undefined`).
  2. *All-ELSE-zero produces defined 0.00:* `WHERE region = 'prod'` with `flag = false` (all rows select ELSE 0) $\to$ `baseline: 0.00`, `target: 0.00`, `delta: 0.00`, `residual: 0`.
  3. *TRUE branches with only NULL measures produce NULL SUM:* Selection containing only row `d` (`amount = NULL, flag = true`) $\to$ `baseline: null`, `target: null`, `delta: null`, `residual: null`.
  4. *NULL term plus ELSE-zero produces defined 0.00:* Selection of row `b` (`amount=20, flag=false` $\to$ ELSE 0) and row `d` (`amount=NULL, flag=true` $\to$ THEN NULL) $\to$ SQL SUM treats NULL as non-contributing and adds 0.00 $\to$ `baseline: 0.00`, `target: 0.00`, `delta: 0.00`, `residual: 0`.
  5. *Undefined endpoints keep gap/residual undefined:* Confirmed `delta: null`, `residual: null`.
  6. *Undefined partial results can be saved and replayed:* Investigation `35a4d0cb-674e-48a5-926c-d2ff2ff8aa80` saved and replayed as run `772b7a86-7782-4f2d-8e40-27c10b776495` with identical undefined endpoints.
  7. *Negative amounts, large values (> $2^{53}$), exact cancellation:* Evaluated on fixture with values `9007199254740842.2500` and `-9007199254740842.2500` $\to$ Baseline `9007199254740842.2500`, Target `50.1250`, Delta `-9007199254740792.1250`, Residual `0`. Cancellation to exactly `0.0000` verified with zero rounding.
  8. *Precision & scale bounds:* Declaring precision $>38$ or scale $>$ precision refused with HTTP 422 without rounding.
- **20 SQL Refusals (HTTP 422):**
  - Multiple WHEN branches, simple CASE (`CASE flag WHEN true...`), omitted ELSE, non-zero ELSE (`ELSE 1`), string ELSE (`ELSE 'zero'`), NULL ELSE (`ELSE NULL`), nested CASE, branch arithmetic (`THEN amount + 1`), branch functions (`THEN ABS(amount)`), plain SUM (`SUM(amount)` under CASE policy), unregistered identifiers, joins, subqueries, extra aggregates (`COUNT(amount)`). All refused with HTTP 422.
- **Tree Limits:**
  - 24 combined leaves accepted (HTTP 201).
  - 26 combined leaves refused with HTTP 422 (`CASE WHERE and WHEN together exceed 96 nodes or 24 leaves`).

### Track 3: Evidence Trust and Replay (PASS)

- **Receipt:** `scratch/v7_3_fresh_receipts/section3_receipt.json`
- **Tampering & Forgery Defenses (10 Attack Vectors):**
  - All 10 tampered evidence files had their SHA-256 checksums recalculated to defeat naive hash matching.
  - Tested attacks:
    1. Forged `delta` discrepancy
    2. Inconsistent `membership` flag
    3. Falsified `baseline_when_state` (`unknown` while `flag=true`)
    4. Inconsistent `baseline_branch` (`else` while `when_state=true`)
    5. Inconsistent `baseline_case_term` (`0.00` while `branch=then`)
    6. Forged `baseline_contribution`
    7. False `baseline_null_term` flag
    8. Altered `measure_value` input
    9. Missing mandatory CASE field (`baseline_when_state` omitted)
    10. Injected illegal field (`forged_column: 123`)
  - **Result:** All 10 tampering vectors rejected with `EVIDENCE_INVALID` by the supervisor evidence validator.
- **Rebinding & Policy Defenses:**
  - Tampered `contract_hash`, `baseline_ir_hash`, or `target_ir_hash` in `case_binding` rejected with `EVIDENCE_INVALID`.
  - Missing trusted context rejected with `EVIDENCE_INVALID`.
  - Cross-policy submissions (submitting CASE IR to contract v4/v5/v6, or submitting subtraction/plain SUM to contract v7) refused with HTTP 422.
- **Source Authenticity Boundary:**
  - As enforced in `packages/metricguard_core/flat_case_evidence.py`, typed artifact inputs do NOT independently prove source authenticity. The supervisor's immutable snapshot/source checks and published artifact digests remain the authoritative source-authenticity boundary.
- **Bounded Pagination & Export:**
  - Evidence pagination verified across offsets 0, 2, and 4 (limit 2).
  - Export to JSON and HTML verified with aligned CASE headers (`Row Identifier`, `WHERE membership`, `Source measure`, `Baseline CASE`, `Target CASE`, `Baseline inclusion / contribution`, `Target inclusion / contribution`, `Row Delta`).

### Track 4: Browser CDP Execution & Async Persistence Fixes (PASS)

- **Receipts:**
  - `scratch/v7_3_fresh_receipts/section4_async_receipt.json`
  - `scratch/v7_3_fresh_receipts/section4_browser_receipt.json`
- **6 Verified Browser Screenshots in `docs/evidence/screenshots/`:**
  1. `v7_3_onboarding.png`: Data Sources file upload and Step 2 contract confirmation.
  2. `v7_3_comparison.png`: Comparison Studio with 3-card KPI strip (`50.00`, `80.00`, `+30.00 unknown currency`) and Value Bridge.
  3. `v7_3_evidence.png`: Evidence tab with aligned CASE columns, WHEN truth states, branches, and row contributions.
  4. `v7_3_refusal.png`: Ad-hoc editor refusal diagnostic banner with verbatim invalid SQL retained.
  5. `v7_3_draft_reopen.png`: Durable draft reopened with retained title, notes, and SQL state.
  6. `v7_3_saved_case.png`: Saved investigation detail view with pinned replay and frozen context.
- **Persistence & Async Verification Matrix:**
  - *Rapid Title/Notes/SQL Edits:* Debounced autosave with `version_token` rotation cleanly saves latest values.
  - *Poll Completion Isolation:* Polling updates for running comparisons do not rehydrate or overwrite active editor text.
  - *Verbatim SQL Retention:* Refused SQL expressions remain intact in the editor across refusal and draft reload.
  - *STALE_DRAFT Optimistic Locking:* Concurrent updates with stale version tokens return HTTP 409 `STALE_DRAFT`, preventing silent overwrites.
  - *Cross-Workspace Isolation:* Drafts in project A return HTTP 403 when accessed by user in project B.
  - *Draft Deletion Safety:* Deleted draft returns HTTP 404; delayed autosaves cannot recreate deleted drafts.
  - *Hard Close vs In-Memory Recovery:* Keystrokes typed within the 800ms debounce window prior to an abrupt browser crash cannot reach SQLite; once autosaved or flushedupon unmount, state is fully durable across reloads.

### Track 5: Full Regression Suite (PASS)

- **Backend Pytest (`.test-tmp/v7-3-closeout`):**
  - Command: `.\.venv\Scripts\python -m pytest -q --basetemp .test-tmp/v7-3-closeout`
  - Result: **586 passed, 8 warnings in 486.54s (8m 06s)**
  - Note: All 45 PostgreSQL collector and integration tests in `test_v4_collector.py` and `test_v4_integration_and_exit_gates.py` ran against `metricguard-test-pg` on port 5433 and passed.
- **Web Build:**
  - Command: `npm run build --prefix apps/web`
  - Result: **Passed** (1595 modules transformed, bundle `dist/assets/index-CEBE9WFr.js`, duration 17.10s).
- **Docker Compose Config:**
  - Command: `docker compose -f infra/compose.yaml config --quiet`
  - Result: **Passed** (exit code 0).
- **Web Unit Tests:**
  - Command: `npm run test --prefix apps/web`
  - Result: **43 passed, 0 failed across all 6 test suites (exit code 0)**.
  - Test suites: `v2Workflow.test.mjs` (5/5), `router.test.mjs` (3/3), `dataSources.test.mjs` (12/12), `metricsLifecycle.test.mjs` (9/9), `changes.test.mjs` (8/8), `investigations.test.mjs` (6/6). Zero failures, zero skips.

---

## 6. Pilot Gates & External Status

In accordance with MetricGuard governance standards:
- **Pilot Gate V2-06 (External Real-World Analyst Validation):** Remains **OPEN**.
- **Pilot Gate V3-06 (Observed Team Workflow Trial):** Remains **OPEN**.
- **Claims Boundary:** No claims of customer production adoption, external analyst sign-off, or operational time savings are made. All evidence in this report represents hermetic synthetic engineering preview verification.

---

## 7. Acceptance Recommendation

Based on zero regressions across the 586-test backend suite, 100% bitwise matching evidence digests during container replays, complete SQL boundary and IR tampering defenses, verified browser CDP workflows, and robust optimistic concurrency controls, MetricGuard V7.3 Restricted CASE WHEN is:

**ACCEPTED — Engineering Preview Certified (2026-10-04)**
