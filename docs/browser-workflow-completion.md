# Browser Workflow Completion Report

**Date:** 2026-09-25  
**Scope:** Fresh browser journey verification for flat snapshot workflow  
**Workspace:** `data/evaluation/flat-olist-browser`

## Summary

The fresh browser workflow check has been **completed and verified**. A user starting from an empty workspace successfully:

1. Uploaded the prepared Olist CSV through the file chooser
2. Previewed the file (3 columns, 5 sample rows)
3. Registered a flat contract with whole-snapshot scope confirmation
4. Attached an external preparation manifest
5. Validated the snapshot through isolated import (99,441 rows)
6. Registered baseline and target SQL definitions
7. Ran a comparison showing the correct endpoints
8. Inspected evidence across multiple pages (page 1-100, then 101-200)
9. Saved the investigation
10. Reloaded the page and reopened the saved case
11. Replayed the pinned case successfully

**Result:** MetricGuard reproduced baseline **16,008,872.12**, target **15,599,675.73**, and gap **−409,196.39** through the complete browser-driven workflow.

## Verified Endpoints

| Metric | Value |
|--------|-------|
| Baseline | 16,008,872.12 |
| Target | 15,599,675.73 |
| Gap (target − baseline) | −409,196.39 |
| Residual | 0.00 |
| Evidence rows | 99,441 |

## UI Corrections Applied

During the browser walkthrough, the following issues were identified and fixed:

1. **Blank SQL editors after reopening** → Fixed: pinned SQL now restored
2. **Stale results after changing inputs** → Fixed: result hidden when draft changes
3. **Raw membership wording** → Improved: clearer labels for inclusion logic
4. **Unclear export bounds** → Fixed: explicitly states "first 100 rows"
5. **No selected-manifest feedback** → Fixed: UI now shows when manifest is attached
6. **Flat case appearing in retail investigation list** → Fixed: proper filtering by mode

## Provenance Tracking

### What MetricGuard Captures

When a saved investigation is created, MetricGuard records:

```json
{
  "preparation_manifest_hash": "9f36358e0db3aae1d5000b440cb39457b84b094ade9c485bb79465353fcc5792"
}
```

**Source:** [`services/api/store.py:170-174`](../services/api/store.py)

The preparation manifest hash is:
- ✅ **Pinned** at investigation save time
- ✅ **Preserved** through case reopen
- ✅ **Available** in replay validation
- ✅ **Exported** in JSON artifacts

The manifest itself contains:

```json
{
  "version": 1,
  "classification": "external_preparation",
  "script": "scripts/prepare_olist_flat_snapshot.py",
  "script_version": "1",
  "transformation": "Group exact payment values by order_id...",
  "source_hashes": {
    "orders_sha256": "8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf",
    "payments_sha256": "4f713964f2815dbbaa40b9488268c55aac3627bfce5aa96cf58d1f3616de3cc0"
  },
  "flat_sha256": "3c50562e66e8c172b3ff29c8738cd3503c52854effbe95e3b00f46f36e9af87a",
  "lineage_sha256": "452ae7b5a16f5e4d87296ab6c479957bd8ec91b148edb1ff58e30d44a0a1baa5"
}
```

### What MetricGuard Does NOT Capture

The following are **referenced by hash only**, not retained or independently verified:

- ❌ Original source files (`olist_orders_dataset.csv`, `olist_order_payments_dataset.csv`)
- ❌ External lineage file (`olist-order-flat-lineage.csv`)
- ❌ Preparation script execution or version control state
- ❌ Semantic correctness of the transformation

**Implication:** Hash identity confirms byte-for-byte reproducibility of the prepared file, but does **not** establish that the preparation logic is correct or that the original sources were authentic.

### Provenance Connection Status

