# MetricGuard online: free-tier deployment plan

**Goal:** run MetricGuard on the internet for $0 a month: the backend on Northflank, the website on Vercel, with a public homepage and sign up / sign in.
**Status:** steps 1–3 (backend) are built and tested; nothing is deployed yet. Written 2026-10-20.
**Bottom line:** a free launch is possible as an **invite-only beta for small datasets**. Five things in today's code must change first, and two free-tier rules limit what you can do (no commercial use on Vercel's free plan, and very small free compute on Northflank).

---

## 1. What runs today, and what breaks online

| Piece today | How it works locally | Problem online |
|---|---|---|
| API (`services/api/app.py`, FastAPI + uvicorn) | One process on port 8000; also serves the built website | Fine. Runs in `infra/Dockerfile.api`. |
| Comparison workers (`services/supervisor/worker.py`) | Start **one Docker container per comparison** (`docker run --network none --memory 1g …`) | **Blocker.** Northflank containers have no Docker daemon to call, so this cannot run as-is. |
| Database | SQLite file, or Postgres in `infra/compose.yaml` | Needs a hosted Postgres. |
| Files (uploads, frozen datasets, evidence) | Local folder `METRICGUARD_DATA_DIR` | **Blocker.** Container disks are wiped on every redeploy. Files need durable storage. |
| Sign in | API keys exchanged for a `SameSite=Strict` session cookie; the API never grants CORS | **Blocker for a split setup.** The website and API must look like *one* site to the browser (solved with a Vercel rewrite, section 4). There is no sign-up at all. |
| Demo data | `scripts.seed_demo` runs on every start; `METRICGUARD_ALLOW_UNVERIFIED_LOCAL=true` lets anyone in without signing in | **Must be off in production.** |
| Website | Vite/React app in `apps/web`, opens straight onto a workspace | Needs a public homepage, sign-up and sign-in pages, and legal pages. |

---

## 2. The free stack

| Need | Service (free tier) | Free allowance (checked 2026-10-20) | Notes |
|---|---|---|---|
| Website + homepage | **Vercel Hobby** | 100 GB fast data transfer, **10 GB fast origin transfer**, 1M CDN requests a month | **Non-commercial, personal use only.** Once you charge money, move to Pro ($20/month) or a free host that allows commercial use (for example Cloudflare Pages). |
| API + comparison worker | **Northflank Developer Sandbox** | 2 services, 2 jobs (one active run at a time), 1 add-on | **Needs a card on file** even when free. Northflank says the sandbox "should not be used for production". The CPU and RAM cap for free services is not published, so check it in the dashboard (step 0). |
| Database (Postgres) | Northflank free add-on (the 1 add-on slot) **or Neon Free** | Neon: 0.5 GB storage, 100 compute-hours a month, sleeps after 5 minutes idle | Neon keeps the Northflank add-on slot free. 0.5 GB is plenty for metadata, because datasets live in object storage. |
| File storage | **Cloudflare R2** | 10 GB storage, 1M writes and 10M reads a month, **no egress fees** | S3-compatible. Holds uploads, frozen datasets, evidence and backups. |
| Sign in | **Google and GitHub sign-in (OAuth)**, built into the API | Free, no third party to pay | Avoids having to send emails for a first version. |
| Email (later: verification, password reset, invites) | **Resend Free** | 3,000 emails a month, 100 a day, 1 domain | Needs a domain you own. Only add it with email + password sign-up. |
| Errors and uptime | Sentry free developer plan, UptimeRobot free | Small free quotas | Optional but recommended. |
| Domain | Use `*.vercel.app` for free | — | A custom domain (about $10/year) is the **only non-free item**, and it's optional. Email sending needs one. |

**Total: $0 a month.** Card verification is required on Northflank. A custom domain (about $10/year) is optional.

### What "free" costs you in limits
- **Small compute.** Today a 1M-row comparison takes about 2 minutes on 1 dedicated vCPU with 1 GiB of RAM. Free Northflank plans are likely much smaller (the smallest is 0.1–0.2 shared vCPU with 256–512 MB), so cap free datasets at about **100,000 rows and 25 MB per upload** until you measure the real limit.
- **One comparison at a time.** Free jobs allow one active run; queue the rest.
- **Vercel origin transfer: 10 GB a month.** Every API call and upload that goes through the Vercel proxy counts against it, which is another reason to keep uploads small.
- **No commercial use** on Vercel Hobby, and Northflank's sandbox is not meant for production. Treat this setup as a **beta**.

