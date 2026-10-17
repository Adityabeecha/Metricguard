# MetricGuard v1 implementation plan

**Goal:** Deliver the planning pack's local SQL reconciliation workbench, with an executable golden investigation and explicit unsupported states.
**Spec:** ../MetricGuard_Planning_Pack/MetricGuard_Full_Specification.md
**Architecture:** React/TypeScript/Vite, FastAPI, SQLGlot restricted IR, DuckDB analytics, durable SQLAlchemy records (PostgreSQL in Compose; SQLite for the immediately runnable local demo). Trusted CLI generates synthetic precomputed artifacts. Uploaded SQL executes only in a restricted Docker runner controlled by a separate supervisor.

## Decisions and constraints
- User requested implementation of the supplied design; reuse its decisions rather than ask for a second design approval.
- Workspace has no Git repository or existing code; implement in this dedicated directory without a worktree.
- Docker daemon is unavailable here. Default to precomputed demo execution, while keeping imports, history, reviews and exports usable. Never execute uploaded SQL in the API.
- Fixed retail schema; INR, Asia/Kolkata, business dates, decimal strings, explicit CSV null policy. No hidden data cleaning.
- SUM and COUNT supported; COUNT DISTINCT if its entity evidence is verified. No joins, CTEs, arbitrary functions, or remote integration.
- Full AST allowlist, immutable version/snapshot hashes, separate definition and value statuses, undefined SUM retained.
- Tests precede numeric/API implementation. Independent golden expectations: A 104000000, B 104700000, Finance 98000000; bridge -4320000, -420000, -1260000.

## Tasks
- [x] 1. Core: `packages/metricguard_core/{parser,snapshot,engine}.py`, `tests/test_core.py`. Parse and bind the full tree, validate snapshots, calculate exact endpoints, row evidence, ordered bridge and provenance. Test golden/negative/NULL/identity/sign reversal and snapshot integrity.
- [x] 2. Application: `services/api/{app,models,store}.py`, `tests/test_api.py`. Durable definitions/versions, snapshot records, reviews, comparisons, evidence, history, baseline decisions. Test idempotency, immutability, contextual rejection and demo execution restrictions.
- [x] 3. Interface: `apps/web`. Six coherent views: overview, comparison (four tabs), inventory/import, snapshot import, history, evidence/baselines. Typed API client, accessible errors, responsive restrained analytics design, no fabricated results.
- [x] 4. Runner: `services/supervisor`, `infra`, `scripts`. Fixed Docker launch policy; durable claim/lease/attempt transitions, cancellation and bounded execution; local precomputed demo seeding and start scripts. Test queue transitions independently of Docker.
- [x] 5. Integration: lock dependencies, build frontend, run complete Python regression and browser journey, inspect desktop/mobile rendering, document run instructions, support matrix and unverified infrastructure gates.

## Shared contracts
See `docs/contracts.md`. Core supplies `parse_definition(sql, time_column=None) -> dict`, `ingest_csv(source: Path, destination: Path, metadata: dict | None=None) -> dict`, `compare(database: Path, baseline: dict, target: dict, context: dict) -> dict`, `CoreError(code, message)`. Result uses the planning pack's comparison_result keys plus `evidence`, `definition_differences`, and `provenance`.

## Verification
Run `.venv/Scripts/python -m pytest -q`, `npm run build --prefix apps/web`, then browser checks of A/Finance, A/B, unsupported import, history, export and mobile layout. Container execution is a separate gate and must not be reported verified without a running daemon.

## Release evidence and remaining gate

- Python suite: 69 tests passed on 2026-09-23, including an HTTP → queue → supervisor → runner → stored result check with only Docker launch replaced by a local test adapter.
- Frontend TypeScript and Vite production build passed. Browser checks showed exact A/Finance and A/B results, six navigable views and a 390px layout. API tests checked unsupported imports, context errors and exports.
- Compose configuration validates. The Docker daemon could not be started in this environment; actual container launch, permissions and resource limits remain unverified. The API stays in precomputed demo mode by default.
- New dataset intake uses the trusted isolated administrator CLI; the browser reuses the golden fixture and explicitly rejects other uploads until isolated intake is available.
