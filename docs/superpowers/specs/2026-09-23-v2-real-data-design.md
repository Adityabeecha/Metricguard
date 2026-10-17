# MetricGuard v2 local investigation design

The approved v2 direction is the **Real-data investigations** release in `docs/next-versions-roadmap.md`. This design implements its local workflow using synthetic examples until independently sourced sanitized cases are available. The original planning pack remains one v1; old snapshots, definitions, runs and exports retain their v1 interpretation.

## Supported input and policy

An analyst registers an immutable, versioned dataset contract for one flat extract. A contract declares the nine analytical roles used by the existing restricted engine: unique row key, eligible entity key, two business dates, two category fields, two fixed-scale decimal amounts, and currency. It also declares the reporting currency, timezone, grain, scope, cutoff and CSV NULL token policy. Source files may name these columns differently and may contain extra columns; the analyst explicitly maps each role to one source column. A changed contract or mapping creates a new hash and version. No inferred currency conversion, timestamp conversion, joins, grain conversion or silent fill of required roles is permitted.

The v2 ingestion runner, inside the fixed Docker image, checks the source layout and types, rejects duplicate keys and mixed currency, converts the approved mapping to the canonical `orders` table, and records source, contract and mapping hashes in the immutable manifest. The API only stages bytes and records the declared mapping. Snapshot reuse includes all three identities. The old v1 ingesters and their manifests remain valid.

## Definitions and investigations

The existing SQL grammar stays intentionally narrow. A v2 definition may use approved source column names; a bounded AST binder translates those identifiers to canonical analytical roles before `parse_definition` runs. The original SQL, bound SQL, contract hash and mapping hash are saved with the definition version. A comparison refuses versions bound to another contract or mapping and pins the same hashes in its context and provenance. A v1 definition on a v1 snapshot follows its original policy.

A saved investigation groups a snapshot, two immutable definition versions, context, relationship, comparison run and local decision. It records missing-context and unsupported states instead of implying success. Reopening resolves pinned IDs and does not silently substitute latest versions or snapshots. A second analyst on the same local machine can replay the case without editing application code; actor labels remain self-declared, as in v1.

## Evidence and pilot boundary

The isolated runner writes a compact result summary and a separate bounded row-evidence artifact. The supervisor validates artifact size, count, hash and reconciliation before publishing it under a server-owned path. The API pages verified evidence from that artifact and exports a bounded summary with explicit evidence coverage. Existing v1 results that embed evidence remain readable. The browser never loads the whole row set to render one page.

Two synthetic extracts with independently calculated totals exercise the workflow end to end. They are development fixtures, not the v2 release gate. V2-06 requires two independently sourced sanitized real cases, external oracles, measured completion times, support coverage and wrong-conclusion counts; it stays unverified until those inputs exist. Local-only operation remains mandatory; no public upload service is introduced.

## Acceptance and refusal

- A changed mapping or contract changes identity; old snapshots and runs keep their original hashes.
- Wrong column names/types, duplicate keys, mixed currency, unknown identifiers and cross-contract comparisons fail before an accepted run.
- Golden CSV/Parquet v1 behavior and old result/evidence endpoints continue to work.
- Evidence paging reads bounded rows and detects tampering; exports identify any omitted row detail.
- Python tests, frontend build, Compose validation and a live Docker v2 flow pass before claiming the local implementation complete.
