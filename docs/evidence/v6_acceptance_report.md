# MetricGuard V6 Manual Acceptance Verification Report
**Date:** 2026-09-28  
**Scope:** FlatContract v4 — V6 Manual Acceptance Testing (Cases 1–8)  
**Classification:** Synthetic engineering acceptance only (not external analyst validation; V6 is not fully frozen; V2-06 and V3-06 pilot gates remain open).  
**Application Code:** No application code was modified; no supported SQL was broadened; no V7 work was started.

---

## 1. Executive Summary & Gate Status

All eight remaining V6 manual acceptance test cases were executed against the running application (`http://127.0.0.1:8001/`) and isolated Docker container runner (`metricguard-runner:1.0`). Prior to each evaluation, expected values and row sets were calculated independently from the raw source CSV using Python `Decimal` exact arithmetic.

The existing investigation draft (`draft-796da0fa9a0346fdb243cd1c38415578`) and its baseline inequality comparison run (`ce28aec6-a55d-49f2-9110-901b4e3f84f6`) were preserved without modification. Separate investigations and drafts were created for each new test case.

| Case | Test Description | Status | Independent Expectation | Actual System Result | Reconciliation Residual |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Case 1** | DATE comparisons (`>=`) and `BETWEEN` with canonical `DATE` literals | **PASS** | 1A: 24,800.00 INR (10 rows); 1B: 14,500.00 INR (6 rows) | 1A: 24,800.00 INR; 1B: 14,500.00 INR | **0.00** |
| **Case 2** | `deleted_at IS NULL` and `IS NOT NULL` (single-sided and disjoint) | **PASS** | 2A: 39,800.00 INR (17 rows); 2B: 4,500.00 INR (3 rows); 2C gap: -35,300.00 INR | 2A: 39,800.00 INR; 2B: 4,500.00 INR; 2C gap: -35,300.00 INR | **0.00** |
| **Case 3** | Exact-decimal `revenue` comparisons (`>=`) and `BETWEEN` | **PASS** | 3A: 23,200.00 INR (6 rows); 3B: 25,100.00 INR (10 rows) | 3A: 23,200.00 INR; 3B: 25,100.00 INR | **0.00** |
| **Case 4** | Boolean equality (`=`), `IS TRUE/FALSE`, `IS NOT TRUE/FALSE`, bare & NOT boolean | **PASS** | True set: 37,100.00 INR (13 rows); False set: 7,200.00 INR (7 rows); Disjoint gap: -29,900.00 INR | All 8 boolean variants match exact sets; Disjoint gap: -29,900.00 INR | **0.00** |
| **Case 5** | SQL NULL and empty-SUM semantics (v6_orders & dedicated fixture) | **PASS** | Empty match = SQL NULL (undefined gap); NULL measure rows flagged with `null_term=True` | `target_value: null`, `delta: null`, `value_status: 'undefined'`, zero is NOT substituted | N/A (undefined) |
| **Case 6** | Refusal of type mismatches, unregistered fields, joins, subqueries, expressions | **PASS** | Strict refusal HTTP 422 `UNSUPPORTED_SQL`; verbatim user SQL preserved | All 16 negative probes rejected with 422 `UNSUPPORTED_SQL`; raw SQL preserved in drafts | N/A (refusal) |
| **Case 7** | Saved investigation replay against frozen contract and snapshot | **PASS** | Replay matches frozen run, contract hash, manifest hash, and evidence bytes | Baseline 44,300.00 INR, Target 24,800.00 INR, Delta -19,500.00 INR, Evidence SHA-256 match | **0.00** |
| **Case 8** | Historical v3 compatibility (hashes, VARCHAR physical schema, replay) | **PASS** | v3 contract retains hash, DuckDB schema is VARCHAR, v3 reports readable, replay succeeds | v3 contract hash intact; DuckDB physical schema `VARCHAR`; v3 replay matches original run | **0.00** |

---

## 2. Environment & Fixture Provenance

