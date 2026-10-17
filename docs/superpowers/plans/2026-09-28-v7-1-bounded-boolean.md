# V7.1 bounded boolean predicates implementation plan

Goal: explicit opt-in flat snapshots supporting typed AND/OR/NOT trees, retaining exact additive evidence and frozen legacy behavior.

Architecture: FlatContract v5 pins predicate_policy=flat-boolean-v1. V3/v4 contracts and IR remain unchanged. A bounded tree module lowers only logical combinators over existing typed leaves, canonicalizes associative AND/OR order without distributive expansion, validates untrusted IR recursively, and compiles parameterized predicates. Existing import/supervisor/run/evidence/save/replay services remain in use. This is an engineering preview authorized by the user; real pilot justification remains open.

Limits: 200 SQL AST nodes (existing); tree depth 8; 63 tree nodes; 16 leaf occurrences; existing 32-value IN and 256-character literals. NOT preserves SQL three-valued logic. No joins, scalar arithmetic, CASE, new measures or aggregate families. Full filter-change attribution only; no per-branch causal attribution or Shapley claim.

- [ ] Freeze documentation to approved V6 receipts, preserving external gates.
- [ ] Implement version-gated contract/tree parser, IR validator and parameterized compiler.
- [ ] Expose explicit upload opt-in and snapshot-specific SQL support guidance using existing controls.
- [ ] Build frontend and runner, refresh Graphify; do not execute tests (user delegates testing to AGY/Flash).
- [ ] Deliver precise Flash acceptance prompt covering truth tables, canonicalization, limits, forged IR, UI workflow/replay and v3/v4 regression.

Files: packages/metricguard_core/flat.py, new flat_boolean.py, services/api/models.py, apps/web/src/api.ts, views/DataSourcesView.tsx, views/InvestigationsView.tsx, docs/project-graph.md, docs/v5-release-evidence.md, new docs/v7-release-evidence.md. No database migration: contracts/IR already use versioned JSON records.
