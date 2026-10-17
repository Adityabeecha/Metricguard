# MetricGuard v1 completion implementation plan

**Goal:** Complete the single v1 planning pack across M0–M5 and MG01–MG26, with measured evidence and explicit conditional/environment limits.

**Architecture:** Seeded evaluation tools reuse the real core while calculating independent oracles. Metadata adapters feed inventory through the existing support policy; no BI-language execution is inferred. Exact bounded Shapley is a separate optional result from the ordered bridge. A read-only Evaluation view presents measured local evidence.

**Tech stack:** Python 3.12, CSV, Decimal, SQLGlot, DuckDB, FastAPI, React/TypeScript, pytest.

**Spec:** `docs/v1-completion-design.md`. Generated data/reports stay in the ignored local `data` directory.

1. Test deterministic scale CSV generation, schema, twelve-month coverage, overwrite refusal and independently checked August totals. Implement `scripts/scale_fixture.py`.
2. Add a checked-in semantic corpus with at least 100 labeled pairs and disjoint concept-family splits. Test `scripts/evaluate_candidates.py` against exact confusion and abstention math; report measured metrics without tuning on holdout.
3. Test `scripts/evaluate_release.py` on a small fixture, including numeric success, failure honesty, bounded output and no source-row/path exposure. Run the standard 100,000-row case, record measured results and investigate any resource limit it reveals.
4. Test `GET /api/v1/evaluation` with missing and valid reports. Add a read-only Evaluation page and build/inspect the frontend.
5. Test the dbt manifest adapter against version/schema variants, compiled/uncompiled nodes, lineage, malformed input and Jinja non-execution. Add bounded local API/UI import paths with metadata-only states.
6. Test the TMDL adapter against supported measure metadata, relationships, unsupported syntax and DAX non-execution. Add bounded local API/UI import paths.
7. Test exact three-group Shapley on independent small truth tables, interaction, undefined hybrid and sum-to-gap invariants. Expose the optional result without changing ordered bridge semantics.
8. Evaluate the optional metadata matcher only after the frozen holdout corpus exists. Add it solely if it improves measured held-out usefulness with the privacy condition satisfied; otherwise record the conditional outcome.
9. Reconcile all 26 backlog items and the release checklist in a v1 matrix. Update README/contracts, run the full regression suite, production build, Compose config and browser smoke tests. State the Docker live-container gate precisely.
