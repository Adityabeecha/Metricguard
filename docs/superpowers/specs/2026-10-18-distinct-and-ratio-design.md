# COUNT(DISTINCT) and ratio metrics on the single-table flat engine — design

Date: 2026-10-18. Status: **R1/R2 implemented, acceptance pending.** Starting point: commit `86f7ffb` (SUM, COUNT(\*), breakdowns, record-context file, PostgreSQL source). R3 remains deferred. Existing SUM/COUNT policy identities stay pinned. Compilation, frontend packaging and explicitly requested performance measurements are separate from acceptance tests.

### Completion decisions (2026-10-18)

- R2 also permits a bounded `SUM(a - b)` ratio part for margin. Both operands must have the same declared unit, and the exact subtraction must fit 38 digits. Raw NULL operands remain NULL; `SUM(a-b)/SUM(a)` is not `AVG((a-b)/a)`.
- The implemented standalone count forms are `COUNT(*)` and `COUNT(DISTINCT category)`. `COUNT(amount)` is available inside a ratio/AVG, not as a standalone metric. R3 distinct-denominator ratios remain refused.
- Ratios use exact fractions for monitoring thresholds and show exact values alongside rounded displays. A nonzero exact gap must never become equality because its display rounds to zero. Multi-amount SUM denominators must retain the same amount expression across endpoints.
- Large local flat snapshots: up to 5,000,000 records / 512 MiB. Above 100,000 records only plain SUM and COUNT(*) comparisons run, with an explicit changed-records evidence projection (up to 100,000 changed records / 256 MiB). Unchanged-record counts are reported. Totals scan in bounded batches and retain final-only exact overflow checks. Other aggregate families refuse above the full-evidence limit. This is not warehouse execution or a measured five-million-row performance guarantee.
- Multiple lookups: relational contract v2 under `relational-star-v1`, one fact and up to four unique-key lookups. Only declared direct many-to-one INNER/LEFT joins, no snowflakes or fanout. The v1 relational path stays separate. Relational limits remain 100,000 rows per table and 25 MiB combined. Schema-2 metric governance covers its selected lookup; additional lookups are explicitly unverified and yield an inconclusive check.
- Evidence indexing checks its bytes against the frozen artifact hash, bounds aggregate cache rows and pages distinct membership lists. No Graphify refresh was run.
- Reproducible timing entry point: `python -m scripts.benchmark_flat_comparisons --rows 1000 10000 --repetitions 3`; add `--docker-image metricguard-runner:1.0` after building the current image to measure isolated jobs. Receipts identify source hashes, versions, repetitions and stage scope; they are synthetic performance measurements.

Acceptance still needed: `tests/test_distinct_and_ratio.py`, `tests/test_large_snapshots.py`, `tests/test_relational_star.py`, `tests/test_aggregate_completion_safety.py`, historical regression tests, and browser upload → compare → evidence → save → replay. These tests were not run during this completion pass, respecting the delegated-testing preference.

## 1. Problem and guarantee to keep

MetricGuard explains a metric difference on one frozen snapshot by listing evidence units whose exact decimal changes add up to the total change, with zero residual. Saved cases replay to identical evidence SHA-256 values. Today the flat engine (`packages/metricguard_core/flat.py`) supports `SUM(measure)` and `COUNT(*)`, where each record contributes an independent term, so record deltas add up directly.

Neither `COUNT(DISTINCT col)` nor a ratio is a sum of per-record terms:

- **COUNT(DISTINCT customer_id).** A record's effect depends on the other records for the same customer. Removing one of a customer's two orders changes nothing.
- **Ratio N/D,** such as AOV, margin % or conversion. A record changes the numerator, the denominator or both, and its effect on the ratio depends on every other record through N and D.

This design gives each one an evidence unit and an exact identity that keep the guarantee:

> The evidence units' exact changes add up to the reported total change. The artifact verifier recomputes every unit's change and every endpoint from the unit's own inputs, so the total is proven by the artifact.

## 2. Decisions at a glance

| # | Decision |
|---|---|
| D1 | COUNT(DISTINCT e) uses the **entity value** as the evidence unit. Each entity contributes 1 at an endpoint when at least one included record has that value. Deltas are −1, 0 or +1 and add up exactly to the change in distinct count. |
| D2 | A distinct run also writes a required, separately hashed **membership file** that maps each record to its entity and its inclusion flags. The supervisor checks it against the entity evidence, so "which records kept this customer in" and the count of included NULL-entity records are verified. |
| D3 | A ratio is **N/D where N and D are each additive aggregates** (`SUM(col)`, `COUNT(*)` or `COUNT(col)`, each with an optional part `FILTER (WHERE …)`) under one shared `WHERE`. `AVG(col)` is lowered to `SUM(col)/COUNT(col)`. Ratios are never computed in DuckDB, which would use floating point. N and D are exact decimals accumulated in Python, and R = N/D is an exact rational. |
| D4 | The ratio change is split exactly by a **two-player Shapley** over (numerator, denominator). Numerator effect = ΔN·(D0+D1)/(2·D0·D1); denominator effect = −ΔD·(N0+N1)/(2·D0·D1). Together they equal ΔR exactly, with no interaction term. |
| D5 | **Per-record attribution is linear in the two effects.** Record i's *scaled effect* is gᵢ = σ·[Δnᵢ·(D0+D1) − Δdᵢ·(N0+N1)], an exact decimal. Its ratio effect is gᵢ/Q, where Q = 2·\|D0·D1\| is one positive constant per run and σ = sign(D0·D1). Σgᵢ = G = 2σ·(N1·D0 − N0·D1), so Σgᵢ/Q = ΔR exactly. Every evidence row stays an exact *decimal*, and only the shared divisor is rational. |
| D6 | A ratio is **undefined** when an endpoint's N or D is SQL NULL or D = 0. The run then reports `value_status: undefined` with a reason code, gives no ratio gap or bridge, and still shows exact, verified component evidence for N and D. |
| D7 | The new IR shapes are gated by an explicit **`aggregate_policy`** field (`flat-distinct-v1` or `flat-ratio-v1`) that only new IRs carry. SUM and COUNT(\*) IRs, their hashes, evidence rows, result bytes and saved-case replays are unchanged. Older code that receives a new IR fails closed on its field-set check. |
| D8 | Scope: **R1** covers distinct on contract v3–v5 and ratios on v5, which needs one decimal measure. **R2** adds a new FlatContract v8 with 2–8 registered decimal measures, which enables `SUM(profit)/SUM(revenue)`. **R3** covers distinct-based ratios at the entity level. |
| D9 | Breakdowns stay exact. A ratio breakdown adds up gᵢ by group, because groups partition records. A distinct breakdown is offered only for columns that take **one value per entity across the whole snapshot**. Other columns are listed as unavailable with the reason. |
| D10 | Values shown to people are rounded half-even and flagged as rounded. Exact values are reduced fractions `p/q` (strings) everywhere they are stored. |