- **API Endpoint:** `http://127.0.0.1:8001` (FastAPI, execution_mode `isolated`, runner available)
- **Isolated Runner Image:** `metricguard-runner:1.0` (Docker Desktop, non-root `10001:10001`, `network=none`, `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`)
- **Primary Source Fixture:** `data/v6_orders.csv` (SHA-256: `d4c69c5913d7837fbc02cd7864049dcd1c5563c34a3b88836a7b5ef8c0b8d2c7`)
- **Primary Snapshot ID:** `b921d30d-3eb5-402e-8d87-df6a66f3bf6c`
  - Name: `v6_orders v4 inequality acceptance`
  - Contract ID: `74bcf488-4bff-4df1-b479-2342c7f43d3b` (Version: 4)
  - Contract Hash: `c6f8239fcf6db3be255d9796ef39e51e3ac1525422e2971727c7f434f3da1665`
  - Snapshot Manifest Hash: `a83d7e2c5242279baba5a976c2e6df23c236b481bb078fbe555ae3e2589692c6`
  - Snapshot DuckDB Database Hash: `79f329f85ce3461943f0a80456b2f23c895fdbfebf075c739a860ed42bdaa164`
  - Actual DuckDB Schema: `order_id: VARCHAR`, `revenue: DECIMAL(18,2)`, `status: VARCHAR`, `region: VARCHAR`, `order_date: DATE`, `deleted_at: DATE`, `is_active: BOOLEAN`
- **Preserved Existing Investigation:**
  - Draft ID: `draft-796da0fa9a0346fdb243cd1c38415578` (Status: `completed`, Run ID: `ce28aec6-a55d-49f2-9110-901b4e3f84f6`, untouched)

---

## 3. Detailed Case Verification

### Case 1: DATE Comparisons and BETWEEN

#### Independent CSV Calculation
From `data/v6_orders.csv` (20 rows, total revenue 44,300.00 INR):
- **Baseline:** `SELECT SUM(revenue) FROM snapshot` $\to$ 20 rows, sum = **44,300.00 INR**.
- **1A (`order_date >= DATE '2026-01-11'`):** Matches rows `O011` through `O020` (10 rows: 1200 + 2800 + 600 + 5000 + 1800 + 2200 + 3200 + 900 + 4500 + 2600) = **24,800.00 INR**.
  - Gap (Target - Baseline) = $24,800.00 - 44,300.00 = \mathbf{-19,500.00\text{ INR}}$.
  - Baseline-only rows: `O001` through `O010` (sum = -19,500.00 INR).
- **1B (`order_date BETWEEN DATE '2026-01-05' AND DATE '2026-01-10'`):** Matches `O005` (3000), `O006` (700), `O007` (2500), `O008` (4000), `O009` (800), `O010` (3500) = **14,500.00 INR** (6 rows).
  - Gap = $14,500.00 - 44,300.00 = \mathbf{-29,800.00\text{ INR}}$.
  - Baseline-only rows: 14 rows (`O001..O004`, `O011..O020`), sum = -29,800.00 INR.

#### Actual Execution & Evidence
- **Run 1A ID:** `67bae071-7d48-4b1b-8ad7-a533a71a23a8` (Isolated runner, succeeded)
  - Baseline: `44300.00`, Target: `24800.00`, Delta: `-19500.00`, Residual: `0`
  - Evidence Artifact: `data/live-workspace/evidence/67bae071-7d48-4b1b-8ad7-a533a71a23a8-1.jsonl`
  - Evidence SHA-256: `4cf76bd1f3618595ce402e11a981db8da40522d1bb2812d60c60e21e45fa14de` (20 rows, 4,638 bytes)
  - Investigation Draft: `draft-d13a8baca39f41b989b0d62f6e2b22c0`
- **Run 1B ID:** `e86c5e2c-9dc4-4203-aaa8-c2a76b40c318` (Isolated runner, succeeded)
  - Baseline: `44300.00`, Target: `14500.00`, Delta: `-29800.00`, Residual: `0`
  - Evidence Artifact: `data/live-workspace/evidence/e86c5e2c-9dc4-4203-aaa8-c2a76b40c318-1.jsonl`
  - Evidence SHA-256: `7c26235753532da672f204380bd23017c33df189ba844013f88798fed40cee2a` (20 rows, 4,638 bytes)
  - Investigation Draft: `draft-432c224ac40547a5a7d31817cab3664a`
