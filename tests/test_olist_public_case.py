import csv
import json
import zipfile

import pytest

from scripts.evaluate_olist_public_case import cents, evaluate
from scripts.acquire_olist_public_case import acquire


def test_olist_evaluation_keeps_payment_rows_and_unpaid_order_distinct(tmp_path):
    orders = tmp_path / 'orders.csv'
    payments = tmp_path / 'payments.csv'
    orders.write_text('order_id,order_status\na,delivered\nb,processing\nc,shipped\nd,canceled\n', encoding='utf-8')
    payments.write_text('order_id,payment_sequential,payment_type,payment_installments,payment_value\n'
                        'a,1,credit_card,2,10.10\na,2,voucher,1,2.05\n'
                        'b,1,credit_card,3,4.00\nc,1,credit_card,1,3.00\n', encoding='utf-8')
    result = evaluate(orders, payments, tmp_path / 'out')
    assert result['orders'] == 4
    assert result['payment_rows'] == 4
    assert result['orders_with_multiple_payment_rows'] == 1
    assert result['orders_without_payment_rows'] == 1
    assert result['baseline_payment_value'] == '19.15'
    assert result['target_delivered_or_shipped_payment_value'] == '15.15'
    assert result['target_minus_baseline'] == '-4.00'
    assert result['independent_sql_cross_check'] == 'matched_exactly'
    with (tmp_path / 'out' / 'order-evidence.csv').open(newline='', encoding='utf-8') as stream:
        rows = {row['order_id']: row for row in csv.DictReader(stream)}
    assert rows['a']['payment_value'] == '12.15'
    assert rows['a']['source_payment_csv_rows'] == '2;3'
    assert rows['d']['payment_rows'] == '0'


def test_olist_amount_refuses_subcent_values():
    with pytest.raises(ValueError, match='fixed to cents'):
        cents('1.001')


def test_olist_acquisition_refuses_extracted_file_that_differs_from_archive(tmp_path):
    with zipfile.ZipFile(tmp_path / 'source.zip', 'w') as archive:
        archive.writestr('olist_orders_dataset.csv', 'order_id,order_status\na,delivered\n')
        archive.writestr('olist_order_payments_dataset.csv', 'order_id,payment_value\na,1.00\n')
    (tmp_path / 'dataset-metadata.json').write_text(json.dumps({'currentVersionNumber': 2}), encoding='utf-8')
    first = acquire(tmp_path, False)
    assert first['files']['olist_orders_dataset.csv']['headers'] == ['order_id', 'order_status']
    (tmp_path / 'olist_orders_dataset.csv').write_text('order_id,order_status\na,canceled\n', encoding='utf-8')
    with pytest.raises(ValueError, match='differs from the downloaded archive'):
        acquire(tmp_path, False)
