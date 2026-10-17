# MetricGuard Headless CLI & CI Automation Guide

`metricguard-ci` (`python -m services.cli compare`) provides a deterministic, headless verification interface for CI/CD pipelines, pre-commit validation, and pull-request comments. It enforces strict exit code semantics, exact decimal precision, credential redaction, and revision-aware dbt lineage preservation.

---

## 1. Command Syntax & Options

```bash
python -m services.cli compare [OPTIONS]
```

### Options

| Option | Environment Variable | Required | Description |
| :--- | :--- | :--- | :--- |
| `--project-id`, `--project` | `METRICGUARD_PROJECT_ID` | Yes | Target project ID |
| `--baseline` | — | Yes | Baseline definition version ID or dbt resource selector (`dbt:<unique_id>` or `<uuid>`) |
| `--target` | — | Yes | Target definition version ID or dbt resource selector (`dbt:<unique_id>` or `<uuid>`) |
| `--snapshot` | — | Yes | Snapshot ID or selector (`<uuid>` or `snapshot:<uuid>`) |
| `--mode` | — | No | Execution mode: `advisory` (default, drift exits 0) or `enforcing` (material drift exits 1) |
| `--absolute-threshold` | — | No | Optional absolute delta threshold for materiality evaluation (e.g. `50.0`) |
| `--relative-threshold` | — | No | Optional relative delta threshold for materiality evaluation (e.g. `0.02` for 2%) |
| `--materiality-policy` | — | No | Path to JSON file or raw JSON string defining materiality policy |
| `--relationship` | — | No | Definition relationship: `candidate` (default), `expected_agreement`, `intentional_variant`, or `unrelated` |
| `--context`, `--context-file`, `--context-json` | — | No | Comparison context as JSON string or path to JSON file |
| `--output`, `--json-output` | — | No | File path to write machine-readable `metricguard_ci_report_v1` JSON report |
| `--markdown-output`, `--md-output`, `--markdown` | — | No | File path to write human-readable Markdown report (suitable for CI artifacts and PR comments) |
| `--format` | — | No | Terminal output format: `summary` (default ASCII table), `json` (stdout JSON), or `quiet` |
| `--json` | — | No | Shorthand for `--format json` |
| `--quiet`, `-q` | — | No | Shorthand for `--format quiet` |
| `--data-dir` | `METRICGUARD_DATA_DIR` | No | Data directory path (defaults to `data/`) |
| `--database-url` | `METRICGUARD_DATABASE_URL` | No | Store SQLite database path |

---

## 2. Authentication & Authorization

CI callers authenticate via environment variables:
- `METRICGUARD_API_KEY`: API key for an authorized user.
- `METRICGUARD_PROJECT_ID`: Project identifier.

**Role Enforcement:** The user must possess at least `analyst` or `owner` role on the specified project. Users with only the `reader` role or without project membership are refused with operational failure (exit code `3`).

**Credential Redaction:** API keys, database connection strings, passwords, and bearer tokens are automatically redacted across all CLI output streams (stdout, stderr, terminal summary, JSON report, and Markdown report).

---

## 3. Strict Exit Codes

MetricGuard enforces four mutually exclusive exit codes to ensure deterministic CI pipeline orchestration:

| Exit Code | Classification | Meaning | Pipeline Action |
| :---: | :--- | :--- | :--- |
| **`0`** | **Agreement / Advisory Drift** | Quantitative analysis completed. In `advisory` mode, material drift still exits 0 (reported in JSON/Markdown). In `enforcing` mode, definitions agree within materiality thresholds. | Pass / Green |
| **`1`** | **Enforcing Material Drift** | Quantitative analysis completed in `enforcing` mode and detected material drift exceeding absolute or relative thresholds. | Block / Red |
| **`2`** | **Domain Refusal** | MetricGuard refused quantitative comparison due to unsupported SQL syntax (CTEs, multi-table JOINs, subqueries), uncompiled Jinja macros, or dbt resources classified as `metadata_only`. Refusal applies in **both** `advisory` and `enforcing` modes. | Investigate / Review |
| **`3`** | **Operational Failure** | Operational error preventing execution: missing snapshot file, corrupted database, authentication failure, unauthorized role, argument error, or invalid output directory. | Fix Infra / Config |

