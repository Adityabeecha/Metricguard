# MetricGuard V6 Release-Gate Audit Report

**Date:** 2026-09-28  
**Scope:** V6 Release Gates Audit & Missing Coverage Verification (Gates 1–6)  
**Classification:** Synthetic engineering acceptance only (not external analyst validation; V2-06 and V3-06 pilot gates remain open).  
**Application Code Status:** No application code was modified; no supported SQL was broadened; no V7 work was started.  

---

## 1. Executive Summary & Gate Status Matrix

All six release gates were audited through a combination of verified existing receipts and newly executed end-to-end tests against the live application (`http://127.0.0.1:8001/`), the isolated Docker container runner (`metricguard-runner:1.0`), and headless browser automation via Chrome DevTools Protocol (CDP).

| Gate | Category / Scope | Status | Verification Mode | Summary of Outcome |
| :--- | :--- | :--- | :--- | :--- |
| **Gate 1** | **Governed Metric lifecycle** | **PASS** | Newly executed | Approved baseline authoritative; candidate self-promotion blocked; explicit approval creates immutable audit transition; old definitions retired with history preserved. |
| **Gate 2** | **`metricguard.yml` configuration** | **PASS** | Newly executed | Valid config validated; missing metric (`METRIC_NOT_FOUND`), cross-workspace (`CROSS_WORKSPACE_METRIC`), and forbidden governance fields rejected; candidate config cannot weaken trusted baseline CI policy. |
| **Gate 3** | **dbt-to-CI workflow** | **PASS** | Newly executed | Material drift exits with code 1 (🔴); clean drift exits with code 0 (🔵); analytical refusal exits with code 2 (🟠) with strict mixture precedence. Evaluated on curated fixture `jaffle_shop_classic` (synthetic); validation gap documented. |
| **Gate 4** | **Security boundaries** | **PASS** | Newly executed | Cross-workspace isolation (HTTP 403/404); evidence/export authorization enforced; credential redaction verified; untrusted URL schemes rejected; Docker runner isolation parameters verified. |
| **Gate 5** | **CI-to-Investigation handoff** | **PASS** | Newly executed | Zero recomputation via exact comparison run ID reuse; evidence artifact hash invariance; idempotent resolution without duplicate drafts; human edits preserved; refusal yields zero fake numbers. |
| **Gate 6.1** | **Narrow Gap: UI Editor Preservation** | **PASS** | Newly executed (Headless Edge CDP) | Unsupported SQL submitted through live React UI (`http://127.0.0.1:8001/`). After refusal (`UNSUPPORTED_SQL`), both baseline and target SQL textareas preserved exact verbatim text (whitespace, comments). |
| **Gate 6.2** | **Narrow Gap: Nullable-Boolean Fixture** | **PASS** | Newly executed (Isolated Docker Runner) | Separate v4 fixture (A: 10.00/true, B: 20.00/false, C: 30.00/NULL, Baseline 60.00). All 10 queries (`=`, `IS TRUE/FALSE`, bare/NOT, `IS NOT TRUE/FALSE`, `IS NULL/NOT NULL`) matched exact targets, memberships, and **0.00** residual. |
| **Prior** | **FlatContract v4 Groups 1–8** | **PASS** | Reused approved receipts | Dates, NULL predicates, exact decimals, booleans, empty/NULL SUM, refusals, v4 replay, and v3 compatibility all passed with exact zero residual. |

---

## 2. Reused Receipts vs. Newly Executed Checks

