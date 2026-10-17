# Olist payment-status case: public-data coverage evaluation

**Classification:** external public-data experiment, 2026-09-24. Olist transactions are public anonymized records; the [published support-ticket narrative](https://github.com/Sahil8877/SQL-Support-Investigation-Lab) is simulated. This is neither a customer pilot nor an observed analyst workflow. The question here is narrowly defined for this experiment: on the **full downloaded snapshot**, compare all recorded `payment_value` with recorded `payment_value` belonging to orders whose `order_status` is `delivered` or `shipped`. The sign is **target minus baseline**. These are independently computed public-data totals, not original company dashboard values.

## Acquisition and reproducibility

Source: [Kaggle Olist dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce); [public download](https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce); [public metadata](https://www.kaggle.com/api/v1/datasets/view/olistbr/brazilian-ecommerce). Retrieved 2026-09-24. The metadata response records dataset version **2**, last updated **2021-10-01**, license **CC BY-NC-SA 4.0**. No downloaded code was executed. The ZIP and the two extracted CSVs are under ignored `data/public-case-olist/`, outside source control.

| Source file | Original headers | SHA-256 |
| --- | --- | --- |
| `olist_orders_dataset.csv` | `order_id`, `customer_id`, `order_status`, `order_purchase_timestamp`, `order_approved_at`, `order_delivered_carrier_date`, `order_delivered_customer_date`, `order_estimated_delivery_date` | `8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf` |
| `olist_order_payments_dataset.csv` | `order_id`, `payment_sequential`, `payment_type`, `payment_installments`, `payment_value` | `4f713964f2815dbbaa40b9488268c55aac3627bfce5aa96cf58d1f3616de3cc0` |

The archive SHA-256 is `967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784`. The ignored `source-manifest.json` records the retrieval and schema details. The hashes match the supplied prior checks. If a future download differs, inspect its version and content before comparing numbers.
The CSV files provide column names but no declared database types; the evaluator reads identifiers/status as text and parses `payment_value` exactly to integer cents.

From the repository root:

```powershell
.\.venv\Scripts\python -m scripts.acquire_olist_public_case --download
.\.venv\Scripts\python -m scripts.evaluate_olist_public_case
```

The acquisition script fetches the public ZIP and metadata only if absent, extracts **only** the two named CSVs, and writes hashes/headers. The evaluator uses Python integer cents and a separate DuckDB SQL query in `scripts/olist_public_case.sql`; it writes `results.json` and a full `order-evidence.csv` in the ignored data directory. Every evidence row identifies the source payment CSV record numbers behind its order total. The source CSVs remain available for record-level inspection. The SQL cross-check does not read the Python result; its per-status totals matched exactly.
An additional read-back audit traced all **103,886** payment record references from the **99,441** order-evidence rows to the original CSV, with no duplicate or missing reference. Recomputed evidence totals matched the baseline and target exactly.

## Independently calculated result

The orders file has **99,441 unique order IDs**. The payments file has **103,886 CSV records** and no blank fields. There are **2,961 orders with multiple payment records**, **one order without a payment record**, no payment record with an unmatched order, and no repeated `(order_id, payment_sequential)` pair. Thus joining payments to the unique order key keeps **103,886 payment rows**, without multiplying them. `payment_installments` is a separate source field; a payment record is not assumed to be an installment. The orders file has blank `order_approved_at` (160), `order_delivered_carrier_date` (1,783), and `order_delivered_customer_date` (2,965); no order status is blank.

| Order status | All orders | Orders with payment | Payment rows | Recorded payment value |
| --- | ---: | ---: | ---: | ---: |
| approved | 2 | 2 | 2 | 241.08 |
| canceled | 625 | 625 | 664 | 143,255.60 |
| created | 5 | 5 | 5 | 688.10 |
| delivered | 96,478 | 96,477 | 100,756 | 15,422,461.77 |
| invoiced | 314 | 314 | 325 | 69,137.99 |
| processing | 301 | 301 | 319 | 69,394.11 |
| shipped | 1,107 | 1,107 | 1,166 | 177,213.96 |
| unavailable | 609 | 609 | 649 | 126,479.51 |
| **All** | **99,441** | **99,440** | **103,886** | **16,008,872.12** |

**Baseline = 16,008,872.12. Target (delivered or shipped) = 15,599,675.73. Target minus baseline = −409,196.39.** The excluded values by status are canceled 143,255.60, unavailable 126,479.51, processing 69,394.11, invoiced 69,137.99, created 688.10, and approved 241.08. Processing plus invoiced is **138,532.10**, or **644 payment rows on 615 orders**. It explains **only part** of the explicitly defined gap; the other excluded statuses total **270,664.29**. The published narrative's statement that 138,532.10 directly accounts for its finance discrepancy is not established for this full-snapshot, all-payments versus delivered/shipped subcase. Its simulated ticket supplies no independently verifiable original company dashboard endpoints.
All supplied reference counts and totals matched these independently computed outputs; none was hard-coded into the calculation.

Payment values are reported in the dataset's numeric units. The two files do **not** contain a currency code; the calculation does not infer one. A status says which bucket a record belongs to. It does not prove missing money, an unpaid order, a refund, a delayed payment, or the operational reason for that status.

## MetricGuard compatibility decision

The source facts and the app contract differ materially:

| Category | Finding |
| --- | --- |
| Directly available | `order_id`, `customer_id`, `order_status`, payment `order_id`, `payment_value`, `payment_sequential`, `payment_type`, and `payment_installments`. Order IDs are unique on the orders side. |
| Safely derivable outside the app | Exact payment cents summed by order, preserving all 103,886 source payment records and order-to-payment record numbers. This preserves the baseline and status totals, but is preparation work, not work performed by MetricGuard. |
| Unavailable or unjustified | Source `payment_date`, `payment_status`, `refund_amount`, per-row currency, business timezone, and business extraction cutoff do not exist in these two CSVs. `order_purchase_timestamp` and `order_approved_at` cannot be relabeled as a payment date. Converting a timestamp to the required business `DATE` needs a declared timezone. Calling an order-level payment sum `order_amount` would change its stated meaning from payment value unless declared explicitly. |
| Unsupported in MetricGuard | The current contract requires **exactly nine canonical roles** and all of their declared types; `validate_contract` rejects a partial schema. The restricted SQL parser accepts one scalar aggregate over the registered `orders` table, not this orders–payments join or a grouped status breakdown. A direct join query was refused with `unsupported_sql`. |

Code references: [`v2_contract.py`](../../packages/metricguard_core/v2_contract.py) defines and validates the fixed contract, [`common.py`](../../packages/metricguard_core/common.py) defines the nine roles, and [`parser.py`](../../packages/metricguard_core/parser.py) enforces one allowlisted table and aggregate grammar. The source files individually fit the 25 MiB upload cap and the order count fits the one-million-row cap, but those limits do not resolve the missing semantics.

**Decision: unsupported for faithful MetricGuard execution.** Filling mandatory fields with invented defaults, treating payment type as payment status, treating approval time as payment time, or silently changing the metric to merchandise revenue would make a run look valid while changing the question. No mapped snapshot or comparison was submitted to MetricGuard. There are therefore **no MetricGuard endpoint, gap, bridge, or evidence results** for this case. The external independent calculation remains useful evidence of the coverage gap; it is not V2-06 analyst-pilot acceptance.

## Preparation and measured machine work

Preparation performed: downloaded the public ZIP and metadata; verified version, license, archive and CSV hashes; extracted two named CSVs; inspected headers and NULLs; checked order-key uniqueness, payment cardinality and unmatched records; calculated integer-cent totals; cross-checked with independent SQL; wrote order evidence with source-record references; evaluated the fixed app contract and parser. No flattening or fabricated fields were produced for MetricGuard.

On this machine, the latest evaluator run measured **1.0175 seconds** for Python source parsing/calculation, **0.7816 seconds** for the independent DuckDB cross-check, and **2.6396 seconds** for the full evaluation including evidence output. Download, metadata retrieval, compatibility reading, and human preparation time were not instrumented. These are machine timings only and imply no analyst time savings.

## Recommendation and limits

Keep this as a recorded **unsupported public-data case** and use it to probe the next real analyst case's data availability *before* expanding the engine. The concrete question is whether analysts repeatedly face a two-table, one-to-many payment reconciliation with unavailable business metadata, and whether deriving a faithful flat snapshot is practical and permitted. One public dataset does not justify adding arbitrary joins or weakening the nine-role contract. A bounded future test can measure preparation effort and compare the resulting evidence with the analyst's existing method.

This experiment establishes the exact full-snapshot payment-status totals, join/cardinality checks, source lineage, and a real coverage boundary. It does not establish adoption, analyst understanding, real dashboard discrepancies, business root cause, or a successful MetricGuard execution.

## Subsequent external flat preparation

The unsupported decision above records the original evaluation and remains intact. A later bounded flat-snapshot policy uses a separately prepared order-level extract. Run `.\.venv\Scripts\python -m scripts.prepare_olist_flat_snapshot` from the repository root after acquiring the two verified source CSVs. This external script checks their recorded SHA-256 hashes, requires unique order IDs, sums each order's recorded payment values in exact cents, and retains every order. It writes ignored `data/public-case-olist/olist-order-flat.csv` with `row_id`, `order_status`, and `payment_value`; `olist-order-flat-lineage.csv` links each row to its source order CSV row and every contributing payment CSV row. The companion `olist-order-flat-manifest.json` pins source and output hashes, counts, measure meaning, and unit context.

The one order without payment records has a blank `payment_value`, representing NULL. It does not establish a zero payment. SUM ignores that NULL, and an all-NULL or empty selection has an undefined SQL SUM rather than a numeric zero. The files contain no currency code; numeric units remain unknown. The preparation manifest carries the source retrieval timestamp separately; it does not turn that timestamp into a business cutoff. The script is preparation outside MetricGuard. Application import, execution, evidence, and replay are separate steps.

## Subsequent MetricGuard flat-policy execution

The earlier unsupported finding above applies to the nine-role retail policy and remains the original evaluation result. The separately implemented **`flat_snapshot_v1` execution policy**, with contract version **3**, accepts this prepared file as one immutable, whole-snapshot table. Its registered measure is `payment_value` (`DECIMAL(18,2)`); the registered category is `order_status`. The raw definitions are `SELECT SUM(payment_value) FROM snapshot` and `SELECT SUM(payment_value) FROM snapshot WHERE order_status IN ('delivered', 'shipped')`. No business date, refund field, timezone, cutoff, or currency was supplied or inferred.

On 2026-09-24, `python -m scripts.verify_flat_olist_live --app-dir data/evaluation/flat-olist-final` submitted the prepared CSV through the local API, ran isolated Docker ingestion and comparison, retrieved paged evidence, saved an investigation, and replayed it through the same fixed runner. [The local report](../../data/evaluation/flat-olist-final/verification-report.json) records contract, snapshot, run and replay IDs. MetricGuard produced baseline **16,008,872.12**, target **15,599,675.73**, and target-minus-baseline **−409,196.39**, exactly matching the previously independent evaluator. The published artifact has **99,441** row contributions; their exact-decimal deltas sum to **−409,196.39**. One evidence row has a NULL measure. The external lineage file still traces all **103,886** contributing payment CSV rows to the prepared extract; MetricGuard executes only that prepared extract and does not perform the join.

This is successful application coverage for the stated public-data calculation, not validation of an original company dashboard value. The source supplies no currency code, so the UI and export label amounts as source numeric units with currency unknown. Filter membership shows which records contribute to each SUM; it does not establish payment loss, an operational cause, or analyst usefulness. The simulated ticket is not a real-user pilot, and V2-06 remains open.

To repeat the local workflow from the repository root, first run the external preparer above and build the fixed worker image with `docker build -f infra/Dockerfile.runner -t metricguard-runner:1.0 .`. In one PowerShell terminal, set `$env:METRICGUARD_MODE='isolated'`, `$env:METRICGUARD_DATA_DIR='data/live-workspace'`, `$env:METRICGUARD_DATABASE_URL='sqlite:///data/live-workspace/metricguard.db'`, then run `.\.venv\Scripts\python -m uvicorn services.api.app:app --host 127.0.0.1 --port 8001`. In a second terminal set the same data directory and database URL and run `.\.venv\Scripts\python -m services.supervisor.worker`. Open `http://127.0.0.1:8001/`, choose **Flat snapshots**, preview `data/public-case-olist/olist-order-flat.csv`, register `row_id` as key, `payment_value` as `DECIMAL(18,2)` measure, and `order_status` as category. Declare its meaning as recorded payment value summed by order, its unit as source numeric units with unknown currency, whole-snapshot scope, comparable values, and CSV blank-as-NULL policy. Select `data/public-case-olist/olist-order-flat-manifest.json` as the optional external preparation manifest, then upload the extract, register the two SQL definitions above, run, inspect paged evidence, save, and replay. The [2026-09-25 fresh browser record](../../data/evaluation/flat-olist-browser/browser-verification.json) captures that path and its limits. `python -m scripts.verify_flat_olist_live` repeats the API/runner verification and writes the ignored local report.
