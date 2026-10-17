# MetricGuard V7.2 Safe Additive Expressions: Architecture & Finalized Design Specification

**Document ID:** `SPEC-2026-09-29-V7-2-FINAL`  
**Date:** 2026-09-29  
**Status:** IMPLEMENTED (Functional Verification Pending Delegated Testing)  
**Author:** Engineering Assistant (Antigravity)  
**Classification:** Speculative Engineering Preview Implementation  
**Pilot Notice:** External pilot gates **V2-06** and **V3-06** remain explicitly **OPEN**. This implementation is prepared as a speculative engineering preview; no claims of real-world pilot justification, customer adoption, or time savings are made.

---

## 1. Supported Expression Scope

The V7.2 slice is strictly restricted to the smallest complete and mathematically exact expression:

1. **Syntax:** Exactly one subtraction of two registered decimal columns inside aggregate `SUM`:
   ```sql
   SELECT SUM(<colA> - <colB>)
   FROM snapshot
   [WHERE <bounded_predicate_tree>]
   ```
2. **Operand Requirements:**
   - Both `<colA>` (minuend) and `<colB>` (subtrahend) must be distinct registered decimal columns in the snapshot's contract.
   - Both operands must declare **identical scale** ($s_A = s_B = s$) and **compatible, explicitly declared units** (e.g. both `INR`, both `points`, or both `units`).
   - Subtracting a column from itself (`colA - colA`) is mathematically degenerate and refused at parsing (HTTP 422 `IDENTICAL_OPERANDS`).
3. **Expression Invariance:**
   - Both baseline and target definitions must evaluate the **identical ordered expression** (e.g., both evaluate `SUM(gross_amount - discount_amount)`).
   - Subtraction is anti-symmetric ($A - B \ne B - A$). Reversing operands or comparing different formulas represents formula drift, not an eligibility filter change, and is strictly refused (`DIFFERENT_MEASURE_EXPRESSIONS`).
4. **Predicates:**
   - All existing bounded V7.1 compound boolean filter capabilities (`AND`, `OR`, `NOT`, `IS NULL`, `IS NOT NULL`, `>`, `<`, `>=`, `<=`, `!=`, `<>`, `IN`, `BETWEEN`, `IS TRUE`, `IS FALSE`) remain fully available in the `WHERE` clause.
5. **Strict Refusals (HTTP 422 `UNSUPPORTED_SQL`):**
   - Multiplication `*`, division `/`, modulo `%`, addition `+`.
   - Numeric or string literals inside the measure (`gross - 10.00` $\to$ refused).
   - Arbitrary nesting (`a - b - c` or `(a - b) - c` $\to$ refused; max 1 binary operator).
   - Functions (`ABS()`, `ROUND()`, `COALESCE()`, `NULLIF()` $\to$ refused).
   - Additional aggregates (`AVG`, `MIN`, `MAX`, `COUNT` $\to$ refused).
   - Joins, CTEs, subqueries, aliases, groupings, or `HAVING` clauses.
   - Formula-change comparisons (comparing `SUM(A)` against `SUM(A - B)` $\to$ refused).

---

## 2. Versioned Contract Specification (FlatContract v6)

### 2.1 Version & Policy Identity
- **Contract Version:** `version: 6` (integer).
- **Measure Policy:** `measure_policy: 'flat-subtraction-v1'`.
- **Predicate Policy:** `predicate_policy: 'flat-boolean-v1'`.
- **Execution Family:** `flat_snapshot_v1`.

### 2.2 Column Representation & Avoiding Competing Meanings
In legacy contracts (v3, v4, v5), `contract['measure']` represented the single physical column in the source data. In V7.2, two physical columns exist in the source data, and the resulting measure is an analytical projection.

