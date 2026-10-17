# MetricGuard V7.3 restricted CASE WHEN specification

**Document ID:** SPEC-2026-09-30-V7-3-FINAL  
**Status:** Approved implementation scope; functional verification pending.  
**Classification:** Engineering preview. External pilot gates V2-06 and V3-06 remain OPEN. No adoption or operational savings claims.

## 1. Scope and refusal boundary

```sql
SELECT SUM(
  CASE WHEN <supported bounded predicate>
       THEN <registered measure>
       ELSE 0
  END
)
FROM snapshot
[WHERE <supported bounded predicate>]
```

Exactly one searched WHEN, one physical registered decimal THEN measure and mandatory numeric literal zero ELSE. Accepted unsigned fixed-point literal-zero spellings (e.g. `0`, `00`, `0.00`, `.0`) normalize to IR string `"0"`. Signed expressions, scientific notation and quoted string zero are not accepted ELSE branches. No simple CASE, nested CASE, arithmetic branches, other functions/aggregates, joins, CTEs, aliases, grouping, or subtraction integration. Do not begin V7.4. Row keys remain identifiers for evidence, NOT additional predicate fields.

Both definitions use the same registered measure and ELSE-zero rule. WHEN and WHERE may change independently. No mechanical CASE-to-WHERE rewrite and no comparison against direct SUM or subtraction under this contract.

## 2. FlatContract v7 and physical ingestion

Explicit opt-in `version: 7`, `measure_policy: flat-case-v1`, `predicate_policy: flat-boolean-v1`. The actual execution-policy constant is `flat_snapshot_v1`; definition IR uses the existing `policy: flat-snapshot-v1` (hyphenated). These distinct existing identities must not be conflated.

The contract freezes the physical measure, precision/scale, typed dimensions, explicit unit/comparability/NULL policy and whole-snapshot scope, NOT an executable CASE definition. Reuse single-measure CSV/Parquet ingestion and `flat_schema`: one VARCHAR row key, one DECIMAL(p,s) measure, and registered typed dimensions. No fabricated CASE/flag/result columns. CSV uses declared blank/literal NULL policy; Parquet requires native_null.

Canonical contract shape (hash placeholder intentionally not an invented hash):

```json
{
  "version": 7,
  "mode": "flat_snapshot",
  "name": "case_fixture",
  "row_key": "id",
  "measure": "amount",
  "precision": 10,
  "scale": 2,
  "categories": ["flag", "region"],
  "dimension_types": {"flag": "boolean", "region": "string"},
  "nullable_categories": ["flag"],
  "measure_meaning": "Declared source amount; cohort defined explicitly in SQL",
  "unit_label": "USD",
  "values_comparable": true,
  "scope": "entire_supplied_snapshot",
  "null_policy": "blank_nullable",
  "measure_policy": "flat-case-v1",
  "predicate_policy": "flat-boolean-v1",
  "execution_policy": "flat_snapshot_v1",
  "contract_hash": "<generated contract SHA-256>"
}
```

API input serialization omits unset incompatible optional fields; explicitly supplied incompatible fields (including null) are refused, not silently dropped. v7 rejects executable contract fields such as `measure_expression`, `decimal_columns`, or CASE definitions. Preserve historical v3/v4/v5/v6 canonical contracts, hashes, grammar limits and replay bytes.

## 3. Definition IR

For `SELECT SUM(CASE WHEN flag IS TRUE THEN amount ELSE 0 END) FROM snapshot WHERE region = 'prod'`, the implementation uses this existing boolean leaf shape and v7 expression shape:

```json
{
  "mode": "flat_snapshot",
  "policy": "flat-snapshot-v1",
  "contract_version": 7,
  "contract_hash": "<generated contract SHA-256>",
  "measure_policy": "flat-case-v1",
  "predicate_policy": "flat-boolean-v1",
  "table": "snapshot",
  "aggregate": "sum",
  "measure_expression": {
    "op": "case",
    "when": {"op": "predicate", "predicate": {"field": "flag", "op": "is_true", "type": "boolean", "values": []}},
    "then": "amount",
    "else": "0"
  },
  "predicate_tree": {"op": "predicate", "predicate": {"field": "region", "op": "eq", "type": "string", "values": ["prod"]}},
  "ir_hash": "<generated IR SHA-256>"
}
```

Absent WHERE is `predicate_tree: null`. `flag IS NOT FALSE` canonicalizes to a `not` node with a `child` leaf `is_false`, rather than inventing a different boolean grammar. `flag = TRUE` uses leaf `op: eq_bool`, `values: ["true"]`.

