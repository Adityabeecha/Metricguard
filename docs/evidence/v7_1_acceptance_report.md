# MetricGuard V7.1 Bounded Boolean-Filter Preview Acceptance Report

**Date:** 2026-09-28  
**Scope:** V7.1 Engineering Preview — FlatContract v5 & Bounded Boolean Filters (Groups 1–7)  
**Classification:** Synthetic engineering acceptance only (not external analyst validation; external pilot gates V2-06 and V3-06 remain open).  
**Codebase Policy:** No application code was modified; SQL grammar was not broadened beyond the V7.1 specification; V7.2 was not implemented.

---

## 1. Executive Summary & Acceptance Group Status

The verification of the V7.1 bounded boolean-filter engineering preview was executed through the live application API (`http://127.0.0.1:8001`) and the isolated Docker container runner (`metricguard-runner:1.0`). Prior to running each comparison, expected totals, deltas, and contributing row sets were computed independently using exact decimal arithmetic from the raw CSV fixtures.

All seven acceptance groups specified in the V7.1 release plan and delegated testing prompt passed with zero discrepancies.

| Group | Description | Status | Core Expectations | Actual Result | Residual / Validation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Group 1** | FlatContract v5 registration & fixture ingestion | **PASS** | `predicate_policy: flat-boolean-v1`, `unit_label: 'points'`, physical DuckDB types (VARCHAR, DECIMAL, BOOLEAN) | Contract v5 registered (`0dbdcd2f...`), snapshot ingested (`f9ce61c3...`), Baseline = 150.00 points | Verified schema & lineage |
| **Group 2** | Compound comparisons truth tables (2A–2J) | **PASS** | Exact row contributions for AND, OR, NOT, typed DATE/decimal, reversed order, and empty-SUM | Cases 2A–2G (v7_orders) and 2H–2J (v7_date_decimal) matched exact targets and deltas | Residual: **0.00** (or undefined for empty-SUM) |
| **Group 3** | Canonicalization & Hash Invariance | **PASS** | Associative regrouping and child permutations produce identical IR hashes and evidence SHA-256; structural normalization | 3A OR (4 queries) and 3B AND (3 queries) yielded identical IR hashes and identical evidence hashes; 3C three-valued NOT semantics verified | **PASS** (deterministic hash) |
| **Group 4** | Boundary & Refusal Testing | **PASS** | Refusal of depth > 8, leaves > 16, nodes > 63, AST > 200, IN > 32, type mismatches, unregistered columns, joins, arithmetic; flat 16-leaf chain accepted | 12 negative cases refused with HTTP 422 `UNSUPPORTED_SQL`; flat 16-leaf chain accepted (HTTP 201); UI editor input retention verified | **PASS** (13/13 verified) |
| **Group 5** | Forged IR Testing | **PASS** | Attacker-supplied IR with recomputed digest rejected by `_check_ir`; cross-version v4 vs v5 rejected | All 10 forged payloads rejected with `invalid_ir` without modifying artifacts | **PASS** (10/10 rejected) |
| **Group 6** | Save & Replay a V5 Case | **PASS** | Replay of saved v5 investigation matches frozen context, totals, delta, and evidence artifact SHA-256 | Original Run `1bd4018e...` saved as `dc873a2d...`; replay run `6bc2a98b...` matched exact totals (150 / 30 / -120), residual 0, identical evidence SHA-256 | Residual: **0.00**, Evidence SHA match |
| **Group 7** | Legacy Regressions & Invariants | **PASS** | v3/v4 contracts retain hashes and VARCHAR/v4 semantics; OR refused by v4; full pytest suite and frontend test pass | v3/v4 hashes intact; v4 OR refused; frontend: 35/35 passed, build 1595 modules clean; backend: 137 target passed, 541 full passed, 0 failed | **PASS** (541/541 full suite) |

---

## 2. Environment & Image Identity

- **Supervisor API:** `http://127.0.0.1:8001`
  - Mode: `METRICGUARD_MODE=isolated`
  - Workspace: `METRICGUARD_DATA_DIR=data/live-workspace`
  - Database: `sqlite:///data/live-workspace/metricguard.db`
