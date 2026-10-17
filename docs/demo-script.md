# MetricGuard demo script

Prepared for the V8 closeout (2026-10-17). Every number below is recomputed independently by `scripts/seed_closeout_demo.py` (Python `Decimal`, no MetricGuard code) and compared with the engine before the demo case is saved.

## Before you present

```powershell
.\scripts\mg.ps1 start -Seed
.\scripts\mg.ps1 status
```

Expect `API: running`, `Supervisor (comparison engine): running`, `mode isolated, engine available: True`, and the seeder printing:

```
Demo · Completed orders only: 750.75 → 410.75 INR (refusal check: …)
Demo · North segment via join: 65.00 → 10.00 points
```

The demo data is in [`demo/closeout/`](../demo/closeout): an 8-row orders file and a 5-row fact file with a 2-row group dimension.

## Two-minute demo: 750.75 → 410.75

| Time | Do | Say |
| --- | --- | --- |
| 0:00 | Open <http://127.0.0.1:8000>. Dashboard. | “Two people report different sales for the same eight orders. MetricGuard finds out why.” |
| 0:10 | **Investigations** → **Demo · Completed orders only**. | “It opens on the answer.” Read the headline: *The proposed calculation is 340.00 INR lower than the current calculation. 3 records account for the difference.* |
| 0:30 | Point at the four figures. | “Current 750.75, proposed 410.75, change −340.00, unexplained difference 0. Nothing is left over.” |
| 0:45 | Point at *Records that explain the difference*. | “R003 pending 200.00, R004 returned 50.00, R007 cancelled 90.00. Those three add up to exactly 340.00.” |
| 1:00 | Open **Missing-value handling**. | “R006 has a blank amount. It is ignored by both totals: not counted as zero, not hidden.” |
| 1:10 | Open **Original SQL**. | “The only difference is `WHERE status = 'completed'`.” |
| 1:20 | Read *What remains uncertain*. | “It explains the gap; it doesn’t claim either number is right, and a lower total isn’t automatically a loss.” |
| 1:30 | **Replay saved inputs**. | “Replay re-runs both calculations in the isolated container on the frozen data.” Wait for *Replay reproduced the saved result exactly*. |
| 1:50 | **Start a comparison** (sidebar). | “A new analyst starts here: choose data, describe two calculations, review, save. No SQL required.” |

### If asked to build it live (about 2 more minutes)

1. **Start a comparison** → choose **Demo sales orders (8 rows)** → *Next*.
2. Current calculation: leave *no conditions*. Proposed: *status · is · completed*. Expand *SQL this form produces* to show the generated SQL.
3. **Check and compare** → the same −340.00 and three records appear.
4. **Next: save explanation** → write a conclusion → **Save investigation**. It opens as a report.

## Honest refusal (30 seconds)

In **Start a comparison**, switch the proposed calculation to **SQL editor** and enter:

```sql
SELECT AVG(sales_amount) FROM snapshot
```

**Check and compare** shows *Query not supported* with the reason and the supported grammar. The SQL stays in the editor. Nothing is computed or saved. Say: “It refuses rather than approximating.”

## Two tables: joins (optional, 45 seconds)

Open **Demo · North segment via join**: 65.00 → 10.00 points. Open **Join behavior**: F3 points to a group (`DX`) that does not exist and F4 has no group; the INNER JOIN drops them before the filter. F2 is in the south segment. F5 has a blank amount.

## Olist walkthrough (real public data, ~5 minutes)

Uses the verified public [Olist case](pilot/olist-public-case.md) (99,441 orders, 103,886 payment records). It needs a one-time public download; the data stays under ignored `data/public-case-olist/`.

```powershell
.\.venv\Scripts\python -m scripts.acquire_olist_public_case --download
.\.venv\Scripts\python -m scripts.evaluate_olist_public_case
.\.venv\Scripts\python -m scripts.prepare_olist_flat_snapshot
```

Then in the app: **Data Sources → Add data source** → upload `data\public-case-olist\olist-order-flat.csv` and answer the questions (row key `row_id`, amount `payment_value`, unit `source numeric units (currency unknown)`, filter column `order_status`). **Start a comparison**: current = no conditions; proposed = *order_status is one of delivered, shipped*.

Expected (independently computed in the case record): **16,008,872.12 → 15,599,675.73, change −409,196.39**, zero unexplained difference. Point out that the currency is reported as unknown rather than assumed.

## GitHub pull-request check (only with suitable repository access)

The composite action in [`.github/actions/metricguard-ci`](../.github/actions/metricguard-ci/action.yml) compares base and pull-request dbt manifests against governed baselines and posts a Markdown summary to the pull request. Exit codes: 0 clean, 1 material drift, 2 refused, 3 operational failure. See [CLI usage](cli-usage.md). It has not been demonstrated on a live pull request during this closeout; doing so needs a repository where you may add a workflow and a token with `pull-requests: write`.

## Reset

```powershell
.\scripts\mg.ps1 stop
Remove-Item -Recurse -Force data\demo-app   # deletes only the demo workspace data
.\scripts\mg.ps1 start -Seed
```