SQLGlot remains the SQL parser. Strictly allowlist all CASE, aggregate, source, branch and predicate AST fields. The isolated runner independently validates the complete IR shape, fields, policies, registered measure, literal types, per-tree and combined bounds, canonical form and hash. Recomputed hashes never replace semantic validation. SQL generation consumes only validated structures, quotes registered identifiers and parameterizes predicate literals.

## 4. Chosen complexity bounds

For v7 only:

- Each predicate tree: logical depth <=8, nodes <=63, leaves <=16.
- Combined WHERE + WHEN: leaves <=24, nodes <=96.
- Whole parsed SQL AST: <=250 nodes.
- Existing literal bound <=256 characters, IN <=32 values, SQL length <=50,000 characters.

Apply combined predicate limits during parsing AND untrusted runner IR validation. The SQL AST cap applies to parsed SQL; IR is governed by its strict structural/tree bounds. These are chosen engineering bounds, not proven optimal values. Historical v3/v4/v5/v6 limits remain unchanged (including their 200-node SQL AST cap).

## 5. Semantics and exact arithmetic

Keep distinct for each side:

1. Outer WHERE truth state and inclusion (`TRUE` alone includes).
2. WHEN TRUE/FALSE/UNKNOWN.
3. Selected THEN/ELSE branch (`TRUE` alone selects THEN).
4. Raw physical measure value.
5. CASE term (raw measure for THEN, exact zero for ELSE).
6. Included NULL-term status (`included AND term IS NULL`).
7. Bookkeeping contribution (included non-NULL term, otherwise zero).

For inspectability, evaluate predicates and CASE terms for all source rows, even outer-excluded rows; those states are diagnostic, NOT aggregate participation. Excluded rows have contribution zero and `null_term: false`. There is no claim that evaluating WHEN on an excluded row changed inclusion.

| Selection | Aggregate |
| --- | --- |
| Empty outer selection | NULL SUM |
| Nonempty all-ELSE selection | Defined zero SUM |
| Only included THEN-NULL terms | NULL SUM |
| Included THEN-NULL plus ELSE-zero | Defined zero SUM |

Use native DuckDB CASE and existing typed predicate compilation. Exact row/endpoint/evidence reconciliation reuses `numeric.py` ScaledInteger utilities, not context-dependent rounded Decimal reconciliation. Defined endpoints must satisfy `abs(endpoint * 10^scale) <= 10^38 - 1` after complete accumulation. Preserve cancellation, signed/fractional values and wider exact gaps between valid endpoints. Overflow is structured ARITHMETIC_OVERFLOW. Undefined endpoints have no numeric gap/residual.

One whole-definition bridge step: `rule: case_definition_change`, not an eligibility-only explanation. No Shapley or per-predicate causal attribution.

## 6. Bounded evidence and trust boundary

Historical ROW_FIELDS remain unchanged. v7 requires additional fields:

- `predicate_inputs`: exactly the registered measure and typed dimension inputs, represented canonically under the frozen contract (decimal/date/timestamp/string strings, boolean `"true"`/`"false"`, native JSON null).
- `measure_value`: raw measure (same immutable row for both definitions).
- `baseline_where_state`, `target_where_state`: true/false/unknown.
- `baseline_when_state`, `target_when_state`: true/false/unknown.
- `baseline_branch`, `target_branch`: then/else.
- `baseline_case_term`, `target_case_term`: exact fixed-point string or null.

Both trusted frozen definition IRs and the trusted frozen contract must accompany validation. Baseline and target can have different WHEN and WHERE. A manifest cannot declare its own trusted executable context. Supervisor and API paging/export retrieve definitions from the store, not the evidence manifest. The artifact binding records contract and both IR hashes as identity checks, not proof of a row's truth state.

Re-evaluate WHERE and WHEN using the typed predicate inputs and the SAME validated predicate compilation/SQL semantics used by execution, in batches of at most 256 rows. Use a private in-memory DuckDB table with the frozen physical schema and parameterized inputs; never execute SQL supplied by an artifact. Require unique sorted row keys, but do not add row keys to the predicate allowlist. Do not implement a separate inconsistent SQL language in the evidence validator. Check reported states, inclusion, branch, raw measure, CASE term, included NULL flag, contribution, membership and exact delta. Reconcile endpoint totals and defined/undefined status from included non-NULL terms as well as delta sums. Recomputed artifact hashes cannot make inconsistent branch metadata valid, even if the attacker consistently changes state/branch/term/contribution but leaves typed source inputs unchanged.