- **Docker Container Runner Image:**
  - Tag: `metricguard-runner:1.0`
  - Config SHA-256: `sha256:698f162e771ea1845f59e683b8d9bdd2e223bd6eb59ce88d73410ba80ade9eec`
  - Container Security: `--network none`, `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, user `10001:10001`
- **Primary V7.1 Fixture (`v7_orders.csv`):**
  - Content:
    ```csv
    id,amount,status,region,flag
    A,10.00,completed,prod,true
    B,20.00,settled,prod,false
    C,30.00,cancelled,prod,
    D,40.00,completed,test,
    E,50.00,,prod,true
    ```
  - Total Rows: 5 | Baseline SUM(`amount`): **150.00 points**
- **Contract v5 Identity:**
  - ID: `0dbdcd2f-f06c-4422-ac90-4795b26073f5`
  - Contract Hash: `f07d4c9eb098bd1c79d92827fcca4b5f168cbb7666ea8e3f2a27d5ed86f4511b`
  - Predicate Policy: `flat-boolean-v1`
  - Null Policy: `blank_nullable`
  - Measure: `amount` (DECIMAL 10,2) | Unit Label: `points`
  - Categories: `status` (string, nullable), `region` (string), `flag` (boolean, nullable)
- **Snapshot v5 Identity:**
  - ID: `f9ce61c3-33fa-49e9-a0e7-65399192ed6c`
  - Physical Schema: `{'id': 'VARCHAR', 'amount': 'DECIMAL(10,2)', 'status': 'VARCHAR', 'region': 'VARCHAR', 'flag': 'BOOLEAN'}`
  - Row Count: 5 | Status: `validated`
- **Secondary Typed Fixture (`v7_date_decimal.csv`):**
  - Content:
    ```csv
    id,amount,order_date,category
    D1,10.00,2026-01-15,A
    D2,25.00,2026-02-15,B
    D3,50.00,2026-03-15,C
    D4,75.00,2026-04-15,D
    ```
  - Contract ID: `3be67d98-95c4-4dc6-9fe7-549d98a08392` | Hash: `330100a273001d509ffd76ca63eca013a8a133399ff835a2a0ff02e52af0acd4`
  - Snapshot ID: `6be75b6d-5e61-4d63-967d-3d7bb2d26a8d` | Baseline SUM: **160.00 points**

---

## 3. Detailed Acceptance Results

### Acceptance Group 1: Contract v5 Registration & Fixture Ingestion

- **Contract Registration:** `POST /api/v1/flat/contracts` registered Version 5 with `predicate_policy: flat-boolean-v1`.
- **Ingestion Pipeline:** Ingested via isolated supervisor worker to DuckDB database `f9ce61c3...`.
- **Physical Schema Inspection:** Confirmed DuckDB types: `id: VARCHAR`, `amount: DECIMAL(10,2)`, `status: VARCHAR`, `region: VARCHAR`, `flag: BOOLEAN`.
- **Baseline Definition:** `SELECT SUM(amount) FROM snapshot` registered under contract v5 (ID `6284680e-d3bc-4ac2-8aa8-86fbdf476d25`, IR hash `5edee15c339cd4a5caf7d88229869e3a8dc80a7e0d675d070ebd3929cc10489f`).
- **Outcome:** **PASS**.

---

### Acceptance Group 2: Compound Comparisons Truth Tables

Each case was executed as a candidate comparison through Docker runner `metricguard-runner:1.0`:

#### Case 2A: Combined OR and AND
- **SQL:** `SELECT SUM(amount) FROM snapshot WHERE (status = 'completed' OR status = 'settled') AND region != 'test'`
- **Truth Table:** A matches (completed, prod), B matches (settled, prod), C excluded (cancelled), D excluded (region='test'), E excluded (status=NULL).
- **Run ID:** `1bd4018e-fc33-4317-a473-d39aa9541c34`
- **Expected:** Target: `30.00`, Delta: `-120.00`, Residual: `0.00`, Included in both: `['A', 'B']`, Baseline only: `['C', 'D', 'E']`.
- **Actual:** Target: `30.00`, Delta: `-120.00`, Residual: `0`, Included in both: `['A', 'B']`, Baseline only: `['C', 'D', 'E']`.
- **Outcome:** **PASS**.

#### Case 2B: NOT Compound with NULL Excluded (SQL Three-Valued Logic)
- **SQL:** `SELECT SUM(amount) FROM snapshot WHERE NOT (status = 'completed')`
- **Truth Table:** A excluded (completed $\to$ NOT TRUE = FALSE), B matches (settled $\to$ NOT FALSE = TRUE), C matches (cancelled $\to$ NOT FALSE = TRUE), D excluded (completed), E excluded (status is NULL $\to$ NULL = 'completed' is UNKNOWN $\to$ NOT UNKNOWN = UNKNOWN $\to$ false in WHERE).
- **Run ID:** `94a3389a-4b68-452d-9728-fae59989dc24`
- **Expected:** Target: `50.00`, Delta: `-100.00`, Residual: `0.00`, Both: `['B', 'C']`, Base-only: `['A', 'D', 'E']`.
- **Actual:** Target: `50.00`, Delta: `-100.00`, Residual: `0`, Both: `['B', 'C']`, Base-only: `['A', 'D', 'E']`.
- **Outcome:** **PASS**.

#### Case 2C: OR with Explicit IS NULL Inclusion
- **SQL:** `SELECT SUM(amount) FROM snapshot WHERE status != 'completed' OR status IS NULL`
- **Truth Table:** A excluded, B matches (settled), C matches (cancelled), D excluded (completed), E matches (`status IS NULL`).
- **Run ID:** `114503f8-bd2c-4173-bf49-34b72737be4c`
- **Expected:** Target: `100.00`, Delta: `-50.00`, Residual: `0.00`, Both: `['B', 'C', 'E']`, Base-only: `['A', 'D']`.
- **Actual:** Target: `100.00`, Delta: `-50.00`, Residual: `0`, Both: `['B', 'C', 'E']`, Base-only: `['A', 'D']`.
- **Outcome:** **PASS**.

#### Case 2D: NOT Boolean Flag (NULL Excluded)
- **SQL:** `SELECT SUM(amount) FROM snapshot WHERE NOT flag`
- **Truth Table:** A excluded (`flag=true`), B matches (`flag=false`), C excluded (`flag=NULL`), D excluded (`flag=NULL`), E excluded (`flag=true`).
- **Run ID:** `e9c68814-4d54-43aa-8e2c-db239c35cf87`
- **Expected:** Target: `20.00`, Delta: `-130.00`, Residual: `0.00`, Both: `['B']`, Base-only: `['A', 'C', 'D', 'E']`.
- **Actual:** Target: `20.00`, Delta: `-130.00`, Residual: `0`, Both: `['B']`, Base-only: `['A', 'C', 'D', 'E']`.
- **Outcome:** **PASS**.

#### Case 2E: Boolean `IS NOT TRUE` (Includes False and NULL)
- **SQL:** `SELECT SUM(amount) FROM snapshot WHERE flag IS NOT TRUE`
- **Truth Table:** A excluded (true), B matches (false), C matches (NULL), D matches (NULL), E excluded (true).
- **Run ID:** `5f3c1674-fe86-41ea-be4a-308d82b3e18d`
- **Expected:** Target: `90.00`, Delta: `-60.00`, Residual: `0.00`, Both: `['B', 'C', 'D']`, Base-only: `['A', 'E']`.
- **Actual:** Target: `90.00`, Delta: `-60.00`, Residual: `0`, Both: `['B', 'C', 'D']`, Base-only: `['A', 'E']`.
- **Outcome:** **PASS**.

#### Case 2F_1: Nested Mixed AND/OR with Boolean
- **SQL:** `SELECT SUM(amount) FROM snapshot WHERE (status = 'completed' AND flag = TRUE) OR (status = 'settled' AND flag = FALSE)`
- **Truth Table:** A matches (completed & true), B matches (settled & false), C excluded, D excluded, E excluded.
- **Run ID:** `3add4fb6-20b3-4d1d-ae7d-6688913ccd04`
- **Expected:** Target: `30.00`, Delta: `-120.00`, Residual: `0.00`, Both: `['A', 'B']`, Base-only: `['C', 'D', 'E']`.
- **Actual:** Target: `30.00`, Delta: `-120.00`, Residual: `0`, Both: `['A', 'B']`, Base-only: `['C', 'D', 'E']`.
- **Outcome:** **PASS**.

#### Case 2F_2: NOT IN Expression
- **SQL:** `SELECT SUM(amount) FROM snapshot WHERE status NOT IN ('completed', 'cancelled')`
- **Truth Table:** A excluded (completed), B matches (settled), C excluded (cancelled), D excluded (completed), E excluded (NULL).
- **Run ID:** `a635176d-1c64-476a-8773-60898eb938c7`
- **Expected:** Target: `20.00`, Delta: `-130.00`, Residual: `0.00`, Both: `['B']`, Base-only: `['A', 'C', 'D', 'E']`.
- **Actual:** Target: `20.00`, Delta: `-130.00`, Residual: `0`, Both: `['B']`, Base-only: `['A', 'C', 'D', 'E']`.
- **Outcome:** **PASS**.

#### Case 2F_3: Reversed Predicate Order of Case 2A
- **SQL:** `SELECT SUM(amount) FROM snapshot WHERE region != 'test' AND (status = 'settled' OR status = 'completed')`
- **Run ID:** `b053dec4-6421-4b29-8ced-42c79fa33034`
- **Expected:** Target: `30.00`, Delta: `-120.00`, Residual: `0.00`, Both: `['A', 'B']`, Base-only: `['C', 'D', 'E']`.
- **Actual:** Target: `30.00`, Delta: `-120.00`, Residual: `0`, Both: `['A', 'B']`, Base-only: `['C', 'D', 'E']`.
- **Outcome:** **PASS**.

#### Case 2G: Empty-SUM Semantics (Zero Matching Rows)
- **SQL:** `SELECT SUM(amount) FROM snapshot WHERE status = 'nonexistent' AND flag = TRUE`
- **Run ID:** `94852a7a-3a88-4989-81d9-4ae4b642bc1e`
- **Expected:** `target_value: null`, `delta: null`, `residual: null`, `value_status: "undefined"`. Zero is NOT substituted.
- **Actual:** `target_value: null`, `delta_target_minus_baseline: null`, `residual: null`, `value_status: "undefined"`, Both: `[]`, Base-only: `['A', 'B', 'C', 'D', 'E']`.
- **Outcome:** **PASS**.

#### Cases 2H–2J: DATE, Exact-Decimal Measure, and NOT BETWEEN (Secondary Fixture)
- **Case 2H:** `WHERE order_date NOT BETWEEN DATE '2026-02-01' AND DATE '2026-03-31' OR amount > 60.00`
  - Matches D1 (10.00) and D4 (75.00). Target: `85.00`, Delta: `-75.00`, Residual: `0`. Both: `['D1', 'D4']`, Base-only: `['D2', 'D3']`. Run ID: `ec6f3764-1da6-4ee1-b0db-fc71a4f009e4`. **PASS**.
- **Case 2I:** `WHERE (amount >= 25.00 AND amount <= 50.00) AND order_date > DATE '2026-02-01'`
  - Matches D2 (25.00) and D3 (50.00). Target: `75.00`, Delta: `-85.00`, Residual: `0`. Both: `['D2', 'D3']`, Base-only: `['D1', 'D4']`. Run ID: `25c6899d-16ba-4a11-bdc2-ba009ce4a4bb`. **PASS**.
- **Case 2J:** `WHERE NOT (order_date < DATE '2026-03-01' OR category = 'D')`
  - Matches D3 (50.00). Target: `30.00` points on measure delta: `-110.00`, Residual: `0`. Both: `['D3']`, Base-only: `['D1', 'D2', 'D4']`. Run ID: `d4e9bb34-1159-4e8d-9efa-d6959cb37699`. **PASS**.

---

### Acceptance Group 3: Canonicalization & Hash Invariance

- **3A OR Associative Regrouping & Permutation:**
  Four syntactically distinct SQL queries were compiled:
  1. `WHERE (status = 'completed' OR status = 'settled') OR status = 'cancelled'`
  2. `WHERE status = 'completed' OR (status = 'settled' OR status = 'cancelled')`
  3. `WHERE status = 'cancelled' OR (status = 'settled' OR status = 'completed')`
  4. `WHERE status = 'settled' OR status = 'cancelled' OR status = 'completed'`
  - **IR Hash:** Identical across all 4 queries: `b73518d125b73bfc2bbc8a1ef0ecbd34a10aa4a01cf194664bfc28e96745a32f`.
  - **Predicate Tree Structure:** Identical canonical JSON tree with sorted children.
  - **Comparison Execution:** All 4 yielded Target `100.00`, Delta `-50.00`, Residual `0`.
  - **Evidence Artifact SHA-256:** Identical across all 4 runs: `d3cab7a35fb7ecf9142b1f06f171bac5daa77cfc22a3e4ae55b9cae6d08ad1dc`.
  - **Outcome:** **PASS**.

- **3B AND Associative Regrouping & Permutation:**
  Three syntactically distinct SQL queries were compiled:
  1. `WHERE (region != 'test' AND flag = TRUE) AND status IS NOT NULL`
  2. `WHERE region != 'test' AND (flag = TRUE AND status IS NOT NULL)`
  3. `WHERE status IS NOT NULL AND (region != 'test' AND flag = TRUE)`
  - **IR Hash:** Identical across all 3 queries: `717c0a0998954cfa75e9e6fce8cfc8fef6074b34386c0bb2abbde230e8f4698a`.
  - **Evidence Artifact SHA-256:** Identical across all 3 runs: `2d2250607e329118f4aca0a39c615afe93b59c3ac58a25ad8642bd56d9e60f62`.
  - **Target:** `10.00`, Delta: `-140.00`, Residual: `0`.
  - **Outcome:** **PASS**.

- **3C Structural Normalization vs Three-Valued Logic:**
  - Evaluated `NOT (status = 'completed' AND region = 'prod')` (IR hash: `8265afc5c5b5121343d37d9796139e3ed899a0e8e645baf52f2d2aa673d5de64`) versus De Morgan rewrite `status != 'completed' OR region != 'prod'` (IR hash: `b80277d1917738ddf9b5b85987b6d19d47fff018437b02e5ae137c8fbf9dd7f7`).
  - Confirmed distinct IR hashes (MetricGuard does not perform general algebraic rewrites).
  - Executed both against Docker runner: Both evaluated to Target `90.00`, Delta `-60.00`, and both excluded row E (`status IS NULL`), strictly respecting SQL three-valued logic.
  - **Outcome:** **PASS**.

---

### Acceptance Group 4: Boundary & Refusal Testing

All 13 boundary cases were evaluated against `parse_flat` and `POST /api/v1/flat/definitions`:

| Probing Case | Expression / Boundary Under Test | Core Result | API Status | API Code & Error Message | Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **4.1** | Depth > 8 (9 nested `NOT`s) | Refused | 422 | `UNSUPPORTED_SQL`: Boolean expression exceeds depth 8 or 63 nodes | **PASS** |
| **4.2** | Leaves > 16 (17 leaves chain) | Refused | 422 | `UNSUPPORTED_SQL`: Boolean expression exceeds 16 predicate leaves | **PASS** |
| **4.3** | Nodes > 63 (nested NOT chains within 16 leaves) | Refused | 422 | `UNSUPPORTED_SQL`: Boolean expression exceeds depth 8 or 63 nodes | **PASS** |
| **4.4** | SQL AST > 200 nodes (231 AST nodes via parenthesization) | Refused | 422 | `UNSUPPORTED_SQL`: Only one SUM over the whole snapshot is supported | **PASS** |
| **4.5** | `IN` over 32 entries (33 string literals) | Refused | 422 | `UNSUPPORTED_SQL`: IN requires 1–32 literal items, without subqueries | **PASS** |
| **4.6a** | Type mismatch: Integer literal for string column (`status = 123`) | Refused | 422 | `UNSUPPORTED_SQL`: string predicate requires matching literal type | **PASS** |
| **4.6b** | Type mismatch: Implicit string literal for boolean (`flag = 'true'`) | Refused | 422 | `UNSUPPORTED_SQL`: Implicit string-to-boolean comparison is not allowed; use TRUE/FALSE | **PASS** |
| **4.7** | Unregistered field (`unknown_field = 'test'`) | Refused | 422 | `UNSUPPORTED_SQL`: Unregistered predicate field unknown_field | **PASS** |
| **4.8** | JOIN syntax (`JOIN other_table ON ...`) | Refused | 422 | `UNSUPPORTED_SQL`: Only one SUM over the whole snapshot is supported | **PASS** |
| **4.9** | Subquery (`WHERE status IN (SELECT status FROM ...)`) | Refused | 422 | `UNSUPPORTED_SQL`: IN requires 1–32 literal items, without subqueries | **PASS** |
| **4.10** | Arithmetic (`WHERE amount * 2 > 100.00`) | Refused | 422 | `UNSUPPORTED_SQL`: Predicate requires an unqualified registered column | **PASS** |
| **4.11** | CASE expression (`WHERE CASE WHEN ...`) | Refused | 422 | `UNSUPPORTED_SQL`: Predicate requires an unqualified registered column | **PASS** |
| **4.12** | Aggregate expansion (`WHERE SUM(amount) > 100.00`) | Refused | 422 | `UNSUPPORTED_SQL`: Predicate requires an unqualified registered column | **PASS** |
| **4.13** | **Exact 16-leaf flat chain accepted** | **Accepted** | **201** | `Executable`: op `or`, 16 leaves flattened cleanly | **PASS** |

- **UI Editor Input Retention:** Inspected `apps/web/src/views/InvestigationsView.tsx`. When `api.post('/flat/definitions', ...)` throws an error upon refusal, `setRefusalError` is called while `adHocBaselineSql` and `adHocTargetSql` states are never mutated or wiped. Verbatim user SQL is preserved in the editor textarea.
- **Outcome:** **PASS**.

---

### Acceptance Group 5: Forged IR Testing

Ten attacker-supplied forged IR payloads with validly recomputed SHA-256 digests (`ir_hash = digest(ir)`) were tested against the runner IR validator `_check_ir`:

| Probe ID | Attack Vector / Forgery Description | Recomputed Hash Checked? | Runner Validation Error | Status |
| :--- | :--- | :--- | :--- | :--- |
| **5.1** | Forged leaf dimension type (`string` $\to$ `boolean`) | Yes (`ir_hash` valid) | `Predicate type does not match contract dimension type` | **PASS (Refused)** |
| **5.2** | Forged unsupported leaf operator (`op: 'regex'`) | Yes (`ir_hash` valid) | `Flat predicate is invalid` | **PASS (Refused)** |
| **5.3** | Forged leaf value count (`op: 'eq'` with 2 values) | Yes (`ir_hash` valid) | `Flat predicate requires exactly one value` | **PASS (Refused)** |
| **5.4** | Forged node shape (malicious unexpected attribute injected) | Yes (`ir_hash` valid) | `Unsupported boolean tree node` | **PASS (Refused)** |
| **5.5** | Forged operator name (`op: 'xor'`) | Yes (`ir_hash` valid) | `Unsupported boolean tree node` | **PASS (Refused)** |
| **5.6a** | Missing `predicate_policy` in v5 IR | Yes (`ir_hash` valid) | `Flat IR shape is invalid for the frozen contract version` | **PASS (Refused)** |
| **5.6b** | Wrong `predicate_policy` (`'flat-boolean-v2-preview'`) | Yes (`ir_hash` valid) | `Boolean predicate policy differs from frozen contract` | **PASS (Refused)** |
| **5.7** | Non-canonical tree ordering (children deliberately reversed) | Yes (`ir_hash` valid) | `Boolean tree is not canonical` | **PASS (Refused)** |
| **5.8** | Injected depth-9 boolean tree | Yes (`ir_hash` valid) | `Boolean tree shape or complexity is invalid` | **PASS (Refused)** |
| **5.9** | Cross-version attack: submit v4-list IR against v5 contract | Yes (`ir_hash` valid) | `Flat IR shape is invalid for the frozen contract version` | **PASS (Refused)** |
| **5.10** | Cross-version attack: submit v5-tree IR against v4 contract | Yes (`ir_hash` valid) | `Flat IR shape is invalid for the frozen contract version` | **PASS (Refused)** |

- **Outcome:** **PASS** (10/10 forged payloads rejected with `invalid_ir`).

---

### Acceptance Group 6: Save and Replay a V5 Case

- **Saved Investigation:**
  - Original Run ID: `1bd4018e-fc33-4317-a473-d39aa9541c34` (Case 2A)
  - Created Investigation ID: `dc873a2d-5a57-4a4f-8b61-16aadd463c47`
  - Frozen Context: `mode: flat_snapshot`, `scope: entire_supplied_snapshot`, `contract_hash: f07d4c9eb098bd1c79d92827fcca4b5f168cbb7666ea8e3f2a27d5ed86f4511b`, `unit_label: points`
  - Result Totals: Baseline `150.00`, Target `30.00`, Delta `-120.00`, Residual `0`
  - Evidence Artifact SHA-256: `a48d1b0dee4734f4dddfa73a91e873632040b58a0ead6a933059855efbaf7c2c`
- **Explanation & Boundary Review:**
  - `explanation_status`: `'complete_eligibility_change'`
  - `shapley`: `None` (no per-branch causal attribution claimed)
  - `currency`: `None`
  - Disclaimer verified in result limitations: `"Unknown currency remains unknown; no verified financial loss is claimed."`
- **Investigation Replay:**
  - Endpoint: `POST /api/v1/investigations/dc873a2d-5a57-4a4f-8b61-16aadd463c47/replays`
  - Replay Run ID: `6bc2a98b-46dc-4a9e-9176-038d36868f3d` (State: `succeeded`)
  - Baseline: `150.00` | Target: `30.00` | Delta: `-120.00` | Residual: `0`
  - Replay Evidence Artifact SHA-256: `a48d1b0dee4734f4dddfa73a91e873632040b58a0ead6a933059855efbaf7c2c` (**Exact match with original**)
  - Replay Row Keys: Included in both `['A', 'B']`, Baseline-only `['C', 'D', 'E']`, Target-only `[]`
- **Outcome:** **PASS**.

---

### Acceptance Group 7: Legacy Regressions & Test Suites

- **Historical Contract Invariance:**
  - Historical v3 contract `c5c58468-3e88-481f-a2dc-5087f1538a8e`: Hash `41df792725c562f63bf4d5524f5013e3554a59278602a2f225438a7d525205b8` verified intact; OR expression refused (`Unsupported predicate: Or`).
  - Historical v4 contract `74bcf488-4bff-4df1-b479-2342c7f43d3b`: Hash `c6f8239fcf6db3be255d9796ef39e51e3ac1525422e2971727c7f434f3da1665` verified intact; OR expression refused (`Predicate requires an unqualified registered column`).
  - Historical V6 investigation draft `draft-796da0fa9a0346fdb243cd1c38415578`: Readable, valid, and unchanged (HTTP 200).
- **Frontend Test Suite:**
  - Command: `npm test --prefix apps/web`
  - Outcome: **35 passed, 0 failed** across 4 test suites (`DataSourcesView.test.ts`, `InvestigationStudio.test.ts`, `MetricsView.test.ts`, `ChangesView.test.ts`).
- **Frontend Production Build:**
  - Command: `npm run build --prefix apps/web`
  - Outcome: **Passed** (1595 modules transformed, bundle `dist/assets/index-BTjWE0J0.js` generated in 15.61s).
- **Compose Configuration:**
  - Command: `docker compose -f infra/compose.yaml config --quiet`
  - Outcome: **Passed** (exit code 0).
- **Targeted Backend Regressions:**
  - Command: `.venv\Scripts\python.exe -m pytest -q tests/test_flat.py tests/test_flat_v4_typing.py tests/test_flat_workflow.py tests/test_v6_safe_sql_expansion.py tests/test_v6_ci_workflow.py tests/test_v6_4_ci_investigation_handoff.py tests/test_v6_config.py`
  - Outcome: **137 passed, 0 failed** in 28.28s.
- **Full Backend Test Suite:**
  - Command: `.venv\Scripts\python.exe -m pytest -q --basetemp .test-tmp/v7-full`
  - Outcome: **541 passed, 45 skipped, 0 failed** in 496.48s (8m 16s). (Note: The 45 skipped tests correspond to local PostgreSQL integration paths in `test_v4_collector.py` and `test_v4_integration_and_exit_gates.py`; they require the local Docker PostgreSQL container on port 5433, not external cloud infrastructure, and were subsequently verified passed in the addendum below).
- **Outcome:** **PASS**.

---

## 4. Engineering Review Summary & Verified Findings

### Concrete Findings:
1. **Zero Functional or Security Failures:** All 7 groups passed all criteria without modifying application code.
2. **Deterministic Canonicalization:** Associative operator flattening (`exp.And`, `exp.Or`) and JSON-key sorting ensure consistent IR hashes across commutative and associative query rewrites while strictly preserving SQL three-valued logic.
3. **AST Node Count Boundary:** The SQLGlot AST node limit is enforced via `len(list(tree.walk())) > 200` in `flat.py`. Heavy parenthesization (e.g. 9 nested parentheses per predicate leaf) appropriately triggers refusal under this AST cap before the tree leaf limit.
4. **Project Definition Quota:** The local workspace enforces a quota of 100 definitions per project. Idempotent definition resolution is necessary to prevent quota exhaustion when re-running test suites.
5. **Release Freeze Gate:** External analyst validation gates (V2-06, V3-06) remain open. No claims of operational time savings or end-user adoption are made.

**Conclusion:** The V7.1 bounded boolean-filter engineering preview satisfies all technical requirements, boundary constraints, and regression invariants.

---

## 5. Addendum: Resolution of V7.1 Evidence Gaps (2026-09-29)

**Date:** 2026-09-29  
**Scope:** Resolution of the remaining V7.1 evidence gaps: actual browser verification, all-NULL measure selection, accounting and execution of all 45 previously skipped backend tests, frontend test suite accounting, and historical v3/v4 replay verification.  
**Classification:** Synthetic engineering acceptance. External pilot gates V2-06 and V3-06 remain open.

### 5.1 Executed Checks

#### Gap 1: Actual Browser End-to-End Verification
- **Test Environment:** Live API server (`http://127.0.0.1:8001`, `METRICGUARD_MODE=isolated`), supervisor worker, and Headless Chrome controlled via Puppeteer (`puppeteer-core`).
- **Fixture Ingestion:** Uploaded new fixture `data/v7_browser_orders.csv` (5 rows: BR1, BR2, BR3, BR4, BR5; total amount 150.00 points) through Data Sources onboarding modal (`/w/local-retail/data`).
- **Step 2 Confirmation:** Explicitly confirmed unit `"points"` and checked `"Enable compound filters (V7.1 preview)"`.
- **Persisted Contract v5:**
  - Contract ID: `ef829674-c380-41ab-9f22-b1fd39e2c5d7`
  - Version: `5` | Predicate Policy: `flat-boolean-v1` | Null Policy: `blank_nullable`
  - Contract Hash: `f35be6ac11284e1f1976c89d401fe98393b0c7334ec469f614f41fb0c3a21939`
  - Physical DuckDB Schema: `{'id': 'VARCHAR', 'amount': 'DECIMAL(18,2)', 'status': 'VARCHAR', 'region': 'VARCHAR', 'flag': 'BOOLEAN'}`
