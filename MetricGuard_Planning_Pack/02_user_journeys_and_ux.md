# 2 User journeys and screen specifications

## 2.1 Primary investigation

An analyst opens a project and imports a dataset. The application shows the schema, row count, grain and validation failures. After confirming the business metadata, the analyst imports existing SQL definitions. MetricGuard suggests related definitions; the analyst decides whether they are expected to agree, intentional variants or unrelated.

The analyst selects two versions, chooses a common reporting period and runs the comparison. The result opens with the observed difference, definition differences and evidence coverage. The analyst can inspect the contributing rows and the ordered rule bridge, then record an explanation or an approved scoped definition. Exporting the report preserves the evidence without changing any source system.

A canonical definition is optional at the start. Existing logic is enough to begin; a reviewed baseline is a possible outcome of the investigation. The first-run form therefore asks for context needed to interpret the logic, not a complete semantic model written from scratch.

## 2.2 Six screens

| Screen | Main content | Primary action | Important states |
| --- | --- | --- | --- |
| Project overview | Imports, reviewed pairs, open cases, definition changes and coverage | Start investigation | Empty project; imports incomplete; no open cases |
| Import and validate | Dataset schema, grain, source metadata, parse support and missing context | Confirm import | Rejected file; duplicate key; unknown currency; unsupported query |
| Metric inventory | Definition name, source, family, version, support and owner | Review suggested pair | Candidate; intentional variant; rejected match |
| Comparison workspace | Context, two definitions, totals, differences and explanation tabs | Run comparison | Needs context; queued; running; partial; failed; complete |
| Definition history | Version timeline, structural changes, same-snapshot replay and recorded decisions | Compare versions | First import; cosmetic change; material change; approved change |
| Baseline and evidence | Scoped definition, known consumers, rationale and exported evidence | Record decision or export | Draft; approved locally; superseded; replay unavailable |

## 2.3 Comparison workspace detail

The top line identifies the metric family, the exact two definition versions and the context. For example: Sales reporting, August 2026, INR, order grain, synthetic snapshot demo01. Below it, show Dashboard A at INR 104000000.00, Finance at INR 98000000.00, and Target minus baseline at INR -6000000.00.

Present four tabs: Definition differences, Value bridge, Evidence rows, and Provenance. Definition differences compares amount formula, business predicate, time field, missing-value behavior and scope. The value bridge shows the ordered steps with signed amounts. Evidence rows provide a paginated contribution table and can be sorted by absolute impact. Provenance provides version, snapshot and engine information for reviewers who need it.

The explanation should read naturally: "On the imported August snapshot, the finance definition is INR 6000000 lower. Applying refund treatment, then payment eligibility, then payment date explains the observed gap. This bridge uses that declared order." Every sentence containing a numeric result must be generated from the stored result, not composed from memory or an LLM estimate.

## 2.4 Status model

Use separate status fields instead of one red or green badge. A metric can have a definition difference while the currently observed values match.

| Dimension | Values | Meaning |
| --- | --- | --- |
| Relationship | Candidate, expected agreement, intentional variant, unrelated | Human interpretation of business purpose |
| Context | Comparable, needs context, not comparable | Whether a fair replay can run |
| Definition | Same supported representation, different, unknown | Structural assessment within the declared support boundary |
| Value | Equal on snapshot, within tolerance, different, not run | Observed numeric assessment |
| Explanation | Complete ordered bridge, partial, row evidence only, unavailable | What the evidence supports |
| Review | Open, reviewed, baseline recorded, superseded | Workflow state, not proof of correctness |

Do not label a candidate as a conflict until the relationship is expected agreement and the context is comparable. Intentional variants can still be compared for understanding but do not trigger an inconsistency alert. Unknown and unsupported require text labels and cannot be shown as passing checks.

## 2.5 Worked acceptance journeys

**US01 Investigate revenue.** Given the eight-row fixture and definitions A and Finance, when the analyst confirms a comparable context, the system returns INR -6000000.00 and exposes the five baseline-included orders. Four rows have nonzero contributions. The bridge and row totals independently reconcile.

**US02 Avoid a false match.** Given Net Sales and Recognized Revenue as names, a name suggestion may appear. The user must choose their relationship. Without confirming purpose and scope, the system cannot declare a conflict or pick one as correct.

**US03 Detect a hidden definition difference.** Given two different predicates and a dataset containing only completed orders, values may match. The result still shows the predicate difference and the label Equal on snapshot.

**US04 Explain why a replay is blocked.** Given an unknown timezone or missing date field, the comparison page lists the missing context with an edit action. It does not produce a numeric impact.

**US05 Review a change.** Given an imported new version that changes order date to payment date, the history page shows the old and new logic, when the change was observed, and a replay on the same dataset. It does not infer when a production report first changed unless source history provides that time.

**US06 Work without an AI service.** The entire investigation, export and baseline flow must work offline using deterministic suggestions and explanation templates.

## 2.6 Design and accessibility

Use a restrained analytics interface with a light background, clear tabular numbers and direct actions. Use INR in exports and allow Indian digit grouping in the UI. Keep the unrounded amount available beside compact crore or lakh formatting. Avoid animated counters and an overall trust percentage.

Use a waterfall for the additive value bridge and a table beside it containing every exact value. Use line charts only for genuine replay periods, not invented historical values. Color is supplementary: include status text and icons. Keyboard focus, labeled form controls, sufficient contrast, useful error messages and a readable narrow-screen layout are part of completion.

The overview's coverage should say "18 of 24 imported definitions executable" rather than imply all company reports were checked. Report impact should say "3 registered consumers potentially affected" when the graph contains only manually registered dependencies.

## 2.7 Errors and incomplete investigations

An upload that fails structural validation remains visible with its error and cannot become a dataset snapshot. A canceled run has a terminal canceled state. A timed-out run retains completed sub-results only as partial evidence. A missing original snapshot makes a previous result viewable but not replayable. If a user changes context, any displayed previous result must remain labeled with its original context or be cleared.

An export should contain the same status and limitations as the screen. Never transform partial findings into a definitive summary for presentation. A locally recorded owner name is a self-declared record, not authenticated sign-off.