## 3. COUNT(DISTINCT column)

### 3.1 SQL accepted

```sql
SELECT COUNT(DISTINCT customer_id) FROM snapshot [WHERE <existing filter grammar>]
```

- The argument must be one registered **category** column, of any dimension type. Canonical values are the entity keys: v4/v5 values already use canonical text for date, timestamp and boolean, and v3 values are text.
- `COUNT(DISTINCT row_key)` is refused. The message is: "Every record has a unique key; use COUNT(\*)." `COUNT(DISTINCT measure)` is refused because counting distinct amounts is almost always a mistake. Both refusals use the code `unsupported_sql`.
- No `COUNT(DISTINCT a, b)`, no expressions, no `FILTER`, and no mixing in one SELECT.
- Contract versions 3, 4 and 5 are supported. Versions 6 and 7 refuse, as they do for `COUNT(*)` today.

### 3.2 IR

v5 example (v3/v4 carry `predicates` instead of `predicate_policy`/`predicate_tree`, exactly as today):

```json
{"mode": "flat_snapshot", "policy": "flat-snapshot-v1", "contract_hash": "…",
 "table": "snapshot", "aggregate": "count_distinct", "measure": "customer_id",
 "aggregate_policy": "flat-distinct-v1",
 "predicate_policy": "flat-boolean-v1", "predicate_tree": {…},
 "ir_hash": "…"}
```

- `measure` holds the entity column. Display code that prints `measure` keeps working, and the UI labels it "distinct customer_id".
- `_check_ir` dispatches on the `aggregate_policy` key. Without it, the existing field set and the `(aggregate, measure) ∈ {('sum', measure), ('count', '*')}` rule apply unchanged. With `flat-distinct-v1`, the field set is the base set plus `aggregate_policy`, `aggregate` must be `count_distinct`, and `measure` must be a registered category.
- `compare_flat` keeps the `different_aggregates` refusal. Both sides must be `count_distinct` **over the same entity column**; otherwise the code is `different_distinct_columns`. Two different entity columns are two different units.

### 3.3 Execution

There is one query per endpoint and no new SQL surface:

```sql
SELECT "<row_key>", <predicate> AS included, "<entity>" FROM snapshot ORDER BY "<row_key>"
```

Python builds the following, as `engine.py::_evaluate` does for retail `count_distinct`:

- `universe` = all non-NULL entity values in the whole snapshot. This is the same for both endpoints, so entities included in neither are listed too, matching flat SUM evidence, which lists every record.
- For each endpoint and each entity: `rows[e]`, the number of included records with entity `e`.
- `present[e] = rows[e] > 0`. Endpoint value = |{e : present}| as an integer string. An empty selection gives `0`, never NULL, which is SQL semantics for COUNT.
- `null_entity_rows`: the number of included records whose entity is NULL. SQL ignores them. They are reported, never counted.

As a cross-check, the runner also asks DuckDB for `COUNT(DISTINCT "<entity>") FILTER (WHERE <predicate>)` and refuses with `reconciliation_failure` if the result differs. This guards against canonicalization drift between DuckDB and the Python grouping. The query is part of the query hash.

### 3.4 Evidence unit and rows (D1)

One row per entity in `universe`, sorted by canonical entity text. The schema reuses the generic row fields, so paging, summary, CSV export and `record_key` work unchanged:

```json
{"order_id": "C3", "baseline_included": false, "target_included": true,
 "baseline_contribution": "0", "target_contribution": "1", "delta": "1",
 "baseline_null_term": false, "target_null_term": false,
 "membership": "target_only",
 "baseline_supporting_rows": 0, "target_supporting_rows": 2}
```

`order_id` holds the entity value, as the retail engine already does. Provenance records `evidence_unit: "entity:customer_id"`, and the UI column header shows the entity column name, never "order". See open question Q1.

Verifier rules (`DistinctEvidenceValidator`, chosen when the manifest has `distinct_binding`):