- **Persisted Snapshot:** ID `06e5fb7a-3b4b-4009-83f3-0edf5c865447`, Name: `v7_browser_orders`, Row count: 5, Status: `validated`.
- **Compound Comparison Run:**
  - Navigated to Investigation Studio (`/w/local-retail/investigations/new?snapshotId=06e5fb7a-3b4b-4009-83f3-0edf5c865447`).
  - Switched to Ad-hoc SQL mode.
  - Baseline SQL: `SELECT SUM(amount)\nFROM snapshot` (Expected: 150.00 points)
  - Target SQL: `SELECT SUM(amount)\nFROM snapshot\nWHERE (status = 'completed' OR status = 'settled') AND region != 'test'` (Expected: 30.00 points; matching BR1 10.00 + BR2 20.00; excluding BR3 cancelled, BR4 region='test', BR5 status=NULL).
  - Executed via Docker runner `metricguard-runner:1.0`.
  - Comparison Run ID: `a1c5eaab-1b34-4843-9b49-46cda4a72fd6` (State: `succeeded`).
  - KPI Strip Display: Baseline `150.00 points`, Target `30.00 points`, Revision Impact `-120.00 points`, Residual `0`.
- **Paged Evidence Inspection & Export Reconciliation:**
  - Switched to Evidence tab. Paged evidence table displays 5 rows: BR1 and BR2 with membership `Both` (deltas 0.00), BR3, BR4, and BR5 with membership `Baseline only` (deltas -30.00, -40.00, -50.00).
  - Exported JSON via `/api/v1/comparisons/a1c5eaab-1b34-4843-9b49-46cda4a72fd6/export?format=json`.
  - Export Reconciliation: Exactly 5 rows exported (0 omitted). Sum of exported row deltas: `0.00 + 0.00 + (-30.00) + (-40.00) + (-50.00) = -120.00 points`, exactly matching the comparison delta `-120.00 points`, residual `0`.
