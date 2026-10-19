# MetricGuard: senior-analyst evaluation, second run (2026-10-18)

This re-evaluates the product after the linked sessions finished their work. Their code is still **uncommitted** on top of `86f7ffb`. It added:

- distinct counts, averages, rates, shares and margins beyond 100,000 rows;
- comparing two data loads;
- correct units in every list;
- refusal warnings before a comparison runs;
- clean-up of auto-created definitions.

The first evaluation is in [analyst-evaluation-2026-10-18.md](analyst-evaluation-2026-10-18.md).

**Harness:** [`scripts/stress_analyst.py`](../../scripts/stress_analyst.py), extended with:

- strict checks for the broad (750,000-record) change;
- comparing two data loads;
- a 500,000-fact two-table dataset.

Every engine value is checked against an independent Python calculation, using integers and exact fractions.

**Raw output** (gitignored):

- `data/evaluation/stress_analyst_run3.log` (shipped settings)
- `stress_analyst_diag.log` (diagnostic settings)
- `pytest_eval3.log`

## Verdict

The breadth gap from the first evaluation is mostly closed:

- distinct counts and ratios run beyond 100,000 rows;
- two data loads can be compared;
- the units in lists are right.

**Every number the engine produced matched the independent calculation exactly.** Zero mismatches across about 60 checks, including:

- 750,000 changed records;
- 1,000,000-row distinct counts;
- 1,000,000-row averages, rates and shares;
- 500,000-row margins.

But **as shipped, the large-data features do not work in the isolated runner.** It gives `/tmp` a 64 MB in-memory area (`services/supervisor/worker.py:202`). The new code spools evidence for large comparisons to temporary files, which need far more than that. So on 1M rows, every check below fails:

- the broad change;
- distinct count;
- average;
- rate;
- share;
- the 500k margin.

The errors read "Temporary evidence storage is unavailable or full" or "Internal runner execution error". With the area raised to 2 GB, all of them pass exactly. The session that lifted the limit tested outside Docker, so this was never seen.

**Score:**

- As shipped: **6.5 / 10.** Large data still fails, now with a worse message than the old upfront refusal.
- After the temp-storage fix: **about 7.5 / 10.**
- With the fixes below: **8 / 10.**

## Stress results

### Shipped settings (64 MB `/tmp`, 1 GB memory)

| Check (1,000,000-row table unless noted) | Result | Time |
|---|---|---|
| Import 1M rows (44 MiB) | Pass | 29.1 s |
| Broad change: all vs paid (749,832 records change) | **Fail**: temporary evidence storage full | 12.1 s |
| SUM, narrow change (50,512 records) | Pass, exact, zero unexplained | 15.8 s |
| CSV of changed records adds up | Pass | 3.0 s |
| Breakdown by region | Pass, exact | 1.3 s |
| Evidence pages with record columns | Pass | 0.06 s |
| Second data load (1M rows), then compare loads | **Refused**: two-load comparison allows at most 100,000 records per load. Refused only after queuing. | 4.4 s |
| COUNT(*) | Pass, exact | 11.9 s |
| COUNT(DISTINCT customer_id) | **Fail**: storage full | 28.2 s |
| AVG / share | **Fail**: "Internal runner execution error" | 13 s / 11 s |
| Rate | **Fail**: storage full | 17.4 s |
| Replay of SUM case | Pass, identical evidence hash | 12.0 s |
| Burst: 8 comparisons queued | Pass, all exact. COUNT runs that were refused last time now pass. | 176.5 s |
| 200 parallel reads during burst | Pass. p50 0.05 s, p95 0.25 s. | — |
| Duplicate key at row 1,000,001 | Refused, key named | 35.2 s |
| Margin on 500,000 rows | **Fail**: storage full | 15.6 s |
| Two tables: 100k facts and 3 lookups | Pass, exact | 45.9 s |
| Two tables, over 100k facts (v2 contract) | Refused | — |
| Edge cases (encoding, keys, decimals, schema) | 17 of 17 behave as expected | — |
| Over 5M rows / 530 MiB upload | Refused | 87 s / 25 s |
| Unsupported SQL (window, HAVING, UNION, CTE, stacked, functions, file reads) | Refused, messages updated | — |

### Diagnostic settings (2 GB `/tmp`, 3 GB memory; reverted afterwards)

| Check | Result | Time |
|---|---|---|
| Broad change, 749,832 records | Pass, exact | **124 s** |
| CSV of 749,832 changed records (37 MiB) | Pass, adds up | 51 s |
| COUNT(DISTINCT) on 1M | Pass, exact (49,685 → 49,106) | **101 s** |
| Records of one customer | Pass | 9 s |
| AVG on 1M | Pass, exact fraction | **284 s** |
| Rate on 1M | Pass, exact fraction | **249 s** |
| Share on 1M | Pass, exact fraction | **1,301 s (21.7 min)** |
| AVG record effects (CSV of 1,000,000 rows) | Pass, effects sum exactly to the gap | 200 s |
| Replays: distinct / AVG / SUM | Pass, identical hashes | 97 s / 311 s / 12.5 s |
| Burst of 8 | Pass, exact | 151 s |
| 200 parallel reads during burst | Pass, 0 errors. **p95 10.5 s, max 14.8 s.** | — |
| Margin on 500,000 rows | Pass, exact | 143 s |

### Other checks

| Check | Result |
|---|---|
| Compare two data loads, 50,000 rows (2% edited, 1% removed, 5,000 added) | Pass, exact, 6 s. Listed changes add up. The export lists **all 55,000 records**, not only changed ones. |
| Two-table dataset, 500,000 facts with 2 lookups, prepared through the API (`prepare-flat`) | Pass: prepare 14.7 s, compare 12.4 s, exact. **API only; no screen for it.** Columns become `field_1…field_3`. |
| Backend tests | 710 passed, **3 failed** (see bug 2) |
| Web tests and build | 78 / 78, build OK |