---

## 3. Architecture

```
Browser ──► https://metricguard.vercel.app
              │  /            → homepage (static)
              │  /signin, /signup, /privacy, /terms → static pages
              │  /w/...       → the app (React)
              └─ /api/*       → Vercel rewrite ──► https://<api>.code.run/api/*   (Northflank service "api")
                                                         │
                                       ┌─────────────────┼─────────────────────┐
                                       ▼                 ▼                     ▼
                              Postgres (Neon)     Cloudflare R2         comparison worker
                              users, cases,       uploads, frozen       (same container as the API,
                              runs, sessions      datasets, evidence    or a Northflank job per run)
```

**Why the rewrite matters.** The session cookie is `SameSite=Strict` and the API refuses cross-origin calls on purpose (CSRF protection in `services/api/auth.py`). With `/api/*` rewritten through Vercel, the browser only ever talks to `metricguard.vercel.app`, so the cookie and the CSRF check keep working unchanged. Vercel adds a secret header (`x-origin-secret`) on every proxied request, and the API rejects requests without it, so nobody can bypass Vercel and hit the API directly.

**Where comparisons run.** There are two options:
1. **In the API container (built: `METRICGUARD_RUNNER=local`).** The engine runs as a child process with a scrubbed environment (no database URL or storage keys), a private scratch folder, the worker's wall-clock timeout, and Linux CPU-time and file-size limits. `python -m services.serve` keeps the worker running beside the API in one container. Simple, and it fits in one free service. The cost: no per-run network isolation. That's acceptable while sign-up is invite-only.
2. **A Northflank job per comparison (later).** The worker triggers the job through Northflank's run-job API, with the run's inputs in R2. Each run gets its own container, but free jobs run one at a time and it takes more work to build.

---

## 4. Configuration

### Northflank service `api` (environment variables)
Build from GitHub with **`infra/Dockerfile.server`** (port 8000; the image honours `PORT` if Northflank sets one). Health check: `/api/v1/health`. The image already sets the hosted defaults marked *(image)*; you only add the rest.

| Variable | Value | Notes |
|---|---|---|
| `METRICGUARD_DATABASE_URL` | `postgresql+psycopg://user:pass@host/db?sslmode=require` (from Neon) | secret |
| `METRICGUARD_STORAGE` | `s3` | durable files (step 2) |
| `METRICGUARD_S3_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` | from Cloudflare R2 |
| `METRICGUARD_S3_BUCKET` | e.g. `metricguard` | |
| `METRICGUARD_S3_ACCESS_KEY_ID` / `METRICGUARD_S3_SECRET_ACCESS_KEY` | R2 API token (object read & write, this bucket only) | secret |
| `METRICGUARD_S3_REGION` / `METRICGUARD_S3_PREFIX` | `auto` / optional, e.g. `prod/` | defaults are fine for R2 |
| `METRICGUARD_ORIGIN_SECRET` | long random value, the same as Vercel's `ORIGIN_SECRET` | secret |
| `METRICGUARD_PUBLIC_ORIGINS` | `https://metricguard.vercel.app` | origins allowed to change data |
| `METRICGUARD_ALLOWED_HOSTS` | the Northflank host name, e.g. `api--metricguard--xxxx.code.run` | without it every request gets 400 |
| `METRICGUARD_APP_URL` | `https://metricguard.vercel.app` | links in exported reports |
| `METRICGUARD_RUNNER` | `local` *(image)* | engine without Docker |
| `METRICGUARD_MODE` | `isolated` *(image)* | comparisons run in the worker |
| `METRICGUARD_WORKERS` | `1` *(image)* | comparisons at the same time |
| `METRICGUARD_ALLOW_UNVERIFIED_LOCAL` | `false` *(image)* | sign-in required |
| `METRICGUARD_SECURE_COOKIES` | `true` *(image)* | session cookie only over HTTPS |
| `METRICGUARD_DATA_DIR` / `METRICGUARD_JOB_DIR` | `/tmp/metricguard` / `/tmp/metricguard/jobs` *(image)* | scratch and cache; durable copies live in R2 |
| `METRICGUARD_RUN_SECONDS` / `METRICGUARD_INGEST_SECONDS` | `300` / `300` *(image)* | lower than local 600 / 1200 |
| `METRICGUARD_SEED_DEMO` | `false` (default) | `true` loads the demo workspace on start |
| `METRICGUARD_LOCAL_MEMORY_MB` | unset | optional address-space cap for the engine; leave off and rely on the plan's memory |
| *Later steps:* `GOOGLE_CLIENT_ID`/`_SECRET`, `GITHUB_CLIENT_ID`/`_SECRET`, `METRICGUARD_SIGNUP`, `METRICGUARD_INVITE_EMAILS`, `METRICGUARD_FREE_MAX_ROWS`, `METRICGUARD_FREE_MAX_UPLOAD_MB`, `SENTRY_DSN` | | steps 4, 6, 8 (not built yet) |