- `*_supporting_rows` are JSON integers ≥ 0, and `*_included ⇔ *_supporting_rows > 0`.
- Contributions are exactly `"1"` when included and `"0"` otherwise. `delta = target − baseline ∈ {-1, 0, 1}`. Null terms are always `false`.
- Entity keys are strictly increasing, so there are no duplicates.
- `finish()` checks Σ baseline contributions = `baseline_value`, Σ target contributions = `target_value`, and Σ delta = `delta_target_minus_baseline`. The existing manifest `delta_sum` check still applies.

Manifest binding: `distinct_binding = {"policy": "flat-distinct-v1", "entity_column": …, "baseline_count": "…", "target_count": "…", "baseline_null_entity_rows": n, "target_null_entity_rows": m, "membership": {sha256,row_count,byte_count}}`. `verify_artifact` refuses a `distinct_binding` when no distinct context is supplied, and refuses a distinct context when the binding is missing. This mirrors `case_binding`.

**Why the total is exact.** Σₑ(presentₜ(e) − present_b(e)) = |Present_t| − |Present_b|. Integer arithmetic only.

**The analyst-relevant decomposition** follows from membership and needs no extra arithmetic. Entities gained = count of `target_only`; entities lost = count of `baseline_only`; net = gained − lost. A zero net change with churn is visible: "2 customers lost, 2 gained".

### 3.5 Membership file (D2)

`members.jsonl` is written by the runner beside `evidence.jsonl`, using the same pattern as `row_context.py`: its own SHA-256 and line bounds. It is **required** for distinct runs. One line per snapshot record, in row-key order:

```json
{"k": "R4", "e": "C3", "b": false, "t": true}
```

`e` is `null` for a NULL entity. The supervisor (`validate_members`) checks that:

1. Keys are unique, strictly increasing and complete (line count = snapshot `row_count`).
2. For every entity, the counts of `b`/`t` lines equal `baseline_supporting_rows`/`target_supporting_rows` in the evidence. Entities missing from the evidence have no lines.
3. The counts of `e = null` lines with `b`/`t` equal the binding's `*_null_entity_rows`.

The evidence hash and the existing record-context file are untouched. The membership file is registered like `evidence_context` (a new `evidence_members` record kind) and powers the entity drill-down.

### 3.6 Breakdown (D9)

The breakdown endpoint for a distinct run adds up entity deltas by group, which is exact. Grouping by a record column is only meaningful when each entity has one value of that column. A customer with orders in two regions would otherwise be counted in both, and the groups would no longer add up to the total.

- The runner writes the record-context file **per entity** (one line per evidence row, unchanged format). It includes only columns that are *functional on the entity*: exactly one value, possibly NULL, across all of the entity's records in the **whole snapshot**, not just the included records. That keeps the grouping the same for both endpoints.
- Columns that fail the check go into the context manifest under `excluded_columns: {"region": "multi_valued_per_entity"}`. `GET …/evidence-breakdown` lists them as unavailable with the reason: "Some customers appear under more than one region, so a per-region distinct count would not add up to the total."
- Per-group distinct counts *within* a group ("distinct customers per region") form a different metric and are out of scope. See Q4.

### 3.7 Result

The existing result fields keep their meaning, with integer strings for values. Additions:

- `aggregate: "count_distinct"`, `method: "flat_distinct_membership_change"` and `explanation_status: "complete_entity_membership_change"`.
- `distinct: {"entity_column", "gained", "lost", "unchanged_present", "baseline_null_entity_rows", "target_null_entity_rows"}`, where all counts are integers.
- Limitation lines:
  - "Evidence rows are distinct customer_id values, not records."
  - "Records with no customer_id are not counted by COUNT(DISTINCT) (n at baseline, m at target)."
  - "A customer is counted once however many of its records match."
- `steps`: the single `eligibility_predicate` step, as for SUM.

## 4. Ratio metrics

### 4.1 Forms accepted in R1 (FlatContract v5, one decimal measure `m`)

| Business metric | SQL | Lowered parts N / D |
|---|---|---|
| Average order value | `SUM(m) / COUNT(*)` | sum(m) / count(\*) |
| Average, ignoring blanks | `AVG(m)` | sum(m) / count(m) |
| Share of amount | `SUM(m) FILTER (WHERE p) / SUM(m)` | sum(m)⟨p⟩ / sum(m) |
| Conversion or rate | `COUNT(*) FILTER (WHERE converted) / COUNT(*)` | count(\*)⟨converted⟩ / count(\*) |
| Value per converting record | `SUM(m) / COUNT(*) FILTER (WHERE converted)` | sum(m) / count(\*)⟨converted⟩ |

The grammar: exactly one `<part> / <part>` or one `AVG(m)`, an optional shared `WHERE`, and an optional parenthesis around each part. `<part>` is `SUM(m)`, `COUNT(*)` or `COUNT(m)`, optionally followed by `FILTER (WHERE <flat-boolean-v1 tree>)`. Each part filter is limited like `WHERE`, and the combined leaf count across shared and part trees is ≤ 24, the same cap as CASE. The following are refused: `NULLIF`, `CAST`, `* 100`, `COALESCE`, nested ratios, `CASE`, constants, and a part that is itself a ratio. The refusal suggests the supported form; for example, `* 100` gets "percent is shown automatically".

`AVG(m)` and `SUM(m)/COUNT(m)` lower to **byte-identical IR**, so they have the same hash. The SQL text is kept only in the definition version, as today.

Margin % (`SUM(profit)/SUM(revenue)`) needs two measures and is R2 (§4.9).

### 4.2 IR

