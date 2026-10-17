"""External preparation of the Olist order/payment flat snapshot.

This script is deliberately outside MetricGuard's import and execution paths.
"""

import argparse
import csv
from collections import defaultdict
import json
from pathlib import Path

from scripts.acquire_olist_public_case import sha256
from scripts.evaluate_olist_public_case import cents, money


ORDERS_HASH = '8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf'
PAYMENTS_HASH = '4f713964f2815dbbaa40b9488268c55aac3627bfce5aa96cf58d1f3616de3cc0'


def prepare(directory: Path, *, verify_source_hashes: bool = True) -> dict:
    orders_path = directory / 'olist_orders_dataset.csv'
    payments_path = directory / 'olist_order_payments_dataset.csv'
    hashes = {'orders_sha256': sha256(orders_path), 'payments_sha256': sha256(payments_path)}
    if verify_source_hashes and hashes != {'orders_sha256': ORDERS_HASH, 'payments_sha256': PAYMENTS_HASH}:
        raise ValueError('Olist source hashes differ from the verified files')
    source_manifest_path = directory / 'source-manifest.json'
    source_manifest = (json.loads(source_manifest_path.read_text(encoding='utf-8'))
                       if source_manifest_path.is_file() else {})

    orders = {}
    with orders_path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'order_id', 'order_status'} <= set(reader.fieldnames or []):
            raise ValueError('Orders source is missing order_id or order_status')
        for source_row, row in enumerate(reader, start=2):
            key = row['order_id']
            if not key or key in orders:
                raise ValueError(f'Missing or duplicate order key: {key!r}')
            if not row['order_status']:
                raise ValueError(f'Missing order status at source row {source_row}')
            orders[key] = (row['order_status'], source_row)

    payments = defaultdict(lambda: {'cents': 0, 'rows': []})
    with payments_path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'order_id', 'payment_value'} <= set(reader.fieldnames or []):
            raise ValueError('Payments source is missing order_id or payment_value')
        for source_row, row in enumerate(reader, start=2):
            key = row['order_id']
            if key not in orders:
                raise ValueError(f'Payment source row {source_row} has no matching order')
            if row['payment_value'] == '':
                raise ValueError(f'Missing payment value at source row {source_row}')
            payments[key]['cents'] += cents(row['payment_value'])
            payments[key]['rows'].append(source_row)

    flat_path = directory / 'olist-order-flat.csv'
    lineage_path = directory / 'olist-order-flat-lineage.csv'
    with flat_path.open('w', encoding='utf-8', newline='') as flat_stream, \
            lineage_path.open('w', encoding='utf-8', newline='') as lineage_stream:
        flat = csv.writer(flat_stream)
        lineage = csv.writer(lineage_stream)
        flat.writerow(['row_id', 'order_status', 'payment_value'])
        lineage.writerow(['row_id', 'source_order_csv_row', 'source_payment_csv_rows', 'payment_record_count'])
        for key, (status, order_row) in orders.items():
            record = payments.get(key)
            # No payment record means unknown payment value, never asserted zero.
            flat.writerow([key, status, money(record['cents']) if record else ''])
            lineage.writerow([key, order_row, ';'.join(map(str, record['rows'])) if record else '',
                              len(record['rows']) if record else 0])

    report = {
        'version': 1,
        'classification': 'external_preparation',
        'script': 'scripts/prepare_olist_flat_snapshot.py',
        'script_version': '1',
        'transformation': 'Group exact payment values by order_id, join unique order status, preserve all orders, and leave payment_value blank when no payment record contributes.',
        'source_hashes': hashes,
        'source_retrieved_at_utc': source_manifest.get('retrieved_at_utc'),
        'flat_file': flat_path.name,
        'flat_sha256': sha256(flat_path),
        'lineage_file': lineage_path.name,
        'lineage_sha256': sha256(lineage_path),
        'orders': len(orders),
        'payment_records': sum(len(record['rows']) for record in payments.values()),
        'orders_without_payment_records': len(orders) - len(payments),
        'measure_meaning': 'sum of recorded payment_value for each order',
        'measure_unit': 'source numeric units; currency unknown',
        'null_semantics': 'blank payment_value means no contributing payment record; no zero payment is inferred',
    }
    (directory / 'olist-order-flat-manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('data/public-case-olist'))
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory), indent=2))
