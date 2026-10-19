import csv
from decimal import Decimal

import pytest

from packages.metricguard_core.common import CoreError
from packages.metricguard_core.flat import validate_contract, ingest_flat, parse_flat, compare_flat


def contract():
    return validate_contract({'version': 3, 'mode': 'flat_snapshot', 'name': 'Other data',
        'row_key': 'item_code', 'measure': 'net_value', 'precision': 10, 'scale': 2,
        'categories': ['segment'], 'measure_meaning': 'Declared net value',
        'unit_label': 'unknown unit', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})


def test_flat_sum_and_evidence(tmp_path):
    source = tmp_path / 'items.csv'
    with source.open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerows([['item_code','net_value','segment'],['i1','2.10','north'],
                          ['i2','5.00','south'],['i3','','north']])
    c = contract()
    database = tmp_path / 'snapshot.duckdb'
    manifest = ingest_flat(source, database, c)
    baseline = parse_flat('SELECT SUM(net_value) FROM snapshot', c)
    target = parse_flat("SELECT SUM(net_value) FROM snapshot WHERE segment = 'north'", c)
    result = compare_flat(database, baseline, target, {'mode':'flat_snapshot','scope':c['scope'],
        'contract_hash':c['contract_hash'],'unit_label':c['unit_label'],
        'values_comparable':True,'manifest_hash':manifest['manifest_hash']})
    assert (Decimal(result['baseline_value']), Decimal(result['target_value']), Decimal(result['delta_target_minus_baseline'])) == (Decimal('7.10'), Decimal('2.10'), Decimal('-5.00'))
    assert sum(Decimal(row['delta']) for row in result['evidence']) == Decimal('-5.00')
    assert result['evidence'][2]['target_null_term'] is True


def test_flat_rejects_unsupported_sql():
    c = contract()
    for sql in ['SELECT SUM(other) FROM snapshot', 'SELECT SUM(net_value) FROM snapshot GROUP BY segment',
                "SELECT SUM(net_value) FROM snapshot WHERE segment = 'north' OR segment = 'south'",
                'SELECT SUM(net_value + 1) FROM snapshot']:
        with pytest.raises(CoreError): parse_flat(sql, c)


def test_flat_duplicate_and_bad_amount(tmp_path):
    for rows in [ [['x','1','a'],['x','2','b']], [['x','1.234','a']] ]:
        source = tmp_path / 'bad.csv'
        with source.open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['item_code','net_value','segment'])
            writer.writerows(rows)
        with pytest.raises(CoreError): ingest_flat(source, tmp_path / 'bad.duckdb', contract())


def test_flat_empty_sum_stays_undefined(tmp_path):
    source = tmp_path / 'nulls.csv'
    source.write_text('item_code,net_value,segment\ni1,,north\n', encoding='utf-8')
    c = contract()
    database = tmp_path / 'nulls.duckdb'
    ingest_flat(source, database, c)
    baseline = parse_flat('SELECT SUM(net_value) FROM snapshot', c)
    target = parse_flat("SELECT SUM(net_value) FROM snapshot WHERE segment = 'south'", c)
    result = compare_flat(database, baseline, target, {'mode':'flat_snapshot','scope':c['scope'],
        'contract_hash':c['contract_hash'],'unit_label':c['unit_label'],'values_comparable':True})
    assert result['baseline_value'] is None and result['target_value'] is None
    assert result['delta_target_minus_baseline'] is None and result['residual'] is None
    assert result['evidence'][0]['baseline_null_term'] is True


def test_flat_missing_referenced_field_rejected(tmp_path):
    source = tmp_path / 'missing.csv'
    source.write_text('item_code,net_value\ni1,1.00\n', encoding='utf-8')
    with pytest.raises(CoreError, match='registered column'):
        ingest_flat(source, tmp_path / 'missing.duckdb', contract())


def test_flat_refuses_values_too_large_for_bounded_evidence(tmp_path):
    source = tmp_path / 'oversized.csv'
    source.write_text('item_code,net_value,segment\n' + 'x' * 257 + ',1.00,north\n', encoding='utf-8')
    with pytest.raises(CoreError, match='256'):
        ingest_flat(source, tmp_path / 'oversized.duckdb', contract())


def test_flat_synthetic_row_boundary_is_atomic(tmp_path, monkeypatch):
    # The row cap is now 5,000,000; the boundary behaviour is checked at a patched cap of 100,000.
    import packages.metricguard_core.flat as flat_module
    monkeypatch.setattr(flat_module, 'MAX_FLAT_ROWS', 100_000)
    for count in (100_000, 100_001):
        source = tmp_path / f'synthetic-{count}.csv'
        with source.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['item_code', 'net_value', 'segment'])
            writer.writerows((f'row{i}', '1.00', 'test') for i in range(count))
        destination = tmp_path / f'synthetic-{count}.duckdb'
        if count == 100_000:
            assert ingest_flat(source, destination, contract())['row_count'] == count
        else:
            with pytest.raises(CoreError, match='exceeds 100000 rows'):
                ingest_flat(source, destination, contract())
            assert not destination.exists()
            assert not destination.with_name(destination.name + '.manifest.json').exists()
