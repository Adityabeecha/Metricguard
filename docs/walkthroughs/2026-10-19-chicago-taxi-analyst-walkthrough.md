# Analyst walkthrough: Chicago taxi trips (2026-10-19)

A first-time analyst used MetricGuard end to end on public data: create a workspace, add data
(one table, two loads of one table, and two connected tables), ask three real questions, save
cases and replay one. Driven with Playwright at 1366×768 against http://127.0.0.1:8000.

## Data

City of Chicago, *Taxi Trips (2024-)*, dataset `ajtu-isnz`, trips starting in January 2024.
Columns: `trip_id` (unique), `trip_start_timestamp`, `fare`, `tips`, `trip_total` (USD),
`payment_type` (7 values), `company` (35 values).

| File | Rows | How it was made |
|---|---|---|
| `chicago_taxi_2024_01.csv` (50 MB) | 425,203 | Full month from the city API |
| `chicago_taxi_50k.csv` | 50,000 | First 50,000 trips by `trip_id` |
| `chicago_taxi_extract_jan25.csv` | 38,280 | The 50k sample as of 25 Jan: later trips removed, and 435 card trips marked `Unknown` (not yet settled) |
| `companies.csv` | 33 companies | Lookup: `fleet_type`, `dispatch`. Two small companies are deliberately missing (10 trips) |

## Questions asked, and what the product answered

| Question | Answer | Correct? | Time |
|---|---|---|---|
| How much January revenue does the dashboard lose by excluding Unknown / No Charge / Dispute payments? (425k rows) | −559,447.33 USD; 98.7% from Unknown | Yes (402,402 records kept = 425,203 − 22,402 − 269 − 130) | Run 19 s, records 1 s, replay 11 s ("seal intact") |
| Why is card revenue higher in the final delivery than in the 25 Jan extract? (two loads) | +168,727.64 USD: 11,720 late trips, 435 card trips "now match the filter" | Yes (435 is exactly the number injected) | Under 20 s |
| Does joining trips to the company lookup drop anything? (two tables) | −179.27 USD from 10 trips | Yes (the 10 trips of the 2 missing companies) | Run 26 s, records ~12 s |

All three numbers were right. The friction is getting data in, two-load comparisons, and
explaining the result.

## What worked well

- Value suggestions: the first condition started on `payment_type` with all 7 values as buttons with counts. Clicking builds an "is none of" list.
- Typing `credit card` showed "does not occur in payment_type; did you mean “Credit Card”?" and "These conditions include none of the 425,203 records".
- "Check and compare" stopped before the run with "No records match … Run anyway".
- The live count ("Includes 402,402 of 425,203 records") was exact.
- The answer-first result and the breakdown by `payment_type` answered question 1 at a glance.
- The case number stayed MG-6E8B from review through saving. Replay matched byte for byte.
- Datasets are listed newest first. Dismiss and Restore under "Needs a conclusion" persisted across a reload.
- The single-table upload guessed the row key, the amount and the units correctly.

## Fixed during this walkthrough

1. **Match-count failed (HTTP 500) when two look-ups ran at once.** This was a bug in the new code. DuckDB shares one instance per file and the hardened connection locks its configuration, so a second concurrent connection failed. Look-ups now run one at a time (`flat_profile.py`), with a regression test that fails without the fix.
2. **The Cases table still clipped at 1366px.** A shared rule (`.table-scroll table { min-width: max-content }`) overrode the earlier fix. The Cases table is now exempt; Change, Status and Updated are visible.
3. **The shorter calculation card's buttons stretched tall** when the other card was taller. Fixed with `align-content: start`.

## Open problems, most serious first

### 1. A large upload reports failure while it succeeds (blocking)
Uploading the 50 MB, 425k-row file showed **"Couldn’t validate snapshot. Snapshot validation timed out after 30 seconds."** The server finished in 48 s and the snapshot was valid. The sidebar still said Datasets 0. "Try again" re-sent all 50 MB and only then said "Snapshot ready" (about 90 s).
*Where:* `apps/web/src/views/DataSourcesView.tsx:500`, a fixed 30 s client timeout.
*Fix:* poll the import until it is validated or failed. Show elapsed time and say the import continues in the background.

### 2. Two loads of one dataset cannot be paired unless they have identical names (blocking for the two-loads feature)
The 25 Jan extract and the final delivery used the same row key, columns, amounts and units, but different names. "Compare two data loads" said *"No dataset has two loads yet. Upload a second file under the same dataset definition."* The dataset definitions differed only in `name`: the contract takes the snapshot name, and the name is part of the hash that pairs loads. There is no "add a new load to this dataset" action.
The workaround (re-uploading under the same name) makes the two loads indistinguishable: same name, and a date without a time ("(10/19/2026) · 38,280 records").
*Fix:* give "Add a new load" on a dataset, which reuses its definition and lets the load have its own label. Or keep the name out of the pairing hash. Show the load time in the pickers.

