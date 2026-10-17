import pytest

from packages.metricguard_core import compare, parse_definition
from packages.metricguard_core.common import CoreError
from packages.metricguard_core.v2_contract import ingest_mapped, validate_contract, validate_mapping
from tests.test_v2_contract import CONTRACT, MAPPING, source_file


def test_mapped_snapshot_requires_pinned_policy_hashes_at_core_boundary(tmp_path):
    source = tmp_path / 'source.csv'
    source_file(source)
    contract = validate_contract(CONTRACT)
    mapping = validate_mapping(MAPPING, contract)
    database = tmp_path / 'snapshot.duckdb'
    ingest_mapped(source, database, contract, mapping)
    baseline = parse_definition('SELECT SUM(order_amount) FROM orders', 'order_date')
    context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01',
               'timezone': 'Asia/Kolkata', 'currency': 'INR', 'unit': 'currency',
               'grain': 'one_row_per_sale', 'scope': 'retail_pilot'}
    with pytest.raises(CoreError) as error:
        compare(database, baseline, baseline, context)
    assert error.value.code == 'not_comparable'
    pinned = {**context, 'contract_hash': contract['contract_hash'],
              'mapping_hash': mapping['mapping_hash']}
    assert compare(database, baseline, baseline, pinned)['delta_target_minus_baseline'] == '0.00'
