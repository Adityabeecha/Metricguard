# 9 Backlog and delivery roadmap

## 9.1 Effort and sequence

The proposed core build requires approximately 150 to 205 focused hours for one developer who can work with Python, SQL and a basic React application. At 15 to 20 hours per week, that is roughly 8 to 14 weeks before contingency. Add 20 to 30 percent for learning unfamiliar tools and resolving integration issues. These are planning estimates, not delivery promises.

| Milestone | Scope | Estimated hours | Exit evidence |
| --- | --- | --- | --- |
| M0 Feasibility | Golden fixture, grammar decisions, isolated execution spike | 12 to 16 | Independent expected totals and supported-subset decision |
| M1 Core comparison | IR, parser support checker, context and endpoints | 28 to 36 | Golden and unsupported fixtures pass |
| M2 Explanation and history | Row evidence, ordered bridge and version diff | 30 to 40 | Reconciliation invariants and drift replay pass |
| M3 Application layer | Metadata model, jobs, API and artifact persistence | 24 to 32 | Interrupted job recovery and reproducible saved results |
| M4 Analyst interface | Imports, inventory, comparison, history and export | 32 to 44 | Complete user journey without direct database intervention |
| M5 Evaluation and presentation | Regression corpus, performance checks, pilot, docs and recording | 24 to 37 | Evidence pack, limitations and measured benchmark |

Build one vertical investigation before implementing every inventory or settings screen. If the project is too large, retain SUM comparisons, row evidence, the ordered bridge and definition history; defer automated suggestions, COUNT DISTINCT and baseline workflow polish. Do not cut the honesty of unsupported and partial states.

## 9.2 Build backlog

| ID | Priority | Work item | Depends on | Done when |
| --- | --- | --- | --- | --- |
| MG01 | Must | Freeze data dictionary and golden fixture | None | Totals verified independently |
| MG02 | Must | Define typed IR and support policy | MG01 | Every supported syntax maps unambiguously |
| MG03 | Must | Implement parser and schema binding | MG02 | Good and bad definitions have stable outcomes |
| MG04 | Must | Validate and freeze imported snapshots | MG01 | Content hash, schema and key validation recorded |
| MG05 | Must | Build restricted runner | MG02 MG04 | Untrusted scans and resource escapes are blocked in tests |
| MG06 | Must | Validate comparison context | MG03 MG04 | Missing or incompatible context blocks impact |
| MG07 | Must | Compute endpoint values | MG05 MG06 | Golden scalar values match the oracle |
| MG08 | Must | Produce additive row evidence | MG07 | Row differences sum to endpoint gap |
| MG09 | Must | Build ordered rule bridge | MG03 MG07 | Every intermediate stored and residual checked |
| MG10 | Must | Classify version differences | MG03 | Cosmetic, structural and unknown changes separated |
| MG11 | Must | Store immutable versions and runs | MG02 | No edit alters previous evidence |
| MG12 | Must | Add durable jobs and recovery | MG05 MG11 | Duplicate requests and dead workers handled |
| MG13 | Must | Expose typed API contracts | MG06 MG11 MG12 | UI contract agrees with server validation |
| MG14 | Must | Build comparison screen | MG08 MG09 MG13 | User can inspect exact gap and evidence |
| MG15 | Must | Build import and inventory screens | MG03 MG04 MG13 | Support and coverage are explicit |
| MG16 | Must | Build history and fixed-data replay | MG10 MG11 MG13 | Definition changes replay on fixed data |
| MG17 | Should | Add deterministic candidate ranking | MG03 MG11 | Evaluated suggestions with manual review |
| MG18 | Should | Add scoped baseline decisions | MG11 MG13 | Approval points to immutable version and scope |
| MG19 | Should | Register report dependencies | MG11 MG13 | Known consumer list shows evidence and coverage |
| MG20 | Must | Export evidence and provenance | MG08 MG09 MG11 | Report and JSON retain all limits |
| MG21 | Must | Complete regression and end-to-end tests | Core milestones | Defined release gates pass |
| MG22 | Must | Prepare demo and documentation | MG14 MG16 MG20 MG21 | Another person reproduces the golden investigation |
| MG23 | Later | dbt manifest metadata adapter | Stable core | Versioned artifacts import without executing macros |
| MG24 | Later | Power BI TMDL metadata adapter | Stable core | Import capability clearly separated from execution |
| MG25 | Later | Exact bounded Shapley allocation | MG09 | All combinations valid and results tested |
| MG26 | Later | Optional metadata embeddings | MG17 | Improves held-out evaluation over baseline |

"Must" describes the full proposed MVP. The reduced first demonstration may omit Should items and COUNT DISTINCT support while explicitly reporting its smaller capability set. Later items must not be quietly bundled into a first coding prompt.

## 9.3 First engineering spike

Before building application screens, prove four things. First, the parser can reliably distinguish the three permitted rule groups and reject unsupported clauses. Second, generated execution queries preserve the tested NULL and decimal behavior. Third, the chosen local environment can start and terminate an isolated runner with no network. Fourth, row contributions and the bridge agree with the independent golden calculation.

Timebox the spike to M0. If arbitrary imported SQL cannot be decomposed reliably, reduce the grammar and explain its support boundary. Do not compensate with an LLM that guesses the missing logic. If isolation is not ready, keep the user interface in precomputed demonstration mode until it is.

## 9.4 Definition of done

Each feature has documented inputs, failure states and a tested acceptance condition. Numeric features include independent expected results or invariants. A feature involving a new query construct includes a negative case and a counterexample for meaning-preserving transformations. UI work includes accessible errors and retains context when a run finishes.

The MVP is done when a fresh local setup reproduces the golden demo, unsupported queries cannot masquerade as successful comparisons, complete bridges reconcile, version history is immutable, jobs survive interruption, and a reviewer can trace every displayed amount to evidence. Do not make broad enterprise scale, security or savings claims from that milestone.

## 9.5 Release checklist

1. Lock runtime and package versions and record the revision used for results.
2. Run the focused regression corpus and the end-to-end golden investigation.
3. Verify snapshot, query and context hashes appear in exports.
4. Test cancellation, timeout, duplicate request and worker-restart behavior.
5. Check that unsupported, undefined and partial results remain explicit everywhere.
6. Inspect the six screens using the smallest supported layout and keyboard navigation.
7. Publish the data dictionary, support matrix, benchmark method and known limits.
8. Record the demo using synthetic data and state which integrations are actually implemented.

## 9.6 Budget assumptions

The local deterministic MVP requires no paid LLM API or managed warehouse. It uses the developer's machine, local storage and an open-source software stack; existing internet, hardware and electricity costs still apply. Check the current license of each dependency before distributing the project.

Hosted cost depends on active runner minutes, stored snapshot bytes, database service choice, backups and outbound downloads. Model it from measured workload instead of choosing an arbitrary monthly price. If an AI matcher is later added, estimate cost from imported metadata count and model token usage, with a hard spending limit and caching.