### 3. The two-loads result speaks the wrong language
- The answer says "The proposed calculation is 168,727.64 USD higher than the current calculation". It should say the later load is higher than the earlier load. The columns also say Current/Proposed.
- The summary table shows raw numbers (`152347.15`, `16380.49`) without digit grouping or a unit.
- Three different counts appear: 11,720 + 435 in the table, "4,599 records account for the difference", and "All records (12,155)".
- The calculation is SQL-only, with no guided form and no value suggestions.
- There is no save action in the panel. The run appears under "Needs a conclusion" titled with its raw SQL on both sides of "→", and the suggested case name is the SQL.

### 4. Two connected tables: the analyst can't see what didn't match
- Trips with no matching company appear as **"blank"** in the `fleet_type` breakdown. That looks the same as a company whose fleet type is empty. It should read "No matching company".
- The link column (`company`) is not offered as a filter or split column. So the analyst cannot list *which* companies are missing from the lookup, which was the question.
- The record list says only "Included → Excluded", not "no matching company".
- Setup guesses nothing: row key, amount and link column must all be picked by hand, even though `company`↔`company` is obvious. The single-table flow does guess.
- Value suggestions and record counts are not available for two-table datasets.

### 5. Breakdown by a timestamp is useless
Splitting by `trip_start_timestamp` gives one row per raw 15-minute value, each about 0.1% of the change. Dates and timestamps need day, week or month grouping.

### 6. Record counts and copy in the result
- The answer says "22,779 records account for the difference", but the panel says "Listed records (22,801)". The 22 extra are blank-amount trips, and nothing explains the gap.
- "Use “Supporting records” to see them all" names a control that doesn't exist; the panel is "Listed records".
- On large runs, the breakdown's Current and Proposed columns are "—" on every row and could be hidden.

### 7. Getting started
- An empty workspace's only call to action is "New comparison". The next screen then says there is no data. Offer "Add data" first.
- "Upload one table" leaves the comparison for the Datasets page and doesn't return afterwards.
- In "Several amount columns", the "More amount columns" list offers text and timestamp columns (`payment_type`, `company`, `trip_start_timestamp`) as amounts.
- The type-check copy says types are suggested "from all CSV rows" and then "17,840 rows checked from the beginning of the file". The file size reads "48835 KB".

### 8. Small items
- A dismissed draft still appears as a Draft in "All cases". That's probably right, but it should be said.
- "Needs a conclusion" shows `-179.27` with a hyphen, while the table uses a true minus sign.

## Not covered
Excel and Parquet uploads, warehouse sources, distinct counts, ratios on the 425k file, phone width, and dark mode.

## Follow-up: fixes made after this walkthrough (not yet re-tested)

| Problem | Change |
|---|---|
| 1. Large upload reports a 30 s timeout | No fixed limit. The import is polled until validated or failed, with elapsed time. After 10 s, "Close and keep importing" lets the analyst leave; the result arrives as a page notice. The size is shown in MB. |
| 2. Two loads pair only with identical names | A dataset has an **Add a newer load** action that reuses its definition. A new upload whose settings match an existing definition offers "Add this file as a new load of …" (ticked by default). Load pickers show when each load was loaded. |
| 3. Two-loads result wording and saving | Answer, totals, record tables and breakdown speak of the earlier and later load. The summary table is formatted with unit and minus sign. The count of records that differ without moving the total is explained. A guided form with value suggestions sits next to SQL, and calculations get descriptive names. The comparison can be saved as a case in place. Lists show "· two loads" and both load names. |
| 4. Two tables | A missing lookup row shows as "No matching … row", not "blank". The link key can be used to split the change (`link.<lookup>`). Excluded records say "no matching lookup row". The row key, amount, link column, lookup key and lookup filters are pre-filled. |
| 5. Timestamp breakdown | Timestamps are grouped by calendar day. |
| 6. Counts and copy | "N more records change inclusion without changing the total" explains the listed-vs-changed gap. The footnote names the real panel. Empty Current/Proposed columns are hidden on large runs. |
| 7. Getting started | An empty workspace offers **Add data** first. Uploading from New comparison returns there with the new dataset selected. Only numeric columns are offered as extra amounts. The type-suggestion copy is corrected. |
| 8. Minus sign | "Needs a conclusion" uses a true minus sign. |

Still open: value suggestions and record counts for two-table datasets; per-day grouping only (no week or month choice).