## Good points (new since the first evaluation)

1. **Exactness held under much heavier load.** 750k changed records, 1M-row distinct counts and ratios, and 500k-row margins all matched to the cent or as exact fractions. Replays were byte-identical.
2. **Compare two data loads.** "Why did the number change between September and October?" is now answerable record by record (added, removed or changed). This was the biggest missing analyst question.
3. **Units are right everywhere I checked.** The Changes list shows "Differs" (no more "Drift detected") with the correct unit.
4. **The guided form offers every calculation type at 1M rows:** SUM, COUNT, distinct, AVG, per-record, rate and share.
5. **Prepared two-table data scales.** 500,000 facts with lookups compared in about 12 s once prepared.
6. **Input safety is still solid.** Every malformed-input and unsupported-SQL case was refused with a specific message.

## Bad points / bugs

1. **Blocker: large non-SUM comparisons fail in the isolated runner** (64 MB `/tmp`). It needs a scratch area sized for the disk-evidence budget, for example a per-job bind-mounted scratch directory with `TMPDIR` pointed at it. Also include it in the live-runner check, so a Docker-only failure like this cannot pass unnoticed again.
2. **Bug: on Windows, a duplicate key in a two-table upload shows a file-in-use error instead of "duplicate key at line N".** `relational_star.py:421` opens the CSV without a `with`. When the import is refused, the file stays open and the temp-folder clean-up fails. The 3 failing backend tests cover exactly this.
3. **Ratios are slow at scale.**
   - AVG and rate take about 4–5 minutes on 1M rows, and share about 22 minutes.
   - Distinct counts and broad SUM changes take about 2 minutes.
   - An analyst will give up before then. Profile the exact-fraction record-effect path. Share is 5× slower than AVG on the same data.
4. **Reads slow down during heavy jobs.**
   - Workspace load went from 0.03 s idle to 7.6 s during a large job, and p95 reads reached 10.5 s.
   - The workspace payload has also grown to about 300 KB, taking 0.7–1.5 s at idle.
5. **Comparing two loads stops at 100,000 rows per load**, and the refusal comes after queuing, not before. The export lists every record rather than only the changed ones.
6. **Large two-table data is API-only.**
   - The prepare step has no screen.
   - Prepared columns are renamed `field_1…`, so analysts must remember the mapping.
   - Preparation runs inside the API process.
7. **Failed runs don't say why in the Changes list.** You have to open each one.
8. **Carried over from the first evaluation:**
   - Comparisons run one at a time.
   - Two-table filters still allow no `<>` or ranges on lookup columns (the harness hit this again).
   - Connectors cover only CSV, Parquet and PostgreSQL.

## Fixes applied and rerun (same day)

### 1. Runner scratch storage

`services/supervisor/worker.py`, `infra/Dockerfile.runner`. Temporary files now go to a fresh anonymous Docker volume mounted at `/job/scratch` (`TMPDIR` and `SQLITE_TMPDIR`). It is disk-backed inside the Docker VM, does not count against the 1 GiB memory limit, and is removed with the container (`--rm`, plus `docker rm -v` on a forced stop). The 64 MiB in-memory `/tmp` and every other isolation setting are unchanged.

- **A host bind mount was tried first.** The broad change took 579 s that way, because Docker Desktop file sharing is slow.
- **Live check.** `scripts/verify_live_runner.py` now writes 200 MiB through `tempfile` in the real container and checks that the volume is gone afterwards: **pass**.
- **No leftover volumes.** There were 10 volumes before the rerun and 10 after it, across about 20 jobs.

### 2. Two-table duplicate key on Windows

`relational_star.py` now opens the source CSV with `with`, so a refusal closes it before the temp folder is removed. The 3 previously failing tests pass.

### Rerun on shipped settings (1 GB memory, 64 MiB `/tmp`)

| Check (1M rows unless noted) | Before | After |
|---|---|---|
| Broad change, 749,832 records | Fail | Pass, exact, **71 s**. CSV adds up, 26 s. |
| COUNT(DISTINCT) | Fail | Pass, exact, **54 s** |
| AVG / rate / share | Fail | Pass, exact fractions, **230 / 221 / 295 s** (share was 1,301 s on the in-memory area) |
| AVG record effects (1,000,000 rows) | — | Pass, sum exactly to the gap, 170 s |
| Replays: distinct / AVG / SUM | — | Identical hashes, 96 / 285 / 11 s |
| Burst of 8 | — | Pass, exact, 118 s. Reads p95 13.4 s; the backend tests were running at the same time. |
| Margin on 500,000 rows | Fail | Pass, exact, **126 s** |
| Two data loads, 1M rows | Refused (100k limit) | Still refused. Not part of this fix. |

**Tests:**

- Backend: **713 passed, 0 failed.**
- Live runner check: pass.

**Score after the fixes:** about 7.5 / 10. Ratio speed (4–5 minutes on 1M rows) is now the main limit.

## Recommended order

1. Fix the runner scratch storage and add a Docker-run check for one large non-SUM comparison. **Blocker.**
2. Close the file handle in the star import, then rerun the 3 failing tests.
3. Profile and speed up ratio and distinct evidence. The target is under 30 s on 1M rows.
4. Lift the two-load limit, or warn before queuing. Export only changed records by default.
5. Add a "Prepare for large comparisons" screen that keeps the original column names.
6. Show failure reasons in lists, and trim the workspace payload.
