# GPT prompt, round 3 (2026-11-01): the simple half

Claude is doing items 1–3, 5, 7–9, 11, 13, 14, 17, 18, 23 and 24 itself. GPT does the items below (numbers refer to the 25-item list in the chat).

## Prompt J1 (items 4, 6, 10, 12, 15, 16, 19, 20, 21, 22, 25)

```
You are working in the MetricGuard repository (Windows, PowerShell, Python venv at .venv\, web app in apps/web, React + Vite + TypeScript). Read docs/HANDOFF.md, AGENTS.md and DESIGN.md first.

SHARED RULES
- Small, simple changes to existing features only. No new pages, routes or features beyond what this prompt lists.
- Don't touch packages/metricguard_core logic, hashes, contracts, the SQL parser or auth logic.
- Claude is changing these files at the same time, so do NOT edit them: services/api/app.py, services/api/store.py, services/api/auth.py, services/api/oauth.py, services/api/guests.py, services/api/rate_limit.py, services/supervisor/*, packages/metricguard_core/*, apps/web/src/api.ts, apps/web/src/views/StartComparison.tsx, apps/web/src/views/CaseReport.tsx, apps/web/src/PublicCasePage.tsx, apps/web/src/views/evidence/CustodyLog.tsx, apps/web/src/views/InvestigationLibrary.tsx, apps/web/src/investigationLibrary.ts, apps/web/src/views/SettingsView.tsx. If an item needs one of them, skip that part and report it.
- Match the surrounding code style and comment density.
- Do NOT commit or push. Do NOT add any AI attribution (no Co-Authored-By or "Generated with" lines).
- When done, run: `.venv\Scripts\python -m pytest -q --basetemp .test-tmp/full`, `npm test --prefix apps/web` and `npm run build --prefix apps/web`. Report the real results; never claim a check passed if you did not run it.
- Finish with: the files changed, what changed per item number, test results, and anything you skipped or could not verify.

TASK J1:
4. Copy invite message. In apps/web/src/views/MembersPanel.tsx, beside each pending invite add a "Copy invite message" button. It copies this text to the clipboard (navigator.clipboard.writeText, with a fallback that selects a read-only textarea): "You're invited to the MetricGuard workspace '<workspace name>' as <role>. Sign in at https://metricguard.vercel.app/signin with Google or GitHub using <email>, and you'll join it automatically." Use window.location.origin instead of the hard-coded address. Show "Copied" for 2 seconds.
6. Docs: limits reset. In docs/deployment-plan.md, in the reliability paragraph, add one sentence: per-address limits and the health check's server error count are kept in memory and reset when the container restarts.
10. Newer-load type check note. Where the newer-load compatibility problem is shown (views/DataSourcesView.tsx), add under it: "Checked on the first rows of the file; the import checks every row." Only when a problem is shown.
12. Cancel an upload. uploadWithProgress in api.ts is Claude's file, so don't edit it there. In the upload screens (views/DataSourcesView.tsx, views/TwoTableUpload.tsx) add a "Cancel upload" button while the percentage is showing. Make it work through an AbortSignal: if uploadWithProgress already accepts one, pass it; if not, skip the button and report it (Claude will add the parameter). After cancelling, show "Upload cancelled. Nothing was added." and reset the step.
15. Number format. In apps/web/src/resultSummary.ts, groupDigits groups by thousands. Add an optional `style: 'western' | 'indian'` argument (default 'western', so existing callers don't change). Indian style groups as 12,40,000 and keeps the sign and decimals untouched; it is display-only text formatting, never arithmetic. Add a helper `digitStyleFor(unit: string | null | undefined): 'western' | 'indian'` that returns 'indian' for INR or ₹. Use it in the result headline, the reconciliation table (views/evidence/Bridge.tsx) and the Where it sits table (views/ResultReport.tsx), only where a unit is known. Add node tests: 1240000 → 12,40,000; -1234567.89 → -12,34,567.89; 999 → 999; western unchanged.
16. Concentration sentence for ratios. In views/ResultReport.tsx, when the result is a ratio (result.ratio is set), the Concentration sentences must say "… holds 62% of the effect on the ratio …" instead of "of the difference". No other logic changes.
19. Docs: who can edit conclusions. In docs/user-guide.md §9, add: "Owners and analysts can edit a conclusion; readers can only read it. Earlier conclusions stay on record." In §13's role list, add "edit conclusions" to Analyst.
20. Sample case. apps/web/src/DemoCasePage.tsx and demoCase.ts show the public sample at /demo/MG-F3A3. Bring it in line with the current case page where the data allows: add the category descriptions (use categoryDescription from views/evidence/categories.ts) and the replay checks table (use replayChecks from views/evidence/replayChecks.ts with the sample's own saved and replayed values, which are identical). Keep its numbers exactly as they are.
21. Screenshot record. In docs/HANDOFF.md, add a short note listing the six guide screenshots in apps/web/public/guide/, what each shows, and the date taken, plus "Retake them when those screens change (see docs/evidence/i2-ui-review.md for how)".
22. Guide sections 11–14. Re-read docs/user-guide.md §11 (Metrics), §12 (Changes), §13 (team) and §14 (settings) against the current screens (MetricsView after its reorganisation, ChangesView with hidden empty sections, MembersPanel, SettingsView) and correct any label, button name or step that no longer matches. Use the real UI text.
25. Smaller first load. In apps/web/src/App.tsx, load these with React.lazy and Suspense (fallback: the existing loading text or a small "Loading…"): PublicPages, SignInScreen, PublicCasePage, MetricsView, ChangesView, SettingsView and GuidePage (via PublicPages). Keep named exports working (use `lazy(() => import('./X').then(m => ({ default: m.X })))`). Run the build and report the main chunk size before and after; the large-chunk warning should be gone or the main chunk clearly smaller.
```