To **avoid fabricating a physical result column** in DuckDB or leaving **ambiguous competing meanings for the existing `measure` field**:
1. **Source Columns:** Registered in an explicit list of exactly two decimal column definitions: `decimal_columns`. Each entry declares its physical `name`, `precision` ($1 \le p \le 38$), `scale` ($0 \le s \le \min(p, 18)$), and `unit_label`.
2. **Physical Storage:** `flat_schema()` for v6 maps `decimal_columns` directly to DuckDB physical types:
   ```python
   {
       contract['row_key']: 'VARCHAR',
       contract['decimal_columns'][0]['name']: f"DECIMAL({p1},{s})",
       contract['decimal_columns'][1]['name']: f"DECIMAL({p2},{s})",
       **{name: PHYSICAL_TYPES[contract['dimension_types'][name]] for name in contract['categories']}
   }
   ```
   No physical result column (such as `net_amount`) is fabricated in the table schema.
3. **Expression Declaration:** Declared under `measure_expression`:
   ```json
   "measure_expression": {
     "op": "sub",
     "minuend": "gross_amount",
     "subtrahend": "discount_amount"
   }
   ```
4. **Exclusion of legacy `measure` & root `precision`:** In `version: 6`, `measure` and root `precision` are completely removed from canonical contract and IR shapes. Display labels are generated dynamically for human inspection (`f"{minuend} - {subtrahend}"`). Conflicting legacy executable fields (`measure`, root `precision`) in v6 contract payloads are strictly rejected (`invalid_contract`).
5. **Immutability & Regressions:** Historical v3, v4, and v5 contracts, contract hashes, IR shapes, and replay records remain completely unchanged. No automatic migration is performed.

### 2.3 Complete Example: FlatContract v6 JSON
```json
{
  "version": 6,
  "mode": "flat_snapshot",
  "name": "Retail Orders with Discount Subtraction",
  "measure_policy": "flat-subtraction-v1",
  "predicate_policy": "flat-boolean-v1",
  "row_key": "order_id",
  "decimal_columns": [
    {
      "name": "gross_amount",
      "precision": 18,
      "scale": 2,
      "unit_label": "INR"
    },
    {
      "name": "discount_amount",
      "precision": 18,
      "scale": 2,
      "unit_label": "INR"
    }
  ],
  "measure_expression": {
    "op": "sub",
    "minuend": "gross_amount",
    "subtrahend": "discount_amount"
  },
  "measure_meaning": "Gross order revenue minus promotional discounts",
  "unit_label": "INR",
  "scale": 2,
  "categories": ["status", "region", "flag"],
  "dimension_types": {
    "status": "string",
    "region": "string",
    "flag": "boolean"
  },
  "nullable_categories": ["status", "flag"],
  "null_policy": "blank_nullable",
  "values_comparable": true,
  "scope": "entire_supplied_snapshot",
  "execution_policy": "flat_snapshot_v1"
}
```

---

## 3. Exact Decimal Rules & Overflow Governance

### 3.1 Precision, Scale, and Capacity Bounds
- **Scale Invariant:** Both operands must have identical declared scale: $s_A = s_B = s$. The scale of the difference $A - B$ is exactly $s$.
- **Required Precision:** The mathematical precision required to represent any row difference without loss is:
  $$p_\text{req} = \max(p_A, p_B) + 1$$
- **Precision Validation Rule:** DuckDB exact decimal types support at most 38 digits (`DECIMAL(38, s)`).
  - If $p_\text{req} > 38$, the contract registration must be rejected with HTTP 422 `UNSUPPORTED_PRECISION` (`"Result precision exceeds maximum supported decimal precision 38"`).
  - Simply capping precision via $\min(38, p_\text{req})$ is **forbidden** because it creates false guarantees of exactness.
- **Signed Operands:** Operands may be positive, negative, or zero:
  - $(20.00) - (30.00) = -10.00$
  - $(-20.00) - (10.00) = -30.00$
  - $(50.00) - (-10.00) = 60.00$

