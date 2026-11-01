# GPT prompts, round 2 (2026-11-01): the simple half

Claude is doing the heavy half itself (items 3–5, 7–9, 11, 13–16, 19–23, 28–30, 32, 33). Item numbers refer to the 40-item list in the chat. Item 31 (the restore drill on Neon) is the owner's job, using Claude's instructions.

Run **I1, then I2**, one after the other. Paste each prompt on its own.

---

## Prompt I1: Small screen and wording fixes (items 1, 2, 6, 10, 12, 17, 18, 25, 26, 27, 34, 36, 37)

```
You are working in the MetricGuard repository (Windows, PowerShell, Python venv at .venv\, web app in apps/web, React + Vite + TypeScript). Read docs/HANDOFF.md, AGENTS.md and DESIGN.md first.

SHARED RULES
- Small, simple changes to existing features only. No new pages, routes or features beyond what this prompt lists.
- Don't touch the calculation engine's logic (packages/metricguard_core), hashes, contracts, the SQL parser or auth logic. Changing the wording of a message string is allowed; changing behaviour is not. If an item seems to need more, skip it and say so.
- Claude is changing these files at the same time, so do NOT edit them: services/api/store.py, services/api/guests.py, services/api/app.py, services/api/evidence_cache.py, services/api/evidence_concentration.py, apps/web/src/views/ResultReport.tsx, apps/web/src/views/CaseReport.tsx, apps/web/src/views/MembersPanel.tsx, apps/web/src/views/evidence/Ledger.tsx, apps/web/src/api.ts. If an item needs one of them, skip that part and report it.
- Match the surrounding code style and comment density.
- Do NOT commit or push. Do NOT add any AI attribution (no Co-Authored-By or "Generated with" lines).
- When done, run: `.venv\Scripts\python -m pytest -q --basetemp .test-tmp/full`, `npm test --prefix apps/web` and `npm run build --prefix apps/web`. Report the real results; never claim a check passed if you did not run it.
- Finish with: the files changed, what changed per item number, test results, and anything you skipped or could not verify.

TASK I1:
1. Sign-in page while signed in. In apps/web/src/SignInScreen.tsx, call api.identity() on mount. If it returns a verified identity with home_workspace_id, replace the URL with homeRedirect(identity) from router.ts (the same helper PublicPages uses), unless the URL has an ?error= parameter (then show the message as now). Add a node test for any pure helper you add.
2. Guide link. In apps/web/src/App.tsx the sidebar "Guide" link has target="_blank". Use target="metricguard-guide" so repeated clicks reuse one tab.
6. Sign-in errors. In apps/web/src/authFlow.ts oauthErrorMessage, add 'unavailable': "Google or GitHub sign-in isn't available right now. Try the other provider, or try again in a few minutes." Make services/api/oauth.py send error=unavailable when a provider is not configured in /start or /callback (it currently uses 'failed'), and when the token exchange gets a 5xx or a network error (httpx.HTTPError while calling the provider). Keep 'failed' for everything else, and never put provider text or emails in the URL. Update tests/test_oauth.py and authFlow.test.mjs.
10. Dataset page labels. In views/DataSourcesView.tsx, rename the visible uppercase labels: "CONTRACT HASH" → "Rules fingerprint", "MANIFEST HASH" → "Data fingerprint", "SOURCE SHA-256 HASH" → "File fingerprint (SHA-256)", "DATASET ID" → "Dataset ID", "COLUMN NAME" → "Column", "DECLARED UNIT" → "Unit", "AMOUNT TOTALLED" → "Amount totalled", "ROWS INCLUDED" → "Rows included". Put the three fingerprints inside a collapsed <details> labelled "Fingerprints (for audits)".
12. Duplicate uploads. When an upload returns an existing dataset (the API reuses identical bytes and returns that snapshot), show "This file is already the dataset '<name>'. Nothing new was added." instead of the normal success message. Detect it in the web app: the returned snapshot id already exists in the dataset list loaded before the upload.
17. Two-load SQL note. In views/SnapshotChange.tsx, after the note that two-load comparisons support only totals and counts, add the link "Why?" pointing to /guide#7b-same-calculation-on-two-data-loads-or-two-sources (open in the guide tab, target="metricguard-guide").
18. Long runs. In views/StartComparison.tsx and views/SnapshotChange.tsx, while a run is queued or running, show under the button: "On the free beta a comparison takes about 20–90 seconds. You can leave this page; the result appears in Cases when it's ready." Check that a queued or running run already shows up in Cases; if it doesn't, leave out the second sentence and report it.
25. Metrics page. In views/MetricsView.tsx, inside the metric detail view, group the existing content under three headings in this order: "Approved calculation", "Latest check" and "History". Move the version and binding tables under "History" in a collapsed <details> labelled "All versions and links". Rename "Observed timestamp" → "Seen at" and "Bound version" → "Linked version". No logic changes.
26. Changes page. In views/ChangesView.tsx, hide the dbt/Power BI sections (resource breakdown, declared coverage, structural signals) when the workspace has no imported revisions, and show one line instead: "Import a dbt or Power BI project in Settings → Integrations to check definition changes here."
27. New-load metric check summary. Where "Check your metrics on the new load" shows its results (views/NewLoadChecksPanel.tsx), add a one-line summary per metric above the details: "<metric>: moved by <difference> (<percent>)" or "<metric>: no change", using values the panel already has.
34. "MiB" left in messages. Search packages/ and services/ for user-facing strings containing "MiB" and change them to "MB" (strings only, never byte arithmetic). Update tests that assert the old text.
36. Help links. Add a "Help" link to the guide's matching section in these existing messages: Dataset checks failed → /guide#16-troubleshooting; the SQL refusal box in StartComparison → /guide#7-run-a-comparison; the guest notice (identity.tsx) → /guide#guest-accounts-free-beta. Check the real anchor ids by rendering docs/user-guide.md with apps/web/src/markdownLite.ts slug(). Open in the guide tab (target="metricguard-guide").
37. First run. In the Cases view (views/InvestigationLibrary.tsx and/or App.tsx), when the workspace has no saved cases and no comparisons, show the existing FirstRunGuide (views/FirstRun.tsx) at the top of Cases, not only on the dashboard. Keep the normal empty state below it.

Update docs/user-guide.md wherever it quotes a label you renamed.
```

