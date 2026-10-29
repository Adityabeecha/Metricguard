# MetricGuard handoff for the next AI assistant

Last updated 2026-10-29. Written for any assistant (Claude, GPT, Gemini, Codex and so on) that picks up this repository. Read this first, then [AGENTS.md](../AGENTS.md) and [project-graph.md](project-graph.md).

## 1. What MetricGuard is

MetricGuard explains why two numbers that should match don't. A user uploads a data file (CSV, Excel, Parquet) or connects a warehouse (PostgreSQL, Snowflake, BigQuery), writes or builds a restricted SQL calculation (SUM, COUNT(*), COUNT DISTINCT, AVG, ratios, `A - B`, one `CASE WHEN`, AND/OR/NOT filters, star joins), and compares two versions: two calculations, two data loads (for example September vs October), or two sources. The result lists the exact records that cause the gap. They add up to the whole difference with zero residual, with no estimation and no AI. Results are hash-sealed evidence and can be saved as cases that replay byte for byte.

- Product description: [PRODUCT.md](../PRODUCT.md). End-user guide: [user-guide.md](user-guide.md). Visual design rules: [DESIGN.md](../DESIGN.md).
- Hosted at **https://metricguard.vercel.app**. The web app is on Vercel and proxies `/api` to the backend on Northflank, with Neon PostgreSQL and Cloudflare R2 for files. Public sample case: `/demo/MG-F3A3`.

## 2. Where the work stands (as of commit `81e6252`)

Everything the user asked for so far is implemented and committed on `main`. The working tree is clean apart from the untracked `.claude/` folder, which holds local tool settings and should not be committed.

Most recent work, newest first:

1. `81e6252` End-to-end user guide ([user-guide.md](user-guide.md)).
2. `e1605dd` Guest accounts. New sign-ups whose email is not on `METRICGUARD_FULL_ACCESS_EMAILS` (fallback `METRICGUARD_INVITE_EMAILS`) are erased with all their data 8 hours after sign-up and get 25 MB of storage. See `services/api/guests.py` and `tests/test_guest_accounts.py`.
3. `1527269` Open sign-up. Google/GitHub sign-in creates an account plus a personal workspace for any verified email. The invite list and waitlist were removed. Workspace invites (Settings → Members) still exist.
4. `3bcbf06` Analyst workflow: SQL editor with column chips, two-source comparisons, saved warehouse connections (credentials encrypted with `METRICGUARD_SECRET_KEY`, private-address blocking on hosted servers).
5. `350a93a` / `8d8cdb8` Reviewer fixes: dataset-linked metrics, dataset upgrades, public shared-case links, evidence concentration, record context for two-load runs.
6. Before that: hosted deployment foundations (local runner without Docker, durable R2 storage, proxy switches), free-tier guardrails, the reconciliation-file UI, the homepage and legal pages, and OAuth.

Full per-feature notes, with file names: the dated sections in [project-graph.md](project-graph.md).

## 3. Open items and known gaps

None of these is in progress. They are candidates for the next piece of work, and the user decides the order.

- **Deployment plan, steps 7–9** ([deployment-plan.md §5](deployment-plan.md)). The site is live, but step 7 is not checked off in the plan. These have never been verified end to end on the deployed stack: real Google/GitHub sign-in, a full comparison → save → replay, R2 replay after a redeploy, account erasure against Neon/R2, and a 20 MB upload through the Vercel rewrite (Vercel may cap bodies at 4.5 MB; the fallback is pre-signed R2 uploads). Step 8 (nightly `pg_dump` to R2, UptimeRobot, Sentry) and step 9 (beta with 5–10 analysts) are not started.
- **Launch placeholders.** `[region]` and `[contact email]` in the privacy/terms copy, and the draft legal text, still need the owner's review.
- **Isolation.** Hosted runs execute next to the API (`METRICGUARD_RUNNER=local`) rather than in per-run containers. Sign-up is open by deliberate decision, and free-tier limits bound the load. Moving runs to Northflank jobs is the long-term fix (deployment plan §3, option 2).
- **External pilot gates V2-06 / V3-06** remain OPEN: no real-analyst adoption has been claimed. See [v2-release-evidence.md](v2-release-evidence.md).
- **Feature limits** noted in project-graph.md: large snapshots (>100,000 records) support only plain SUM/COUNT(*); two-load comparisons do not cover distinct counts, ratios, subtraction, CASE or relational data; relational joins are capped at one fact plus four lookups.
- **Acceptance pending.** Several 2026-10-18+ passes (distinct/ratio aggregates, two-load comparison, reviewer rounds) were implemented with unit tests but the user's browser acceptance was delegated and is not recorded as passed. Never report them as accepted.
- **Generated code graph** (`graphify-out/`) may be stale. It has not been refreshed since recent commits. Refresh it only when the user asks (see §5).

