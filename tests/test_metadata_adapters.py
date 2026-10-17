import json

import pytest

from services.api.metadata_adapters import AdapterError, import_dbt_manifest, import_tmdl


@pytest.mark.parametrize('version,compiled_key', [(7, 'compiled_sql'), (12, 'compiled_code')])
def test_dbt_manifest_versioned_metadata_and_no_execution(version, compiled_key):
    artifact = {'metadata': {'dbt_schema_version': f'https://schemas.getdbt.com/dbt/manifest/v{version}.json',
                              'dbt_version': '1.11.0', 'project_name': 'retail'},
                'nodes': {'model.retail.orders': {'unique_id': 'model.retail.orders', 'resource_type': 'model',
                             'name': 'orders', 'original_file_path': 'models/orders.sql',
                             'raw_code': '{{ malicious_macro() }}', compiled_key: 'SELECT * FROM orders',
                             'depends_on': {'nodes': ['source.retail.orders']}}},
                'metrics': {'metric.retail.revenue': {'unique_id': 'metric.retail.revenue',
                             'name': 'revenue', 'type': 'simple', 'depends_on': {'nodes': ['model.retail.orders']}}}}
    result = import_dbt_manifest(json.dumps(artifact).encode())
    assert result['format'] == 'dbt_manifest'
    assert result['schema_version'] == version
    assert result['capabilities'] == {'import_metadata': True, 'parse_logic': False,
                                       'execute_natively': False, 'reconcile_quantitatively': False}
    model = next(item for item in result['resources'] if item['resource_type'] == 'model')
    assert model['compiled_sql_present'] is True
    assert model['compiled_sql'] == 'SELECT * FROM orders'
    assert model['lineage'] == ['source.retail.orders']
    assert model['support_state'] == 'metadata_only'
    assert '{{ malicious_macro() }}' not in json.dumps(result)
    assert any(item['resource_type'] == 'metric' for item in result['resources'])


def test_dbt_uncompiled_node_and_bad_schema_are_explicit():
    artifact = {'metadata': {'dbt_schema_version': 'https://schemas.getdbt.com/dbt/manifest/v12.json'},
                'nodes': {'model.x.y': {'unique_id': 'model.x.y', 'resource_type': 'model', 'name': 'y',
                            'raw_code': '{{ ref("orders") }}'}}, 'metrics': {}}
    result = import_dbt_manifest(json.dumps(artifact).encode())
    assert result['resources'][0]['compiled_sql_present'] is False
    assert result['resources'][0]['compiled_sql'] is None
    with pytest.raises(AdapterError, match='version'):
        import_dbt_manifest(json.dumps({**artifact, 'metadata': {'dbt_schema_version': 'v99'}}).encode())
    with pytest.raises(AdapterError, match='nodes'):
        import_dbt_manifest(json.dumps({**artifact, 'nodes': []}).encode())


def test_tmdl_measure_columns_relationships_are_metadata_only():
    tmdl = """model Model
table Sales
    column 'Order Amount'
        dataType: decimal
        sourceColumn: order_amount
    measure 'Net Revenue' =
            SUMX(Sales, Sales[Order Amount])
        formatString: #,##0.00
        lineageTag: measure-tag
relationship rel-1
    fromColumn: Sales[customer_id]
    toColumn: Customer[id]
"""
    result = import_tmdl(tmdl.encode())
    assert result['format'] == 'power_bi_tmdl'
    assert result['capabilities']['execute_natively'] is False
    assert result['measures'][0]['name'] == 'Net Revenue'
    assert 'SUMX' in result['measures'][0]['expression']
    assert result['measures'][0]['support_state'] == 'metadata_only'
    assert result['columns'][0]['source_column'] == 'order_amount'
    assert result['relationships'][0]['from_column'] == 'Sales[customer_id]'
    assert result['relationships'][0]['to_column'] == 'Customer[id]'
    assert result['measures'][0]['lineage_tag'] == 'measure-tag'
    assert result['limitations']


def test_tmdl_rejects_unsupported_or_ambiguous_document():
    with pytest.raises(AdapterError):
        import_tmdl(b'not a TMDL model')
    with pytest.raises(AdapterError):
        import_tmdl(b'table Sales\n    measure X = 1\n    measure X = 2')
    with pytest.raises(AdapterError):
        import_tmdl(b'table Sales\n    measure X = ```\n       unclosed')