### 3.2 Endpoint Range Bound & Exact Serialization
- **Aggregate Endpoint Bound (v6):** Terms accumulate exactly using Python `Decimal(prec=100)`. After complete accumulation, each defined endpoint aggregate must satisfy:
  $$\left|\text{endpoint} \times 10^s\right| \le 10^{38} - 1$$
  - Evaluated using exact integer/Decimal arithmetic.
  - Applied to the final endpoint aggregate, allowing exact cancellation of intermediate sums.
  - Undefined endpoints (empty or all-NULL matches) remain `NULL` (no bound evaluation).
  - An out-of-range endpoint raises structured `ARITHMETIC_OVERFLOW` (exit code 2, terminal job failure).
- **Exact Gap Serialization:** The gap between baseline and target totals ($\text{Target} - \text{Baseline}$) may exceed endpoint precision (e.g. over millions of rows). Python serializes that difference **exactly** using `Decimal(prec=100)` rather than silently applying the endpoint precision cap to the gap.
- **Zero Coercion:** Never round, clamp, truncate, or convert through float. Legacy policy behavior is strictly preserved.

### 3.3 Execution Lifecycle & Overflow Isolation
- **Row Expression Execution:** DuckDB evaluates:
  ```sql
  SELECT
    "order_id",
    <predicate> AS included,
    ("gross_amount" - "discount_amount") AS measure_val,
    "gross_amount" AS op_a,
    "discount_amount" AS op_b
  FROM snapshot
  ORDER BY "order_id"
  ```
- **Error Lifecycle Boundary:**
  - *Synchronous Validation:* Immediate API request validation (e.g. invalid SQL, mismatched scales, unsupported precision) returns HTTP 422 with domain error code.
  - *Asynchronous Execution Failure:* In the isolated Docker runner, if an arithmetic overflow occurs during row execution or aggregate accumulation, the runner fails with structured error code `ARITHMETIC_OVERFLOW`. The supervisor marks the comparison run as `state: 'failed'`, recording `error: {'code': 'ARITHMETIC_OVERFLOW', 'message': '...'}`. The completed job failure does not alter the already-returned HTTP 202/201 submission response.
- **Zero Coercion:** Floating-point `float` is never instantiated. No silent rounding, Banker's rounding, or precision truncation is permitted.

---

## 4. NULL Logic & Rich Row-Evidence Semantics

### 4.1 SQL Three-Valued Logic for Expressions
- If either operand is `NULL`, the expression evaluates strictly to `NULL`:
  - `100.00 - NULL = NULL`
  - `NULL - 20.00 = NULL`
  - `NULL - NULL = NULL`
- Rows where the expression evaluates to `NULL` have:
  - `expression_value: None`
  - `null_term: True`
  - `contribution: '0.00'` (bookkeeping zero for delta summation)

### 4.2 Explicit Separation of Inclusion, NULL Terms, and Bookkeeping Contributions
To ensure analytical honesty, the following concepts are strictly separated:
1. **Filter Inclusion:** Whether the row satisfies the `WHERE` filter (`baseline_included`, `target_included`).
2. **NULL Expression Term:** Whether the row's calculated expression is NULL (`baseline_null_term`, `target_null_term`).
3. **Bookkeeping Contribution:** The numerical contribution to the aggregate SUM (`baseline_contribution`, `target_contribution`).

**Primary Fixture Demonstration:**
In the primary fixture `v7_2_orders.csv`, Row 5 ($A=150, B=\text{NULL}$, `region='prod'`) and Row 6 ($A=\text{NULL}, B=30$, `region='prod'`):
- In the baseline (unfiltered), both R5 and R6 are filter-included (`baseline_included: True`).
- In the target (`WHERE region = 'prod'`), both R5 and R6 are also filter-included (`target_included: True`).
- Both rows have `membership: 'included_in_both'`. They are **NOT** labeled filter-excluded.
- Because their expressions evaluate to NULL, their `baseline_null_term: True` and `target_null_term: True`.
- Their bookkeeping contributions are `0.00` and row delta is `0.00`. They are correctly ignored by SQL `SUM`.

