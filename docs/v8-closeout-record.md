# V8 usability and portfolio closeout: completion record

**Date:** 2026-10-17. **Scope:** finishing work before pausing at V8. No new analytical capability; the comparison engine, contracts, evidence formats and authorization rules are unchanged.

**Verification status (updated 2026-10-17, second pass, run at the user's request):** partially verified. Results below; every check not listed as passed is still open.

| Check | Result |
| --- | --- |
| A Backend tests | **Passed:** 547 passed, 45 skipped (PostgreSQL-dependent), 0 failed. Later: the 14 backup/restore/access tests, including the new restore regression test, pass after the restore fix. |
| B Web unit tests | **Passed:** 60/60, including 17 new closeout tests. |
| C Web build | **Passed.** |
| D Compose config | **Passed** (exit 0). |
| E Startup + seed | **Passed with Docker running:** API and supervisor started from `data\demo-app`; both demo cases seeded and matched the independent `Decimal` totals (750.75 → 410.75 INR, −340.00, unexplained 0; 65.00 → 10.00 points, −55.00, unexplained 0). The script printed every step and exited 0 (seeder refusal reason: “Only one SUM over the whole snapshot is supported”). When its output was captured by another process it returned only after a long delay, so prefer running it directly in a terminal. |
| L Guided totals (engine) | Engine totals verified through the seeder; the guided form itself was not clicked through. |
| N Refusal | **Passed:** `SELECT AVG(sales_amount) FROM snapshot` was refused and no definition was stored. Editor retention after refusal was not clicked through. |
| O Discovery and replay | **Passed in the browser:** both case types listed together with type/status filters; the saved case opened on results (“…340.00 INR lower… 3 records account for the difference”, R003/R004/R007); **Replay saved inputs** reported an exact reproduction. |
| Q 375px overflow | **Passed after fixes:** dashboard, start, investigations, saved report, data, metrics, changes and settings all measured `scrollWidth = 375` at a 375×812 viewport. Fixes: mobile containment rules and a positioned table scroller in `closeout.css` (an absolutely positioned screen-reader label was escaping its scroller). |
| R Restored-case replay through Docker | **Passed after a fix:** restoring the default workspace into a fresh installation failed with a duplicate owner-membership error (`services/api/backup.py`); fixed and covered by `tests/test_v8_restore_default_workspace.py`. `scripts/verify_restored_replay.py` then backed up the demo workspace, restored it into an empty `data\restore-check`, started a separate API + supervisor on that copy and replayed both cases: identical totals and identical evidence SHA-256 (`761d2b86…a7`, `36c87d23…65`). Report: `data/evaluation/restored_replay_report.json`. |
| Shared key | **Done:** the four V8 final-acceptance keys (`owner/analyst/reader/outsider@metricguard.internal` in `data\v8_final_acceptance`) are deactivated. The scripts that read `.auth_keys.json` there will get 401 until re-provisioned. |
| README screenshot | **Done:** `docs/images/result-summary.png`, captured from the seeded case. |

Still open: F, G, H, I, J, K, M, P, S, the two-minute recording and a live GitHub pull-request demo. An observed analyst session is not planned: verification with real public data (below) replaces it.

## What changed

| # | Item | Implementation |
| --- | --- | --- |
| 1 | Sign-in and workspace selection | Dedicated signed-out screen with **Connect to workspace**, an explanation of API-key sign-in and where keys come from; distinct screens for loading, access denied (with **Choose a workspace**), service unavailable, and “your connection ended when the page reloaded” (a session marker, never the key). After connecting, the app stays in an allowed workspace, enters a sole workspace automatically, or opens the chooser. The chooser and sidebar list only workspaces the identity can open (`GET /api/v1/projects`, now with the caller’s role). Authorization checks are unchanged. Keys stay in memory only; persistent sign-in was deliberately not added. `scripts/issue_api_key.py` issues and deactivates keys. Files: `AuthScreens.tsx`, `authState.ts`, `App.tsx`, `identity.tsx`, `WorkspaceSwitcher.tsx`, `services/api/app.py`, `store.py`. |
| 2 | Misleading information | Dataset summaries derive key, amount, unit, filter columns and join from the snapshot’s own flat **or relational** contract (`datasetSummary.ts`); no more `order_id`/`order_amount` fallbacks; missing values read “Not declared” and are listed. Dashboard counts renamed: Tracked metrics, Calculation definitions, Saved investigations (with run count), Datasets. Long exact values wrap inside their cards. |
| 3 | Guided first comparison | New **Start a comparison** page (sidebar and dashboard): Choose data → Define calculations → Review difference → Save explanation. The guided form (columns, conditions, LEFT/INNER for two tables) writes SQL that is registered through the same `/flat/definitions` or `/relational/definitions` validation as the **SQL editor** (`comparisonBuilder.ts`), so both produce identical validated structures. Upload questions reworded (“Which column uniquely identifies each row?” etc.) with examples; precision, NULL policy and lineage under **Advanced details**; required confirmations stay visible. |
| 4 | Investigation discovery | One list for single-table, two-table and orders cases plus drafts, with dataset-type and status filters and search by case, metric and dataset (`investigationLibrary.ts`, `InvestigationLibrary.tsx`). Separate **Start new comparison** and **Open**. A saved case opens on a report (`CaseReport.tsx`); **Edit comparison** opens the previous editor. |
| 5 | Results first | Shared `ResultReport.tsx` answers the five questions (two calculations, totals, change, records, uncertainty) in plain language (`resultSummary.ts`), then expandable Supporting records, Join behavior, Missing-value handling, Reconciliation, Original SQL, Data and calculation history. Undefined totals are never shown as zero; explained ≠ correct and lower ≠ loss are stated. |
| 6 | Keyboard, readability, recovery | `useDialog` (focus in, Tab contained, Escape, focus returned) on new and Data Sources/workspace dialogs; a global manager gives every other `aria-modal` dialog the same behavior. Project minimum text size set to **12px** (135 occurrences raised). Tables scroll in their containers. Disabled actions show the reason. Refused calculations keep the entered SQL/form. Save and connection failures show recovery actions. Skip-to-content link and visible focus outlines. |
| 7 | Demonstration package | `scripts/mg.ps1 start|status|stop|restart` (Docker missing/stopped, occupied port and duplicate-start detection; separate `data\demo-app` data directory). `scripts/seed_closeout_demo.py` loads two cases and checks one refusal. New short [README](../README.md), [demo script](demo-script.md); the old README is [technical-readme.md](technical-readme.md). `.gitignore` extended for keys, databases, logs, uploads and machine-specific folders. |
| 8 | Clean demo workspace | The demo uses its own data directory, so acceptance cases, duplicate names and extreme-number fixtures under `data\` are preserved but never shown to a beginner. |

## Checks for you to run

Run from the repository root in PowerShell. “Expected” is what a pass looks like.

| # | Check | Command / action | Expected |
| --- | --- | --- | --- |
| A | Backend tests | `.\.venv\Scripts\python -m pytest -q --basetemp .test-tmp/full` | All pass, including new `tests/test_v8_closeout_access.py` (4 tests). Report any pre-existing failures separately. |
| B | Web unit tests | `npm test --prefix apps/web` | All pass, including new `src/closeout.test.mjs` (17 tests). |
| C | Web build | `npm run build --prefix apps/web` | Succeeds (a >500 kB chunk warning is expected). |
| D | Compose config | `docker compose -f infra/compose.yaml config --quiet` | No output, exit 0. |
| E | Startup from stopped | `.\scripts\mg.ps1 stop` then `.\scripts\mg.ps1 start -Seed` | Prerequisites listed, “API ready”, supervisor started, seeder prints 750.75 → 410.75 INR and 65.00 → 10.00 points. |
| F | Duplicate start / port | Run `.\scripts\mg.ps1 start` again; then stop it and start another server on 8000 and run start | “already running”; then “Port 8000 is already in use by …”. |
| G | Docker stopped | Quit Docker Desktop, `.\scripts\mg.ps1 restart` | Clear message that Docker is not running; nothing half-started. |
| H | Sign-in screens | `.\scripts\mg.ps1 restart -RequireKey`, open the app | Signed-out screen. Wrong key → “That API key was not accepted”. Key from `python -m scripts.issue_api_key --data-dir data\demo-app --name T --email t@x --workspace local-retail:analyst` → enters the workspace without editing the URL. Reload → “Your connection ended when the page reloaded”. |
| I | Access denied | With that key, open `/w/other/dashboard` | “You can’t open ‘other’” with **Choose a workspace** listing only `local-retail`. |
| J | Service unavailable | With the page open, `.\scripts\mg.ps1 stop`, then navigate | “MetricGuard service is unavailable” with **Try again**; after `start`, Try again recovers. |
| K | Dataset summaries | Data Sources → each demo dataset | Single table: row key `row_id`, amount `sales_amount`, unit INR. Two tables: keys `fact_items.fact_id · dim_groups.group_id`, amount `fact_items.amount`, unit points, join shown. No `order_id`/“Unspecified”. |
| L | Guided totals | Start a comparison on the 8-row dataset, proposed *status is completed* | 750.75 → 410.75, change −340.00 INR, 3 records (R003, R004, R007), unexplained 0. Same result via the SQL editor. Two-table: INNER + *segment is north* → 65.00 → 10.00 points. |
| M | NULL and undefined | Proposed *status is completed AND region is blank (NULL)* | Total 60.00 (R008 only). A filter matching only R006, whose amount is blank (*status is completed AND channel is web AND region is south*), gives an undefined total, shown as “Undefined (no values)”, not 0. |
| N | Refusal | SQL editor: `SELECT AVG(sales_amount) FROM snapshot` | “Query not supported” with reason; SQL kept; nothing saved. |
| O | Discovery and replay | Investigations list → filter Two tables → open → **Replay saved inputs** | Both demo cases listed together; report opens on results; replay says it reproduced exactly. **Edit comparison** opens the editor. |
| P | Keyboard | Tab through sign-in, chooser, Add data source and the guided flow; open/close dialogs with Escape | Focus enters each dialog, stays inside on Tab/Shift+Tab, Escape closes, focus returns to the opener. |
| Q | Overflow | Laptop width (1366×768) and narrow window; dashboard with the large-number fixtures in `data\` (point `-DataDir data`) | Long exact numbers wrap inside cards; tables scroll inside their panel; no page-level horizontal scroll. |
| R | Restored-case replay through Docker | Back up a workspace, restore it, then replay a saved case with the supervisor running | Replay succeeds with the same totals and evidence hash. **Still open from before this closeout** (restore-path fix passed targeted checks only). |
| S | Live runner report | `.\.venv\Scripts\python -m scripts.verify_live_runner --output data/evaluation/live_runner_report.json` | As per AGENTS.md; record which paths it covers. |

## Known limitations and remaining gaps

- **Not verified:** the checks still marked open above. In particular, the global dialog manager and the sign-in/access screens have only been type-checked and unit-tested.
- **Two-minute recording:** not produced. The [demo script](demo-script.md) is ready for recording.
- **GitHub Action on a pull request:** not demonstrated (needs repository access and a token with `pull-requests: write`).
- **Leaked test key:** deactivated (see the table above). The new `data\demo-app` directory starts with no keys.
- **Persistent sign-in** and a **visual calculation builder** beyond SUM/filter were intentionally not built (optional per scope).
- Draft investigations still open in the previous editor; the plain-language summary is on the guided flow and saved-case reports, not inside that editor.
- The guided form covers SUM with AND-joined conditions (and one subtraction contract); OR/NOT trees and CASE contracts use the SQL editor.
- External analyst/team pilot gates (V2-06, V3-06) remain open.

## Public-data and adversarial verification (2026-10-17, third pass)

Run against the UCI Bike Sharing dataset (Fanaee-T 2013, DOI 10.24432/C5W894, CC BY 4.0) with `scripts/verify_fresh_public_data.py`. Artifacts are in ignored `data/evaluation/fresh-public-20261017/`. This is public-data engineering verification, not analyst validation.

| Area | Result |
| --- | --- |
| Source check | 17,379 hourly records reconcile exactly to all 731 daily totals before import. |
| Single-table comparison | 3,292,679 → 2,292,410 workday rentals (−1,000,269); all 17,379 row contributions match an independent calculation; saved-case replay reproduces the evidence hash. |
| Two-table comparison | Same workday result through fact + calendar dimension; a duplicate dimension key is rejected. Synthetic NULL/unmatched joins: 11.21 → 2.37 (−8.84), replay hash identical. |
| Subtraction and conditional SUM | Daily subtraction 620,017 → 303,285 (−316,732) and CASE WHEN 2,292,410 → 1,542,338 (−750,072) pass, each with replay. |
| Date boundary, empty and all-NULL SUM | Pass once the harness used explicit `DATE` literals; empty or all-NULL totals are undefined, not 0. |
| Exact decimals | Large positive and negative values that nearly cancel reconcile exactly. |
| Refusals and malformed input | AVG, unregistered column, GROUP BY, arithmetic and multiple statements are refused; duplicate key, NULL key, over-scale decimal, NaN and missing measure are rejected at import. |
| Browser | Guided flow completes on the real dataset (choose data → workday filter → run → review → save); invalid-key recovery and Add-data-source dialog keyboard behaviour pass. |
| Backend regression | 548 passed, 45 skipped. Web: 60/60. |
| Live Docker runner (`scripts.verify_live_runner`) | **Passed:** CSV, Parquet and mapped imports (−6,000,000.00, residual 0), mapped-case replay, invalid Parquet refused. Isolation observed: no network, read-only root, no capabilities, UID 10001, 1 GiB memory, 64 PIDs. Not covered live: run-deadline timeout, out-of-memory kill, container crash mid-run, Docker stopping during a job. |

**Fixed from this pass:** the saved report showed only the first 1,000 of 17,379 records, with no next-page control, and pointed to a "full report" export that contains only 100 rows. Supporting records now page through every record (Previous/Next, 100 per page; checked: records 1–100 → 101–200 of 17,379). Large datasets load one page up front instead of ten.

**Known limitation:** each evidence page re-verifies the whole artifact (the V8.3 integrity guarantee), so on 17,379 rows a page takes about 4 seconds. The summary sentence for large datasets counts only the records loaded so far ("At least 10 of the first 100 of 17,379 records…").

## Improvement pass (2026-10-17, fourth pass)

| Area | Change | Verified |
| --- | --- | --- |
| Key issuing | `POST /api/v1/users` now requires a verified owner of at least one workspace (it previously accepted anyone who could reach the server). | `tests/test_v8_improvements.py` |
| Workspace listing | `GET /api/v1/workspaces` returns the same filtered list as `/projects`; workspaces the caller cannot open are never listed. | test |
| Result wording | “Unknown currency remains unknown” no longer appears for datasets with a declared unit; it is reworded in the report (engine output is unchanged, so saved results and replays are unaffected). | web tests, browser |
| Drafts vs cases | `GET /api/v1/investigations/{id}` declares `record_type` (`investigation` or `draft`); the report hands drafts to the guided flow. | test, browser |
| Restore | Backups are unpacked with Python's `data` filter (no links, devices or absolute paths). | backup/restore tests |
| Runner failure modes | New live check `scripts/verify_runner_failure_modes.py`: a run past its deadline is killed (INGEST_TIMEOUT), out-of-memory and crashes fail cleanly (INGEST_FAILED), the container has no network, and no container is left behind. **Passed live.** Docker stopping during a job is still not exercised. | live Docker |
| Evidence speed | First read of a run's evidence is fully verified as before; later pages are served from a verified index, re-checking each row's SHA-256, and any change to the file forces full re-verification. | `tests/test_v8_evidence_cache.py` (including tampering that keeps size and timestamp) |
| Change summary | `GET /comparisons/{id}/evidence-summary`: exact changed-record and blank-amount counts and the 20 largest changes, across all rows. | test, browser |
| Full export | `GET /comparisons/{id}/evidence.csv` streams every record after verification (spreadsheet formulas in keys are neutralised). | test, browser |
| Record table | Supporting records: find by key, “only records that changed”, “largest change first”, previous/next and jump to page. Filtering and sorting run on the server across all rows. | browser |
| Numbers | Exact values show thousands separators (1,000,269) without losing digits; the minus sign stays copy-safe. | web tests |
| Guided flow replaces the old editors | **Edit comparison**, draft **Continue**, comparison runs opened from the dashboard, Metrics or Changes, and the “new investigation” links all open the guided flow, prefilled. Editing a saved case creates a new case; the original stays unchanged. Running a comparison saves a draft that appears in the list until it is saved. The old single-table and two-table editors (`InvestigationsView.tsx`, `RelationalInvestigations.tsx`) were removed. | browser: edit → change → run (−140.00) → draft listed → continue → save → draft removed |
| Two-table upload | Built into **Start a comparison** with plain questions, visible confirmations and advanced settings tucked away. | browser: 5-row upload → 65.00 → 10.00 points, “3 records account for the difference” |
| OR conditions | “Any condition (OR)” in the guided form for datasets set up with compound filters; other datasets explain why only AND is available. | web tests |
| Saved calculations | Each side can start from a saved calculation; an unchanged one is reused instead of registered again. | browser |
| Staying signed in | “Keep me signed in on this computer for 8 hours”: the server issues an HttpOnly, SameSite=Strict session cookie. The key is never stored in the browser; sessions expire, end on sign-out, and end when the key is deactivated. Cookie-authenticated changes require the web app's request header, which blocks forged requests from other local pages. | tests; browser: sign in → reload stays signed in → sign out → 401 |
| Regression | Backend: 556 passed, 45 skipped (PostgreSQL-dependent), 0 failed. Web: 64/64. Frontend build passes. | full suites |

## End-to-end check and fixes (2026-10-18)

A full end-to-end check on a clean, separate instance (fresh data folder, seeded demo, key-required second API) found 12 issues. All are fixed, and each fix was rechecked in the browser on a fresh instance.

| # | Issue found | Fix | Recheck |
| --- | --- | --- | --- |
| 1 | Upload sent the chosen row key or amount as a filter column too, giving a confusing “Column names must be distinct” rejection. | Key, amount and subtraction columns are removed from the filter list when chosen and never sent as filters. | `ref_code` filter → chosen as key → validates |
| 2 | Each “Check and compare” registered new “Current/Proposed calculation” definitions, even for unchanged or refused attempts. | Identical validated SQL on the same dataset is reused; new definitions get descriptive names (e.g. “amount over all rows where state is paid”). | re-run adds 0 definitions |
| 3 | Dashboard marked comparisons with a saved case as “Unreviewed / needs review”; replays and repeats flooded the queue. | Saved, replayed and superseded runs are excluded; unsaved differences show “Not yet explained · Review and save”. | saved case not flagged; unsaved −40.00 run flagged |
| 4 | When the intended key repeated, the dialog silently suggested the amount column as key and no amount. | Every row is scanned for repeated or blank values; amount-like columns are never suggested as keys; `_ref/_no/_code` names count as key names. | `order_ref` suggested, amount `net_value` |
| 5 | No warning that the chosen key repeats. | Warning under the key (“repeats "A1"”) and Validate is blocked with the reason. | shown and blocked |
| 6 | Generic “check precision, scale” suggestion for unrelated contract errors. | Suggestion matches the message (one role per column, blank handling, decimals); duplicate/blank key errors from the runner are mapped. | unit tests |
| 7 | Refusal guidance listed filter operators for an AVG refusal. | Guidance matches the reason (one SUM; single statement; no GROUP BY; unknown column). | AVG shows the SUM example |
| 8 | Two-table datasets labelled “CSV extract”. | Labelled “Two tables” / “Two connected CSV tables (fact + lookup)”. | Data Sources |
| 9 | `mg.ps1` suggested the same busy port. | Suggests the next free port. | “start -Port 8001” |
| 10 | `mg.ps1 status -Port N` showed another instance's details. | Status reports the recorded instance on its own port and says when it differs. | checked |
| 11 | Sign-in help hard-coded `data\demo-app`; default workspace labelled “Sample / Demo workspace”. | Help names the data folder generically; default workspace labelled “Default”. | checked |
| 12 | Outage screen took about 10 seconds after connecting. | A network failure while connecting shows the outage screen at once. | about 2 seconds |

Withdrawn during the check: `mg.ps1 stop` does stop the server (killing the venv launcher also ends its child process). Tests: web 67/67 with new tests for inference, key warnings and error guidance; frontend build passes. No backend code changed in this round (the last full backend run: 556 passed, 45 skipped).

## Analyst feedback pass (2026-10-18)

Built from the senior-analyst evaluation. Stress results are from `scripts/stress_test.py` at 100,000 rows on a fresh instance (report: `data/evaluation/stress-v2/stress_report.json`): **24/24 checks pass**, every total exact against an independent Decimal calculation.

| Item | What changed | Verified |
| --- | --- | --- |
| Breakdown of the difference | “Where the difference comes from”: split the change by any filter column, exact sums and share, largest first. Single tables use a new record-context file written by the runner and validated line-by-line by the supervisor; evidence hashes and replay identity are unchanged. Two-table runs use the values already in their evidence. Runs made before this change show “Replay it to enable breakdowns”. | 100k by status exact; two-table by segment exact; browser |
| Columns beside records | “Show columns” (up to 3) in Supporting records. | 100k context matches source; browser |
| COUNT(*) | Single-table datasets can compare numbers of records (guided “What do you want to compare?” or SQL). Separate IR aggregate, so existing SUM IRs and hashes are unchanged; COUNT of nothing is 0; COUNT and SUM cannot be mixed; COUNT(DISTINCT)/COUNT(column)/COUNT(1) are refused. | unit tests; 100k COUNT exact; browser 8 → 5 records |
| COUNT(DISTINCT) and ratios | Not built. They are not sums of per-record contributions, so “record changes add up exactly to the total change” cannot hold without a new evidence model. | — |
| Warehouse source | “Use a PostgreSQL table” in Start a comparison: read-only, one REPEATABLE READ transaction, sample first then full table. The collector now writes the same typed schema as uploads (dates, booleans, timestamps), records the real contract version, validates typed and nullable columns, and rejects subtraction/CASE contracts. The connection string is used per request and stored without its password. Row limit stays 100,000. | 2 new tests against a real PostgreSQL 16; existing 38 collector tests pass |
| Speed | Supervisor verifies evidence once (the copy is checked by SHA-256); two-table join pages cached after one full check; runner image precompiled; job folders can live outside OneDrive (`METRICGUARD_JOB_DIR`, set by `mg.ps1`); supervisor polls every 0.5 s. | 100k compare 31.7 → 29.0 s; 5-job burst 153 → 124 s; two-table compare 61 → 49 s; join pages 19 s each → 0.03–0.06 s after the first; first evidence read 5.7 s → 0.6 s when the breakdown has already verified it |

Full backend suite with a live PostgreSQL: 609 passed, 0 skipped. Web: 67/67.

Remaining speed limit: about 25 s of each 100k comparison is Docker Desktop file sharing and container overhead on Windows, not calculation (in-process the same comparison takes about 5 s).