```json
{"mode": "flat_snapshot", "policy": "flat-snapshot-v1", "contract_hash": "…",
 "table": "snapshot", "aggregate": "ratio", "aggregate_policy": "flat-ratio-v1",
 "numerator":   {"aggregate": "sum",   "measure": "amount", "filter": null},
 "denominator": {"aggregate": "count", "measure": "*",      "filter": null},
 "predicate_policy": "flat-boolean-v1", "predicate_tree": {…},
 "ir_hash": "…"}
```

- The part `aggregate` is `sum` or `count`. `measure` is the registered measure, or `*` for count only. `filter` is `null` or a canonical flat-boolean-v1 tree, lowered and validated with the same `lower_boolean`/`validate_boolean` callbacks.
- There is no top-level `measure` field. The field set is exactly the set shown above, and `_check_ir` dispatches on `aggregate_policy == "flat-ratio-v1"`.
- Comparison rule (`compare_flat`): both sides must be `ratio`, both numerators must have the same `(aggregate, measure)`, and both denominators must be in the same **unit family**. Count(\*) and count(m) are both "records"; sum(m) is the measure unit. Part filters and the shared `WHERE` may differ, because that is the drift being explained. So `SUM(m)/COUNT(*)` against `AVG(m)` is **allowed**. That is a classic real discrepancy: AVG skips blank amounts. Other mismatches refuse with `different_ratio_shape`.

### 4.3 Execution

There is one query per endpoint. No division happens in SQL:

```sql
SELECT "<key>",
       <shared> AS included,
       <shared> AND <num_filter> AS in_num,     -- TRUE when no part filter
       <shared> AND <den_filter> AS in_den,
       "<m>" AS m
FROM snapshot ORDER BY "<key>"
```

For each endpoint and record, Python computes:

- nᵢ = m if `in_num IS TRUE` and m is not NULL; for count(\*) it is 1 if `in_num IS TRUE`; otherwise 0.
- dᵢ is computed the same way for the denominator. For count(m), dᵢ = 1 only when m is not NULL.
- N = Σnᵢ and D = Σdᵢ as exact `Decimal`/`ScaledInteger`, with SQL NULL semantics: a **sum** part is NULL when no non-NULL term matched, and a **count** part is never NULL.

Note on semantics, which the report must state when the forms differ: `SUM(a)/SUM(b)` aggregates each column separately, so a record with `a` NULL still adds to D. It is not `SUM(a/b)` or `AVG(a/b)`, which are not supported.

The endpoint capacity check is the same as v7: |N|, |D| ≤ DECIMAL(38, s) after full accumulation, else `arithmetic_overflow`. As a cross-check, DuckDB computes `SUM(m) FILTER (…)` and `COUNT(…) FILTER (…)` for each part, never their quotient. Any mismatch is a `reconciliation_failure`.

### 4.4 Exact decomposition (D4, D5)

Let R0 = N0/D0 and R1 = N1/D1, both defined (D ≠ 0, neither NULL). Let σ = sign(D0·D1) ∈ {+1, −1} and Q = 2·|D0·D1| > 0.

**Aggregate bridge: two-player Shapley over (N, D).** The four corners are R(N0,D0), R(N1,D0), R(N0,D1) and R(N1,D1). Averaging the two orders gives:

```
numerator_effect   = ΔN · (D0 + D1) / (2·D0·D1)
denominator_effect = −ΔD · (N0 + N1) / (2·D0·D1)
numerator_effect + denominator_effect = (N1·D0 − N0·D1)/(D0·D1) = ΔR      (exact)
```

There is no interaction term; Shapley splits it evenly. Rejected alternative: the sequential three-term form (numerator-first plus an interaction term). It depends on the chosen order and invites the question "why this order?". The four corner values are stored, so anyone can recompute either form.

**Per-record attribution.** Both effects are linear in ΔN = ΣΔnᵢ and ΔD = ΣΔdᵢ, so they distribute over records exactly:

```
gᵢ = σ · ( Δnᵢ·(D0 + D1) − Δdᵢ·(N0 + N1) )        exact decimal (products of decimals)
record_effectᵢ = gᵢ / Q
Σ gᵢ = σ · 2·(N1·D0 − N0·D1) = G                   ⇒  Σ record_effectᵢ = G/Q = ΔR
```

- Each record's evidence value gᵢ is an **exact decimal** with scale s_n + s_d. Only the single run-wide divisor Q is shared, so the existing additive verifier machinery (decimal deltas that add up to a claimed total) carries over unchanged.
- σ makes Q positive, so sign(gᵢ) = sign(record effect) and sorting by |gᵢ| sorts by |effect|.
- The intuition shown in the report: a record entering both N and D (Δn = v, Δd = 1) has effect (v − P)·(D0+D1)/Q·σ, where P = (N0+N1)/(D0+D1) is the **pooled average**. Records above the pooled average raise an average; records below lower it. A record that only leaves the denominator, such as a blank amount dropped by AVG, raises it by P·(D0+D1)/Q.
- This is attribution through the two aggregate effects. It is not a record-as-player Shapley, which is exponential and not needed. The report says it is not causal.

Precision: |gᵢ| ≤ 2·10³⁸·10³⁸·10^(s) magnitude. All products are evaluated under `localcontext(prec=100, traps=[Inexact, Overflow])`, or on `ScaledInteger` coefficients, which are exact Python ints. Any `Inexact` aborts with `arithmetic_overflow`. Serialized strings must fit `MAX_NUMERIC_STR_LEN` (128 characters), which holds for 38+38+1 digits plus scale and sign.

### 4.5 Undefined ratios (D6)

