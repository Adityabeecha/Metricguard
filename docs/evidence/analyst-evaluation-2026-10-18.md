# MetricGuard: senior-analyst evaluation and stress test (2026-10-18)

How useful MetricGuard is to a working senior data analyst, judged on the running product. Two inputs:

- **Hands-on use.** I walked through the guided flow, the result report, Investigations, Changes, Metrics and the Dashboard.
- **A stress run.** [`scripts/stress_analyst.py`](../../scripts/stress_analyst.py) ran against the live instance: Docker runner, `http://127.0.0.1:8010`. Every engine value was checked against an independent Python calculation (integers and exact fractions).

Raw logs are in `data/evaluation/stress_analyst_run1.log` and `stress_analyst_run2.log`; the JSON reports are under `data/evaluation/stress_analyst/`. Both locations are gitignored.

## Verdict

MetricGuard answers one question very well: **"Why do these two calculations of the same metric disagree, record by record?"**

- The answer is exact and reproducible.
- That is a real, recurring pain for analysts: finance revenue versus product revenue, a dbt model change before merge, an audit asking "prove it".
- On that job it is better than what analysts do today (ad-hoc SQL diffs in a notebook).

Its usefulness is capped by three things:

1. **Scale.** The new ratio and distinct-count metrics stop at 100,000 rows.
2. **Change size.** No comparison may change more than 100,000 records.
3. **Scope.** It compares two *definitions* on one frozen dataset. It does not explain why a metric *moved over time* between two loads, which is the question analysts get asked most often.

**Score for a senior analyst today:** 6.5 / 10.

- It is a strong, trustworthy tool for definition reconciliation and metric-change review.
- It is not yet a daily driver for warehouse-scale investigations.

## Stress test results

### Single table, 1,000,000 rows (44 MiB CSV)

Includes blank amounts, negatives and about 50,000 customers.

| Check | Result | Time |
|---|---|---|
| Import 1M rows | Pass | 23.7 s |
| Broad change: all orders vs paid orders (749,832 records change) | **Refused** (`EVIDENCE_LIMIT`, over 100,000 changed records) | 6.1 s, refused after running |
| SUM, paid vs paid outside one region (50,512 records change) | Pass. Exact to the cent, zero unexplained difference. | 8.9 s |
| Listed records add up to the change (CSV export, 50,512 rows) | Pass | 2.3 s |
| Breakdown by region, exact per group | Pass | 0.9 s |
| Evidence pages with record columns | Pass | 0.02 s per page |
| COUNT(*) | Pass, exact | 6.7 s |
| COUNT(DISTINCT customer_id) | **Refused** (`TOO_MANY_ROWS`: over 100,000 rows) | 1.8 s |
| AVG, rate (conversion), share | **Refused** (`TOO_MANY_ROWS`) | about 1.5 s each |
| Replay of the 1M SUM case | Pass. Identical evidence SHA-256. | 9.5 s |
| Burst: 8 comparisons queued at once | 5 SUM runs exact. 3 COUNT runs refused (`EVIDENCE_LIMIT`: 175k changed records each). Runs execute **one at a time**. | 70.5 s total |
| 200 parallel reads during the burst | Pass, 0 errors. p50 0.05 s, p95 0.42 s, max 0.97 s. | — |
| Duplicate key at row 1,000,001 | Refused with the key named | 18.2 s |

### Several amount columns (margin %)

| Rows | Result |
|---|---|
| 100,000 | Margin exact as a fraction, 16.8 s |
| 500,000 | **Refused** (`TOO_MANY_ROWS`) |

### Below 100,000 rows

Every calculation kind matched the oracle exactly:

- SUM, COUNT(*), COUNT(DISTINCT), AVG, rate and share, each with record effects that add up to the change exactly;
- the margin above;
- replays of the distinct and average cases were identical.

### Fact table with three lookups

The table had 100,000 facts and three lookups (20,000 / 5,000 / 200 rows), with 1% blank links and 1% links that match no lookup row.

- Import: 4.4 s.
- Comparison with an INNER join, a LEFT join and a third INNER join with filters: exact, 17.1 s.
- 100,001 facts: refused.
- `<>` in a lookup filter: **refused**. Two-table filters allow only equality, IN and IS NULL tests.

### Input safety and edge cases

**Accepted:**
- UTF-8 BOM, CRLF line endings, quoted commas and quotes, Devanagari and emoji keys;
- formula-like keys (`=1+1`, `@x`), which are neutralised in the CSV export;
- a 256-character key;
- a header-only file;
- extra columns, which are silently ignored.

**Refused, with row-level messages:**
- an empty file (returned as HTTP 413, which is the wrong status code);
- a 10,000-character key (keys are capped at 256 characters);
- 3 decimals, `1e5`, `abc`, `"1,000.00"`;
- precision overflow;
- a missing column, a blank key, a duplicate key, a ragged row;
- Latin-1 bytes.

**Large files:**
- 5,000,001 rows: refused after 57.8 s of reading.
- A 530 MiB streamed upload: refused with HTTP 413 in 6.5 s.