### 4.3 Aggregate Semantics & Undefined Status
- SQL `SUM` ignores `NULL` terms.
- If zero rows match the filter, OR if all matching rows evaluate to `NULL`:
  $$\text{endpoint\_value} = \text{None}, \quad \text{delta} = \text{None}, \quad \text{residual} = \text{None}, \quad \text{value\_status} = \text{'undefined'}$$
  Zero is **never** substituted for an undefined aggregate.

### 4.4 Exact Row Reconciliation & Artifact Safety
When both endpoints are defined, reconciliation satisfies:
$$\Delta_i = c_{i,\text{target}} - c_{i,\text{baseline}}$$
$$\sum_{i=1}^N \Delta_i = \sum_{i=1}^N c_{i,\text{target}} - \sum_{i=1}^N c_{i,\text{baseline}} = \text{Target} - \text{Baseline} = \text{Gap}$$
$$\text{Residual} = |\text{Gap} - \sum \Delta_i| \equiv 0$$
The bridge method remains `flat_filter_change` (whole-filter eligibility change).

**Supervisor Evidence Artifact Validation Invariant:**
`services/supervisor/evidence_artifact.py`'s `_row_delta(row)` validates that all required legacy row fields exist and permits optional expression fields:
```python
ROW_FIELDS = {'order_id', 'baseline_included', 'target_included',
              'baseline_contribution', 'target_contribution', 'delta',
              'baseline_null_term', 'target_null_term', 'membership'}
ALLOWED_EXPRESSION_FIELDS = {'baseline_operands', 'target_operands',
                             'baseline_expression_value', 'target_expression_value'}
```
This guarantees that v6 runner evidence is validated, paginated, and exported safely without breaking existing artifact schema checks or failing legacy v3/v4/v5 verification.

```json
{
  "order_id": "R1",
  "baseline_included": true,
  "target_included": true,
  "baseline_operands": {"gross_amount": "100.00", "discount_amount": "20.00"},
  "target_operands": {"gross_amount": "100.00", "discount_amount": "20.00"},
  "baseline_expression_value": "80.00",
  "target_expression_value": "80.00",
  "baseline_contribution": "80.00",
  "target_contribution": "80.00",
  "delta": "0.00",
  "baseline_null_term": false,
  "target_null_term": false,
  "membership": "included_in_both"
}
```

---

## 5. Validated Execution & Security Architecture

### 5.1 Canonical Intermediate Representation (IR)
The AST lowering compiles the query into deterministic IR:
```json
{
  "mode": "flat_snapshot",
  "policy": "flat_snapshot_v1",
  "contract_version": 6,
  "measure_policy": "flat-subtraction-v1",
  "predicate_policy": "flat-boolean-v1",
  "table": "snapshot",
  "aggregate": "sum",
  "measure": "gross_amount - discount_amount",
  "measure_expression": {
    "op": "sub",
    "minuend": "gross_amount",
    "subtrahend": "discount_amount"
  },
  "predicate_tree": {
    "op": "and",
    "children": [
      {
        "field": "region",
        "op": "eq",
        "type": "string",
        "values": ["prod"]
      }
    ]
  },
  "contract_hash": "a1b2c3d4e5f6...",
  "ir_hash": "9f8e7d6c5b4a..."
}
```

### 5.2 Preservation of Operand Order
Because subtraction is anti-symmetric ($A - B \ne B - A$), the parser and IR builder preserve strict operand positions:
- `minuend`: the left operand of subtraction.
- `subtrahend`: the right operand of subtraction.
- Sorting operands alphabetically or canonicalizing operand order is **strictly prohibited**.

### 5.3 Runner-Side Re-validation (`_check_ir`)
The isolated container runner does not trust client-supplied IR or hashes:
1. Re-computes SHA-256 digest of IR and asserts bitwise match with `ir_hash`.
2. Validates `measure_policy == 'flat-subtraction-v1'` and `contract_version == 6`.
3. Validates `measure_expression` structure: exactly keys `{"op", "minuend", "subtrahend"}`.
4. Validates `measure_expression['op'] == 'sub'`.
5. Validates that `minuend` and `subtrahend` are both present in `contract['decimal_columns']`.
6. Validates `minuend != subtrahend`.
7. Re-validates that baseline and target evaluate the identical measure expression:
   `baseline_ir['measure_expression'] == target_ir['measure_expression']`.
