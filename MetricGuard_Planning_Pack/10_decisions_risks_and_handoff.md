# 10 Decisions risks and coding handoff

## 10.1 Architecture decision record

| ID | Decision | Reason | Revisit when |
| --- | --- | --- | --- |
| ADR01 | SQL first with a declared grammar | Makes evidence and limits testable | Core reconciliation passes its evaluation |
| ADR02 | One common immutable snapshot | Separates logic impact from data changes | A validated cross-source mapping case is available |
| ADR03 | DuckDB for analytics and PostgreSQL for records | Separates calculation from application transactions | Measured operational overhead outweighs the benefit |
| ADR04 | Typed IR regenerated into SQL | Bounded execution and structured differences | A supported construct cannot be expressed faithfully |
| ADR05 | Ordered bridge before Shapley | Small, inspectable and reproducible | Interactions make order sensitivity a practical user problem |
| ADR06 | Human review of metric relationships | Similar names can describe different valid concepts | Strong evidence supports a narrowly bounded automation |
| ADR07 | Deterministic explanation templates | Prevents fabricated causal and numeric statements | A constrained summarizer adds measured usefulness |
| ADR08 | One worker and PostgreSQL job records | Adequate for portfolio workload | Queue measurements justify more concurrency |
| ADR09 | Local deployment first | Keeps data access and operational scope manageable | Real pilot users require remote access |
| ADR10 | No automatic source correction | Owners must decide intended business meaning | A separately approved reviewed-change workflow exists |

## 10.2 Risk register

| Risk | Why it matters | Mitigation or decision |
| --- | --- | --- |
| Scope grows to every BI language | The core engine never reaches a credible result | Publish support levels and phase adapter work |
| Valid variants become false alerts | Users lose confidence quickly | Review intent before calling a definition a conflict |
| Hidden report filters are unavailable | Same formula can produce different visible values | Mark context incomplete; require native BI context later |
| Query transformation changes meaning | Explanation can be numerically neat but wrong | Typed subset, independent fixtures and no best-effort execution |
| Interacting rules double count impact | Percentages can be misleading | Mutually exclusive row partitions and a named ordered bridge |
| Mutable or incomplete snapshots | Replays cannot isolate definition effects | Freeze data, record cutoff and validate scope |
| Untrusted SQL reaches the host | File access or resource exhaustion | Restricted IR plus isolated runner and hard limits |
| A polished demo hides unsupported cases | Portfolio claims become hard to defend | Demonstrate an unsupported and an equal-value case |
| Market gap is overstated | Product differentiation lacks evidence | Interview users and describe tested workflow, not uniqueness |
| Schedule depends on unfamiliar React or infrastructure | A solo build can expand substantially | Budget learning contingency and prioritize the numeric core |

## 10.3 Defaults selected for this plan

No user clarification is required to start the bounded prototype. The default domain is online retail, the first language is DuckDB SQL, the first currency is INR, the demonstration timezone is Asia/Kolkata, and the first deployment is local. The main calculation is additive sales reconciliation, followed by order and customer counts. Source data is synthetic.

These are reversible product choices. Revisit them if an actual user's sanitized example demands a different domain or source. Preserve the core goal of evidence-backed reconciliation rather than adding fashionable tools to the stack.

## 10.4 Questions to resolve through evidence

Ask potential users whether their disputed KPI should agree across reports or serves intentionally different purposes. Determine whether they can obtain source definitions and a comparable extract, whether hidden filters are common, and whether they primarily need a one-off reconciliation or ongoing monitoring. Ask what evidence they would need before changing a metric definition.

During the spike, resolve the acceptable SQL grammar, runner isolation on the target laptop, precision and empty-result handling, and the measurable latency of the standard fixture. Before a real pilot, resolve authentication, access boundaries, retention, data handling and the person responsible for approving a baseline.

## 10.5 Repository plan

Use a single repository containing `apps/web` for the frontend, `services/api` for the HTTP interface, `services/supervisor` for trusted job orchestration, `packages/metricguard_core` for parsing and reconciliation, `schemas` for contracts, `tests` for golden and negative fixtures, `demo` for synthetic data, `docs` for this plan, and `infra` for local packaging.

Keep the analytical core importable independently of FastAPI and React. The first tests should exercise it without a browser or application database. This is a directory plan, not a request to create the implementation during the planning stage.

## 10.6 Initial coding brief

When implementation begins, use the following brief as the first bounded task. The instructions describe intended future work, not actions already performed.

> Build MetricGuard milestone M0 and the smallest part of M1 needed to validate it. Read the product scope, SQL grammar, reconciliation method and test fixture in this pack. Implement only the declared SQL subset for one frozen order dataset. Preserve original SQL, validate the full syntax tree, produce a typed IR, and calculate the three golden endpoint totals using a restricted runner. Independently verify expected values and add negative tests for unsupported syntax, NULL status, empty SUM and file access. Document the exact supported constructs and any blockers. Do not add Power BI, Excel, a warehouse connector, an LLM, automatic source edits or a full dashboard in this milestone. Finish with a reproducible golden calculation, a tested support matrix and the updated decision log.

The next task should implement row evidence and the ordered bridge only after those gates pass. Then add durable records and a minimal comparison screen. This order lets the application grow around a verified result.

## 10.7 Portfolio evidence and accurate claims

After the implementation is complete and verified, a defensible project description could say: "Built a SQL KPI reconciliation tool that compares versioned definitions on frozen datasets, explains additive differences with row evidence, and detects changes in metric logic." Add only the integrations and measured results actually achieved.

Use the synthetic demo as a reproducibility claim, not an assertion of real cost savings. Do not put customer counts, accuracy percentages, enterprise adoption or reduced investigation time on the resume until the corresponding evidence exists. Before implementation, describe MetricGuard as a proposed project.

The project complements an analytical portfolio by demonstrating that you can question the meaning of a KPI, design a fair comparison, calculate its impact and communicate limitations. Its distinct value should come from that work rather than from calling every component an AI agent.