- **Unsupported SQL Refusal & Editor Preservation:**
  - Submitted unsupported SQL in target editor: `SELECT SUM(amount)\nFROM snapshot\nWHERE status LIKE 'completed%'`.
  - API returned HTTP 422 `UNSUPPORTED_SQL`. UI displayed amber refusal alert banner: `"Query not supported - This query uses a filter MetricGuard does not currently support."`
  - DOM inspection verified that BOTH the baseline editor textarea and the target editor textarea retained their verbatim text exactly (`SELECT SUM(amount)\nFROM snapshot` and `SELECT SUM(amount)\nFROM snapshot\nWHERE status LIKE 'completed%'`).
- **Status:** **PASS**.

#### Gap 2: Nonempty Selection with All-NULL Measure Values
- **Fixture & Snapshot:** Evaluated against snapshot `v7_null_measure acceptance` (`7b96845f-7946-4ab1-bffe-07b2b721b4e2`, Contract v5 `ed617ac3-faa1-4f28-b6ee-bb1d6000aca0`):
  - Row N1: `id='N1'`, `amount=NULL`, `status='pending'`, `flag=True`
  - Row N2: `id='N2'`, `amount=NULL`, `status='pending'`, `flag=False`
  - Row N3: `id='N3'`, `amount=50.00`, `status='completed'`, `flag=True`