**SQL refused:** window functions, HAVING, UNION, CTE, stacked statements, function calls, `read_csv_auto(...)`.

**One quirk:** a trailing `-- comment` is accepted and ignored. That is correct SQL, but the comment text stays in the saved SQL.

## Good points (what an analyst will value)

1. **Numbers you can defend.**
   - Every result matched an independent calculation to the cent or as an exact fraction.
   - The unexplained difference was zero in every defined case.
   - A replay reproduces byte-identical evidence. This is the product's strongest asset: it holds up in a finance or audit review.
2. **Record-level answers, not just totals.**
   - "These 50,512 orders explain −X", with a breakdown by any declared column that adds up exactly, plus a CSV export.
   - The distinct-count drill-down shows which records made a customer count or not.
3. **The ratio bridge is real analysis.**
   - AVG, conversion and margin changes are split exactly into a top-part effect and a bottom-part effect, then into per-record effects.
   - Most BI tools cannot tell you *why* an average moved.
4. **Honest refusals.** Unsupported SQL, bad rows and limits are refused with specific messages rather than producing a wrong number.
5. **Fits the analytics-engineering workflow.** Frozen snapshots, saved investigations with a written conclusion, metric lifecycle, dbt import, a CI gate and a PostgreSQL collector.
6. **Fast enough at 1M rows for SUM/COUNT.**
   - Import takes about 24 s and a comparison about 9 s.
   - Evidence pages, breakdowns and exports respond in under a few seconds.
   - Reads stay fast under 20 parallel readers.

## Bad points (what will frustrate an analyst)

1. **The scale ceiling contradicts the new metrics.**
   - Distinct counts, averages, rates, shares and margins are refused on any dataset over 100,000 rows.
   - Warehouse tables are usually far larger, so "warehouse scale" and "ratios and distinct counts" work together only on small extracts.
2. **The changed-record cap blocks the most common comparison.**
   - On 1M rows, "all orders vs paid orders" (750k records change) is refused.
   - The refusal comes only *after* the comparison runs (about 6 s), and the guided form does not warn in advance.
3. **The UI offers what the engine will refuse.** On the 300,000-row dataset the guided form still offers AVG, rate, share and distinct count. The analyst finds out only after "Check and compare".
4. **Wrong units in lists.** The Investigations list, Dashboard and Changes list attach the dataset's currency to every change:
   - a distinct-count change shows as "-1 INR";
   - a COUNT change shows as "INR -2,223";
   - a margin change shows as "INR -0.025478" instead of "-2.55 pts".

   The result page itself is correct. For an analyst this undermines trust in the very place it is supposed to be strongest.
5. **One definition, one snapshot.**
   - The product explains definition differences on fixed data.
   - It cannot compare *the same metric across two loads* (September vs October, before vs after a pipeline fix). That is the most frequent "why did the number change?" question.
6. **Two-table work is much narrower than single-table work.**
   - Limits: 100,000 facts, 25 MiB.
   - Filters: equality, IN or NULL tests only. No `<>`, ranges or OR on lookup columns.
   - Two-table comparisons support no distinct counts or ratios.
7. **Upfront modelling before any answer.**
   - You must declare the row key, amount, unit and filter columns before uploading.
   - You can filter only on declared columns, so a forgotten column means re-registering the dataset.
   - That is right for governance, but slow for a first look.
8. **Comparisons run one at a time.**
   - 8 queued comparisons on 1M rows took 70 s.
   - A team sharing one instance will feel the queue.
9. **Noise builds up.**
   - Every comparison registers definitions: the Metrics page now lists 44 "unassigned query definitions".
   - Running the same comparison twice leaves two identical drafts.
   - Every candidate run is labelled "Drift detected" on the Changes page, which sounds like an alert when it is just a difference.
10. **Out-of-date refusal messages.** HAVING and CTE refusals still say "Only one SUM, or COUNT(*)…", although DISTINCT, AVG and ratios are now supported.
11. **Connectors.** CSV/Parquet upload and PostgreSQL only. There is no Snowflake, BigQuery or Redshift collector, and no Excel input.

## Recommended next steps, by analyst impact

1. Fix units in the Investigations list, Dashboard, Changes list and CaseReport header. This is small, and it protects trust.
2. Warn in the guided form before running:
   - hide or disable distinct, average and ratio choices on datasets over 100,000 rows, with the reason;
   - estimate changed records with a cheap count and warn before a comparison breaches 100,000.
3. Lift the 100,000-row ceiling for distinct counts and ratios. The changed-records-only evidence path used for SUM over 100k rows could extend to them, because unchanged records contribute nothing to the bridge.
4. Add snapshot-vs-snapshot comparison of one definition (period-over-period explanation).
5. Bring two-table filters up to single-table parity: `<>`, ranges, OR.
6. Tidy up: collapse duplicate drafts, hide auto-registered definitions from the Metrics catalog, rename "Drift detected" to "Differs", and update the refusal messages.
7. Run comparisons concurrently, or at least show queue position.
