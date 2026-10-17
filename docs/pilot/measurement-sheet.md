# Whole-workflow observation and measurement sheet

**Case ID:** ___  **Date:** ___  **Observer:** ___  **Analyst role:** ___  **Reviewer role:** ___  **Baseline process observed before app demonstration?** yes / no (why) ___

Use one event log for the existing process and another for MetricGuard. Preserve each process's actual order and loops. **Active effort** is human work time; **elapsed waiting** is time blocked on jobs, access, replies, or handoff. Do not add waiting to active effort. Mark retrospective numbers **estimate**, not measurement. Note concurrent work separately so elapsed wall time is not double counted.

| Workflow (existing / MetricGuard) | Start–end | Actual action, source/tool, and hypothesis | Active min | Waiting min | Already necessary work / added by MetricGuard / uncertain | Assistance, error, unsupported operation, workaround | Observed / estimated |
| --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |

Use the log to capture, where they occur: finding definitions and access; obtaining/sanitizing data; preparing extracts and mapping; rewriting SQL; running and revising comparisons; reading totals, bridge and records; checking original endpoints; discussing findings; writing the explanation; reviewer handoff; reopening/replay. Do not assume the existing process is linear or identical to the app flow. Record builder intervention distinctly from analyst work and specify what the analyst could not do unaided.

## Outcome and correctness

| Question | Observation and evidence |
| --- | --- |
| Were original baseline and target values independently checked, with compatible units, period, scope, filters and cutoff? |  |
| MetricGuard reproduced baseline; original baseline; reproduced minus original |  |
| MetricGuard reproduced target; original target; reproduced minus original |  |
| Reproduced gap; original reported gap; are they comparable? |  |
| Independent oracle and agreement for **each** endpoint; method/source of oracle |  |
| Bridge residual and which steps are only ordered attribution |  |
| Wrong conclusion, unsupported claim, or missing context observed? Who noticed and when? |  |
| Was a usable explanation delivered to the recipient? What evidence did they inspect? |  |
| Could the reviewer explain the result, limitations, and remaining uncertainty without builder help? |  |
| Did a second analyst reopen and reproduce the saved case? Record run IDs and any difference. |  |
| Was the method actually reused for another supported question? Distinguish from stated willingness. |  |

## Effort comparison and notes

**Existing workflow:** active ___ min; waiting ___ min; elapsed ___ min; estimates identified ___

**MetricGuard workflow:** active ___ min; waiting ___ min; elapsed ___ min; builder assistance ___ min; estimates identified ___

**Additional preparation caused by MetricGuard:** ___  **Work already required in either workflow:** ___

**Where the product helped:** ___  **Where it delayed or obscured work:** ___  **Support refusal and workaround cost:** ___

Compare the whole process through reviewer handoff. Query runtime alone is not a usefulness measure. Mixed or negative findings belong in the same record as benefits.