8. Generates SQL exclusively from validated identifiers and parameterizes all predicate literals.

---

## 6. Complete Application Integration & UI

### 6.1 Data Sources Onboarding (`DataSourcesView.tsx`)
In Step 2 of the snapshot onboarding modal ("Confirm Meaning"):
1. **Opt-in Control:** Expose a clean checkbox:
   `[ ] Enable subtraction expression (V7.2 preview)`
2. **Column Pickers (When Checked):**
   - *Minuend column:* Dropdown listing all detected decimal columns (e.g. `gross_amount`).
   - *Subtrahend column:* Dropdown listing remaining decimal columns (e.g. `discount_amount`).
   - Types are not restricted solely to guessed numeric types; users can declare types explicitly.
3. **Coordination & Policy Pinning:**
   - Checking subtraction automatically pins `version: 6`, `measure_policy: 'flat-subtraction-v1'`, and `predicate_policy: 'flat-boolean-v1'`. Users cannot configure contradictory policy combinations.
   - Legacy upload defaults remain unchanged (version 4 default).
4. **Validation & Visual Feedback:**
   - Checks that both columns have identical scale.
   - Displays live preview: `Expression: gross_amount - discount_amount (INR)`.
   - Displays NULL policy note: *"If either gross_amount or discount_amount is blank/NULL, the expression evaluates to NULL."*

### 6.2 Investigation Studio (`InvestigationsView.tsx`)
1. **Starter SQL Template:**
   When opening an investigation against a v6 snapshot, default starter SQL populates:
   ```sql
   SELECT SUM(gross_amount - discount_amount)
   FROM snapshot
   ```
   Selecting or reopening a saved investigation preserves the saved/draft SQL without overwriting it.
2. **Refusal Display & Text Retention:**
   - If unsupported SQL (e.g. `gross * 2`, mismatched baseline/target expressions, or unapproved operators) is submitted, an amber refusal banner renders the exact backend error code (`UNSUPPORTED_SQL`, `DIFFERENT_MEASURE_EXPRESSIONS`).
   - **Critical Invariant:** Both baseline and target textareas retain verbatim user input across validation errors.
3. **Evidence Table Display:**
   - Renders row key, baseline contribution, target contribution, delta, and operand values (`gross_amount`, `discount_amount`).

---

## 7. Historical Compatibility & Changed Assumptions

### 7.1 Consumer Audit for `contract['measure']`

| Consumer | Location | Legacy Assumption (v3–v5) | V7.2 Version-Aware Adaptation (v6) |
| :--- | :--- | :--- | :--- |
| **Schema Generation** | `packages/metricguard_core/flat.py:76` | Assumes single `contract['measure']` mapped to `DECIMAL(p, s)`. | Branches on `ver == 6`: maps each column in `contract['decimal_columns']` to `DECIMAL(p, s)`. |
| **CSV/Parquet Ingestion** | `packages/metricguard_core/flat.py:216` | Reads and validates single `contract['measure']`. | Branches on `ver == 6`: reads and validates both `colA` and `colB` independently; stores tuple `(key, a, b, *cats)`. |
| **Typed Predicates** | `packages/metricguard_core/flat.py:389` | Registers single `contract['measure']` as allowed decimal field. | Branches on `ver == 6`: registers both `colA` and `colB` as allowed decimal fields. |
| **SQL Parser** | `packages/metricguard_core/flat.py:480` | Requires `exp.Column` matching `contract['measure']`. | Branches on `ver == 6`: requires `exp.Sub` with minuend and subtrahend matching `contract['decimal_columns']`. |
| **IR Validation (`_check_ir`)** | `packages/metricguard_core/flat.py:655` | Checks `ir['measure'] == contract['measure']`. | Branches on `ver == 6`: validates `ir['measure_expression']` and `measure_policy`. |
| **Query Generator** | `packages/metricguard_core/flat.py:740` | Projects single `contract['measure']`. | Branches on `ver == 6`: projects `(colA - colB) AS measure_val, colA AS op_a, colB AS op_b`. |
| **Comparison Route** | `services/api/app.py:914` | Validates `a['ir']['measure'] == b['ir']['measure']`. | Branches on `ver == 6`: validates `a['ir']['measure_expression'] == b['ir']['measure_expression']`. |
| **Evidence Artifact** | `services/supervisor/evidence_artifact.py:30` | Validates exact set `ROW_FIELDS`. | Validates `ROW_FIELDS.issubset(row)` and permits `ALLOWED_EXPRESSION_FIELDS`. |
| **Web API Client** | `apps/web/src/api.ts:16` | FlatContract has single `measure: string`. | Adds optional `decimal_columns`, `measure_expression`, `measure_policy`. |
| **Investigation Studio** | `apps/web/src/views/InvestigationsView.tsx:255` | Generates starter SQL with `SELECT SUM(${meta.measure})`. | If `meta.decimal_columns`, generates `SELECT SUM(${colA} - ${colB})`. |