| Condition at an endpoint | `reason` |
|---|---|
| D is NULL (a sum part with no non-NULL terms) | `null_denominator` |
| D = 0 | `zero_denominator` (also an empty COUNT) |
| N is NULL, D defined and ≠ 0 | `null_numerator` (SQL `NULL/x` is NULL; no COALESCE is applied) |

If either endpoint is undefined:

- `baseline_value` or `target_value` is `null` for the undefined side, the defined side's ratio is still shown, `delta_target_minus_baseline` and `residual` are `null`, `value_status` is `undefined`, `explanation_status` is `components_only`, and `ratio.undefined = [{"endpoint": "baseline", "reason": "zero_denominator"}]`.
- The evidence rows are still written with nᵢ and dᵢ for both endpoints, so the component changes ΔN and ΔD are verified and explained, and `gᵢ = "0"`. The binding records `status: "components_only"`, under which the verifier requires every `delta` to be `"0"` and requires Σnᵢ and Σdᵢ to match the bound N and D. `delta_sum` is `"0"`, and the worker passes `expected_delta="0"`.
- Report text: "The baseline rate is undefined because no records matched its denominator. The numerator changed by … and the denominator by …; these component changes are exact."

There is no epsilon, no "treat 0/0 as 0", and no partial Shapley.

### 4.6 Evidence rows and binding

There is one row per snapshot record, in key order, which is the same coverage as flat SUM:

```json
{"order_id": "O3", "baseline_included": false, "target_included": true,
 "membership": "target_only",
 "baseline_numerator": "0", "target_numerator": "30.00",
 "baseline_denominator": "0", "target_denominator": "1",
 "baseline_null_term": false, "target_null_term": false,
 "delta": "-150.00"}
```

- `*_included` is the shared `WHERE` outcome, so `membership` keeps its existing meaning. `*_null_term` is true when the record is in the numerator part but m is NULL, which keeps the "blank amounts" summary working. Extra booleans `*_in_numerator` and `*_in_denominator` record part-filter outcomes.
- `delta` is gᵢ. There are deliberately no `baseline_contribution`/`target_contribution` fields, so generic code cannot mistake gᵢ for a value difference. The breakdown code branches on the binding (§4.7).
- Manifest binding: `ratio_binding = {"policy": "flat-ratio-v1", "status": "defined" | "components_only", "N0", "N1", "D0", "D1", "sigma", "Q", "G", "numerator": {...part IR}, "denominator": {...part IR}}`, with every number an exact decimal string.
- `RatioEvidenceValidator`, routed like `relational_context`, which bypasses `ROW_FIELDS`, checks each row:
  - an excluded side has value 0;
  - a count part is 0 or 1;
  - nulls are consistent;
  - `delta == σ·(Δn·(D0+D1) − Δd·(N0+N1))` recomputed from the row and the binding, or `"0"` in components-only mode.

  `finish()` checks Σn and Σd at each endpoint against N0, N1, D0 and D1; that Q, σ and G are recomputed from N and D; and that `expected_result.ratio` matches.
- `verify_artifact` is called with `expected_delta = ratio_binding.G` (exact), **not** the rounded display delta. `worker.py` selects this when `ratio_context` is present.

### 4.7 Result and breakdown

Result fields:

- `aggregate: "ratio"`, `method: "flat_ratio_shapley"`, and `explanation_status: "complete_ratio_bridge"` or `"components_only"`.
- `baseline_value`, `target_value` and `delta_target_minus_baseline` hold **rounded display decimals** (half-even, `display_places` = contract scale + 4, capped at 12). `value_is_rounded: true` is set. `residual: "0"` applies to the exact fractions.
- `ratio`:
  - `{"baseline": {"numerator": N0, "denominator": D0, "exact": "p/q"}, "target": {…}}`;
  - `"gap_exact": "p/q"`, `"G"`, `"Q"`, `"sigma"`;
  - `"effects": [{"rule": "numerator_effect", "exact": "p/q", "display": "…"}, {"rule": "denominator_effect", …}]`;
  - `"corners": {"n0d0", "n1d0", "n0d1", "n1d1"}` as exact fractions or `null`;
  - `"pooled_average": "p/q"`;
  - `"unit"`: `"<measure unit> per record"`, `"share"` or `"rate"`. It is a share or rate when N and D have the same unit family; then the UI shows a percent.
- `steps`: two entries, numerator effect and denominator effect, with display decimals. `shapley` holds the existing `{status, method: "exact_two_part_shapley", contributions: [{rule, numerator, denominator, display_decimal}]}` shape, which `shapley.py` grows a tiny two-player sibling for.
- `definition_differences`: shared `WHERE`, numerator part and denominator part, each with a `changed` flag.

Report (UI):

1. Headline: "Average order value: 50.00 → 60.00 (+10.00)". A "Rounded; exact values available" affordance shows `p/q` on hover and in exports.
2. Component cards: numerator `SUM(amount)` 150.00 → 180.00 (+30.00 exact); denominator records 3 → 3 (0), "1 record left, 1 joined".
3. Bridge: numerator effect +10.000000, denominator effect 0.000000, total +10.000000. Displayed figures may differ from the total by rounding; the exact fractions add up exactly.
4. Record table, sorted by |effect|, showing N and D values per side, the effect (display; exact on demand) and a hint: "above the pooled average 55.00" or "left the denominator (blank amount)".
5. Breakdown by any record column. Groups partition records, so each group's effect is Σgᵢ/Q, which is exact and adds up to ΔR. Each group also shows N_g and D_g per side and the group ratio. `evidence_cache.breakdown` branches on `ratio_binding`: it sums gᵢ, nᵢ and dᵢ per group, then formats gᵢ/Q for display with exact fractions alongside.

