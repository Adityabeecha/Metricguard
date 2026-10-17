# 3 Metric contract and SQL support

## 3.1 Definitions and context

A metric definition is a versioned implementation. A metric family is a reviewed grouping of related business concepts. A comparison context supplies the exact period, snapshot and interpretation needed to execute a definition. A baseline is an approved definition for a particular purpose and scope. Keep these objects separate.

The normalized representation, called the metric IR, is a typed intermediate representation of the supported SQL. It retains the aggregate, amount expression, eligibility predicate and time predicate as separate components. It is a restricted execution description, not an attempt to capture every analytical language.

| Field group | Required information | Handling of missing information |
| --- | --- | --- |
| Identity | Definition ID, immutable version, source label, original text and declared dialect | Reject an import without text or dialect |
| Meaning | Display name, purpose, family suggestion and owner label | Meaning remains unreviewed until confirmed |
| Population | Table, row grain, business predicate and scope | Block impact if grain or scope is unknown |
| Calculation | Aggregate, typed expression, NULL behavior and unit | Reject expressions outside the supported grammar |
| Time | Date column, inclusive start, exclusive end, business timezone and as-of cutoff | Ask the user to confirm; never invent a date field |
| Data | Snapshot ID, schema, content hash and mapping version | Require a valid immutable snapshot |
| Review | Relationship classification and optional scoped baseline | Allow investigation before approving a baseline |
| Provenance | Parser, IR, executor and configuration versions | Store versions in every run |

## 3.2 Exact MVP SQL boundary

The first release accepts the DuckDB dialect only. An expression being parseable by SQLGlot is necessary but not sufficient. It must also fit the MetricGuard allowlist and be fully lowerable to the typed IR. SQLGlot documents best-effort transpilation behavior, so adapters must raise on unsupported translation and undergo tests [S4]. The MVP avoids cross-dialect transpilation.

| Construct | MVP policy |
| --- | --- |
| Statement shape | Exactly one SELECT with one scalar aggregate and optional alias |
| FROM | Exactly one registered flat table, with an optional alias |
| Aggregation | SUM of approved numeric expression, COUNT star, COUNT DISTINCT one approved column |
| Numeric expression | Numeric columns and decimal literals, addition, subtraction, multiplication, COALESCE with typed numeric fallback |
| Predicates | AND, OR, NOT, parentheses, comparisons, IN with literals, IS NULL and IS NOT NULL |
| Period | Direct date or timestamp column with typed start and end parameters; half-open interval |
| Literals | Typed numbers, strings, dates and timestamps; single quotes for strings |
| Disallowed | Joins, CTEs, subqueries, windows, UDFs, GROUP BY, HAVING, UNION, ORDER BY, LIMIT and nested aggregates |
| Disallowed expressions | Division, arbitrary CASE, approximate functions, volatility such as current time or random |
| Non-query operations | DDL, DML, COPY, ATTACH, configuration and extension statements rejected |
| Table functions | All user-supplied file, URL and function-based scans rejected |

The UI can supply a period to a query that lacks a date predicate only after the analyst selects a known column and explicitly confirms that this is the intended reporting context. Preserve the supplied original query and the resolved execution form. If a query has a conflicting hard-coded period, do not silently override it.

A parser should identify the period clause using the confirmed time field and the permitted predicate shape. Ambiguous clauses, multiple competing time fields or an inseparable OR containing the time restriction require manual clarification or an unsupported result. Parenthesized business predicates are allowed; arbitrary semantic rewriting is not.

## 3.3 Proposed definition example

The example JSON in `examples/metric_definition.json` proposes the API-level record. SQL remains the user-facing source. The server creates and validates the IR; users do not send a claimed parsed tree that the runner simply trusts.

Core example: metric `sales_reporting`, implementation `dashboard_a_v1`, dialect `duckdb`, aggregate `SUM(order_amount)`, predicate `status = 'Completed'`, time field `order_date`, grain `one row per order_id`, currency `INR`, date semantics `business_date`, timezone `Asia/Kolkata`. The interpretation of order_amount as excluding tax must be stated in the dataset dictionary. If a different source includes tax, comparison is blocked until the difference is modeled.