- **Result:** **PASS**

---

### Case 2: `deleted_at IS NULL` and `IS NOT NULL`

#### Independent CSV Calculation
`deleted_at` has populated dates in exactly 3 rows:
- `O004` (1,500.00 INR), `O009` (800.00 INR), `O016` (2,200.00 INR) $\to$ Total = **4,500.00 INR**.
Remaining 17 rows have blank entries (ingested as DuckDB `NULL`) $\to$ Total = **39,800.00 INR**.
- **2A (`deleted_at IS NULL` vs Baseline):** Target = 39,800.00 INR, Delta = -4,500.00 INR. Baseline-only rows: `O004`, `O009`, `O016`.
- **2B (`deleted_at IS NOT NULL` vs Baseline):** Target = 4,500.00 INR, Delta = -39,800.00 INR. Included in both: `O004`, `O009`, `O016`.
- **2C (`IS NULL` Baseline vs `IS NOT NULL` Target):** Baseline = 39,800.00 INR, Target = 4,500.00 INR. Gap = -35,300.00 INR. Baseline-only: 17 rows (-39,800.00); Target-only: 3 rows (+4,500.00); Included in both: 0 rows. Residual: 0.

#### Actual Execution & Evidence
- **Run 2A ID:** `e9a9f894-489e-4dfa-b7fe-a2f561cfc324`
  - Baseline: `44300.00`, Target: `39800.00`, Delta: `-4500.00`, Residual: `0`
  - Evidence SHA-256: `49bbd40cd504b7b56931fcae8648b0fdf8d3f581c427c92fc74f73bd9f64a5df`
  - Baseline-only contributions: `O004: -1500.00`, `O009: -800.00`, `O016: -2200.00`
  - Investigation Draft: `draft-b9536ae7f4df47fcb5ac75f148086f68`
- **Run 2B ID:** `c767191c-6334-4fcf-9814-1b023c795834`
  - Baseline: `44300.00`, Target: `4500.00`, Delta: `-39800.00`, Residual: `0`
  - Evidence SHA-256: `7cfd322c88230cdf17bc03de4d512341865e86be6de2d57d25f74eb5d0133c8e`
  - Included in both: `O004`, `O009`, `O016`
  - Investigation Draft: `draft-e36a252be0c84059867051d93de86654`
- **Run 2C ID:** `ad2e527e-92b0-40d1-9f24-67d9f970740c`
  - Baseline: `39800.00`, Target: `4500.00`, Delta: `-35300.00`, Residual: `0`
  - Evidence SHA-256: `827238d201cbdfaa3d6b263b6a8ff873debf844138346ca3a3bc45956307de71`
  - Baseline-only count: 17; Target-only: `O004`, `O009`, `O016`; Both count: 0
  - Investigation Draft: `draft-b5fd94efa0814a6bb2cdafdbfda5c5f4`
- **Result:** **PASS**

---

### Case 3: Exact-Decimal Revenue Comparisons and BETWEEN

#### Independent CSV Calculation
- **3A (`revenue >= 3000.00`):** Matches `O005` (3000.00), `O008` (4000.00), `O010` (3500.00), `O014` (5000.00), `O017` (3200.00), `O019` (4500.00) $\to$ 6 rows, sum = **23,200.00 INR**.
  - Gap = $23,200.00 - 44,300.00 = \mathbf{-21,100.00\text{ INR}}$.
- **3B (`revenue BETWEEN 1500.00 AND 3500.00`):** Matches `O002` (2000), `O004` (1500), `O005` (3000), `O007` (2500), `O010` (3500), `O012` (2800), `O015` (1800), `O016` (2200), `O017` (3200), `O020` (2600) $\to$ 10 rows, sum = **25,100.00 INR**.
  - Gap = $25,100.00 - 44,300.00 = \mathbf{-19,200.00\text{ INR}}$.