---

## Prompt I2: Phone layout, accessibility, print check and guide screenshots (items 24, 35, 38, 39, 40)

```
You are working in the MetricGuard repository (Windows, PowerShell, Python venv at .venv\, web app in apps/web, React + Vite + TypeScript). Read docs/HANDOFF.md, AGENTS.md and DESIGN.md first.

SHARED RULES
- Small, simple changes to existing features only. Styling, markup attributes and docs; no new features.
- Don't touch packages/, services/ or apps/web/src/api.ts. For apps/web/src/views/ResultReport.tsx, apps/web/src/views/CaseReport.tsx and apps/web/src/views/evidence/Ledger.tsx, change only className/aria attributes and CSS (Claude is changing their logic). Put new styles in the existing CSS files.
- Do NOT commit or push. Do NOT add any AI attribution.
- When done, run: `npm test --prefix apps/web` and `npm run build --prefix apps/web` (and the Python suite if you touched anything that could affect it). Report the real results.
- Finish with: files changed, what changed per item number, test results, screenshots taken, and anything you could not verify.

TASK I2:
38. Phone layout of results. Below 640px wide, the reconciliation table (views/evidence/Bridge.tsx), the Records table (views/evidence/Ledger.tsx and the stack-table in ResultReport) and the Where it sits table (.where-table) must not need sideways scrolling: stack each row into a small card that shows each cell with its label (use the data-label attributes; add them where missing). Check at 375px and 420px wide with the dev server (`.claude/launch.json` has web-dev and api-local) or Playwright, and take before/after screenshots.
39. Keyboard and screen readers. Check these with keyboard only and fix what's missing (focus order, visible focus, labels, aria-live where text changes): the guest notice and its "Need a permanent account?" button (identity.tsx), the replay checks table (CaseReport, a real <caption>), the Week/Month "grouped by" select (ResultReport), the SQL editor's refused-token underline (views/SqlEditor.tsx: add a visually hidden text "Refused here" after the underlined token, or an aria-describedby on the textarea pointing to the refusal message), and the "Waking up the server…" note (App.tsx, role="status" aria-live="polite").
40. Dark-mode contrast. Check in dark mode that the urgent guest notice (.alert.guest-notice.urgent in style.css), .tok-refused (views/sqlEditor.css), .tok-keyword/.tok-function, the waking notice and the "Concentrated" tag reach WCAG AA contrast (4.5:1 for text, 3:1 for the underline against the background). Adjust them with existing design tokens only. Report the measured ratios.
24. Print check. Print the case page and the public shared case page to PDF in Chromium (Playwright page.pdf or the browser's print to PDF) using a seeded demo case (`python -m scripts.seed_demo`). Fix what looks broken: tables split across pages (use break-inside: avoid on rows), cut-off columns, empty first pages, dark backgrounds printed. Attach the before/after PDFs' file paths in your report.
35. Guide screenshots. Using the seeded demo and the dev server, capture 6 screenshots at 1280×800 in light mode: the Datasets page after Add dataset, the column questions step, New comparison with two calculations, a result's reconciliation (Balanced ✓), the Where it sits tab, and the Share dialog. scripts/capture_ui.ps1 may help. Save them as PNG files under apps/web/public/guide/ (keep each under 250 KB), and add them to docs/user-guide.md with image syntax ![…](/guide/<file>.png) in the matching sections. Then extend apps/web/src/markdownLite.ts to render images: only for paths starting with /guide/, as <img src alt loading="lazy">, with a test in markdownLite.test.mjs. Images must also show on the /guide page.
```