Check the bucket before the first deploy: with the same `METRICGUARD_STORAGE`/`METRICGUARD_S3_*` values set in a terminal, `python -m scripts.check_storage` uploads, reads back and deletes one small file.

### Vercel project
- Root directory `apps/web`, framework Vite, build `npm run build`, output `dist`.
- Environment variable: `ORIGIN_SECRET` (the same value as `METRICGUARD_ORIGIN_SECRET`).
- `apps/web/vercel.json` (new):

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "routes": [
    {
      "src": "/api/(.*)",
      "dest": "https://<api-host>.code.run/api/$1",
      "transforms": [
        { "type": "request.headers", "op": "set", "target": { "key": "x-origin-secret" }, "args": "$ORIGIN_SECRET", "env": ["ORIGIN_SECRET"] }
      ]
    },
    { "handle": "filesystem" },
    { "src": "/(.*)", "dest": "/index.html" }
  ]
}
```
Vercel now honours upstream cache headers on external rewrites by default, so the API must send `Cache-Control: no-store` on every `/api` response (step 3), and no user's data can ever be cached on the CDN. (`routes` cannot be combined with a `headers` block in `vercel.json`, so this is set at the API.)

### OAuth apps
- Google: authorised redirect URI `https://metricguard.vercel.app/api/v1/auth/oauth/google/callback`. Publishing the consent screen needs a privacy policy URL and a homepage URL.
- GitHub: callback `https://metricguard.vercel.app/api/v1/auth/oauth/github/callback`.

---

## 5. Order of work

Each step ends in something you can check. Steps 1–4 are backend, 5–6 frontend, 7–9 going live.

| # | Step | What changes | Done when |
|---|---|---|---|
| 0 | **Verify the free tiers** (you, about 30 minutes) | Create the Northflank (card required), Vercel, Neon and Cloudflare accounts. In Northflank, note the largest compute plan the free project allows and whether volumes are allowed. | You know the free CPU/RAM. That sets the row limit in step 6. |
| 1 ✅ | **Runner without Docker** | `LocalLauncher` in `services/supervisor/worker.py` (`METRICGUARD_RUNNER=local`); the engine reads its folders from `METRICGUARD_JOB_INPUT`/`_OUTPUT`; `services/serve.py` runs the API and a self-restarting worker pool; `infra/Dockerfile.server`. | Done: a comparison completes in the `Dockerfile.server` image (Linux, no Docker) and reconciles. |
| 2 ✅ | **Durable files on R2** | `services/durable.py`: frozen datasets (with manifest sidecars) and evidence are copied to an S3-compatible bucket before a run or import is recorded, and fetched back on first use. Hashes are unchanged, so replay still verifies byte for byte. `scripts/check_storage.py` checks a real bucket. | Done: with the container's files wiped, old evidence reads and a replay reproduces the same evidence hash. Tested with a folder-backed bucket and a mocked S3 endpoint, **not yet against real R2**. |
| 3 ✅ | **Production switches** | Origin secret (`x-origin-secret`, except `/api/v1/health`); `METRICGUARD_PUBLIC_ORIGINS` and `METRICGUARD_ALLOWED_HOSTS` (the old guards accepted localhost only); `Cache-Control: no-store` on `/api`; Secure session cookie; demo seed off by default; sign-in required in the image. | Done: without the secret → 403, foreign origin → 403, unknown host → 400, API responses not cacheable. Postgres was not exercised in these tests (it already works via `infra/compose.yaml`). |
| 4 | **Sign up / sign in** | OAuth routes (Google, GitHub) that create or find the user and issue the existing session cookie; create a personal workspace on first sign-in; invite allow-list; sign-out; delete-my-account. | A new Google user lands in an empty workspace. Someone not on the allow-list sees "You're on the waitlist". |
| 5 | **Homepage and auth pages** | Public `/` explaining the product (the problem, how a comparison works, the evidence seal, a screenshot, a "Sign up" button), plus `/signin`, `/signup`, `/privacy`, `/terms`, and a 404 page. Signed-in users who open `/` go to Cases. | You can share the homepage URL; sign-in works end to end. |
| 6 | **Free-tier guardrails** | Upload and row caps with clear messages; one running comparison per workspace; a daily run limit; delete datasets after N days of no use; show usage in Settings. | A 200k-row upload gets a friendly "Free beta limit: 100,000 rows" message before it runs. |
| 7 | **Deploy** | Northflank service from GitHub plus env vars; Vercel project plus `vercel.json`; OAuth redirect URLs. | `metricguard.vercel.app` loads, signs in, runs a comparison, saves and replays a case. |
| 8 | **Backups and monitoring** | Nightly `pg_dump` to R2 (Northflank free cron job); UptimeRobot on `/api/v1/health`; Sentry on the API and web. | A restore of last night's dump works on a scratch database. |
| 9 | **Beta launch** | Invite 5–10 analysts; collect feedback; watch usage against the free limits. | Real users finish a case without help. |

