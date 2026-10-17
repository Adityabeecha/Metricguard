# MetricGuard analyst pilot: current state and runbook

**Prepared 2026-09-24. Status: preparation only.** No real participant, case, result, timing, or outcome is claimed here. Begin by [observing the analyst's existing process](observation-guide.md) before showing MetricGuard. Use the [intake](case-intake.md), [measurement sheet](measurement-sheet.md), and [decision record](decision-record.md) for each case.

The [Olist public-data evaluation](olist-public-case.md) is a separate external coverage check. Its published ticket is simulated and its exact dataset calculations do not count as an analyst pilot.

## The actual local v2 state

The guided six-step flow in `apps/web/src/V2Investigations.tsx` creates a versioned nine-role contract, previews and explicitly maps a CSV/Parquet extract, validates an immutable snapshot, binds source SQL, compares two versions on that snapshot, and saves/replays a pinned investigation. The API routes and persistence are in `services/api/app.py` and `store.py`; the restricted parser, mapping validator, and reconciliation are in `packages/metricguard_core/parser.py`, `v2_contract.py`, `v2_binding.py`, and `engine.py`. The trusted worker in `services/supervisor/worker.py` runs a fixed Docker image and validates its outputs. The [API contract](../contracts.md) specifies field and error shapes.

In **isolated mode**, the live local app accepts supported user files and SQL. In **demo mode**, only seeded, precomputed synthetic comparisons execute. The local browser and health endpoint were checked on 2026-09-24 at `http://127.0.0.1:8001/`: the guided page rendered in isolated mode and health reported an available runner. This confirms a running local surface, not an end-to-end real-case result. Existing [v2 release evidence](../v2-release-evidence.md) records synthetic automated and live-Docker checks; V2-06 real-case acceptance remains open.

The flow requires one flat table with nine mapped roles: row and customer identifiers; order and payment `DATE` fields; order, payment and currency codes; and `DECIMAL(18,2)` order/refund amounts. Every role needs a distinct source column. A source may have extra columns, but they are not executable roles. Input is at most 25 MiB and one million rows. CSV NULL tokens or Parquet native NULLs must match the approved contract. The runner rejects duplicate row keys, incompatible types/currencies, and changed or missing context. It does not convert currencies, timestamps, timezones, or grain. SQL supports one scalar `SUM`, `COUNT(*)`, or `COUNT(DISTINCT customer_id)` with a restricted expression/predicate grammar; joins, CTEs, groups, windows, ratios, arbitrary fields, and native DAX are unsupported. Read [the contract](../contracts.md) before preparing an extract.

The UI shows a bounded preview with representative values, mapping names and hashes, import/run status, explicit support reasons, comparison period/unit, target-minus-baseline sign, ordered rule steps, overall endpoint evidence, limitations, and a saved case. A changed comparison configuration is flagged on the full result page. The rule bridge is order-dependent and is not a causal or policy judgment. The row-evidence table shows **overall endpoint evidence**, not contributions to each bridge step. Distinct-count evidence describes customer membership, not one additive monetary contribution per order. Provenance is available without being the primary explanation.

### Important distinction for a real case

The current result stores **MetricGuard's reproduced baseline and target values on the selected snapshot**, plus their reproduced gap. It does not capture the two values originally shown in the reports or calculate each original-versus-reproduced endpoint difference. A bridge with zero residual only reconciles the two reproduced definitions. It does **not** establish that the original business discrepancy was reproduced.

For the pilot, keep both original report values in the intake and measurement sheet. Confirm unit, period, scope, filters, refresh/cutoff, grain and source versions before comparing. For each endpoint independently calculate `reproduced value − original reported value`; do not treat equal gaps as sufficient. If either original value or its context is missing/incompatible, mark that endpoint **not comparable**, state why, and avoid an “explained original discrepancy” claim. Do not call the difference between the two gaps an unexplained amount unless the four endpoints and contexts are genuinely comparable. A future bounded UI addition could store optional original values and compatibility declarations beside a pinned case, then show these two endpoint checks. Pilot evidence should determine whether that workflow is worth building; the manual check is sufficient to avoid a misleading first pilot result.

## Prioritized readiness findings

| Priority | Finding | Evidence and pilot response |
| --- | --- | --- |
| Verified gate | No qualifying real case, external expected totals, or second-analyst observation exists. | [V2-06 evidence](../v2-release-evidence.md); obtain case 1 and independent oracle before making usefulness claims. |
| Verified fit blocker when present | A case requiring unavailable fields, joins, ratios, mixed units, timestamp conversion, or a broader grain cannot run as-is. | `v2_contract.py`, `parser.py`, [API contract](../contracts.md); record refusal and preparation cost. Do not rewrite business meaning silently to fit. |
| Verified interpretation gap | Original report endpoints are absent from the result model. | `services/api/models.py`, `packages/metricguard_core/engine.py`, `apps/web/src/V2Investigations.tsx`; use the endpoint worksheet below. |
| Verified clarity defects, corrected | Result cards did not identify reproduced values; distinct-count evidence was labeled order-level; unsupported SQL cleared the guided editor. | `apps/web/src/main.tsx`, `V2Investigations.tsx`; copy now identifies reproduced totals and evidence unit, and a refused definition leaves the draft visible. Check with an analyst after rebuild. |
| Untested UX hypothesis | Nine-role preparation or SQL rewriting may cost more effort than the evidence saves. | Measure the analyst's existing workflow and the whole MetricGuard workflow, including review/handoff. |
| Untested UX hypothesis | Bounded preview, support errors, or saved-case navigation may still confuse a first-time analyst. | Observe unaided attempts, recovery, and reviewer understanding; avoid redesign before seeing this. |

These are local pilot findings, not a security or production-readiness review. The existing supervisor is for trusted local use and must not be exposed as a public upload/execution service.

## Case sequence and exact local workflow

Use three **proposed** cases, without inventing their data or outcomes: (1) a previously resolved discrepancy, withholding the known explanation from the investigator where practical; (2) a separately sourced case that tests different assumptions; (3) a current discrepancy observed through handoff as it unfolds. For **each** case, write down the expected endpoint values or independent calculation, essential business conditions, and benefit being tested *before* running MetricGuard. A retrospective oracle or participant time is labeled as an estimate. Do not let a known explanation leak into the first investigator's process.

Start from the repository root on Windows with Docker Desktop running. Install dependencies if needed using [the README](../../README.md), then run:

```powershell
npm run build --prefix apps/web
docker build -f infra/Dockerfile.runner -t metricguard-runner:1.0 .
$env:METRICGUARD_MODE = 'isolated'
$env:METRICGUARD_DATA_DIR = (Join-Path (Get-Location) 'data/pilot-workspace')
New-Item -ItemType Directory -Force -Path $env:METRICGUARD_DATA_DIR | Out-Null
$pilotDb = (Join-Path $env:METRICGUARD_DATA_DIR 'metricguard.db').Replace('\','/')
$env:METRICGUARD_DATABASE_URL = "sqlite:///$pilotDb"
.\.venv\Scripts\python -m uvicorn services.api.app:app --host 127.0.0.1 --port 8001
```

In a **second** PowerShell terminal at the repository root, use the same data directory and start the supervisor:

```powershell
$env:METRICGUARD_DATA_DIR = (Join-Path (Get-Location) 'data/pilot-workspace')
$pilotDb = (Join-Path $env:METRICGUARD_DATA_DIR 'metricguard.db').Replace('\','/')
$env:METRICGUARD_DATABASE_URL = "sqlite:///$pilotDb"
.\.venv\Scripts\python -m services.supervisor.worker
```

Set both variables in both terminals as shown; the worker's default database URL does not derive from `METRICGUARD_DATA_DIR`. Do not delete an existing pilot directory: its snapshots and saved cases are evidence. Confirm [health](http://127.0.0.1:8001/api/v1/health) reports `mode: isolated` and `runner_available: true`, then open [the local app](http://127.0.0.1:8001/) and choose **Guided investigations**.

After observing the existing process, work through the UI: approve the contract; choose and preview the permitted extract; check sample values and explicitly map all nine roles; validate the snapshot; bind the two actual definitions with their business-date roles; select matching versions, period, unit and relationship; run; inspect both reproduced endpoints, gap, ordered bridge, limitations and paged evidence; perform the manual original-endpoint checks; save the case and reopen/replay it. If an operation is unsupported, retain the original definition and log the error and workaround rather than silently changing its meaning. Run the reviewer handoff with the saved case and the endpoint worksheet.

Record all active work, waiting, builder assistance, errors, and reviewer effort in [the measurement sheet](measurement-sheet.md). At the end of each case, transfer the observations to [the decision record](decision-record.md). Keep source extracts local and use only data whose use for the pilot is permitted.

## Endpoint worksheet (copy per case)

| Endpoint | Original report value and source | Reproduced value and run ID | Reproduced minus original | Unit/period/scope/cutoff compatible? | Conclusion or reason not comparable |
| --- | --- | --- | --- | --- | --- |
| Baseline |  |  |  |  |  |
| Target |  |  |  |  |  |

Record the original reported gap separately, then compare it with MetricGuard's **reproduced target minus baseline** gap only after both endpoints are comparable. A zero bridge residual answers a different question.