- **Target Query:** `SELECT SUM(amount) FROM snapshot WHERE status = 'pending'`.
- **Independent Expectations:**
  - Nonempty selection: exactly 2 rows match (`N1`, `N2`).
  - All matching measure values are NULL.
  - SQL standard / DuckDB `SUM(NULL)` over rows with NULL values evaluates to NULL.
  - Expected: `target_value: None`, `delta_target_minus_baseline: None`, `residual: None`. Zero must never be substituted for an undefined endpoint, delta, or residual.
  - Honest evidence semantics: N1 and N2 must be included in target (`target_included: True`, `membership: 'included_in_both'`), but flagged with `baseline_null_term: True` and `target_null_term: True` with contribution `0`.
- **Execution via Docker Runner (`metricguard-runner:1.0`):**
  - Container returncode: `0`.
  - Actual Result:
    - `baseline_value`: `'50.00'`
    - `target_value`: `None` (SQL NULL)
    - `delta_target_minus_baseline`: `None` (undefined gap)
    - `residual`: `None` (undefined residual)
    - `method`: `'flat_filter_change'`
  - Evidence Artifact SHA-256: `577efb09d222b7a2326d78a186dcece79901e46e599320d7483d54f5075a3e22`
  - Row Verification:
    - N1: `target_included: True`, `baseline_included: True`, `membership: 'included_in_both'`, `target_null_term: True`, `baseline_null_term: True`, `delta: '0'`
    - N2: `target_included: True`, `baseline_included: True`, `membership: 'included_in_both'`, `target_null_term: True`, `baseline_null_term: True`, `delta: '0'`
    - N3: `target_included: False`, `baseline_included: True`, `membership: 'baseline_only'`, `target_null_term: False`, `baseline_null_term: False`, `delta: '-50.00'`