Hash original text separately from normalized structure. Preserve meaningful details including NULL behavior, numeric types, timezone, case-sensitive identifiers and date boundaries. Only cosmetic changes with proven-safe normalization, such as whitespace or an output alias, may share a normalized fingerprint. Parser upgrades do not silently rewrite previous fingerprints.

## 3.4 Snapshot requirements

Each accepted snapshot contains one immutable analytical table and a manifest. The manifest includes content hashes, ingestion time, extraction cutoff, row count, schema types, primary key, business timezone, currency and any upstream transformation description. The hash represents bytes or a precisely defined canonical serialization; a row-count match is not a content identity check.

CSV ingestion requires explicit decimal, date and NULL handling. A blank string and SQL NULL are not automatically interchangeable. Invalid dates or decimal overflow fail validation or enter a clearly reported rejected-record file; they cannot disappear silently. Uniqueness of order_id must be checked before row-level evidence is enabled.

Currency conversion, exchange rates, source timezone conversion and hidden security filters are outside the MVP. Imported data must already use a documented single currency and a compatible scope. A normalized transformation made during ingestion must be recorded and versioned. The application must not silently clean away the reason reports disagree.

## 3.5 Comparability policy

Require the same snapshot, agreed logical table mapping, compatible grain, unit, currency, calendar, reporting window, business timezone and user-visible population scope. Amount rule, business predicate and date column are permitted differences to investigate; they must not be normalized away. A different declared security scope is a blocker rather than an unexplained contribution.

Three results are possible: comparable, needs context, and not comparable. Needs context means missing information can reasonably be supplied. Not comparable means the current data or scope cannot support the intended calculation. A future cross-source adapter will need explicit grain mapping, source completeness and reconciliation checks; the same column names are not enough.

## 3.6 Type and empty-result semantics

Use DECIMAL for money and checked integer arithmetic for counts. Keep decimal strings in JSON so browser floating-point conversion does not alter stored evidence. INR outputs are displayed to two decimals, but calculations retain the configured precision until the final presentation stage. Reject overflow and narrowing casts.

SQL SUM ignores NULL terms and returns NULL when there are no non-NULL terms. COUNT star counts rows; COUNT DISTINCT ignores NULL values. Preserve these behaviors unless the original definition explicitly uses COALESCE. A row where a SUM term is NULL contributes no numeric amount, but it must still be flagged in the evidence. If either scalar endpoint is NULL, do not calculate an arithmetic gap by treating it as zero. Show an empty or undefined result.

For a money comparison, propose a one-paisa numeric tolerance. Business materiality is a separate threshold set by a user. For relative differences, use baseline magnitude as the denominator and display the direction separately. If the baseline is zero, the percentage is undefined. For rates in a future release, distinguish percentage points from percent change.

## 3.7 Integration support levels

Every adapter exposes four independent capabilities: import metadata, parse logic, execute natively, and reconcile quantitatively. Supporting the first does not imply the others.

Power BI TMDL provides model metadata [S5]. A later importer should preserve measures, referenced columns, relationships and available provenance. DAX measures also rely on filter context propagated through model relationships [S6]. Numeric reconciliation therefore requires the actual model, equivalent identity and security scope, the exact filter context and refresh state. Native execution is preferable to claiming a general DAX-to-SQL converter.

For dbt, use version-aware manifest parsing and compiled SQL when present. Compiled content is not guaranteed for every node [S7]. Never execute imported Jinja or macros on the MetricGuard server. Lineage can identify dependencies, but a model is not automatically a KPI definition. Identify the specific aggregate query or metric definition to compare.

For Excel, formula text and cached values are separate evidence. A later importer must distinguish named ranges, workbook links, calculation settings and stale cached results. Until a bounded evaluator is tested, treat formulas as metadata and CSV exports as data only.