### 2.1 Reused Verified Receipts
The following prior receipts were audited and verified to match current checkout code without re-execution:
1. **FlatContract v4 Acceptance Groups 1–8:**
   - Report: [docs/evidence/v6_acceptance_report.md](file:///C:/Users/badit/OneDrive/Desktop/metricguard/docs/evidence/v6_acceptance_report.md) (approved 2026-09-28).
   - Scratch Artifacts: `case_1_result.json` through `case_8_result.json` under `scratch/`.
   - Immutable Live Workspace Draft: `draft-796da0fa9a0346fdb243cd1c38415578` (Run `ce28aec6-a55d-49f2-9110-901b4e3f84f6`).
2. **Full Regression Test Suites:**
   - Full Python Suite: `data/evaluation/v4-regression.xml` (586 passed, 0 failed, 0 skipped).
   - Web Unit Tests: `data/evaluation/v4-web-tests.log` (43 passed, 0 failed). Re-confirmed via `npm test --prefix apps/web` (43 passed).
   - Production Web Build: `data/evaluation/v4-build.log` (1595 modules transformed cleanly).
   - Live Isolated Runner Certification: `data/evaluation/live_runner_report.json`.

### 2.2 Newly Executed Checks
1. **Gate 1 Execution:** `scratch/audit_gate_1_lifecycle.py` $\to$ `scratch/gate_1_result.json`.
2. **Gate 2 Execution:** `scratch/audit_gate_2_config.py` $\to$ `scratch/gate_2_result.json`.
3. **Gate 3 Execution:** `scratch/audit_gate_3_dbt_ci.py` $\to$ `scratch/gate_3_result.json`.
4. **Gate 4 Execution:** `scratch/audit_gate_4_security.py` $\to$ `scratch/gate_4_result.json`.
5. **Gate 5 Execution:** `scratch/audit_gate_5_ci_investigation.py` $\to$ `scratch/gate_5_result.json`.
6. **Gate 6.1 Execution:** `scratch/audit_gate_6_1_ui_refusal.mjs` $\to$ `scratch/gate_6_1_result.json`, `scratch/ui_refusal_screenshot.png`.
7. **Gate 6.2 Execution:** `scratch/audit_gate_6_2_nullable_boolean.py` $\to$ `scratch/gate_6_2_result.json`.
8. **Focused Test Suite:** Ran fresh test suite in `.test-tmp/v6-audit-regressions` covering `test_v6_config.py`, `test_v6_ci_workflow.py`, `test_v6_4_ci_investigation_handoff.py`, and `test_workspace_isolation_architecture.py` (76 passed, 0 failed).

---

## 3. Detailed Gate Audits & Evidence

### Gate 1: Governed Metric Lifecycle
- **Status:** **PASS**
- **Test Script:** `scratch/audit_gate_1_lifecycle.py`
- **Receipt:** `scratch/gate_1_result.json`
- **Verified Invariants:**
  1. *Authoritative Baseline:* Metric created with active definition $A/V_1$. Baseline calculation resolves to $A/V_1$.
  2. *Candidate Isolation & Non-Self-Promotion:* Definition $B$ bound as `candidate`. Attempting to approve version $B/V_1$ as active version without formal promotion raises `DomainError('INVALID_BINDING')`. Candidate evaluation does not alter active binding.
  3. *Immutable Transition Trail:*
     - Version approval within active definition ($A/V_1 \to A/V_2$) records `version_approved` transition.
     - Definition promotion ($B/V_1$ promoted) records `definition_promoted` transition with `previous_definition_id: Def A` and `previous_approved_version_id: V2`.
     - Invariant verified: exactly 1 active binding, historical binding retired, 3 immutable records in `metric_state_transitions`.

### Gate 2: `metricguard.yml` Configuration & Boundary
- **Status:** **PASS**
- **Test Script:** `scratch/audit_gate_2_config.py`
- **Receipt:** `scratch/gate_2_result.json`
- **Verified Invariants:**
  1. *Valid Config:* Valid YAML with workspace `retail`, `app.base_url`, and model bindings succeeds under store-backed and schema-only validation.
  2. *Missing Metric:* Metric binding referencing non-existent ID fails with `METRIC_NOT_FOUND`.
  3. *Cross-Workspace Metric:* Binding referencing a metric in another workspace (`finance`) fails with `CROSS_WORKSPACE_METRIC`.
  4. *Forbidden Governance Fields:* Attempting to specify `importance` or `approved_version_id` in repository YAML fails with `FORBIDDEN_GOVERNANCE_FIELD` (Model B store governance boundary enforced).
  5. *CI Policy Weakening Protection:* Base branch config specifies `ci_action: fail` (`TRUSTED_BASE`). Candidate PR branch attempts `ci_action: ignore` (`UNTRUSTED_CANDIDATE`). Policy diff detects proposed change (`is_weakening=True`); execution strictly enforces base branch policy.

### Gate 3: dbt-to-CI Workflow
- **Status:** **PASS**
- **Test Script:** `scratch/audit_gate_3_dbt_ci.py`
- **Receipt:** `scratch/gate_3_result.json`
- **Manifest Source & Provenance:**
  - Used `data/evaluation/manifests/jaffle_shop_classic.manifest.json` (canonical curated dbt v11 schema / dbt 1.7.0 fixture).
  - *Fixture Classification:* **Synthetic curated fixture**.
  - *Real-Project Validation Gap:* Local verification tests against compiled SQL in standard dbt manifests. Multi-warehouse dialect specific syntax (e.g. Snowflake/BigQuery UDFs), live Git PR fork permissions, and dbt Semantic Layer / MetricFlow integration remain unexercised locally.
- **Verified Invariants & Precedence:**
  1. *Material Drift (Supported Changed SQL):* Run against frozen golden snapshot `orders_golden`. Result exits with **code 1**; JSON output adheres to `metricguard_ci_report_v1`; PR comment contains Zero-Green 🔴 badge.
  2. *Clean Drift (Supported Filter Change):* Result exits with **code 0**; PR comment contains Zero-Green 🔵 badge.
  3. *Analytical Refusal (Unsupported Complex CTEs/Joins):* Model referencing `jaffle_shop.customers` with multi-table joins is refused (`UNSUPPORTED_SQL`). CI exits with **code 2**; PR comment contains 🟠 badge.
  4. *Precedence Rule:* When a run contains both material drift on Metric 1 and analytical refusal on Metric 2, exit code **2** takes precedence over exit code 1.

### Gate 4: Security Boundaries
- **Status:** **PASS**
- **Test Script:** `scratch/audit_gate_4_security.py`
- **Receipt:** `scratch/gate_4_result.json`
- **Verified Invariants:**
  1. *Cross-Workspace Isolation:* Workspace `ws-beta` attempting to read drafts or data from `ws-alpha` receives HTTP 403 Forbidden. Cross-workspace URL path spoofing receives HTTP 403.
  2. *Evidence / Export Authorization:* Unauthenticated requests to `/comparisons/{id}/evidence` and `/comparisons/{id}/export` return HTTP 404 (or 401/403).
  3. *Credential Redaction:* `redact_ci_secrets` verified across PostgreSQL connection URIs, API keys, GitHub tokens, and Bearer authorization headers. Passwords and tokens are replaced with `[REDACTED]` or `[REDACTED_TOKEN]`.
  4. *Untrusted URL Handling:* Schemes like `javascript:`, `file:`, `ftp:` and URLs with queries/fragments are rejected with `INVALID_APP_URL`. Candidate PR configs cannot inject unverified deep link URLs.
  5. *Isolated Container Restrictions:* Verified in `services/supervisor/worker.py`:
     - Non-root user: `--user 10001:10001`
     - Network disabled: `--network none`
     - Read-only root filesystem: `--read-only`
     - Dropped capabilities: `--cap-drop ALL`
     - No privilege escalation: `--security-opt no-new-privileges`
     - Restricted tmpfs: `--tmpfs /tmp:rw,noexec,nosuid,size=64m`

### Gate 5: CI-to-Investigation Handoff
- **Status:** **PASS**
- **Test Script:** `scratch/audit_gate_5_ci_investigation.py`
- **Receipt:** `scratch/gate_5_result.json`
- **Verified Invariants:**
  1. *Zero Recomputation:* CI execution creates comparison run `run-impact-...` and draft `draft-ci-...`. Opening `/api/v1/investigations/{draft_id}` directly binds to the existing run ID without executing SQL.
  2. *Evidence Hash Invariance:* CI evidence fingerprint and investigation evidence fingerprint match exactly.
  3. *Idempotency:* Re-running CI on the same commit/diff resolves to the exact same draft ID; no duplicate drafts created.
  4. *Human Edits Preserved:* Draft updated by human analyst (custom notes and title) is re-evaluated by CI; custom notes and title are preserved verbatim.
  5. *Analytical Refusal Handoff:* Refused metric handoff generates refusal draft with `is_refusal: true`, and all quantitative fields (`baseline_value`, `target_value`, `exact_delta`) are strictly `None` (zero invented numbers).

---

## 4. Narrow Coverage Gaps Verification

### Gate 6.1: Actual UI SQL Editor Input Preservation after Refusal
- **Status:** **PASS**
- **Test Script:** `scratch/audit_gate_6_1_ui_refusal.mjs`
- **Receipt:** `scratch/gate_6_1_result.json`, `scratch/ui_refusal_screenshot.png`
- **Methodology:** Automated live browser verification using headless Microsoft Edge (`msedge.exe`) connected via Chrome DevTools Protocol (CDP) to the running MetricGuard server at `http://127.0.0.1:8001/w/local-retail/investigations`.
- **Execution Flow:**
  1. Navigated to Investigations view, clicked "New investigation", and enabled Ad-hoc SQL mode.
  2. Entered baseline SQL containing whitespace and comments:  
     `SELECT SUM(revenue) FROM snapshot\n/* baseline query with comment and whitespace */\nWHERE order_date >= DATE '2026-01-01'`
  3. Entered target SQL containing unsupported subqueries and arithmetic expressions:  
     `SELECT SUM(revenue * 2) FROM snapshot\nWHERE status IN (SELECT status FROM snapshot) -- unsupported subquery and expression`
  4. Recorded exact DOM `.value` properties before clicking Run.
  5. Clicked "Run comparison".
  6. Observed refusal banner in UI (`UNSUPPORTED_SQL`).
  7. Immediately read both textarea DOM values after refusal:
     - Baseline before == Baseline after: **`true`** (exact byte match).
     - Target before == Target after: **`true`** (exact byte match).
  8. Verified that refusal updates error state without resetting or clearing the controlled textarea state variables. Captured visual screenshot receipt.

---

### Gate 6.2: Nullable-Boolean Fixture Acceptance
- **Status:** **PASS**
- **Test Script:** `scratch/audit_gate_6_2_nullable_boolean.py`
- **Receipt:** `scratch/gate_6_2_result.json`
- **Contract & Snapshot:**
  - Contract ID: `f99b9156-f199-4ef0-a10d-adb2c7e8c911` (Version 4, measure `amount`, dimension `flag: BOOLEAN`, `nullable_categories: ['flag']`, `null_policy: 'blank_nullable'`).
  - Snapshot ID: `a7353de8-af3a-47ba-b85e-44b70e47865c` (DuckDB physical schema: `row_key: VARCHAR`, `amount: DECIMAL(10,2)`, `flag: BOOLEAN`).
- **Fixture Data:**
  - Row A: `amount: 10.00`, `flag: true`
  - Row B: `amount: 20.00`, `flag: false`
  - Row C: `amount: 30.00`, `flag: NULL`
  - **Baseline SUM:** $10.00 + 20.00 + 30.00 = \mathbf{60.00\text{ USD}}$.

- **Ten Target Comparisons Evaluated through Isolated Docker Runner:**

| Case | SQL Predicate | Expected Target | Actual Target | Expected Delta | Actual Delta | Included in Both | Baseline Only | Residual | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `eq_true` | `WHERE flag = TRUE` | 10.00 | 10.00 | -50.00 | -50.00 | `['A']` | `['B', 'C']` | **0** | **PASS** |
| `is_true` | `WHERE flag IS TRUE` | 10.00 | 10.00 | -50.00 | -50.00 | `['A']` | `['B', 'C']` | **0** | **PASS** |
| `bare_flag` | `WHERE flag` | 10.00 | 10.00 | -50.00 | -50.00 | `['A']` | `['B', 'C']` | **0** | **PASS** |
| `eq_false` | `WHERE flag = FALSE` | 20.00 | 20.00 | -40.00 | -40.00 | `['B']` | `['A', 'C']` | **0** | **PASS** |
| `is_false` | `WHERE flag IS FALSE` | 20.00 | 20.00 | -40.00 | -40.00 | `['B']` | `['A', 'C']` | **0** | **PASS** |
| `not_flag` | `WHERE NOT flag` | 20.00 | 20.00 | -40.00 | -40.00 | `['B']` | `['A', 'C']` | **0** | **PASS** |
| `is_not_true` | `WHERE flag IS NOT TRUE` | 50.00 | 50.00 | -10.00 | -10.00 | `['B', 'C']` | `['A']` | **0** | **PASS** |
| `is_not_false`| `WHERE flag IS NOT FALSE`| 40.00 | 40.00 | -20.00 | -20.00 | `['A', 'C']` | `['B']` | **0** | **PASS** |
| `is_null` | `WHERE flag IS NULL` | 30.00 | 30.00 | -30.00 | -30.00 | `['C']` | `['A', 'B']` | **0** | **PASS** |
| `is_not_null`| `WHERE flag IS NOT NULL`| 30.00 | 30.00 | -30.00 | -30.00 | `['A', 'B']` | `['C']` | **0** | **PASS** |

All 10 runs produced exact delta reconciliation and zero unexplained residual (`residual: 0`). Exact row contributions sum to the endpoint delta in every case.

---

## 5. Engineering Freeze Assessment

### Supported Boundary
1. **Engineering Requirements Met:**
   - Safe SQL expansion across comparison operators, canonical DATE literals, exact decimals, 3-valued boolean NULL logic, and interval boundaries is mathematically proven and verified.
   - FlatContract v4 typed ingestion pins schema and types into contract hashes without altering legacy v3.
   - Declarative `metricguard.yml` enforces Model B store governance.
   - Turnkey CI/PR change gate (`diff-ci`) respects exit code precedence (0/1/2/3) and Zero-Green reporting.
   - CI-to-Investigation handoff operates with zero recomputation, hash invariance, and draft idempotency.
   - UI SQL editors preserve input across refusals; three-valued boolean logic on nullable columns reconciles with zero residual.
2. **The V6 Engineering Freeze is SUPPORTED from an internal software verification standpoint.**

### Strict External Release Disclaimers
- **Synthetic Verification Only:** All test fixtures (`v6_orders.csv`, `orders_golden.csv`, `jaffle_shop_classic.manifest.json`, nullable boolean fixture) are synthetic or curated engineering test cases.
- **External Pilot Gates Remain Open:** The V2-06 external analyst validation gate and the V3-06 team pilot gate have not recorded observed real-world trials and remain open.
- **No Value Claims:** No claims of real-user time savings, reduced data-team toil, or financial loss prevention are made.
- **Future Direction:** V7+ development remains future roadmap scope; no V7 code was started.