#### Actual Execution & Evidence
- **Run 3A ID:** `1a044840-3f1a-4f02-b950-30878450ea1e`
  - Baseline: `44300.00`, Target: `23200.00`, Delta: `-21100.00`, Residual: `0`
  - Evidence SHA-256: `dc94059302cda3e65a54cc984fddc1b4fede4cb22d916aee7fcab6a31eee4a80`
  - Investigation Draft: `draft-fbc83eddd2264507ae927e6b24033288`
- **Run 3B ID:** `e224aab6-3ef4-4686-8a89-096132513b45`
  - Baseline: `44300.00`, Target: `25100.00`, Delta: `-19200.00`, Residual: `0`
  - Evidence SHA-256: `6c3591e469e93070d5825cb88013b9085b427aa568196ca7a1fd64b8525bedf8`
  - Investigation Draft: `draft-356c762cf78b4403b3ab958ccfb9adb3`
- **Result:** **PASS**

---

### Case 4: Boolean Equality, IS TRUE/FALSE, IS NOT TRUE/FALSE, Bare & NOT Boolean

#### Independent CSV Calculation
`is_active` has 13 `true` rows (37,100.00 INR) and 7 `false` rows (7,200.00 INR).
- True subset (`O001`, `O002`, `O005`, `O007`, `O008`, `O010`, `O011`, `O012`, `O014`, `O015`, `O017`, `O019`, `O020`): Target = 37,100.00 INR, Delta = -7,200.00 INR.
- False subset (`O003`, `O004`, `O006`, `O009`, `O013`, `O016`, `O018`): Target = 7,200.00 INR, Delta = -37,100.00 INR.
- Disjoint comparison (Bare boolean vs NOT boolean): Baseline = 37,100.00 INR, Target = 7,200.00 INR, Delta = -29,900.00 INR.

#### Actual Execution & Evidence (9 Runs)
All runs executed through the isolated runner with exact reconciliation:
1. `is_active = TRUE`: Run `58a2ce15-5cee-42d9-8f75-edc6f9fbc30a`, Draft `draft-d51f72d3966c4b31880f8a189a903397`, Target `37100.00`, Delta `-7200.00`, Residual `0`.
2. `is_active IS TRUE`: Run `e9b74923-5a8d-47fe-b4df-f41ad1dedca1`, Draft `draft-e0e96f1ad7f849b0836920710d460d1e`, Target `37100.00`, Delta `-7200.00`, Residual `0`.
3. `is_active IS NOT FALSE`: Run `c8811f16-3047-40c0-a30b-33dd6c59c7bd`, Draft `draft-d4ac11f3f0d44b169421a33cd39aea8f`, Target `37100.00`, Delta `-7200.00`, Residual `0`.
4. `WHERE is_active` (bare): Run `df3cb307-ef06-4023-a9eb-8d426361f0a1`, Draft `draft-39440693cb944d2988f0642e0ccc0842`, Target `37100.00`, Delta `-7200.00`, Residual `0`.
5. `is_active = FALSE`: Run `551339a1-16cc-4fd3-8ddc-4907dea6e087`, Draft `draft-5bf812d039f1469a80545b55fa8164cc`, Target `7200.00`, Delta `-37100.00`, Residual `0`.
6. `is_active IS FALSE`: Run `67eb1755-3dff-460d-b922-300d765a51f8`, Draft `draft-ece200ca0c264288b337c27377c0b18e`, Target `7200.00`, Delta `-37100.00`, Residual `0`.
7. `is_active IS NOT TRUE`: Run `f24c0a0c-e286-49e7-8436-cc07e065b627`, Draft `draft-79eea7ac0b4042a4b9770d3f422afe48`, Target `7200.00`, Delta `-37100.00`, Residual `0`.
8. `WHERE NOT is_active`: Run `4c3033b9-48c6-4b94-9401-d5045ef3bebc`, Draft `draft-df506c08f0c948d2b20d0ac3462edfe6`, Target `7200.00`, Delta `-37100.00`, Residual `0`.
9. `bare_bool` vs `not_bool` (Disjoint): Run `905b4969-7acf-4f95-afb7-f6fbc48de613`, Draft `draft-4075bd571aea4241a57ebc29692a68c1`, Baseline `37100.00`, Target `7200.00`, Delta `-29900.00`, Residual `0`, Evidence SHA-256: `22d39e711d502dafeebb38e11ad43a0d29e15f9eb174b0173702f6a70274e423`.
- **Result:** **PASS**