A mix-and-rate split per group is a separate, also exact, decomposition, deferred to Q5.

### 4.8 What does not change

- SUM and COUNT(\*) parse paths, IR dicts, `ir_hash`, `_query` SQL, evidence rows, manifests, result bytes and replays.
- The test suite pins this with golden hashes (§6.4).
- `different_aggregates` still refuses SUM against COUNT(\*). It now also refuses SUM or COUNT against distinct or ratio, with an explicit message that names both aggregates.

### 4.9 R2: multi-measure ratios (FlatContract v8)

- A new contract `version: 8` with `measure_policy: "flat-multi-measure-v1"` and `predicate_policy: "flat-boolean-v1"`. `decimal_columns` holds 2–8 entries (`name`, `precision`, `scale`, `unit_label`), as in v6 but with no fixed count and **no expression**. There is no legacy `measure`.
- Allowed aggregates: `SUM(col)` for any registered decimal, `COUNT(*)`, `COUNT(col)`, `COUNT(DISTINCT category)`, and ratios whose parts reference any registered decimal.
- Unit rule for ratios: N and D must both carry declared units. Same unit gives a share/percent; different units give "N-unit per D-unit". Comparison requires the same numerator and denominator *columns*. Filters may differ.
- Precision: gᵢ has scale s_n + s_d ≤ 36, and its magnitude is bounded as in §4.4.
- Data Sources, the PostgreSQL collector (`flat_schema`) and `ingest_flat` need a v8 branch. This is the largest piece of R2 and is why it is separate.

### 4.10 R3: distinct-based ratios (deferred)

Forms like `COUNT(DISTINCT customer) FILTER (WHERE converted) / COUNT(DISTINCT customer)`, or `COUNT(*) / COUNT(DISTINCT customer)` (orders per customer). The evidence unit would be the **entity**: nₑ and dₑ are the per-entity part values (0/1 for distinct parts, and the per-entity sum of record terms for additive parts), and the same gₑ formula applies. Records with a NULL entity contribute to additive parts but belong to no entity. They need an explicit "(no customer)" pseudo-unit whose key cannot collide with a real value. That is the open design point, so R3 waits (Q3).

## 5. Implementation map

| Area | Change |
|---|---|
| `packages/metricguard_core/flat.py` | `parse_flat`: COUNT(DISTINCT) and ratio/AVG branches for v3–v5 / v5. `_check_ir`: dispatch on `aggregate_policy`. `_query`: distinct and ratio column lists. `compare_flat`: new endpoint builders and result blocks, via helpers in new modules so the SUM path stays byte-identical. |
| new `packages/metricguard_core/flat_distinct.py` | Parse/check helpers, entity grouping, evidence rows, functional-column test. |
| new `packages/metricguard_core/flat_ratio.py` | Part lowering (including AVG), exact N/D accumulation, σ/Q/G, row gᵢ, fractions and display rounding. |
| `packages/metricguard_core/shapley.py` | `exact_two_part_shapley(corners)`, returning Fractions in the existing JSON shape. |
| new `packages/metricguard_core/flat_distinct_evidence.py`, `flat_ratio_evidence.py` | Validators with `add(row)` and `finish(expected_result)`, plus `binding(context)`. |
| `services/supervisor/evidence_artifact.py` | `distinct_context` / `ratio_context` parameters in `write_artifact`/`verify_artifact`. Exclusive-binding checks for all bindings, so exactly one or none is present. |
| `services/supervisor/runner.py`, `worker.py` | Pass the contexts; write and validate `members.jsonl`; per-entity context with `excluded_columns`; `expected_delta` = G for ratios. |
| `services/supervisor/row_context.py` | `write_entity_context`, `validate_members`. |
| `services/api/evidence_cache.py`, `app.py` | Breakdown and summary branches for distinct and ratio; per-run aggregate-aware labels; registration of `evidence_members`. |
| `apps/web` | `resultSummary.ts`/`ResultReport.tsx`: distinct and ratio headline, component cards and bridge. `comparisonBuilder.ts`/`StartComparison.tsx`: "distinct count of" and "ratio / average" choices that generate the SQL above. Breakdown: unavailable-column reasons. |
| `services/cli.py`, `ci_workflow.py` | Report `aggregate` and, for ratios, rounded values plus exact fractions. The CI materiality comparison uses the exact fraction. |

## 6. Test plan

All fixtures are small enough to check by hand, and each has an **independent oracle**: plain Python over the fixture rows using `fractions.Fraction` and `Decimal`, with no import from `flat*.py`, no DuckDB and no shared helpers.

### 6.1 Distinct fixtures (contract v5, entity `customer_id`, categories `customer_id`, `status`, `segment`, `region`)

| key | customer_id | status | segment | region |
|---|---|---|---|---|
| R1 | C1 | paid | retail | N |
| R2 | C1 | refunded | retail | N |
| R3 | C2 | paid | trade | N |
| R4 | C3 | refunded | retail | S |
| R5 | C3 | refunded | retail | S |
| R6 | *NULL* | paid | retail | N |
| R7 | C4 | pending | trade | S |
| R8 | C2 | paid | trade | S |

- **D-1, gain.** Baseline `WHERE status = 'paid'` gives {C1, C2} = 2. Target `WHERE status IN ('paid','refunded')` gives {C1, C2, C3} = 3. Rows:
  - C1 both (supporting 1 → 2), C2 both (2 → 2), C3 target_only (0 → 2), C4 neither;
  - Σdelta = 1; gained 1, lost 0;
  - null-entity rows: baseline 1 (R6), target 1.
