# MetricGuard v1 workflow design

This work closes three local-workflow gaps from the planning pack: browser CSV imports through the isolated runner, deterministic definition candidates, and an explicit fixed-data version replay. The supported orders schema and restricted SQL grammar remain unchanged.

An isolated-mode upload writes bounded bytes to a server-generated staging path and creates a durable import record. The API never parses the CSV. The supervisor claims imports with a lease, calls the existing restricted ingestion runner, verifies its manifest and artifact, then publishes a validated immutable snapshot. Queued, running and failed imports remain visible; only validated snapshots can be compared. Demo mode continues to reject a new dataset and may reuse an identical validated one.

Candidate ranking uses only latest executable typed definitions. It reports heuristic points and reasons for shared aggregate, date concept, amount source, shared rule columns and name terms, with an explicit variant warning for gross/net/booked/recognized differences. It never asserts equality or approves a pair. A recorded human review supersedes the candidate state.

The history view selects an older and newer version of one definition plus one frozen snapshot and reporting context. It routes through the normal comparison validation and execution; the result and export retain both immutable version IDs, snapshot hash and context. `observed_at` means when this workspace saw each version. Optional `source_changed_at` and `effective_from` are analyst-declared dates; no source-change date is inferred.

Acceptance: a valid upload moves queued → validated with a selectable snapshot; malformed CSV moves queued → failed without a snapshot; repeated bytes and metadata reuse the validated snapshot; a lost worker lease is recoverable; candidates are stable and explained; replay is tied to one immutable snapshot and shows a numeric delta only when the normal comparison succeeds.
