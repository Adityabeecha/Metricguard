# 11 Sources and glossary

## 11.1 Research notes

Sources below were reviewed on 22 September 2026. They establish the capabilities and limitations used in the design. They do not validate MetricGuard's commercial demand, uniqueness, accuracy, performance or delivery estimates. All product requirements and engineering defaults in this pack are proposed decisions.

**S1 dbt Semantic Layer FAQs.** The relevant documented behavior is centralized metric definition and querying with MetricFlow. This supports treating a semantic layer as an adjacent solution rather than claiming the metric-governance problem is untouched.

https://docs.getdbt.com/docs/use-dbt-semantic-layer/sl-faqs

**S2 Soda documentation.** Observability monitors data and metric behavior, while contracts capture declared expectations. These are relevant adjacent capabilities; this review does not establish the absence of a particular reconciliation feature.

https://docs.soda.io/data-observability

https://soda.io/product/data-contracts

**S3 Datafold Monitors as Code.** Data diff monitors compare datasets, and metric monitors observe standard or custom metrics. Dataset diffing therefore belongs in the competitive context.

https://docs.datafold.com/data-monitoring/monitors-as-code

**S4 SQLGlot documentation and semantic diff.** SQLGlot exposes parsing, expression inspection and AST differences. Its documentation describes best-effort transpilation and a strict unsupported mode. MetricGuard's business-level comparability and validated IR are additional application logic.

https://sqlglot.com/sqlglot.html

https://sqlglot.com/sqlglot/diff.html

**S5 Microsoft TMDL documentation.** TMDL and PBIP can expose semantic model metadata. This supports a future metadata importer and does not establish that a measure can be evaluated from its text alone.

https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-tmdl-view

**S6 Microsoft DAX glossary.** Filter context includes filters directly applied to model columns and filters propagated through relationships. This is why the first release does not promise generic DAX-to-SQL equivalence.

https://learn.microsoft.com/en-us/dax/dax-glossary

**S7 dbt manifest reference.** The manifest describes project resources and dependency maps. Compiled query content is conditional rather than guaranteed for all nodes; artifact schema handling must be version-aware.

https://docs.getdbt.com/reference/artifacts/manifest-json

**S8 DuckDB security guidance.** Untrusted SQL requires proper isolation. Configuration limits provide additional restrictions but do not replace a sandbox.

https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview

The Dview article cited in the starting concept was also reviewed as a discussion of the problem. It is not used as proof of a unique market gap or as a technical execution specification.

https://dview.io/blogs/semantic-drift-why-your-metrics-change-without-warning

## 11.2 Glossary

| Term | Meaning in this project |
| --- | --- |
| KPI | A business measure used to assess performance |
| Reconciliation | Explaining how two comparable calculations produce different results |
| Metric family | A reviewed grouping of related business measures |
| Definition version | An immutable snapshot of one metric implementation |
| Semantic drift | A change in intended or implemented metric meaning over time |
| IR | Typed intermediate representation of the supported calculation rules |
| AST | The syntax tree produced by a parser from SQL text |
| Grain | What one row represents, such as one order |
| Snapshot | Frozen analytical data with recorded identity and context |
| Comparability | Whether the data and context support a fair comparison |
| Ordered bridge | A sequence of rule changes whose signed effects connect the endpoints |
| Residual | The part of the observed gap not accounted for by the reported bridge |
| Shapley allocation | An attribution convention that averages marginal effects across rule orders |
| Lineage | Recorded dependencies between data, definitions and reports |
| Canonical baseline | An approved definition for a declared purpose and scope |
| Idempotency | Repeating an equivalent request does not create duplicate work |
| Golden fixture | A small fixed example with independently established expected results |
| Abstention | An explicit statement that the system cannot support a conclusion |

## 11.3 Preimplementation completion criteria

This pack covers the problem, target users, competitive context, scope, acceptance criteria, journeys, screens, SQL policy, metric contracts, calculation method, architecture, storage, APIs, security, demo data, testing, delivery sequence, risks and coding handoff. Implementation can begin with the bounded feasibility milestone. Real customer validation and measured engineering performance remain open work, not prerequisites that require inventing answers.