---

## 8. Delegated Acceptance Specification & Fixtures

### 8.1 Primary Acceptance Fixture (`v7_2_orders.csv`)
```csv
id,gross_amount,discount_amount,status,region,flag
R1,100.00,20.00,completed,prod,true
R2,200.00,50.00,settled,prod,false
R3,50.00,70.00,completed,test,true
R4,120.00,0.00,pending,prod,
R5,150.00,,completed,prod,false
R6,,30.00,cancelled,prod,false
R7,-40.00,10.00,settled,test,true
R8,80.00,-20.00,completed,prod,true
```

**Fixture Properties:**
- Total Rows: 8
- Registered Decimal Columns: `gross_amount` DECIMAL(18,2), `discount_amount` DECIMAL(18,2)
- Unit: `INR`
- Expression: `gross_amount - discount_amount`
- Row-Level Expressions:
  - R1: $100.00 - 20.00 = 80.00$
  - R2: $200.00 - 50.00 = 150.00$
  - R3: $50.00 - 70.00 = -20.00$ (negative difference)
  - R4: $120.00 - 0.00 = 120.00$ (zero discount)
  - R5: $150.00 - \text{NULL} = \text{NULL}$ (null discount operand; included in baseline and prod)
  - R6: $\text{NULL} - 30.00 = \text{NULL}$ (null gross operand; included in baseline and prod)
  - R7: $-40.00 - 10.00 = -50.00$ (negative gross operand)
  - R8: $80.00 - (-20.00) = 100.00$ (negative discount operand: subtraction of negative)

**Baseline Evaluation:**
- SQL: `SELECT SUM(gross_amount - discount_amount) FROM snapshot`
- Included Terms: R1 (80.00), R2 (150.00), R3 (-20.00), R4 (120.00), R7 (-50.00), R8 (100.00)
- Excluded NULL Terms: R5 (null_term: True), R6 (null_term: True)
- **Baseline SUM:** $80 + 150 - 20 + 120 - 50 + 100 = \mathbf{380.00\text{ INR}}$

---

### 8.2 Eight Planned Acceptance Groups

#### Group 1: Ordinary Subtraction with Filter Changes
- **Target SQL:** `SELECT SUM(gross_amount - discount_amount) FROM snapshot WHERE region = 'prod'`
- **Target Rows:** R1 (80.00), R2 (150.00), R4 (120.00), R8 (100.00) (R5, R6 are included rows with NULL terms)
- **Target Total:** $80 + 150 + 120 + 100 = \mathbf{450.00\text{ INR}}$
- **Expected Gap:** $450.00 - 380.00 = \mathbf{+70.00\text{ INR}}$
- **Expected Residual:** $\mathbf{0.00}$
- **Row Reconciliation:** Excluded R3 contributes $-(-20.00) = +20.00$; excluded R7 contributes $-(-50.00) = +50.00$. Sum of deltas = $+70.00$.