- **D-2, churn with zero net.** Baseline `status = 'paid'`, target `status = 'refunded'`. Baseline {C1, C2}, target {C1, C3}; gap 0; C2 lost, C3 gained; `value_status` equal but 2 changed entities. Null-entity rows 1 → 0.
- **D-3, empty.** Target `status = 'void'` has count 0, not NULL, and gap −2.
- **D-4, breakdown.** `segment` is functional (C1/C3 retail, C2/C4 trade), so D-1 by segment gives retail +1, trade 0, which adds up to 1. `region` is not functional (C2 is in N and S), so it appears in `excluded_columns` and the endpoint reports it unavailable.
- **D-5, refusals.** `COUNT(DISTINCT key)`, `COUNT(DISTINCT amount)`, `COUNT(DISTINCT a, b)`, distinct on v6/v7, comparing distinct customer against distinct segment (`different_distinct_columns`), and SUM against distinct.
- **D-6, members tamper.** Flipping one `t` flag, dropping a line, duplicating a key, or changing an `e` value makes the supervisor reject the run, and the evidence rows remain individually valid. This shows the membership check matters.

Oracle: `present = {e for (k, e, ...) in rows if e is not None and pred(row)}`, and the count is its length, computed for both endpoints. A cross-check against DuckDB `COUNT(DISTINCT)` lives in a separate test.

### 6.2 Ratio fixtures (contract v5, measure `amount` DECIMAL(10,2), unit INR, categories `status`, `channel`, `converted` boolean)

| key | amount | status | channel | converted |
|---|---|---|---|---|
| O1 | 100.00 | paid | web | true |
| O2 | 50.00 | paid | app | false |
| O3 | 30.00 | refunded | web | true |
| O4 | *NULL* | paid | app | false |
| O5 | 20.00 | pending | web | false |

- **RT-1, AOV against AVG with blanks (headline fixture).** Baseline `SELECT SUM(amount)/COUNT(*) FROM snapshot WHERE status = 'paid'`, so N0 = 150.00, D0 = 3 (O1, O2, O4) and R0 = 50. Target `SELECT AVG(amount) FROM snapshot WHERE status IN ('paid','refunded')`, so N1 = 180.00, D1 = 3 (O1, O2, O3) and R1 = 60.
  - σ = +1, Q = 18, D0+D1 = 6, N0+N1 = 330.00, G = 2·(180·3 − 150·3) = 180.00.
  - gᵢ: O3 = 30.00·6 − 1·330.00 = **−150.00**; O4 = 0·6 − (−1)·330.00 = **+330.00**; O1, O2, O5 = 0. Σ = 180.00 = G, so ΔR = 180/18 = **10** exactly.
  - Record effects: O3 = −25/3 (display −8.333333), O4 = 55/3 (18.333333).
  - Bridge: numerator effect 30·6/18 = 10, denominator effect 0 (ΔD = 0). The records show offsetting composition. Pooled average 55.
- **RT-2, AVG against SUM/COUNT(m) hash equality.** The two produce identical `ir_hash`.
- **RT-3, conversion, non-terminating.** `COUNT(*) FILTER (WHERE converted)/COUNT(*)`. Baseline with no WHERE: 2/5. Target `WHERE channel = 'web'`: 2/3.
  - ΔR = 4/15 (display 0.266667). σ = +1, Q = 30, D0+D1 = 8, N0+N1 = 4.
  - O2 Δn 0, Δd −1 → g = 4; O4: 4; O1, O3, O5 Δ 0 → g = 0. Σg = 8 = G = 2·(2·5 − 2·3) = 8, so 8/30 = 4/15 ✓.
- **RT-4, share with a part filter.** `SUM(amount) FILTER (WHERE channel = 'web') / SUM(amount)`. Baseline has no WHERE: 150/200 = 3/4. Target `WHERE status = 'paid'`: 100/150 = 2/3. ΔR = −1/12. Verify g by hand and Σg = G.
- **RT-5, negative denominator (σ = −1).** A variant fixture with refund rows stored as negative amounts so that D1 < 0 < D0. Q stays positive, signs of gᵢ match effect signs, and Σgᵢ/Q = ΔR.
- **RT-6, undefined.**
  - Target `WHERE channel = 'store'` makes D1 = 0 for COUNT, giving `zero_denominator`.
  - `SUM(amount) FILTER (WHERE amount IS NULL)/…` makes the numerator NULL, giving `null_numerator`.
  - `…/SUM(amount) FILTER (WHERE status = 'void')` gives `null_denominator`.
  - In each case, rows have `delta` "0", component sums match, the result gap and residual are null, and the defined side's ratio is still shown.
- **RT-7, breakdown.** RT-1 by `channel`: web Σg = −150.00 (O3) → −25/3; app Σg = 330.00 (O4) → 55/3. The groups add up to 10. Group ratios: web 100/1 → 130/2, app 50/2 → 50/1.
- **RT-8, refusals.** `SUM(a)/SUM(b)` on v5 gives "needs a dataset with two amount columns (R2)". Also refused: `*100`, `NULLIF`, nested ratio, `SUM(amount/2)`, a ratio on v3/v4/v6/v7, ratio against SUM, `COUNT(*)/SUM(amount)` against `SUM(amount)/COUNT(*)` (`different_ratio_shape`), and leaf caps.

Oracle (`tests/oracles/ratio_oracle.py`, test-only): for each endpoint it walks the fixture rows with its own predicate lambdas, builds N and D as `Fraction`s with SQL NULL rules, returns R = N/D or a reason, and computes ΔR. Tests assert:

