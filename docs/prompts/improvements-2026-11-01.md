# GPT prompts: small improvements to existing features (2026-11-01)

Split agreed on 2026-11-01: Claude does the heavy or risky items itself (engine, auth, security, hosting). GPT does the small, simple ones below. Item numbers refer to the 55-item improvement list.

There are three prompts. **Run them one at a time, in order (G1 → G2 → G3)**, because some files are shared. Each prompt starts with the same shared rules block, so each one can be pasted on its own.

Claude's server work, done at the same time, adds two fields these prompts use:
- `guest_storage_bytes` in `GET /api/v1/auth/identity`
- `storage_quota_bytes` and `guest_expires_at` in `GET /api/v1/usage`

If a field isn't there yet when you run a prompt, the prompt says what to do.

---

## Prompt G1: Wording and copy (items 1–8)

```
You are working in the MetricGuard repository (Windows, PowerShell, Python venv at .venv\, web app in apps/web, React + Vite + TypeScript). Read docs/HANDOFF.md, AGENTS.md and DESIGN.md first.

SHARED RULES
- Small, simple changes to existing features only. No new pages, routes or features beyond what this prompt lists.
- Don't touch the calculation engine (packages/metricguard_core), hashes, contracts, the SQL parser or auth logic. If an item seems to need that, skip it and say so in your report.
- Match the surrounding code style and comment density.
- Do NOT commit or push. Do NOT add any AI attribution (no Co-Authored-By or "Generated with" lines).
- When done, run: `.venv\Scripts\python -m pytest -q --basetemp .test-tmp/full`, `npm test --prefix apps/web` and `npm run build --prefix apps/web`. Report the real results; never claim a check passed if you did not run it. Update tests whose expected text you changed.
- Finish with: the files changed, what changed per item number, test results, and anything you skipped or could not verify.

TASK G1: make the wording correct for hosted users and consistent everywhere.

1. Local-only instructions shown to hosted users. "Start it with scripts\mg.ps1 start" (and similar) appears in apps/web/src/views/StartComparison.tsx, views/SnapshotChange.tsx, views/CaseReport.tsx and AuthScreens.tsx. Add one helper, e.g. apps/web/src/hostedCopy.ts with `isLocalHost(hostname)` and `engineDownMessage(hostname)`, plus a node test (apps/web/src/hostedCopy.test.mjs, registered in the package.json "test" script). On any host other than localhost/127.0.0.1/[::1], show: "The comparison engine is busy or restarting. Try again in a minute; if it keeps happening, send Feedback." Keep the mg.ps1 wording locally. Use the helper at every place that mentions mg.ps1 or "Check that the comparison engine is running".
2. Units. Use "MB" in all user-facing text instead of "MiB" (services/api/app.py messages such as "Dataset must contain 1 byte to 25 MiB" and "Upload exceeds the … MiB limit", and "Source exceeds 512 MiB" in the web app). Don't change any byte arithmetic.
3. Sentence case. Change Title Case UI strings to sentence case: "Selection Required", "Distinct Fields Required", and table headers in views/MetricsView.tsx and views/ChangesView.tsx such as "Bound At", "Bound Version", "Observed Date", "Observed Timestamp", "Change Kind", "Resource Unique ID", "Importance Tiers", "Declared Metric Coverage", "Secondary Detail", "Non-Metric", "Explicit Only". Search for others of the same kind.
4. Page names. The sidebar says "Datasets"; replace "Data Sources" in user-facing text with "Datasets". Rename "Investigate in studio" to "Open in New comparison".
5. Metrics page jargon (views/MetricsView.tsx). Keep internal values (active/candidate, critical/important/standard). Change only the visible text:
   - "Bind a query definition as a candidate or active baseline." → "Link a calculation to this metric, either to try it out or as the approved one."
   - "Active (Immediately replaces active baseline)" → "Approved: replaces the current approved calculation now"
   - "Candidate (Recommended for verification)" → "Trial: compare it with the approved one first (recommended)"
   - "None (Leave unbound for now)" → "Don't link a calculation yet"
   - "Active Implementation" → "Approved calculation"; "Candidate evaluation" → "Trial result"; "Add implementation" → "Link a calculation"; "Import new SQL query" → "Add SQL"
   - importance options → "Critical: shown first, red badge when it moves", "Important: shown prominently", "Standard: normal"
6. Changes page (views/ChangesView.tsx). Put raw payloads, exit codes and diagnostics ("CI Exit Code 2 (Refusal Precedence)", "Raw Diagnostic:", "Diagnostics payload:", "Diagnostics / Details", "Structural Signals", "Support State:") inside a collapsed <details> labelled "Technical details". Make the visible headline plain, e.g. "Quantitative Analysis Refused (Amber Warning)" → "Refused: this change can't be measured exactly", with the reason under "Why it was refused".
7. Access error (apps/web/src/identity.tsx, describeAuthError). Browser-session text: "You don't have access to this workspace, or your role can't do this. Ask the workspace owner, or switch workspace." API-key text: "This API key doesn't have access to this workspace, or its role can't do this." Keep the server's message in parentheses only for the API-key variant.
8. Leftover technical words in normal screens. Outside Advanced/Technical sections, replace "minuend" → "amount to start from", "subtrahend" → "amount to subtract", and "outside the supported grammar" → "uses SQL MetricGuard can't check exactly yet". Leave hashes and Advanced sections alone.

Finally, update docs/user-guide.md wherever it quotes a label you renamed.
```