## 4. How the code is laid out

| Area | Path |
| --- | --- |
| Calculation engine (parser, contracts, exact comparison, evidence) | `packages/metricguard_core/` (`parser.py`, `flat*.py`, `relational_star.py`, `engine.py`, `free_tier.py`) |
| API (FastAPI routes, storage, auth, shares, usage) | `services/api/` (`app.py`, `store.py`, `models.py`, `oauth.py`, `guests.py`, `evidence_cache.py`, `case_shares.py`) |
| Job runner and supervisor | `services/supervisor/` (`worker.py`, `runner.py`, `collector.py`, `evidence_artifact.py`, `row_context.py`) |
| Hosting | `services/serve.py`, `services/durable.py`, `services/maintenance.py`, `infra/Dockerfile.server`, `apps/web/vercel.json` |
| CLI and CI gate | `services/cli.py`, `services/ci_workflow.py`, `metricguard.py`, `.github/actions/metricguard-ci/` |
| Web app (React + Vite + TypeScript) | `apps/web/src/` (`App.tsx`, `router.ts`, `api.ts`, `views/`, `views/evidence/`) |
| Tests | `tests/` (pytest), `apps/web/src/*.test.mjs` (web) |
| Planning pack and release records | `MetricGuard_Planning_Pack/`, `docs/v*-release-evidence.md`, `docs/evidence/` |

The "Find the right file" table in [project-graph.md](project-graph.md) maps each feature to its exact source and test files.

## 5. Rules the user has set (follow them)

1. **Testing is delegated.** Don't run the test suite or acceptance cases unless the user asks. At each handoff, list the exact checks to run, what they should show, and what is unverified. Never call an unrun check "passed". Full checks:
   - `.venv\Scripts\python -m pytest -q --basetemp .test-tmp/full`
   - `npm run build --prefix apps/web`
   - `docker compose -f infra/compose.yaml config --quiet`
2. **No AI attribution in git.** Do not add `Co-Authored-By: <AI>` or "Generated with …" lines to commits or PRs. Commit or push only when asked.
3. **Graphify refresh is manual.** Only when the user asks: `$env:GRAPHIFY_MAX_WORKERS = '1'; graphify update . --no-cluster`. Keep `graphify-out/graph.json` and `manifest.json`. Don't run it after each edit, and don't regenerate `GRAPH_TREE.html` unless asked separately.
4. **Keep the docs current.** If a change alters a component boundary, data flow or requirement status, add a dated section to [project-graph.md](project-graph.md) and update the relevant release-evidence file.
5. **Work split.** The user usually has a planning assistant (Claude) design the work and write ready-to-paste implementation prompts, and a coding assistant (GPT through their AGY CLI / Flash workflow) write the code and run the tests. If you are the coding assistant, implement what the prompt specifies. If you are the planning assistant, produce the prompt rather than the code.
6. **Platform.** Windows 11, PowerShell. The Python virtualenv is `.venv\`. Local start/stop: `scripts/mg.ps1`.

## 6. Engineering invariants (don't break these)

- Evidence must reconcile exactly. Record contributions plus the residual equal the total change, using exact `Decimal` arithmetic and no floats.
- Frozen contracts, snapshots and evidence are immutable and hash-pinned. Never change a historical hash, IR or policy identity. New behavior gets a new contract version or policy (for example FlatContract v3 → v8).
- Saved cases must replay byte for byte (same evidence SHA-256).
- User SQL is parsed into a restricted IR and never run raw. Refuse unsupported SQL with a clear, plain-language reason instead of guessing.
- Multi-workspace isolation and role checks (reader / analyst / owner) apply to every route.

## 7. Suggested first steps for a new session

1. Run `git status` and `git log --oneline -10` to confirm you are at or after `81e6252`. If newer commits exist, read them and update this file.
2. Ask the user which open item from §3 to take on, unless they have already said.
3. For code questions, run `graphify query "<question>" --budget 800`, then confirm against current source, because the graph may be stale.
4. When you finish a batch of work, update §2 and §3 of this file so the next assistant can continue.