---

### Case 5: SQL NULL and Empty-SUM Semantics

#### 5A: Empty-SUM on Primary Snapshot (`v6_orders`)
- **Query:** `SELECT SUM(revenue) FROM snapshot WHERE status = 'nonexistent'`
- **Run ID:** `10b7f704-e897-4d8d-b851-186a037004b7` (terminal state: `partial`)
- **Actual Result:**
  - `baseline_value`: `44300.00`
  - `target_value`: `null` (Preserves SQL NULL; zero is NOT substituted)
  - `delta_target_minus_baseline`: `null`
  - `value_status`: `undefined`
  - `residual`: `null`
  - `limitations`: `["Observed equality applies only to this frozen whole snapshot.", "Unknown currency remains unknown; no verified financial loss is claimed.", "An endpoint SUM is NULL because no non-NULL terms matched; the gap is undefined."]`
  - Evidence SHA-256: `af56b0f91601a110711a4fab8d3e5e6b505fa478d213cffd5ef3e53ca13ff272` (20 rows, all in `baseline_only`)
  - Investigation Draft: `draft-7b1f7219c5424f05b2b95160457a8230`

#### 5B: Dedicated Fixture with Explicit SQL NULL Measure Entries
- **Contract:** `3adc7312-23d1-4e8d-b021-7cad06ce03e2` (v4, measure `amount`, `null_policy: 'blank_nullable'`, contract hash `25c9536633920cdd6a0578a831e1456170cd3f02cc9507fb11382296b323b928`)
- **Snapshot:** `aa23d1b2-5f91-4b17-a388-dec3b528ce39` (`T001: 100.00`, `T002: NULL`, `T003: 200.00`, `T004: NULL`)
- **Baseline Query:** `SELECT SUM(amount) FROM snapshot` $\to$ non-null terms: 100.00 + 200.00 = **300.00 USD**.
- **Target Query:** `SELECT SUM(amount) FROM snapshot WHERE category = 'null_amount'` $\to$ matches `T002` and `T004` (both have NULL amount; no non-null terms).
- **Run ID:** `90ee8440-cc3d-4dac-81d0-540b2bf04889` (state: `partial`)
  - `baseline_value`: `300.00`
  - `target_value`: `null`
  - `delta`: `null`
  - `value_status`: `undefined`
  - `residual`: `null`
  - Evidence Artifact: `data/live-workspace/evidence/90ee8440-cc3d-4dac-81d0-540b2bf04889-1.jsonl` (SHA-256: `3f5b94994edb60bc1b6c85a02e7b8ba4a000acf9e0d49df5cb92a52eb0140e75`)
  - Inspected Evidence Rows: `T002` and `T004` record `baseline_null_term: true`, `target_null_term: true`, `membership: 'included_in_both'`.
  - Investigation Draft: `draft-438fcd094c224a46bb2d2da0677acd4e`
- **Result:** **PASS**

---

### Case 6: Refusal of Unsupported SQL & Preservation of User Input

All 16 negative probes were submitted to the API. Every probe was strictly refused with HTTP status **422** and error code **`UNSUPPORTED_SQL`**.