**Trust boundary:** These checks prove internal consistency of artifact values with both trusted definitions. They do NOT independently prove attacker-supplied input values came from the frozen snapshot. Authenticity continues to rely on the trusted supervisor/isolated runner, verified immutable snapshot/source identity, and stored artifact byte/SHA-256 checks. A threat actor who can replace the typed inputs AND all associated integrity metadata is outside that assurance; frozen IR alone does not authenticate source values. Do not claim otherwise.

Keep existing artifact byte/row/line caps and paged APIs (<=100 rows/page), bounded export and storage quotas. CASE fields/bindings/verification are v7-gated; old evidence remains valid. Preserve existing source/artifact integrity checks.

## 7. Complete product workflow

1. Data Sources has an explicit mutually exclusive CASE mode (not subtraction/direct SUM simultaneously). Confirm physical measure, dimensions/types, NULL policy, unit and whole-snapshot comparability.
2. Contract/schema inspection shows CASE policy and physical registered measure, never a fabricated expression column.
3. Offer this editable starter example, explicitly NOT an inferred business definition:
   ```sql
   SELECT SUM(CASE WHEN amount IS NOT NULL THEN amount ELSE 0 END)
   FROM snapshot
   ```
   Substitute only the registered measure identifier. Never guess a category value or cohort. Do not overwrite existing editor text on reopening, view changes, validation refusal, or draft restore.
4. Validate definitions, queue execution through existing jobs/storage, inspect comparison and evidence, export bounded verified rows, save and replay pinned definitions/contract/snapshot.
5. Every result/evidence consumer must support CASE inclusion, WHEN state, branch and CASE term. Keep analytical typography and existing blue/amber/red design semantics; no green assurance or unrelated redesign.

## 8. Acceptance oracle (not executed during implementation)

Original five-row CSV, blank_nullable, nullable `flag`:

```csv
id,amount,flag,region
C1,10.00,true,prod
C2,20.00,false,prod
C3,30.00,,prod
C4,,true,prod
C5,40.00,true,test
```

- Baseline WHEN `flag IS TRUE`: **50.00**. Target WHEN `flag IS NOT FALSE`: **80.00**. Gap **+30.00**, residual zero. C3 alone changes contribution by +30; C4 is included THEN-NULL with zero bookkeeping contribution.
- Both with outer `WHERE region = 'prod'`: **10.00 / 40.00 / +30.00**. C5 is outer-excluded, not reinterpreted as a WHEN failure.
- Empty: outer `WHERE region = 'nonexistent'`: NULL.
- Nonempty all-ELSE: `WHEN region = 'nonexistent'`, outer `WHERE region = 'prod'`: defined zero.
- THEN-NULL only: `CASE WHEN flag IS TRUE THEN amount ELSE 0 END WHERE amount IS NULL`: NULL.
- THEN-NULL plus ELSE-zero: same CASE, `WHERE amount IS NULL OR flag IS FALSE`: defined zero.
- Genuine UNKNOWN: `WHEN flag = TRUE`; C3 WHEN is UNKNOWN and selects ELSE. `IS TRUE` instead evaluates FALSE on C3 and does not exercise UNKNOWN.
- Independent WHERE-only, WHEN-only and simultaneous changes, including asymmetric predicates and empty/NULL endpoints.
- Signed scale-4 fixture (-150.2500 TRUE, 50.1250 FALSE): baseline -150.2500, target 50.1250, gap +200.3750.
- Endpoint boundary, overflow, intermediate cancellation and wider exact gaps; corrected 102-digit evidence forgery guards.
- Every individual/combined complexity boundary, malformed/forged IR with recomputed hash, incompatible explicit fields, all forbidden SQL structures.
- Forged inconsistent metadata and self-consistent state/branch/term/contribution mutations with unchanged typed inputs and recomputed artifact hashes must fail. Distinguish this from replacing typed inputs under the authenticity boundary above.
- Actual browser CSV/Parquet import, validation/refusal editor retention, run, evidence, export, save and bitwise replay. Historical retail and v3/v4/v5/v6 regressions.

No row-key filtering broadening to accommodate examples. Flash owns functional verification. Implementation is handed off as **Implemented; functional verification pending.**

## 9. Delivery sequence

Correct specification; implement contract/parser/runner; evidence verification and trusted context; API/store/UI integration; compile/build/package (no functional tests); Graphify AST update and visual tree; project map/release evidence and detailed Flash prompt. Record full runner image identity and exact startup commands. Do not delete shared records, bypass quotas or expose credentials.
