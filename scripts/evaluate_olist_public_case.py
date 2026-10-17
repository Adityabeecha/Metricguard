"""Independent exact-cent calculation and DuckDB cross-check for the Olist subcase."""

import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time

import duckdb

from scripts.acquire_olist_public_case import sha256


def cents(value: str) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f'Invalid payment value: {value!r}') from exc
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise ValueError(f'Payment value is not fixed to cents: {value!r}')
    return int(amount * 100)


def money(value: int) -> str:
    return f'{Decimal(value) / 100:.2f}'


def evaluate(orders_path: Path, payments_path: Path, output_dir: Path) -> dict:
    start = time.perf_counter()
    orders = {}
    order_nulls = Counter()
    with orders_path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        order_headers = reader.fieldnames
        for row in reader:
            for name, value in row.items():
                if value == '': order_nulls[name] += 1
            key = row['order_id']
            if not key or key in orders:
                raise ValueError(f'Missing or duplicate order key: {key!r}')
            orders[key] = row['order_status']
    per_order = defaultdict(lambda: {'payment_rows': 0, 'cents': 0, 'source_payment_rows': []})
    payment_nulls = Counter()
    payment_rows = 0
    unmatched_payment_rows = 0
    payment_sequences = set()
    duplicate_payment_sequences = 0
    with payments_path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        payment_headers = reader.fieldnames
        for row in reader:
            for name, value in row.items():
                if value == '': payment_nulls[name] += 1
            key = row['order_id']
            if not key: raise ValueError('Payment row has no order_id')
            sequence_key = (key, row['payment_sequential'])
            if sequence_key in payment_sequences: duplicate_payment_sequences += 1
            payment_sequences.add(sequence_key)
            if key not in orders: unmatched_payment_rows += 1
            record = per_order[key]
            record['payment_rows'] += 1
            record['cents'] += cents(row['payment_value'])
            payment_rows += 1
            record['source_payment_rows'].append(payment_rows + 1)  # Header is CSV row 1.
    python_seconds = time.perf_counter() - start

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = output_dir / 'order-evidence.csv'
    breakdown = defaultdict(lambda: {'orders_with_payments': 0, 'payment_rows': 0, 'cents': 0})
    with evidence_path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['order_id', 'order_status', 'payment_rows', 'payment_value', 'included_in_target', 'source_payment_csv_rows'])
        for key in sorted(set(orders) | set(per_order)):
            status = orders.get(key, '<unmatched order>')
            record = per_order.get(key, {'payment_rows': 0, 'cents': 0, 'source_payment_rows': []})
            included = status in {'delivered', 'shipped'}
            writer.writerow([key, status, record['payment_rows'], money(record['cents']), str(included).lower(),
                             ';'.join(map(str, record['source_payment_rows']))])
            if record['payment_rows']:
                group = breakdown[status]
                group['orders_with_payments'] += 1
                group['payment_rows'] += record['payment_rows']
                group['cents'] += record['cents']
    totals = {status: {'orders_with_payments': values['orders_with_payments'],
                       'payment_rows': values['payment_rows'], 'payment_value': money(values['cents'])}
              for status, values in sorted(breakdown.items())}
    baseline_cents = sum(row['cents'] for row in breakdown.values())
    target_cents = sum(row['cents'] for status, row in breakdown.items() if status in {'delivered', 'shipped'})

    sql_start = time.perf_counter()
    with duckdb.connect(':memory:') as con:
        sql = (Path(__file__).with_name('olist_public_case.sql').read_text(encoding='utf-8')
               .format(orders_path=str(orders_path.resolve()).replace('\\', '/').replace("'", "''"),
                       payments_path=str(payments_path.resolve()).replace('\\', '/').replace("'", "''")))
        statements = [statement.strip() for statement in sql.split(';') if statement.strip()]
        for statement in statements[:-1]: con.execute(statement)
        sql_rows = con.execute(statements[-1]).fetchall()
        sql_order_count = con.execute('SELECT COUNT(*), COUNT(DISTINCT order_id) FROM source_orders').fetchone()
        sql_unpaid_orders = con.execute('SELECT COUNT(*) FROM source_orders o WHERE NOT EXISTS (SELECT 1 FROM source_payments p WHERE p.order_id=o.order_id)').fetchone()[0]
    sql_seconds = time.perf_counter() - sql_start
    sql_breakdown = {status: {'orders_with_payments': entities, 'payment_rows': rows,
                              'payment_value': f'{value:.2f}'}
                     for status, rows, entities, value in sql_rows}
    if totals != sql_breakdown or sql_order_count != (len(orders), len(orders)) or sql_unpaid_orders != len(orders) - len(set(per_order) & set(orders)):
        raise ValueError('Independent DuckDB cross-check differs from Python calculation')
    report = {
        'classification': 'external_public_data_experiment',
        'source_hashes': {'orders_sha256': sha256(orders_path), 'payments_sha256': sha256(payments_path)},
        'source_headers': {'orders': order_headers, 'payments': payment_headers},
        'orders': len(orders), 'payment_rows': payment_rows,
        'order_status_counts': dict(sorted(Counter(orders.values()).items())),
        'orders_with_multiple_payment_rows': sum(1 for key in orders if per_order[key]['payment_rows'] > 1),
        'orders_without_payment_rows': sum(1 for key in orders if per_order[key]['payment_rows'] == 0),
        'unmatched_payment_rows': unmatched_payment_rows,
        'duplicate_order_payment_sequence_pairs': duplicate_payment_sequences,
        'payment_rows_after_unique_order_join': payment_rows,
        'null_or_blank_fields': {'orders': dict(order_nulls), 'payments': dict(payment_nulls)},
        'status_breakdown': totals, 'baseline_payment_value': money(baseline_cents),
        'target_delivered_or_shipped_payment_value': money(target_cents),
        'target_minus_baseline': money(target_cents - baseline_cents),
        'processing_plus_invoiced_payment_value': money(sum(breakdown[s]['cents'] for s in ('processing', 'invoiced'))),
        'independent_sql_cross_check': 'matched_exactly',
        'python_calculation_seconds': round(python_seconds, 4),
        'duckdb_cross_check_seconds': round(sql_seconds, 4),
        'total_evaluation_seconds': round(time.perf_counter() - start, 4),
        'order_evidence_file': evidence_path.name,
    }
    (output_dir / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('data/public-case-olist'))
    args = parser.parse_args()
    print(json.dumps(evaluate(args.directory / 'olist_orders_dataset.csv',
                              args.directory / 'olist_order_payments_dataset.csv', args.directory), indent=2))