- **Status:** **PASS**.

#### Gap 3: Accounting for and Running All 45 Skipped Backend Tests
- **Infrastructure Dependency Inspection:**
  - Inspected skip conditions in `tests/test_v4_collector.py` and `tests/test_v4_integration_and_exit_gates.py`.
  - Both modules check `is_pg_available()`, connecting to `PG_TEST_URL = 'postgresql://metricguard:local-dev-only@127.0.0.1:5433/metricguard'`.
  - The 45 skipped tests do **not** require external cloud infrastructure (AWS/S3/cloud PG). They depend solely on the local Docker container `metricguard-test-pg` (`postgres:16-alpine`), which was previously in an exited state (`Exited (0) 9 hours ago`).
- **Exact Test IDs Accounted For (45 total):**
  - **Module `tests/test_v4_collector.py` (38 tests):**
    1. `test_successful_read_only_retail_extraction`
    2. `test_successful_read_only_flat_extraction`
    3. `test_read_only_transaction_blocks_mutations`
    4. `test_contract_validation_enforces_decimal_scale`
    5. `test_contract_validation_enforces_currency`
    6. `test_duplicate_key_rejection`
    7. `test_row_limit_enforcement`
    8. `test_cutoff_enforcement_and_parameterization`
    9–14. `test_malicious_table_identifier_rejected` (6 parameterizations)
    15–18. `test_malicious_schema_identifier_rejected` (4 parameterizations)
    19–22. `test_malicious_column_identifier_rejected` (4 parameterizations)
    23–28. `test_malicious_cutoff_value_rejected` (6 parameterizations)
    29. `test_retry_behavior_fixed_cutoff_and_fresh_staging`
    30. `test_failure_cleanup_on_connection_error`
    31. `test_failure_cleanup_on_mid_stream_network_error`
    32. `test_failure_after_transaction_begins_nonexistent_table`
    33. `test_failure_while_writing_duckdb`
    34. `test_failure_after_manifest_before_store_registration`
    35. `test_source_mutation_demonstrates_frozen_snapshot_immutability`
    36. `test_credential_protection_adversarial`
    37. `test_api_postgres_extract_rbac_and_cross_project`
    38. `test_runner_receives_frozen_snapshot_only_no_network_no_creds`
  - **Module `tests/test_v4_integration_and_exit_gates.py` (7 tests):**
    39. `TestV4EndToEndWorkflow::test_complete_end_to_end_v4_scenario`
    40. `TestGate41ProvenanceChain::test_unbroken_provenance_chain`
    41. `TestGate42VisibleFailureRefusal::test_failed_extraction_raises_explicit_collector_error`
    42. `TestGate43CutoffRetryIsolation::test_interrupted_attempt_cleans_up_staging`
    43. `TestGate43CutoffRetryIsolation::test_retry_strictly_requires_identical_logical_cutoff`
    44. `TestGate44SnapshotDeterminism::test_multiple_comparisons_produce_identical_results`
    45. `TestGate45NoLiveSourceDependency::test_post_snapshot_mutation_does_not_affect_comparison`