**Rough effort:** steps 1–3 are the biggest (2–4 days), steps 4–5 about 2–3 days, and steps 6–8 about 2 days. Step 0 is yours.

---

## 6. Things easy to miss
- **Privacy policy and terms.** Users upload their own business data, and Google sign-in requires a privacy policy to publish its consent screen. Say what is stored, where (Neon, Cloudflare R2, Northflank region), for how long, and how to delete it.
- **Delete my data.** A Settings button that deletes the workspace, its datasets in R2 and its cases.
- **Region.** Put Northflank, Neon and R2 in the same region (for example EU or US), or every comparison pays cross-region latency.
- **Warehouse connections.** Snowflake, BigQuery and PostgreSQL sources connect *out* from Northflank. Some customer databases only allow known IP addresses, and the free tier has no fixed outbound IP. Mark warehouse sources "may not work with IP allow-lists" during the beta.
- **Isolation.** Without per-run containers, a comparison runs next to the API. Keep sign-up invite-only until runs move to Northflank jobs (option 2 in section 3).
- **Cold starts.** Neon sleeps after 5 minutes idle; the first request after a pause takes a little longer. That's fine for a beta.
- **Leaving the free tier.** When you charge money: Vercel Pro ($20/month) or another host, and a paid Northflank plan (1 vCPU / 2 GB is $24/month at Northflank's published rate of $0.01667 per vCPU-hour and $0.00833 per GB-hour).

---

## 7. Open questions to confirm in step 0
1. The largest compute plan the Northflank free project allows (not published).
2. Whether free Northflank projects can attach volumes (not published). If yes, step 2 could start on a volume and move to R2 later.
3. Whether large request bodies pass through Vercel's external rewrite (not documented; Vercel Functions cap bodies at 4.5 MB). Test a 20 MB upload in step 7. If it fails, upload straight to R2 with a pre-signed URL.

## Sources
- [Northflank pricing](https://northflank.com/pricing) and [project types / sandbox limits](https://northflank.com/docs/v1/application/billing/pricing-on-northflank)
- [Northflank compute plans and quotas](https://northflank.com/docs/v1/application/billing/resource-plans-and-quotas) and [run-job API](https://northflank.com/docs/v1/api/project/jobs/run-job)
- [Vercel Hobby plan](https://vercel.com/docs/plans/hobby) and [Vercel rewrites (external origins, origin secret, caching)](https://vercel.com/docs/routing/rewrites)
- [Vercel function body size limit](https://vercel.com/guides/how-to-bypass-vercel-body-size-limit-serverless-functions)
- [Neon free plan limits](https://neon.com/faqs/free-plan-limits-and-quotas)
- [Cloudflare R2 pricing summary](https://filebase.com/blog/cloudflare-r2-pricing-costs-savings-and-alternatives-in-2026/)
- [Resend free tier](https://resend.com/blog/new-free-tier.md)