---

## Prompt G2: Guest, sign-in, upload and comparison screens (items 11, 13, 14, 15, 16, 19, 21, 23–27, 30, 31, 35, 36)

```
You are working in the MetricGuard repository (Windows, PowerShell, Python venv at .venv\, web app in apps/web, React + Vite + TypeScript). Read docs/HANDOFF.md, AGENTS.md and DESIGN.md first.

SHARED RULES
- Small, simple changes to existing features only. No new pages, routes or features beyond what this prompt lists.
- Don't touch the calculation engine (packages/metricguard_core), hashes, contracts, the SQL parser or auth logic. If an item seems to need that, skip it and say so in your report.
- Match the surrounding code style and comment density.
- Do NOT commit or push. Do NOT add any AI attribution (no Co-Authored-By or "Generated with" lines).
- When done, run: `.venv\Scripts\python -m pytest -q --basetemp .test-tmp/full`, `npm test --prefix apps/web` and `npm run build --prefix apps/web`. Report the real results; never claim a check passed if you did not run it. Update tests whose expected text you changed.
- Finish with: the files changed, what changed per item number, test results, and anything you skipped or could not verify.

CONTEXT: The guest notice is GuestNotice in apps/web/src/identity.tsx, with the text from guestNotice() in apps/web/src/authFlow.ts (tests in authFlow.test.mjs). GET /api/v1/auth/identity returns guest_expires_at (Unix seconds) and guest_storage_bytes (null for full accounts). GET /api/v1/usage returns storage_bytes, storage_quota_bytes, guest_expires_at, max_runs_per_day, runs_last_24h, max_rows and max_upload_mb. If one of these fields is missing, treat it as null and say so in your report; don't add it on the server yourself.

TASK G2:
11. Final-hour warning. Change guestNotice(expiresAt, storageBytes, nowMs) to take the storage size (formatted as "25 MB"; fall back to "25 MB" when null). When less than 60 minutes remain, return the warning: "Less than an hour left. Open a case and use Records (CSV) to keep a copy." The notice uses a warning style (add .alert.guest-notice.urgent in style.css with existing tokens). Add tests for the threshold.
13. Guest terms before sign-up. Add `guest: {hours: int, storage_mb: int} | null` to GET /api/v1/auth/providers in services/api/oauth.py, read from the env variables used in services/api/guests.py (METRICGUARD_GUEST_HOURS default 8, METRICGUARD_GUEST_STORAGE_MB default 25). It's null when guests.full_access_emails() is empty. Import those helpers; don't change guests.py logic. On the sign-up page (SignInScreen.tsx), when guest is not null, show: "Free beta: new accounts are guest accounts. Everything is erased {hours} hours after sign-up, with {storage_mb} MB of storage." Add one sentence each about guest erasure to the Privacy and Terms text in PublicPages.tsx. Add a test in tests/test_oauth.py for the providers field.
14. Permanent account request. In the guest notice, add the button "Need a permanent account?" that opens the existing FeedbackDialog with the message prefilled: "Please give this account full access." Add an optional `initialMessage` prop to FeedbackDialog.
15. Unverified email message. In services/api/oauth.py, verified_profile raises ValueError('unverified') for unverified emails. In the callback, set error = 'unverified' for exactly that case (keep 'failed' for everything else, and never put emails or provider text in the URL). In authFlow.ts oauthErrorMessage, map 'unverified' to "Your Google or GitHub email isn't verified. Verify it in that account, or try the other provider." Add tests.
16. Usage bars (views/UsagePanel.tsx). Show "used of limit" with a simple bar for storage (when storage_quota_bytes is set) and for comparisons in the last 24 hours (when max_runs_per_day is set). For guests (guest_expires_at set), add "Erased in 6 h 20 min". Use existing tokens only.
19. Upload progress. For dataset uploads (Add dataset, newer load, two connected tables), show the percentage uploaded. Add an upload helper in api.ts using XMLHttpRequest with upload.onprogress, which sends the same headers and credentials and throws the same ApiError shapes as the existing request helper. Then continue with the existing checking steps.
21. Currency symbols. Where the dataset preview or checks show an amount column whose sample values contain ₹ $ € £ , or a trailing currency code, show the hint "Remove currency symbols and thousands separators: 1,250.00 → 1250.00". This is a hint in the web app only; don't change parsing.
23. Datasets list. Show rows, number of loads and the date of the latest comparison for each dataset, using only data the bootstrap response already includes. Skip whatever isn't there and list it in your report.
24. Delete refused. In services/api/item_management.py, when deletion is refused because a case, metric transition or report uses the item, include up to 3 of their names in the message: "Used by the case 'October revenue'. Delete or change that first." Update tests/test_item_management.py.
25. Newer load mismatch. When "Add newer load" is refused because the columns don't match, list the missing columns, the extra columns and the columns whose type changed. Find where that refusal is raised in services/api; if the information is already available there, add it to the message; if not, skip and report.
26. Excel note. On the file preview step for .xlsx files, show: "Only the first worksheet is read. If your data is on another sheet, move it to the first sheet or save that sheet as CSV." Don't add a sheet picker.
27. Two-load limits shown upfront. In views/SnapshotChange.tsx, options that two-load or two-source comparisons don't support (distinct counts, ratios, subtraction, CASE, connected tables) show as disabled with the note "Not available when comparing two loads yet; use Two calculations on the same data". Check which options the component offers today and disable only the unsupported ones.
30. Runs left today. Next to "Check and compare" and "Compare the two loads/sources", show "N of 30 comparisons left today" from GET /api/v1/usage (hide it when max_runs_per_day is null). When a run is queued and waiting, change the waiting text to "Waiting for your other comparison to finish".
31. Keep inputs when going back. In StartComparison.tsx, going back between "Choose data" and "Define calculations" with the same dataset must keep the calculations. Changing to a different dataset may reset them, as now. Add a unit test where the logic lives (comparisonSelection.ts or comparisonBuilder.ts).
35. Save-as hidden. In views/WarehouseSource.tsx, when the user is an owner and saving_available is false, show "Saving connections isn't enabled on this server." where the Save as field would be.
36. Date picker. In WarehouseSource.tsx, when the cutoff column type is date use <input type="date">; for timestamp use <input type="datetime-local">. Send the same value format as today: check what the API expects and convert if needed.

Update docs/user-guide.md sections 3, 6, 7, 14 and 16 to match.
```