> [!IMPORTANT]
> Advisory mode **never** converts an unsupported analysis (exit code 2) into a success (exit code 0). Refusals are strictly distinguished from agreements.

---

## 4. Output Formats

### 4.1 ASCII Terminal Summary (`--format summary`)
Clean 80-column monospace summary displaying comparison identity, metric values, delta, materiality verdict, row evidence summary, and top bridge steps:

```
================================================================================
METRICGUARD HEADLESS COMPARISON REPORT
================================================================================
Project:          my-project
Status:           SUPPORTED
Materiality:      MATERIAL_DRIFT
Execution Mode:   enforcing
Final CI State:   FAILED (Material drift detected in enforcing mode)
Exit Code:        1
--------------------------------------------------------------------------------
COMPARISON IDENTITY
--------------------------------------------------------------------------------
Baseline:         def-ver-1
Target:           def-ver-2
Snapshot:         snap-001
...
```

### 4.2 Machine-Readable JSON Report (`--output report.json`)
Conforms to the versioned `metricguard_ci_report_v1` schema with exact decimal string serialization (`baseline_total`, `target_total`, `delta`, `abs_delta`):

```json
{
  "report_schema_version": "metricguard_ci_report_v1",
  "generated_at": "2026-09-25T14:00:00Z",
  "project_id": "my-project",
  "mode": "enforcing",
  "exit_code": 1,
  "status": "supported",
  "baseline_anchor": { ... },
  "target_anchor": { ... },
  "snapshot_anchor": { ... },
  "result": {
    "baseline_total": "100.00",
    "target_total": "150.00",
    "delta": "50.00",
    "abs_delta": "50.00",
    "is_material": true,
    "materiality_verdict": "MATERIAL_DRIFT",
    "evidence_summary": { ... },
    "top_bridge_steps": [ ... ]
  }
}
```

### 4.3 Human-Readable Markdown Report (`--markdown-output report.md`)
Generates structured GitHub-flavored Markdown tables appropriate for CI pull-request review comments and build artifacts. AI narrative is strictly prohibited; all statements are deterministically derived from MetricGuard evidence.

---

## 5. CI / CD Integration Examples