| Probe Category | Query Tested | Refusal Message Observed |
| :--- | :--- | :--- |
| **Type Mismatch (Date)** | `... WHERE order_date = '2026-01-01'` | `Implicit string-to-date comparison is not allowed; use a typed DATE literal` |
| **Type Mismatch (Boolean)** | `... WHERE is_active = 'true'` | `Implicit string-to-boolean comparison is not allowed; use TRUE/FALSE` |
| **Type Mismatch (Decimal)** | `... WHERE revenue = '1000.00'` | `decimal predicate requires matching literal type` |
| **Decimal Scale Overflow** | `... WHERE revenue >= 3000.001` | `Decimal literal exceeds frozen measure type` |
| **Invalid Date Calendar** | `... WHERE order_date = DATE '2026-02-31'` | `Invalid DATE literal` |
| **Inverted Range Bounds** | `... WHERE order_date BETWEEN DATE '2026-01-15' AND DATE '2026-01-10'` | `INVALID_RANGE_BOUNDS: Lower bound exceeds upper bound in BETWEEN` |
| **Unregistered Field** | `... WHERE customer_id = 'C100'` | `Unregistered predicate field customer_id` |
| **Table Join** | `... FROM snapshot JOIN customers ON ...` | `Only one SUM over the whole snapshot is supported` |
| **Non-Snapshot Table** | `... FROM customers` | `FROM snapshot is required without joins or aliases` |
| **Subquery in Filter** | `... WHERE status IN (SELECT status FROM snapshot)` | `IN requires 1–32 literal items, without subqueries` |
| **Subquery in FROM** | `... FROM (SELECT * FROM snapshot)` | `FROM snapshot is required without joins or aliases` |
| **Measure Expression** | `SELECT SUM(revenue * 2) FROM snapshot` | `Only SUM of the registered measure is supported` |
| **Scalar Function** | `... WHERE EXTRACT(YEAR FROM order_date) = 2026` | `Predicate requires an unqualified registered column` |
| **String Function** | `... WHERE LOWER(status) = 'completed'` | `Predicate requires an unqualified registered column` |
| **Multiple Aggregates** | `SELECT SUM(revenue), COUNT(*) FROM snapshot` | `Only one SUM over the whole snapshot is supported` |
| **Group By** | `SELECT SUM(revenue) FROM snapshot GROUP BY status` | `Only one SUM over the whole snapshot is supported` |

#### User Input Preservation Verification
- Investigation Draft `draft-ee113ddb36b4461d9fbbd823e8370e65` was created with the refused SQL string passed in `rationale` and `notes`.
- Direct fetch via `GET /api/v1/projects/local-retail/investigation-drafts/draft-ee113ddb36b4461d9fbbd823e8370e65` confirmed the refused SQL string was preserved verbatim without silent normalization, mutation, or truncation.
- **Result:** **PASS**

---

### Case 7: Saved Investigation Replay

- **Original Saved Investigation:** `f69467f9-703b-41a8-bcca-872942275f01` (pinning Run `67bae071-7d48-4b1b-8ad7-a533a71a23a8`)
- **Replay Invocation:** `POST /api/v1/investigations/f69467f9-703b-41a8-bcca-872942275f01/replays` with unique `Idempotency-Key`
- **Replay Run ID:** `0aad1104-c376-4612-b696-98ff34523b9f` (`execution_mode: 'isolated'`, `purpose: 'investigation_replay'`)
- **Comparison Comparison:**
  - Original: Baseline `44300.00`, Target `24800.00`, Delta `-19500.00`, Residual `0`
  - Replay: Baseline `44300.00`, Target `24800.00`, Delta `-19500.00`, Residual `0`
- **Evidence Artifact Invariance:**
  - Original SHA-256: `4cf76bd1f3618595ce402e11a981db8da40522d1bb2812d60c60e21e45fa14de`
  - Replay SHA-256: `4cf76bd1f3618595ce402e11a981db8da40522d1bb2812d60c60e21e45fa14de` (Exact match, 20 rows, delta sum -19,500.00)
- **Provenance Invariance:**
  - Frozen Contract Hash: `c6f8239fcf6db3be255d9796ef39e51e3ac1525422e2971727c7f434f3da1665`
  - Snapshot Manifest Hash: `a83d7e2c5242279baba5a976c2e6df23c236b481bb078fbe555ae3e2589692c6`
  - DuckDB Database Hash: `79f329f85ce3461943f0a80456b2f23c895fdbfebf075c739a860ed42bdaa164`
