import csv
import json
from pathlib import Path

import pytest

from scripts.prepare_olist_flat_snapshot import prepare


def _write(path: Path, name: str, rows: list[list[str]]) -> None:
    with (path / name).open('w', newline='', encoding='utf-8') as stream:
        csv.writer(stream).writerows(rows)


def test_external_flat_preparation_preserves_null_and_lineage(tmp_path):
    _write(tmp_path, 'olist_orders_dataset.csv', [['order_id', 'order_status'], ['a', 'delivered'], ['b', 'canceled']])
    _write(tmp_path, 'olist_order_payments_dataset.csv', [['order_id', 'payment_value'], ['a', '1.20'], ['a', '2.03']])
    result = prepare(tmp_path, verify_source_hashes=False)
    with (tmp_path / result['flat_file']).open(newline='', encoding='utf-8') as stream:
        assert list(csv.DictReader(stream)) == [
            {'row_id': 'a', 'order_status': 'delivered', 'payment_value': '3.23'},
            {'row_id': 'b', 'order_status': 'canceled', 'payment_value': ''},
        ]
    with (tmp_path / result['lineage_file']).open(newline='', encoding='utf-8') as stream:
        assert list(csv.DictReader(stream)) == [
            {'row_id': 'a', 'source_order_csv_row': '2', 'source_payment_csv_rows': '2;3', 'payment_record_count': '2'},
            {'row_id': 'b', 'source_order_csv_row': '3', 'source_payment_csv_rows': '', 'payment_record_count': '0'},
        ]
    assert result['orders_without_payment_records'] == 1


def test_external_preparation_keeps_retrieval_time_separate(tmp_path):
    _write(tmp_path, 'olist_orders_dataset.csv', [['order_id', 'order_status'], ['a', 'delivered']])
    _write(tmp_path, 'olist_order_payments_dataset.csv', [['order_id', 'payment_value'], ['a', '1.00']])
    (tmp_path / 'source-manifest.json').write_text(json.dumps({
        'retrieved_at_utc': '2026-09-24T11:27:36+00:00'}), encoding='utf-8')
    result = prepare(tmp_path, verify_source_hashes=False)
    assert result['source_retrieved_at_utc'] == '2026-09-24T11:27:36+00:00'
    assert 'cutoff_at' not in result and 'timezone' not in result


@pytest.mark.parametrize('orders,payments,error', [
    ([['order_id', 'order_status'], ['a', 'x'], ['a', 'y']], [['order_id', 'payment_value']], 'duplicate order'),
    ([['order_id', 'order_status'], ['a', 'x']], [['order_id', 'payment_value'], ['b', '1.00']], 'no matching order'),
    ([['order_id', 'order_status'], ['a', 'x']], [['order_id', 'payment_value'], ['a', '1.001']], 'fixed to cents'),
])
def test_rejects_invalid_sources(tmp_path, orders, payments, error):
    _write(tmp_path, 'olist_orders_dataset.csv', orders)
    _write(tmp_path, 'olist_order_payments_dataset.csv', payments)
    with pytest.raises(ValueError, match=error):
        prepare(tmp_path, verify_source_hashes=False)