### GitHub Actions
```yaml
name: Metric Drift Verification
on: [pull_request]

jobs:
  verify-metric:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Run MetricGuard Comparison
        env:
          METRICGUARD_API_KEY: ${{ secrets.METRICGUARD_API_KEY }}
          METRICGUARD_PROJECT_ID: ${{ vars.METRICGUARD_PROJECT_ID }}
        run: |
          python -m services.cli compare \
            --baseline dbt:model.orders_v1 \
            --target dbt:model.orders_v2 \
            --snapshot snapshot-prod-latest \
            --mode enforcing \
            --absolute-threshold 10.0 \
            --output metricguard-report.json \
            --markdown-output metricguard-report.md

      - name: Post PR Comment
        if: always()
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            if (fs.existsSync('metricguard-report.md')) {
              const body = fs.readFileSync('metricguard-report.md', 'utf8');
              github.rest.issues.createComment({
                issue_number: context.issue.number,
                owner: context.repo.owner,
                repo: context.repo.repo,
                body: body
              });
            }

---

## 6. Revision-Level Metric Diff (`metricguard diff`)

`metricguard diff` (`python -m services.cli diff`) orchestrates a full revision-level Metric Diff between two dbt import revisions against an immutable snapshot. It combines the V5-01 change-set engine and V5-02 controlled metric-impact engine to resolve all declared metrics affected by changed resources, execute impact comparisons, aggregate analytical refusals, and produce a versioned `metricguard_metric_diff_v1` report.

### 6.1 Command Syntax & Options

```bash
python -m services.cli diff [OPTIONS]
```

| Option | Environment Variable | Required | Description |
| :--- | :--- | :--- | :--- |
| `--project-id`, `--project` | `METRICGUARD_PROJECT_ID` | Yes | Target project ID |
| `--baseline-revision`, `--baseline` | — | Yes | Baseline dbt import revision ID |
| `--target-revision`, `--target` | — | Yes | Target dbt import revision ID |
| `--snapshot` | — | Yes | Reference snapshot ID or selector (`<uuid>` or `snapshot:<uuid>`) |
| `--mode` | — | No | Execution mode: `advisory` (default, drift exits 0) or `enforcing` (material drift exits 1 if no refusals) |
| `--absolute-threshold` | — | No | Optional absolute delta threshold for materiality evaluation (e.g. `50.0`) |
| `--relative-threshold` | — | No | Optional relative delta threshold for materiality evaluation (e.g. `0.02` for 2%) |
| `--materiality-policy` | — | No | Path to JSON file or raw JSON string defining materiality policy |
| `--context`, `--context-file`, `--context-json` | — | No | Comparison context as JSON string or path to JSON file |
| `--output`, `--json-output` | — | No | File path to write machine-readable `metricguard_metric_diff_v1` JSON report |
| `--markdown-output`, `--md-output`, `--markdown` | — | No | File path to write human-readable Markdown report |
| `--format` | — | No | Terminal output format: `summary` (default ASCII table), `json` (stdout JSON), or `quiet` |
| `--json` | — | No | Shorthand for `--format json` |
| `--quiet`, `-q` | — | No | Shorthand for `--format quiet` |
| `--data-dir` | `METRICGUARD_DATA_DIR` | No | Data directory path (defaults to `data/`) |
| `--database-url` | `METRICGUARD_DATABASE_URL` | No | Store SQLite database path |

### 6.2 Deterministic Exit Code Precedence

MetricGuard enforces strict exit code precedence for multi-metric revision diffs:

$$\text{Operational Failure (3)} > \text{Refusal (2)} > \text{Enforcing Material Drift (1)} > \text{Clean / Advisory (0)}$$

| Exit Code | Classification | Meaning |
| :---: | :--- | :--- |
| **`0`** | **Clean / Advisory** | All affected declared metrics are analyzable with no material drift, or all analyzable metrics completed under `advisory` mode with **zero refusals**. |
| **`1`** | **Enforcing Material Drift** | In `enforcing` mode, at least one affected metric experienced material drift and **zero** metrics/resources were refused. |
| **`2`** | **Analytical Refusal** | At least one affected metric or resource was refused (e.g. CTEs, JOINs, snapshot incompatibility). **Refusal strictly takes precedence over materiality.** A mixture of 3 pass, 1 material, and 2 refused exits `2` in both advisory and enforcing modes. |
| **`3`** | **Operational Failure** | Missing snapshot, invalid revision IDs, cross-project access, unauthorized role, or internal error. |

### 6.3 Revision Outcomes

The versioned `metricguard_metric_diff_v1` result and Markdown report categorize revisions into five deterministic outcomes:
1. `completed`: All affected metrics analyzable, zero material drift.
2. `completed_with_material_drift`: All affected metrics analyzable, at least one material drift.
3. `completed_with_refusals`: Some or all metrics/resources refused, zero material drift on analyzed metrics.
4. `completed_with_material_drift_and_refusals`: Both material drift on analyzed metrics and refusals present.
5. `operational_failure`: Operational execution error.

### 6.4 Four Distinct Coverage Views

1. **Structural/Resource Coverage:** Total changed resources, declared metric-linked resources, undeclared changed resources, supported/executable count, refused count, and refusal reasons.
2. **Declared Metric Coverage:** Total affected declared metrics, analyzable count, refused count, and coverage %.
3. **Importance-Weighted Coverage:** Breakdown by explicit declared business importance (`critical`, `important`, `standard`, `unclassified`). Zero heuristic inference.
4. **Observable Structural Signals:** Separate reporting of explicit exposure references and dbt metric references without mixing them into declared importance.

### 6.5 Example CI Invocation

```bash
python -m services.cli diff \
  --project my-project \
  --baseline-revision rev-base-123 \
  --target-revision rev-target-456 \
  --snapshot snap-prod-pinned \
  --mode enforcing \
  --output metric-diff.json \
  --markdown-output metric-diff.md
```
