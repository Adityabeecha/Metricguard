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

Still open: F, G, H, I, J, K, M, P, S, the two-minute recording, a live GitHub pull-request demo and the analyst observation.

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

## Entry-level analyst observation (to run after checks pass)

Give one analyst who has not seen MetricGuard the running demo and this single instruction: *“Find out why the proposed sales total is lower than the current one, and save your explanation.”* Do not coach. Record:

| Moment | Where they hesitated | Label they misread | Help they asked for | Time |
| --- | --- | --- | --- | --- |
| Sign-in / first screen | | | | |
| Choosing data | | | | |
| Defining calculations | | | | |
| Reading the difference | | | | |
| Saving the explanation | | | | |
| Finding it again later | | | | |

Success: they state, in their own words, that three orders (pending, returned, cancelled) account for the 340.00 INR difference, and they save it without help.

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