#### Group 2: Negative Differences and Signed Operands
- Verified through R3 ($-20.00$), R7 ($-50.00$), and R8 ($+100.00$).
- Target SQL: `SELECT SUM(gross_amount - discount_amount) FROM snapshot WHERE gross_amount < 0`
- Target Rows: R7 ($-50.00$)
- Target Total: $\mathbf{-50.00\text{ INR}}$
- Expected Gap: $-50.00 - 380.00 = \mathbf{-430.00\text{ INR}}$, Residual: $\mathbf{0.00}$.

#### Group 3: NULL Semantics & Three-Valued Logic
1. **Partial NULL Operands:** R5 and R6 evaluate to `NULL`; row evidence records `null_term: True`, `contribution: '0.00'`.
2. **All-NULL Selection:** `WHERE status = 'cancelled'` (matches R6 only).
   - Target Total: `None`
   - Delta: `None`, Residual: `None`, Value Status: `'undefined'`. Zero is NOT substituted.
3. **Empty Selection:** `WHERE region = 'nonexistent'`.
   - Target Total: `None`, Delta: `None`, Residual: `None`, Value Status: `'undefined'`.

#### Group 4: Precision Rejection & Overflow Bounds
1. Contract with `gross_amount` DECIMAL(38,2) and `discount_amount` DECIMAL(38,2) ($p_\text{req} = 39 > 38$):
   - Expected: Refused at contract registration with HTTP 422 `UNSUPPORTED_PRECISION`.
2. Contract with mismatched scales (`gross_amount` scale 2, `discount_amount` scale 3):
   - Expected: Refused at contract registration with HTTP 422 `SCALE_MISMATCH`.

#### Group 5: Different / Reversed Expressions Refused
1. Reversed Operands:
   - Baseline: `SELECT SUM(gross_amount - discount_amount) FROM snapshot`
   - Target: `SELECT SUM(discount_amount - gross_amount) FROM snapshot`
   - Expected: HTTP 422 `DIFFERENT_MEASURE_EXPRESSIONS`.
2. Formula Drift:
   - Baseline: `SELECT SUM(gross_amount) FROM snapshot`
   - Target: `SELECT SUM(gross_amount - discount_amount) FROM snapshot`
   - Expected: HTTP 422 `DIFFERENT_MEASURE_EXPRESSIONS`.
3. Identical Operands ($A - A$):
   - Query: `SELECT SUM(gross_amount - gross_amount) FROM snapshot`
   - Expected: HTTP 422 `IDENTICAL_OPERANDS`.
4. UI Textarea Retention: Both SQL textareas retain verbatim input across refusals.

#### Group 6: Forged IR & Runner Rejection
1. Attacker recomputes valid `ir_hash` with `op: "mul"` $\to$ Runner rejects with `invalid_ir`.
2. Attacker recomputes valid `ir_hash` with unregistered column $\to$ Runner rejects with `invalid_ir`.
3. Attacker submits v6 subtraction IR against a v5 snapshot $\to$ Runner rejects with `invalid_ir`.
4. Attacker submits identical minuend and subtrahend ($A - A$) $\to$ Runner rejects with `invalid_ir`.

#### Group 7: Save, Replay & Evidence Invariance
- Save Group 1 comparison as a saved investigation.
- Trigger replay via `POST /api/v1/investigations/{id}/replays`.
- Verify: Terminal state `succeeded`, exact matching totals ($380.00 / 450.00 / +70.00$), residual $0$, and exact bitwise SHA-256 match of evidence artifact JSONL.

#### Group 8: Legacy Backward Compatibility (v3, v4, v5)
- Queries with `SUM(col1 - col2)` against v3, v4, or v5 contracts must fail with HTTP 422 `UNSUPPORTED_SQL` (`"Measure expressions require a FlatContract v6 snapshot"`).
- Existing v3, v4, and v5 investigations replay with identical historical totals and evidence hashes.
