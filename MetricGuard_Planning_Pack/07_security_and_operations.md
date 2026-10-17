# 7 Security and operational requirements

## 7.1 Trust boundaries

The MVP is a local single-user application for imported extracts and synthetic demonstrations. Uploaded files, SQL, descriptions, filenames and any future model output are untrusted inputs. The API, parser, supervisor and runner image are trusted application components. The runner receives no production credentials and has no network access.

Do not rely on a SELECT-only string check. A SELECT can call functions that read files or perform expensive work. Enforce a typed allowlist across the whole parsed structure, registered table references and approved expressions, then regenerate executable SQL from the IR. Bind period values separately and quote validated identifiers through the SQL library.

DuckDB settings can reduce capabilities but are not a complete sandbox [S8]. Apply external-access restrictions, disable extension installation and autoloading, lock configuration after setup, and open the prepared database read-only. Also restrict the process at the container and host level. Do not claim read-only access alone prevents file reads or resource exhaustion.

## 7.2 Initial limits

These are proposed conservative defaults to validate during the engineering spike. They are not measured scale claims.

| Resource | Proposed initial limit | Failure behavior |
| --- | --- | --- |
| Uploaded analytical file | 25 MiB | Reject before ingestion; explain the configured limit |
| Imported definitions per project | 100 | Ask user to remove or split the import |
| SQL definition length | 50000 characters | Reject before parse |
| Snapshot rows | 1000000 | Reject or request a smaller extract; no silent sampling |
| Analytical concurrency | One job | Queue additional jobs |
| Per-query wall time | 15 seconds | Terminate execution and mark partial or failed |
| Total run wall time | 60 seconds | Stop remaining work and preserve honest status |
| Runner memory | 512 MiB DuckDB target within a 1 GiB container limit | Terminate on hard limit; avoid claiming engine limit covers all memory |
| Output rows displayed | 100 per page | Cursor pagination |
| Evidence export size | 100000 rows or 25 MiB | Offer aggregate evidence and mark full-row export limited |
| Rule groups in ordered bridge | Five | Decline more complex attribution for MVP |

The normal performance target of 30 seconds applies to the standard 100000-row fixture under the named development hardware. The larger accepted maximum is a hard admission limit, not a promise of the same latency. Record query count and total runtime so expensive decompositions remain visible.

## 7.3 File and runner controls

Use generated filenames and a managed root, not user-supplied paths. Check MIME and extension against actual parser behavior. Reject archive uploads initially to avoid path traversal and decompression complexity. CSV and Parquet readers need type, size and structural validation. Malformed files should be isolated during ingestion just like queries during execution.

Run the fixed analytical image as a non-root user with a read-only root filesystem, dropped capabilities, a bounded temporary directory, process and CPU limits, and no network. Mount only that job's snapshot read-only and a dedicated output directory with a quota or equivalent host-enforced bound. The supervisor must validate result size and schema before reading it into application memory.

The local supervisor can use container launch privileges but has no remotely exposed arbitrary-command interface. Its launch template, image digest and mount roots are application-controlled. If this isolation cannot be reliably provided on the development machine, use the precomputed demo mode until the runner is fixed; do not quietly run uploaded SQL in the API process.

## 7.4 Data minimization and exports

Use synthetic order and customer IDs for the portfolio. Real names, emails and payment details are unnecessary for reconciliation. Row evidence defaults to IDs, membership flags and numerical contributions. An explicit user choice is required before adding other columns to an evidence export.

Do not send source records or SQL literals to an AI provider in the MVP. If an optional metadata matcher is later enabled, document what fields leave the machine and provide an offline alternative. Descriptions and comments remain data even when they contain instructions addressed to a model.

Escape HTML in displayed source text and reports. Make spreadsheet-compatible CSV exports safe from formula injection by treating untrusted text beginning with formula characters as text. Keep the exact machine-readable evidence separately so display escaping does not change underlying values.

## 7.5 Authentication and review

The local version uses a self-declared owner label and loopback-only access. It should say Recorded locally instead of Authenticated approval. Before a network-accessible private pilot, add identity, project-level authorization on every object, protection against cross-project references, secure sessions and download authorization.

Reader, analyst and owner roles are a later deployment concern. Readers can view approved evidence, analysts can import and investigate, owners can record scoped baselines. Do not invent an enterprise compliance badge or claim an audit log is tamper-proof; the local database administrator can modify it.

## 7.6 Retention and recovery

Default local retention can preserve snapshots and evidence until the project owner deletes them. Show disk usage and allow deleting a project or snapshot with clear dependent-run consequences. A private pilot should define a stated retention period with its users rather than inherit unlimited retention accidentally.

Back up metadata and artifact files together with a manifest of hashes. Test one restore before using real pilot data. A database-only backup cannot reproduce runs whose snapshots were lost. Clean abandoned temporary run directories after a defined interval while retaining accepted result artifacts.

## 7.7 Operational runbook

For a stuck job, inspect the worker heartbeat and lease, terminate the expired runner if it still exists, and requeue only through an explicit new attempt. For a corrupted snapshot, block new runs and ask for reimport; preserve existing reports with an integrity warning. For a parser upgrade, rerun the regression corpus before changing the policy version used for new analyses.

Monitor run states, failure codes, parse coverage, context-blocked cases, queue wait, execution time, output size and reconciliation failures. Avoid raw-data logging. A successful API health check says nothing about whether the analytical runner can complete a job, so include a bounded synthetic health fixture in operational checks when needed.