| Aspect | Status | Notes |
|--------|--------|-------|
| Prepared CSV hash | ✅ Pinned | Immutable snapshot cannot change |
| Preparation manifest hash | ✅ Pinned | Saved case references it |
| Original source hashes | 📄 Referenced | In manifest, not retained by MetricGuard |
| Lineage file hash | 📄 Referenced | In manifest, not retained by MetricGuard |
| Transformation script | 📄 Referenced | Name and version recorded, not executable |
| Manifest availability at replay | ✅ Preserved | Same hash shown in export |

**Conclusion:** A saved investigation successfully pins and preserves the preparation manifest hash. Replay reproduces the prepared CSV result. However, **tracing back to original files requires external custody of those files and the lineage artifact**—MetricGuard does not re-execute or independently verify the preparation step.

## 100,000-Row Limit

### Current Implementation Bound

**Location:** [`packages/metricguard_core/flat.py:21`](../packages/metricguard_core/flat.py)

```python
MAX_FLAT_ROWS = 100_000
```

**Enforcement:** [`services/supervisor/worker.py:410`](../services/supervisor/worker.py)

```python
not 0 <= manifest['row_count'] <= 100_000
```

### Rationale

The 100,000-row limit is a **current implementation bound** for:

1. **In-memory comparison** – The core comparison engine constructs full row evidence in memory before serialization
2. **Evidence publication** – The supervisor verifies the complete artifact before publishing
3. **Bounded exports** – JSON/HTML exports include evidence coverage with clear included/omitted counts

This is **not**:
- A performance benchmark or throughput claim
- A fundamental architectural limit
- A claim about concurrent-service capacity

### Verification Evidence

#### At-Limit Case (100,000 rows exactly)
- **Check:** Direct flat core ingestion fixture
- **State:** ✅ Validated
- **Kind:** Synthetic boundary fixture

#### Over-Limit Case (100,001 rows)
- **Check:** Live isolated API and Docker supervisor
- **Import ID:** `9b546734-7476-40c9-a291-04f740c675f9`
- **State:** ❌ Failed
- **Error Code:** `TOO_MANY_ROWS`
- **Error Message:** "Flat snapshot exceeds 100000 rows"
- **Snapshot Created:** None (workspace snapshot count unchanged)

**Source:** [`data/evaluation/flat-olist-browser/browser-verification.json:57-76`](../data/evaluation/flat-olist-browser/browser-verification.json)

### User Experience

An oversized upload receives:
1. **Clear refusal** with error code `TOO_MANY_ROWS`
2. **Explicit message** stating the limit
3. **No partial snapshot** created
4. **Workspace integrity** preserved (count unchanged)

The Olist case (99,441 rows) fits within the limit with **559 rows of headroom**.

## Remaining Work

### Immediate Next Steps

1. ✅ **Fresh browser journey** – Completed and verified
2. ⏭️ **Second independent case** – Not yet tested
   - Use different column names within SUM/filter scope
   - Record preparation steps and any unsupported requirements
   - Verify that the new mode generalizes beyond Olist
3. ⏭️ **Result screen review** – Using completed investigation
   - Check that a reader immediately understands:
     - What each definition includes
     - Why the comparison differs by −409,196.39
     - Which records support the difference
     - What preparation happened outside MetricGuard
     - What remains unknown

### Outstanding Questions

1. **Source preparation automation** – Not yet demonstrated
   - Current workflow requires external Python script
   - Manual preparation manifest creation
   - No analyst time savings measured

2. **Second public case** – Required before claiming generalization
   - Different schema and categories
   - Independent oracle verification
   - Documented coverage boundaries

3. **Analyst pilot** – V2-06 gate remains open
   - No independently sourced sanitized real cases
   - No measured analyst completion times
   - No second-analyst observations
   - No wrong-conclusion checks

## References

- [V2 Release Evidence](v2-release-evidence.md#fresh-browser-workflow-and-preparation-provenance-2026-09-25)
- [Browser Verification Report](../data/evaluation/flat-olist-browser/browser-verification.json)
- [Olist Public Case Evaluation](pilot/olist-public-case.md)
- [Preparation Manifest](../data/public-case-olist/olist-order-flat-manifest.json)