1. The engine's exact fractions equal the oracle's.
2. Σ `Fraction(Decimal(gᵢ))` / Q equals the oracle ΔR.
3. Numerator effect + denominator effect equals the oracle ΔR.
4. Each corner equals oracle `Fraction(Nx)/Fraction(Dy)`.
5. Display strings equal `round_half_even(oracle, places)`.

### 6.3 Property and tamper tests

- **Seeded random snapshots.** 200 cases, 1–40 rows, amounts with scale 0–4 including negatives and NULLs, and random predicate pairs from a small leaf pool. For distinct, the engine count equals the oracle and the deltas add up. For ratios, the oracle agrees on endpoints, ΔR, effects and Σgᵢ/Q whenever defined, and the undefined reasons agree otherwise. Fixed seed list; the project does not depend on Hypothesis, so none is added.
- **Artifact tamper.** Each change must make `verify_artifact` raise `EVIDENCE_INVALID`:
  - change one nᵢ, dᵢ or gᵢ;
  - swap σ;
  - change Q or G in the binding;
  - drop a row;
  - set `status` to components_only with nonzero deltas;
  - attach a `ratio_binding` to a SUM run, or the reverse;
  - attach both `distinct_binding` and `case_binding`.
- **Precision.** DECIMAL(38,18) extremes force a 77-digit gᵢ that still serializes within 128 characters. A constructed case that would need more digits refuses with `arithmetic_overflow`, not silent rounding.

### 6.4 Compatibility (must pass before any merge)

- **Golden IR hashes.** Before implementation, record `ir_hash` for the existing SUM and COUNT(\*) fixtures on v3, v4, v5, v6 and v7 in `tests/golden/flat_ir_hashes.json`. After implementation they must be byte-identical.
- **Golden results.** `compare_flat` JSON for those fixtures, minus `elapsed_ms`, and the evidence SHA-256 values are byte-identical.
- **Saved-case replay.** Existing saved v3, v4, v5 and COUNT(\*) cases replay with an identical evidence SHA-256 (extends `tests/test_flat_workflow.py`).
- **Fail closed.** A `count_distinct` or `ratio` IR fed to the pre-change `_check_ir` (simulated by stripping the dispatch) raises `invalid_ir`, which shows older runners cannot misinterpret new IRs.

### 6.5 Checks for the handoff (per AGENTS.md; the user runs them)

```
.venv\Scripts\python -m pytest -q --basetemp .test-tmp/full
npm run build --prefix apps/web
docker compose -f infra/compose.yaml config --quiet
.venv\Scripts\python -m scripts.verify_live_runner --output data/evaluation/live_runner_report.json
```

Plus manual UI cases: RT-1, D-2 and RT-6 through Start a comparison. Check the headline, the bridge, the record table, the breakdown, saving the case and replay.

## 7. Delivery slices

1. **S1, distinct.** Parse, IR, engine, entity evidence, membership file, validator, result and report. Breakdown with the functional-column filter.
2. **S2, ratio core.** Parse (including AVG and FILTER), IR, exact N/D, Shapley bridge, gᵢ evidence, validator and undefined handling. CLI and CI fields.
3. **S3, ratio UI and breakdown.** Report cards, record table hints, exact-fraction display and group ratios.
4. **S4, R2.** FlatContract v8 multi-measure (contract, ingestion, collector, Data Sources), then ratios across measures.
5. **S5, R3.** Distinct-based ratios, after Q3 is decided.

S1 and S2 touch disjoint new modules but share edits in `flat.py`, `evidence_artifact.py` and `worker.py`, so they should land one after the other, not in parallel.

## 8. Open questions

- **Q1, evidence key name.** Reusing `order_id` for entity values and ratio records keeps every reader working, at the cost of a misleading field name. The alternative is a new `unit_key` field, with `record_key()` and the cache taught to read it. Recommendation: reuse `order_id` now, consistent with the retail engine, and rename across all policies in one later migration.
- **Q2, display places.** Contract scale + 4 (capped at 12) is a guess. Analysts may want a percent shown to 2 decimal places for rates. This is presentation only; exact fractions are unaffected.
- **Q3, the NULL-entity pseudo-unit in R3.** It needs a reserved key that cannot collide with a real canonical value (for example, a key outside the 256-character limit, or a separate field), and a decision on how the UI labels it.
- **Q4, distinct counts per group.** "Distinct customers by region" (each group's own COUNT DISTINCT, which need not add up to the total) is a common ask. It could be offered as a separate, clearly labeled non-additive table, or refused. This design refuses it in the breakdown.
- **Q5, mix and rate per group.** For ratio breakdowns, the classic split ΔR = Σ_g (rate effect + mix effect) is also exact and very useful ("AOV fell because the app share grew, not because app orders got smaller"). It is a second decomposition, so it should not be added until the record/group effect view has been tested with users.
- **Q6, v4 ratios.** Restricting ratios to v5 avoids a second filter dialect for part filters. If many live datasets are still v4, ratios without part filters (AOV, AVG) could be allowed on v4.
- **Q7, materiality for ratios.** CI and recurring-check thresholds are absolute decimals today. A ratio probably needs a threshold in percentage points; the policy shape is to be decided with `metric-governance-v1`/`v2`.
- **Q8, maximum evidence size for distinct.** One row per entity plus one membership line per record stays within the 100k-row cap. The membership file adds about 40 bytes per record; this needs confirming against `MAX_CONTEXT_BYTES`-style limits for a new `MAX_MEMBERS_BYTES`.
