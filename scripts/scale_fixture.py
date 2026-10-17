"""Deterministic synthetic orders and a direct Decimal oracle for evaluation."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from random import Random


FIELDS = ['order_id', 'customer_id', 'order_date', 'payment_date', 'status',
          'payment_status', 'order_amount', 'refund_amount', 'currency']
FIRST_DAY = date(2025, 9, 1)
LAST_EXCLUSIVE = date(2026, 9, 1)
CUTOFF = date(2026, 9, 2)
GENERATOR_VERSION = 'synthetic-orders-v1'


def _money(cents: int) -> str:
    return f'{Decimal(cents) / 100:.2f}'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def generate_fixture(csv_path: Path, oracle_path: Path, rows: int = 100000,
                     seed: int = 20260923, overwrite: bool = False) -> dict:
    csv_path, oracle_path = Path(csv_path), Path(oracle_path)
    if rows < 1 or rows > 1000000:
        raise ValueError('rows must be between 1 and 1000000')
    if csv_path.resolve() == oracle_path.resolve():
        raise ValueError('CSV and oracle paths must differ')
    if not overwrite and (csv_path.exists() or oracle_path.exists()):
        raise FileExistsError('Fixture or oracle already exists; use --force to replace them')
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    oracle_path.parent.mkdir(parents=True, exist_ok=True)
    rng = Random(seed)
    totals = {'dashboard_a': Decimal(0), 'dashboard_b': Decimal(0), 'finance': Decimal(0)}
    span = (LAST_EXCLUSIVE - FIRST_DAY).days
    created_csv = False
    try:
        with csv_path.open('w' if overwrite else 'x', newline='', encoding='utf-8') as destination:
            created_csv = True
            writer = csv.DictWriter(destination, fieldnames=FIELDS, lineterminator='\n')
            writer.writeheader()
            for index in range(rows):
                order_date = FIRST_DAY + timedelta(days=rng.randrange(span))
                status_roll = rng.randrange(100)
                status = ('Completed' if status_roll < 78 else 'Pending' if status_roll < 90
                          else 'Cancelled' if status_roll < 98 else '')
                paid = (status == 'Completed' and rng.randrange(100) < 88) or (
                    status == 'Pending' and rng.randrange(100) < 10)
                payment_date = order_date + timedelta(days=rng.randrange(4)) if paid else None
                if payment_date and payment_date > CUTOFF:
                    payment_date, paid = None, False
                amount_cents = rng.randrange(5000, 2_500_001)
                refund_roll = rng.randrange(100)
                refund_cents = (rng.randrange(amount_cents // 2 + 1)
                                if paid and refund_roll < 5 else None if refund_roll == 5 else 0)
                amount = Decimal(amount_cents) / 100
                refund = Decimal(refund_cents or 0) / 100
                record = {'order_id': f'O{index + 1:07d}',
                          'customer_id': f'C{rng.randrange(max(1, rows // 3)) + 1:07d}',
                          'order_date': order_date.isoformat(),
                          'payment_date': payment_date.isoformat() if payment_date else '',
                          'status': status, 'payment_status': 'Paid' if paid else 'Unpaid',
                          'order_amount': _money(amount_cents),
                          'refund_amount': _money(refund_cents) if refund_cents is not None else '',
                          'currency': 'INR'}
                writer.writerow(record)
                if date(2026, 8, 1) <= order_date < LAST_EXCLUSIVE:
                    if status == 'Completed':
                        totals['dashboard_a'] += amount
                    if status not in ('Cancelled', ''):
                        totals['dashboard_b'] += amount
                if payment_date and date(2026, 8, 1) <= payment_date < LAST_EXCLUSIVE:
                    totals['finance'] += amount - refund
        oracle = {'generator_version': GENERATOR_VERSION, 'seed': seed, 'rows': rows,
                  'period_start': FIRST_DAY.isoformat(),
                  'period_end_exclusive': LAST_EXCLUSIVE.isoformat(),
                  'currency': 'INR', 'timezone': 'Asia/Kolkata',
                  'scope': 'synthetic_scale_orders', 'grain': 'one_row_per_order_id',
                  'cutoff_at': CUTOFF.isoformat(), 'source_hash': _sha256(csv_path),
                  'august_totals': {key: f'{value:.2f}' for key, value in totals.items()}}
        with oracle_path.open('w' if overwrite else 'x', encoding='utf-8') as destination:
            json.dump(oracle, destination, indent=2, sort_keys=True)
            destination.write('\n')
        return oracle
    except Exception:
        if created_csv and not overwrite:
            csv_path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate a reproducible synthetic scale fixture')
    parser.add_argument('--output-dir', type=Path, default=Path('data/evaluation'))
    parser.add_argument('--rows', type=int, default=100000)
    parser.add_argument('--seed', type=int, default=20260923)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    oracle = generate_fixture(args.output_dir / 'orders_scale.csv',
                              args.output_dir / 'orders_scale_oracle.json',
                              rows=args.rows, seed=args.seed, overwrite=args.force)
    print(json.dumps({'rows': oracle['rows'], 'source_hash': oracle['source_hash'],
                      'august_totals': oracle['august_totals']}, indent=2))


if __name__ == '__main__':
    main()