---

## Prompt G3: Results, cases, warehouse messages and the waking-up note (items 32, 39, 41, 42, 44, 45, 46, 47, 50)

```
You are working in the MetricGuard repository (Windows, PowerShell, Python venv at .venv\, web app in apps/web, React + Vite + TypeScript). Read docs/HANDOFF.md, AGENTS.md and DESIGN.md first.

SHARED RULES
- Small, simple changes to existing features only. No new pages, routes or features beyond what this prompt lists.
- Don't touch the calculation engine (packages/metricguard_core), hashes, contracts, the SQL parser or auth logic. If an item seems to need that, skip it and say so in your report.
- Match the surrounding code style and comment density.
- Do NOT commit or push. Do NOT add any AI attribution (no Co-Authored-By or "Generated with" lines).
- When done, run: `.venv\Scripts\python -m pytest -q --basetemp .test-tmp/full`, `npm test --prefix apps/web` and `npm run build --prefix apps/web`. Report the real results; never claim a check passed if you did not run it. Update tests whose expected text you changed.
- Finish with: the files changed, what changed per item number, test results, and anything you skipped or could not verify.

TASK G3:
32. Warehouse error messages. In services/supervisor/warehouse_inspect.py and the collector/extract paths, plus views/WarehouseSource.tsx, map common failures to plain text with the next step. Never echo passwords, tokens or full connection strings:
   - login refused → "The database refused the login. Check the user and password or token."
   - host unreachable / timeout → "Couldn't reach the database. It must accept connections from the internet."
   - SSL required → "The database requires SSL. Add sslmode=require to the connection string."
   - private address blocked → "This address is on a private network, which the hosted site can't reach."
   - table/schema not found → "Table not found. Check the schema and table name."
   - permission denied → "The database user can't read this table. Grant it SELECT."
   Add tests with mocked exceptions (see tests/test_warehouse_connections.py for the mocking style).
39. Concentration sentence. In views/ResultReport.tsx's Concentration section, add one plain sentence per item using values already returned by /evidence-concentration, e.g. "region = South holds 62% of the difference but only 18% of the records." Don't compute new statistics; if the record share isn't in the response, use only the difference share.
41. Detail expiry shown in advance. Find the retention setting behind "Detailed records expired — rerun to see them" (search services/ for evidence_expired). If a fixed period exists, show "Detailed records kept until {date}" on the result and the case page. If not, show "Detailed records are kept while storage allows; you can always rerun the case."
42. Cases search and sort. In views/InvestigationLibrary.tsx, add a search box (case name, conclusion, dataset name) and a sort menu (last updated, name, size of the difference), working in the browser over the list the page already loads. Put the filter/sort logic in a pure function with a node test.
44. Print layout. Add @media print styles for the case page and the public shared case page (PublicCasePage): hide the sidebar, topbar, buttons and tabs; show the answer, the reconciliation table, the conclusion and the visible records; add the page URL and print date in a footer via CSS or a print-only element.
45. Share details. On the public shared case page, add "Read-only copy shared from MetricGuard · link expires {date}" when the expiry date is in the response. In CaseShareDialog.tsx, show the created and expiry dates for each link if the list response includes them; otherwise skip and report.
46. Copy summary link. In breakdownSelection.ts (the copy-summary helper), add an optional case URL and append it when the result belongs to a saved case. Update its test.
47. Category tooltips. In views/evidence/categories.ts, add a one-line description per category label for each mode (two calculations, two loads, two sources), shown as title text or an info tooltip wherever the label appears in ResultReport. Use the same wording as the glossary in docs/user-guide.md §8 and §17. Add a test that every category has a description.
50. Waking-up note. In apps/web/src/api.ts, when a GET request takes longer than 3 seconds, show a small global note "Waking up the server…" until it finishes (the free database sleeps when idle). Use one small event or store that App.tsx renders; no retries, and nothing for POSTs. Add a unit test for the timing logic if you can do it without the browser.

Update docs/user-guide.md sections 8–10 to match.
```

---

## What the owner checks after each prompt

- GPT's report gives the real results of `pytest`, `npm test` and `npm run build`.
- `git diff --stat` touches only the files the prompt is about, with no `packages/metricguard_core` changes.
- Nothing is committed, and there are no Co-Authored-By lines.
- Then send me the report, and I'll review the diff before you commit.