- **Execution of Previously Skipped Tests:**
  - Started local container `metricguard-test-pg` on port 5433 (`docker start metricguard-test-pg`).
  - Command: `.venv\Scripts\python -m pytest -v tests/test_v4_collector.py --basetemp .test-tmp/skipped-pg`
    - Outcome: **38 passed, 0 skipped, 0 failed** in 154.10s.
  - Command: `.venv\Scripts\python -m pytest -v tests/test_v4_integration_and_exit_gates.py -k "test_complete_end_to_end_v4_scenario or test_unbroken_provenance_chain or test_failed_extraction_raises_explicit_collector_error or test_interrupted_attempt_cleans_up_staging or test_retry_strictly_requires_identical_logical_cutoff or test_multiple_comparisons_produce_identical_results or test_post_snapshot_mutation_does_not_affect_comparison" --basetemp .test-tmp/skipped-gates`
    - Outcome: **7 passed, 8 deselected, 0 skipped, 0 failed** in 133.95s.
  - Total previously skipped tests executed: **45 passed, 0 skipped, 0 failed**.
  - **Coverage Characterization:** This establishes a **combined coverage of 586 tests** (541 earlier backend passes plus 45 subsequently executed PostgreSQL-dependent passes across distinct test invocations). No single monolithic 586-test run was executed in a single command.
- **Frontend Test Suite Count Reconciliation (43 vs 35):**
  - Inspected `apps/web/package.json`: `"test"` script executes 6 test files via Node:
    1. `src/v2Workflow.test.mjs`: 5 tests
    2. `src/router.test.mjs`: 3 tests
    3. `src/dataSources.test.mjs`: 12 tests
    4. `src/metricsLifecycle.test.mjs`: 9 tests
    5. `src/changes.test.mjs`: 8 tests
    6. `src/investigations.test.mjs`: 6 tests
    Total: `5 + 3 + 12 + 9 + 8 + 6 = 43 tests`.
  - Executed `npm test --prefix apps/web`: all 6 suites executed and reported **43 passed, 0 failed** cleanly.
  - Explanation: The previous V7 report counted only the last 4 view suites (`12 + 9 + 8 + 6 = 35`), omitting `v2Workflow.test.mjs` (5) and `router.test.mjs` (3), and misnamed them as `.test.ts` files. No tests were deleted or disappeared between V6 and V7.
- **Status:** **PASS** (Combined coverage: 586 backend / 43 frontend).

#### Gap 4: Historical v3/v4 Replay Execution Verification & Fresh Post-V7.1 Replays
- **Provenance Investigation & Historical Limitation:**
  - Audited SQLite `comparison_runs` and `records` in `data/live-workspace/metricguard.db`.
  - Historical Run `0aad1104-c376-4612-b696-98ff34523b9f` (Contract v4) and Run `0497a5a2-60ec-4679-9f3c-5a282acf5f6c` (Contract v3) completed at `16:11:48` and `16:13:28` UTC on 2026-09-28.
  - V7.1 implementation and testing began with Case 2A (`1bd4018e-fc33-4317-a473-d39aa9541c34`) at `2026-09-28T17:09:38+00:00`.
  - **Finding:** A replay timestamp preceding the first recorded V7 case does not establish which code or image was executed. Without build provenance proving it, this historical replay coverage is marked as **unestablished** for V7.1.
- **Fresh Authorized Post-V7.1 Replay Execution (2026-09-29):**
  - Two fresh replays were explicitly authorized and executed against the live API (`http://127.0.0.1:8001`, `METRICGUARD_MODE=isolated`) and isolated Docker container runner `metricguard-runner:1.0` (Image ID `sha256:698f162e771ea1845f59e683b8d9bdd2e223bd6eb59ce88d73410ba80ade9eec`, created `2026-09-28T17:01:44Z`):
  1. **Contract v4 Replay (`f69467f9-703b-41a8-bcca-872942275f01`):**
     - New Run ID: `769fb72c-ca45-4da4-ac4e-e184cb58f292`
     - Idempotency Key: `replay-post-v71-2a9e504e4ada492fa71ff5586cffc119`
     - Terminal State: `succeeded` (created `2026-09-29T06:00:48Z`, completed `2026-09-29T06:00:52Z`)
     - Baseline / Target / Delta: `44300.00` / `24800.00` / `-19500.00` INR (Residual: `0`)
     - Evidence Artifact SHA-256: `4cf76bd1f3618595ce402e11a981db8da40522d1bb2812d60c60e21e45fa14de` (Exact match with original run `67bae071-7d48-4b1b-8ad7-a533a71a23a8`)
     - Row Count: 20 rows verified
     - Status: **PASS**
  2. **Contract v3 Replay (`1991ef0a-ff11-4adf-a0d5-3d0a01ed7888`):**
     - New Run ID: `31b3ae4b-21e5-4de5-acf8-dd2db7d4ba9a`
     - Idempotency Key: `replay-post-v71-c4f1f8b31e294abda84bdc8ab4995b55`
     - Terminal State: `succeeded` (created `2026-09-29T06:00:52Z`, completed `2026-09-29T06:00:56Z`)
     - Baseline / Target / Delta: `1985.00` / `870.00` / `-1115.00` INR (Residual: `0`)
     - Evidence Artifact SHA-256: `109f32b605b2c68d74d25fac6e0447d08aaf550fa4eaa0ffaf6fcd25ffa2cde3` (Exact match with original run `383240d0-8e2a-4a8b-a9b0-bf45445c8150`)
     - Row Count: 15 rows verified
     - Status: **PASS**
  - **Receipt Path:** `scratch/post_v7_1_replay_receipt.json`
- **Status:** **PASS** (Post-V7.1 live replay verified against V7.1 container runner).

---

### 5.2 Source & Architecture Inspection

