# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Data analysts and analytics engineers. Their recurring job is to find out, with proof, why two numbers for the same metric disagree: two definitions on the same data (finance revenue vs product revenue, a dbt change before merge), or one definition on two data loads (September vs October). They then write a conclusion that finance, audit or their team will rely on. They are fluent in SQL and spreadsheets, and they work at a desk on a laptop or a large monitor.

## Product Purpose

MetricGuard explains a metric difference record by record and exactly. It freezes the data, runs both calculations in an isolated sandbox, and shows which records account for the change, how it splits by any column, and whether it reproduces on replay. Success means the analyst gets a defensible answer quickly and can hand over a saved investigation that anyone can re-run.

## Positioning

Exactness and proof, not dashboards. Every result reconciles to the cent or as an exact fraction, with zero unexplained difference. Evidence is frozen and hash-verified, and a replay reproduces byte-identical evidence. Unsupported SQL or bad data is refused with a specific reason rather than approximated. A BI tool or a notebook diff cannot truthfully claim all of this together.

## Operating Context

- **Data sources:** CSV, Parquet or Excel (.xlsx) uploads; PostgreSQL, Snowflake or BigQuery tables; two connected tables (a fact table plus lookups).
- **Calculations:** SUM, COUNT, COUNT DISTINCT, AVG, rates, shares and margins, defined with a guided form or SQL.
- **Saved investigations:** a written conclusion, HTML and JSON reports, and replay.
- **Governance:** metric lifecycle, dbt change sets, and a CI gate.
- **Runtime:** runs locally (`scripts\mg.ps1 start`) with Docker; workspaces carry roles (reader, analyst, owner).

## Capabilities and Constraints

- Datasets up to 5,000,000 rows. Comparisons run in an isolated Docker runner; up to four run at once.
- Values are exact decimals or fractions and are never shown as floats. An undefined total is not zero; a lower number is not automatically a loss.
- Every current feature must keep working through any redesign: the guided comparison, the result report (breakdown, record list, CSV), two-load comparison, prepare for large two-table comparisons, investigations, the Changes, Metrics and Datasets pages, and sign-in and workspaces.
- Snowflake and BigQuery have been tested only against stand-in servers.

## Brand Commitments

- The name MetricGuard stays. Nothing else about the current look is binding; the user asked for a free redesign.

## Evidence on Hand

- **Demo data:** `demo/closeout/` (8-row sales orders; a two-table points example).
- **Stress and evaluation records:** `docs/evidence/`.
- **Absences:** there are no customer logos, testimonials, pricing or usage numbers. Do not invent any.

## Product Principles

1. **Answer first.** Lead with the result and what to do next; put explanation one step away, never in front of the task.
2. **Fewer places to look.** One obvious path from data to explained difference to saved investigation.
3. **Ask only what the engine needs.** Infer, prefill and default; every extra field must earn its place.
4. **Never blur exactness.** Exact values, units and honest limits stay visible wherever a number appears.
5. **Trust is shown, not claimed.** Verification, replay and refusals are visible facts, not marketing.

## Accessibility & Inclusion

Keyboard operation and visible focus throughout, and readable contrast in light and dark. Never convey status by colour alone. The layout must stay usable at 375 px width (already enforced by earlier work).