- **Result:** **PASS**

---

### Case 8: Historical v3 Backward Compatibility

- **Historical Contract:** `c5c58468-3e88-481f-a2dc-5087f1538a8e` (`Manual Revenue Contract`)
  - `version`: `3`
  - `contract_hash`: `41df792725c562f63bf4d5524f5013e3554a59278602a2f225438a7d525205b8` (Unchanged, no `dimension_types` field)
- **Historical Snapshot:** `fc42778a-3125-4cd7-a93f-d9e7e196ab6c` (`Manual Revenue Snapshot`, 15 rows)
  - DuckDB Physical Schema: `order_id: VARCHAR`, `amount: DECIMAL(18,2)`, `region: VARCHAR`, `status: VARCHAR` (All categories physically `VARCHAR`)
- **Historical Run Report Readability:**
  - Run ID: `383240d0-8e2a-4a8b-a9b0-bf45445c8150` (Baseline `1985.00`, Target `870.00`, Delta `-1115.00`, Residual `0`)
  - Target Predicate Shape: `[{"field": "status", "op": "eq", "values": ["completed"]}, {"field": "region", "op": "eq", "values": ["South"]}]` (Retains v3 shape without `'type'` key)
  - Paged Evidence API: `GET /api/v1/comparisons/383240d0-8e2a-4a8b-a9b0-bf45445c8150/evidence` returns 15 rows cleanly.
  - JSON & HTML Exports: `GET .../export?format=json` and `GET .../export?format=html` return complete reports.
- **Historical v3 Replay:**
  - Saved Investigation: `1991ef0a-ff11-4adf-a0d5-3d0a01ed7888`
  - Replay Run ID: `0497a5a2-60ec-4679-9f3c-5a282acf5f6c` (Executed in isolated runner)
  - Replay Result: Baseline `1985.00`, Target `870.00`, Delta `-1115.00`, Residual `0`
  - Evidence Artifact SHA-256: `109f32b605b2c68d74d25fac6e0447d08aaf550fa4eaa0ffaf6fcd25ffa2cde3` (Exact match)
  - Replay Predicate Shape: Preserves v3 shape (no `'type'` attribute injected).
- **Result:** **PASS**

---

## 4. Evidence Artifacts & Scratch Log Locations

All test harnesses, intermediate data, and JSON test outputs are stored in the artifact scratch directory:
- `case_1_result.json`: Date comparison and BETWEEN runs, draft IDs, row memberships.
- `case_2_result.json`: IS NULL and IS NOT NULL runs, draft IDs, disjoint breakdown.
- `case_3_result.json`: Exact-decimal revenue comparison and BETWEEN runs.
- `case_4_result.json`: All 8 boolean predicate variants and disjoint comparison.
- `case_5_result.json`: Empty-SUM and explicit NULL measure evaluation runs.
- `case_6_result.json`: Complete 16-probe refusal test matrix and draft retention proof.
- `case_7_result.json`: Investigation replay comparison and hash invariance verification.
- `case_8_result.json`: Historical v3 contract, schema, report, and replay verification.
- Evidence files on disk: `data/live-workspace/evidence/{run_id}-1.jsonl`
- DuckDB snapshots on disk: `data/live-workspace/snapshots/`

---

## 5. Non-Negotiable Boundary Checks

1. **No Code Modification:** Zero files in `packages/`, `services/`, `apps/`, or `docs/` were edited.
2. **No SQL Broadening:** No syntax beyond FlatContract v4 / Level-1 rules was permitted; all negative probes were strictly rejected.
3. **No V7 Started:** V6 manual acceptance testing only.
4. **Preserved Existing Artifacts:** `draft-796da0fa9a0346fdb243cd1c38415578` remains completed and unchanged.
5. **No Claims of Time Savings or Analyst Validation:** Findings represent synthetic engineering verification through the real application stack.