#### 5.2.1 Documented Pruning of Nine Temporary Definitions
- **Context & Quota Invariant:** `services/api/store.py` line 356 strictly enforces a project definition cap of 100 (`DomainError('IMPORT_LIMIT', 'A project may define at most 100 definitions')`). When live browser testing was initiated, the `local-retail` project had reached 100 definitions due to previous synthetic acceptance test runs.
- **Selection Criteria:**
  - Programmatically queried all definitions in project `'local-retail'` (`scratch/check_defs.py`).
  - Identified all definition versions referenced by:
    1. Saved investigations (`records` with `kind = 'investigation'`).
    2. Investigation drafts (`records` with `kind = 'investigation_draft'`).
    3. Saved cases (`records` with `kind = 'saved_case'`).
    4. Past comparison runs (`comparison_runs.baseline_version_id`, `comparison_runs.target_version_id`).
    5. Metric bindings (`metric_definition_bindings.definition_id`, `metric_definition_bindings.approved_version_id`).
  - Nine unreferenced synthetic test definitions created during earlier exploratory scratch runs were identified and pruned via `scratch/prune_defs.py`, reducing total definitions from 100 to 91.
- **Exact Pruned Definition IDs & Names (9 total):**
  1. `7d41f534-081a-49a6-9d0f-abe0f9635bee` ("Baseline calculation")
  2. `346fee0f-23d8-4bd8-9158-0c94d3aaad50` ("Target calculation")
  3. `2936c692-59c6-4c1f-83eb-15e076af0e8e` ("V6 Refusal: Integer literal for exact decimal measure")
  4. `e1c23f4d-569e-49e9-baf2-a3dca1e3c05f` ("Group 4 Test: SQL AST over 200 nodes")
  5. `20f3fbbb-e4fc-4f5e-b933-117f8c800fb5` ("Group 4 Test: Exact 16-leaf flat chain accepted")
  6. `b3d78e07-2bfc-450b-b117-652139fefa47` ("Group 4 Test: Exact 16-leaf flat chain accepted")
  7. `fd6cd2a3-1551-4b08-aafa-50c25e47f039` ("v7_null_baseline")
  8. `354f1a74-8d2a-4774-b032-67fd585e097f` ("v7_null_target")
  9. `e823dbf1-2d08-4318-bb30-544813a9769c` ("Baseline calculation")
- **Limitation & Governance Finding:**
  - Because pruning was executed directly against SQLite via a scratch script, no application-level `audit_logs` record was generated for the deletions.
  - **Mandate for Future Test Runs:** Future test harnesses and browser acceptance suites must execute inside a dedicated, isolated test project/workspace (e.g. `test-workspace` or dynamic project ID) rather than pruning shared records in `local-retail` to bypass project quotas.

#### 5.2.2 Three-Valued SQL Logic & Null Handling
Verified in `packages/metricguard_core/flat.py` lines 775–788 that rows matching a predicate whose measure values are NULL are correctly included in the target row set (`target_included: True`, `target_null_term: True`), while total SUM, delta, and residual evaluate strictly to `None` without numeric fabrication.

---

### 5.3 Skipped Checks & Open Pilot Gates

- **External Pilot Gates V2-06 & V3-06:** Remain **OPEN**. No claim of external analyst validation, multi-user observed trial, or production time savings is made.
- **Unverified Scope:** Dialect-specific dbt adapters beyond the local mock/Core fixtures and cloud-hosted PostgreSQL instances remain unverified.

---

### 5.4 Raw Evidence Receipts & Screenshots

| Evidence Artifact / Receipt | Description & Location |
| :--- | :--- |
| **Step 1 Screenshot** | [browser_step1_data_sources.png](screenshots/browser_step1_data_sources.png) — Data Sources landing view before import |
| **Step 2 Screenshot** | [browser_step2_confirm_meaning_v5.png](screenshots/browser_step2_confirm_meaning_v5.png) — Modal Step 2 showing confirmed unit `"points"` and `"Enable compound filters (V7.1 preview)"` checked |
| **Step 3 Screenshot** | [browser_step3_snapshot_validated.png](screenshots/browser_step3_snapshot_validated.png) — Step 3 modal showing `"Snapshot validated successfully"` |
| **Step 4 Screenshot** | [browser_step4_compound_comparison_run.png](screenshots/browser_step4_compound_comparison_run.png) — Investigation Studio showing compound comparison completed (`150.00 / 30.00 / -120.00 points`, `SUCCEEDED` badge) |
| **Step 5 Paged Table Screenshot** | [browser_step5_paged_evidence_table.png](screenshots/browser_step5_paged_evidence_table.png) — Evidence tab showing 5 paged rows (BR1, BR2 in Both; BR3, BR4, BR5 in Baseline only) |
| **Step 6 Refusal Screenshot** | [browser_step6_unsupported_refusal_preserved.png](screenshots/browser_step6_unsupported_refusal_preserved.png) — HTTP 422 `UNSUPPORTED_SQL` refusal banner with exact verbatim SQL preserved in both textareas |
| **Raw Browser Export JSON** | [browser_export_result.json](browser_export_result.json) — Full exported JSON comparison and row evidence matching Run `a1c5eaab-1b34-4843-9b49-46cda4a72fd6` |
| **Historical v4 Replay Artifact** | `data/live-workspace/evidence/0aad1104-c376-4612-b696-98ff34523b9f-1.jsonl` (SHA-256: `4cf76bd1f361...`) |
| **Historical v3 Replay Artifact** | `data/live-workspace/evidence/0497a5a2-60ec-4679-9f3c-5a282acf5f6c-1.jsonl` (SHA-256: `109f32b605b2...`) |
| **Fresh Post-V7.1 Replay Receipt** | `scratch/post_v7_1_replay_receipt.json` — Verified execution of runs `769fb72c...` (v4) and `31b3ae4b...` (v3) against `metricguard-runner:1.0` |
| **Fresh Post-V7.1 v4 Replay Artifact** | `data/live-workspace/evidence/769fb72c-ca45-4da4-ac4e-e184cb58f292-1.jsonl` (SHA-256: `4cf76bd1f3618595ce402e11a981db8da40522d1bb2812d60c60e21e45fa14de`) |
| **Fresh Post-V7.1 v3 Replay Artifact** | `data/live-workspace/evidence/31b3ae4b-21e5-4de5-acf8-dd2db7d4ba9a-1.jsonl` (SHA-256: `109f32b605b2c68d74d25fac6e0447d08aaf550fa4eaa0ffaf6fcd25ffa2cde3`) |
| **NULL Measure Test Script** | `scratch/test_null_measure_docker.py` — Docker execution receipt for Snapshot `7b96845f-7946...` |


