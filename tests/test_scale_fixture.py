import csv
from datetime import date
from decimal import Decimal
import hashlib
import json

import pytest

from scripts.scale_fixture import generate_fixture


def test_seeded_scale_fixture_is_reproducible_and_oracle_matches_csv(tmp_path):
    first = tmp_path / 'first.csv'
    second = tmp_path / 'second.csv'
    oracle_path = tmp_path / 'oracle.json'
    generated = generate_fixture(first, oracle_path, rows=1200, seed=17)
    generate_fixture(second, tmp_path / 'second-oracle.json', rows=1200, seed=17)
    assert first.read_bytes() == second.read_bytes()
    assert generated['source_hash'] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert json.loads(oracle_path.read_text()) == generated

    amounts = {'dashboard_a': Decimal(0), 'dashboard_b': Decimal(0), 'finance': Decimal(0)}
    months = set()
    with first.open(newline='', encoding='utf-8') as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == ['order_id', 'customer_id', 'order_date', 'payment_date', 'status',
                                     'payment_status', 'order_amount', 'refund_amount', 'currency']
        rows = list(reader)
    assert len(rows) == 1200
    assert len({row['order_id'] for row in rows}) == 1200
    for row in rows:
        order_date = date.fromisoformat(row['order_date'])
        assert date(2025, 9, 1) <= order_date <= date(2026, 8, 31)
        months.add(row['order_date'][:7])
        assert row['currency'] == 'INR'
        amount = Decimal(row['order_amount'])
        if row['order_date'].startswith('2026-08'):
            if row['status'] == 'Completed':
                amounts['dashboard_a'] += amount
            if row['status'] not in ('Cancelled', ''):
                amounts['dashboard_b'] += amount
        if row['payment_date'].startswith('2026-08') and row['payment_status'] == 'Paid':
            amounts['finance'] += amount - Decimal(row['refund_amount'] or '0')
    assert len(months) == 12
    assert generated['august_totals'] == {key: f'{value:.2f}' for key, value in amounts.items()}


def test_scale_fixture_refuses_overwrite_or_invalid_row_count(tmp_path):
    csv_path, oracle_path = tmp_path / 'orders.csv', tmp_path / 'oracle.json'
    generate_fixture(csv_path, oracle_path, rows=3, seed=1)
    original = csv_path.read_bytes()
    with pytest.raises(FileExistsError):
        generate_fixture(csv_path, oracle_path, rows=4, seed=2)
    assert csv_path.read_bytes() == original
    with pytest.raises(ValueError, match='rows'):
        generate_fixture(tmp_path / 'bad.csv', tmp_path / 'bad.json', rows=0, seed=1)
