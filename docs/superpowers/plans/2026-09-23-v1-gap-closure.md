# MetricGuard v1 Gap Closure Implementation Plan

> **For agentic workers:** Execute each task in order, verifying the focused test before and after its implementation. This workspace is not a Git repository, so commit steps cannot run here.

**Goal:** Close the local v1 Parquet and candidate-evaluation gaps, and record the remaining external release gates accurately.

**Architecture:** Preserve the fixed orders contract and immutable snapshots. Add Parquet as a second isolated ingestion format using DuckDB, then carry the format through the API, staging, supervisor, runner, UI and documentation. Audit the frozen candidate report against the planning pack's actual top-ranked target, preserving all denominators and unknown outcomes.

**Tech Stack:** Python 3.12, DuckDB, FastAPI, pytest, React/TypeScript.

**Spec:** `MetricGuard_Planning_Pack/01_product_and_prd.md`, `05_architecture.md`, `07_security_and_operations.md`, `08_demo_and_evaluation.md`; current state in `docs/v1-release-evidence.md`.

## Global constraints

- Keep the documented nine-column orders schema, INR, Asia/Kolkata, one row per unique order ID, 25 MiB upload ceiling and one-million-row ceiling.
- Untrusted file bytes are read in the isolated ingestion runner; the API only stages them. Preserve source hashes, database hashes and sidecar validation.
- SQL comparisons remain restricted to regenerated IR on immutable DuckDB snapshots. Existing CSV snapshots and reports remain readable.
- No live Docker claim without a running daemon. No analyst-pilot claim without actual external participants and comparable extracts.

---

### Task 1: Parquet core ingestion

**Files:** Add `tests/test_parquet_ingestion.py`; modify `packages/metricguard_core/snapshot.py` and `packages/metricguard_core/__init__.py`.

**Interfaces:** Produce `ingest_parquet(source: Path, destination: Path, metadata: dict | None = None) -> dict`, with the same manifest keys as `ingest_csv` and `ingestion_version='parquet-v1'`.

- [x] Write a failing test that creates a typed Parquet copy of the golden CSV through DuckDB, calls `ingest_parquet`, and asserts 8 rows, `inspect_snapshot` success and A→Finance gap `-6000000.00`.
- [x] Run `pytest tests/test_parquet_ingestion.py -q`; confirm the missing function causes failure.
- [x] Validate exact ordered column names/types, nullability, unique IDs, INR, dates, amounts and row limit before loading. Bind the input path as a query parameter. Check source hash before/after. Write the ordinary snapshot sidecar and clean partial output on errors.
- [x] Add negative fixtures for duplicate keys, mixed currency, missing columns and invalid bytes. Re-run the focused tests.

### Task 2: Isolated upload workflow

**Files:** Modify `tests/test_workflows.py`, `services/api/app.py`, `services/supervisor/worker.py`, `services/supervisor/runner.py`.

**Interfaces:** Upload accepts `.csv` or `.parquet`; staged source keeps that suffix. `isolated_ingest` copies the supported suffix into the runner input; `ingest_job` dispatches to the matching core ingester. Parquet uses `null_policy='native_null'`.

- [x] Add a failing HTTP→queue→supervisor→runner test using a typed Parquet golden fixture; assert immutable published snapshot, correct source hash and final A→Finance comparison.
- [x] Run the focused test; confirm the API rejects `.parquet`.
- [x] Add bounded format dispatch and native-null metadata without changing the CSV path. Return a bounded Parquet validation error rather than publishing a snapshot on invalid input.
- [x] Run `pytest tests/test_workflows.py tests/test_supervisor.py -q` and confirm both formats pass.

### Task 3: UI and contract text

**Files:** Modify `apps/web/src/main.tsx`, `README.md`, `docs/contracts.md`, `docs/v1-release-evidence.md`.

- [x] Update the file picker and import status copy to say CSV or Parquet; retain the CSV template download.
- [x] Document the exact fixed-schema Parquet acceptance rules and native null behavior.
- [x] Run `npm run build --prefix apps/web`; confirm TypeScript and bundle pass.

### Task 4: Candidate release gate and final verification

**Files:** Modify `docs/v1-release-evidence.md`, `docs/next-versions-roadmap.md`; change `scripts/evaluate_candidates.py` only if the existing report cannot state the target faithfully.

- [x] Rerun the frozen evaluator and record top-ranked known precision, known denominator, unknown top picks, pair precision/recall, and synthetic-data limits. Keep development and holdout concepts disjoint.
- [x] Run the full Python suite, frontend build and Compose config. Try `docker info`; if daemon is absent, retain the live-container gate.
- [x] Update the release matrix and roadmap so the local implementation state and remaining external work are accurate. Run `graphify update . --no-cluster` after code edits and regenerate the tree.

