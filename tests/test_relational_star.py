"""Relational contract v2: one fact table with several lookup tables (star schema).

Hand-checkable fixture: six orders, a customer lookup and a product lookup.
"""
import csv
import io
from copy import deepcopy
import zipfile

import pytest
from fastapi.testclient import TestClient

from packages.metricguard_core.common import CoreError
from packages.metricguard_core.common import digest
from packages.metricguard_core.relational import parse_relational, validate_contract
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.runner import ingest_job, run_job
from services.supervisor.worker import Supervisor

CONTRACT = {
    'version': 2, 'mode': 'relational_snapshot', 'policy': 'relational-star-v1', 'name': 'Orders star',
    'fact': {'table': 'orders', 'key': 'order_id', 'grain': 'one row per order', 'categories': ['status']},
    'dimensions': [
        {'role': 'customer', 'table': 'customers', 'key': 'customer_id', 'grain': 'one row per customer',
         'categories': ['segment'], 'fact_foreign_key': 'customer_id'},
        {'role': 'product', 'table': 'products', 'key': 'product_id', 'grain': 'one row per product',
         'categories': ['category'], 'fact_foreign_key': 'product_id'},
    ],
    'measure': {'column': 'amount', 'precision': 12, 'scale': 2}, 'measure_meaning': 'Order amount',
    'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}

ORDERS = [['order_id', 'customer_id', 'product_id', 'amount', 'status'],
          ['O1', 'C1', 'P1', '100.00', 'paid'], ['O2', 'C2', 'P2', '50.00', 'paid'],
          ['O3', 'C1', 'P2', '30.00', 'refunded'], ['O4', '', 'P1', '20.00', 'paid'],
          ['O5', 'C9', 'P3', '40.00', 'paid'], ['O6', 'C2', '', '10.00', 'paid']]
CUSTOMERS = [['customer_id', 'segment'], ['C1', 'retail'], ['C2', 'trade']]
PRODUCTS = [['product_id', 'category'], ['P1', 'books'], ['P2', 'toys']]


def _csv(rows):
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode('utf-8')


def test_star_contract_and_sql_rules():
    c = validate_contract(CONTRACT)
    assert [d['role'] for d in c['dimensions']] == ['customer', 'product']
    ir = parse_relational('SELECT SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.customer_id '
                          "LEFT JOIN products p ON p.product_id = o.product_id WHERE p.category = 'books' AND c.segment = 'retail'", c)
    assert ir['joins'] == {'customer': 'inner', 'product': 'left'} and ir['contract_version'] == 2
    no_join = parse_relational("SELECT SUM(o.amount) FROM orders o WHERE o.status = 'paid'", c)
    assert no_join['joins'] == {}
    for sql in ["SELECT SUM(o.amount) FROM orders o WHERE c.segment = 'retail'",  # lookup not joined
                'SELECT SUM(o.amount) FROM orders o JOIN customers c ON o.product_id = c.customer_id',  # wrong key
                'SELECT SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.customer_id '
                'JOIN customers c2 ON o.customer_id = c2.customer_id']:  # same lookup twice
        with pytest.raises(CoreError):
            parse_relational(sql, c)
    with pytest.raises(CoreError):  # two lookups cannot share one foreign key
        validate_contract({**CONTRACT, 'dimensions': [CONTRACT['dimensions'][0],
                                                      {**CONTRACT['dimensions'][1], 'fact_foreign_key': 'customer_id'}]})


def _snapshot(tmp_path, contract=None, tables=None):
    from packages.metricguard_core.relational_snapshot import ingest_relational
    contract = validate_contract(contract or CONTRACT)
    tables = tables or {'fact': ORDERS, 'customer': CUSTOMERS, 'product': PRODUCTS}
    source = tmp_path / 'source.zip'
    with zipfile.ZipFile(source, 'w') as bundle:
        for role, rows in tables.items():
            bundle.writestr('fact.csv' if role == 'fact' else f'dim_{role}.csv', _csv(rows))
    database = tmp_path / 'snapshot.duckdb'
    manifest = ingest_relational(source, database, contract)
    return database, contract, manifest


@pytest.mark.parametrize('sql', [
    'SELECT SUM(o.amount) FROM orders o RIGHT JOIN customers c ON o.customer_id = c.customer_id',
    'SELECT SUM(o.amount) FROM orders o CROSS JOIN customers c',
    'SELECT SUM(o.amount) FROM orders o JOIN customers c USING (customer_id)',
    'SELECT SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.customer_id '
    'JOIN products p ON c.customer_id = p.product_id',
    'SELECT SUM(DISTINCT o.amount) FROM orders o',
    'SELECT SUM(o.amount) FROM orders o GROUP BY o.status',
    "SELECT SUM(o.amount) FROM orders o WHERE o.status = 'paid' OR o.status IS NULL",
    'WITH x AS (SELECT * FROM orders) SELECT SUM(x.amount) FROM x',
    'SELECT SUM(o.amount) FROM orders o; SELECT 1',
])
def test_star_refuses_queries_outside_bounded_fact_lookup_scope(sql):
    with pytest.raises(CoreError):
        parse_relational(sql, CONTRACT)


def test_star_four_lookups_preserve_fact_grain_and_reconcile(tmp_path):
    from packages.metricguard_core.relational import context_for
    from packages.metricguard_core.relational_compare import compare_relational
    from packages.metricguard_core.relational_evidence import RelationalEvidenceValidator

    contract = deepcopy(CONTRACT)
    for role in ('region', 'channel'):
        contract['dimensions'].append({'role': role, 'table': role + 's', 'key': role + '_id',
                                       'grain': 'one row per ' + role, 'categories': ['label'],
                                       'fact_foreign_key': role + '_id'})
    orders = [ORDERS[0] + ['region_id', 'channel_id']] + [row + ['R1', 'H1'] for row in ORDERS[1:]]
    database, contract, manifest = _snapshot(tmp_path, contract, {
        'fact': orders, 'customer': CUSTOMERS, 'product': PRODUCTS,
        'region': [['region_id', 'label'], ['R1', 'domestic']],
        'channel': [['channel_id', 'label'], ['H1', 'web']]})
    baseline = parse_relational('SELECT SUM(o.amount) FROM orders o', contract)
    target = parse_relational('SELECT SUM(o.amount) FROM orders o '
                              'JOIN customers c ON o.customer_id = c.customer_id '
                              'JOIN products p ON o.product_id = p.product_id '
                              'JOIN regions r ON o.region_id = r.region_id '
                              "LEFT JOIN channels h ON o.channel_id = h.channel_id WHERE h.label = 'web'", contract)
    result = compare_relational(database, baseline, target, context_for(contract))
    assert (result['baseline_value'], result['target_value'], result['delta_target_minus_baseline'], result['residual']) == (
        '250.00', '180.00', '-70.00', '0.00')
    assert len(result['evidence']) == manifest['join_proof']['fact_rows'] == 6
    assert manifest['join_proof']['fanout_rows'] == 0 and len(manifest['join_proof']['dimensions']) == 4
    context = {'contract': contract, 'manifest': manifest, 'baseline': baseline, 'target': target}
    with RelationalEvidenceValidator(context, include_join_evidence=True) as validator:
        for row in result['evidence']:
            validator.add(row)
        validator.finish(result)
    contract['dimensions'].append({**contract['dimensions'][0], 'role': 'fifth', 'table': 'fifths', 'fact_foreign_key': 'fifth_id'})
    contract.pop('contract_hash')
    with pytest.raises(CoreError):
        validate_contract(contract)


@pytest.mark.parametrize('role', ['fact', 'customer', 'product'])
def test_star_duplicate_keys_never_publish_snapshot(tmp_path, role):
    tables = deepcopy({'fact': ORDERS, 'customer': CUSTOMERS, 'product': PRODUCTS})
    tables[role].append(tables[role][1])
    with pytest.raises(CoreError, match='duplicated'):
        _snapshot(tmp_path, tables=tables)
    assert not (tmp_path / 'snapshot.duckdb').exists()
    assert not (tmp_path / 'snapshot.duckdb.manifest.json').exists()


def test_star_null_endpoint_and_lookup_unknown_evidence(tmp_path):
    from packages.metricguard_core.relational import context_for
    from packages.metricguard_core.relational_compare import compare_relational
    from packages.metricguard_core.relational_evidence import RelationalEvidenceValidator
    from packages.metricguard_core.relational_star import explain_row
    database, contract, manifest = _snapshot(tmp_path)
    baseline = parse_relational('SELECT SUM(o.amount) FROM orders o', contract)
    target = parse_relational('SELECT SUM(o.amount) FROM orders o '
                              'LEFT JOIN customers c ON o.customer_id = c.customer_id '
                              "WHERE c.segment = 'absent'", contract)
    result = compare_relational(database, baseline, target, context_for(contract))
    assert result['target_value'] is None and result['delta_target_minus_baseline'] is None
    assert result['residual'] is None and result['steps'] == []
    context = {'contract': contract, 'manifest': manifest, 'baseline': baseline, 'target': target}
    rows = {r['fact_key']: r for r in result['evidence']}
    assert explain_row(context, rows['O4'])['target']['outcome'] == 'excluded_filter_unknown'
    with RelationalEvidenceValidator(context) as validator:
        for row in result['evidence']:
            validator.add(row)
        validator.finish(result)


def test_star_evidence_and_manifest_forgery_are_rejected(tmp_path):
    from packages.metricguard_core.relational import context_for, validate_ir
    from packages.metricguard_core.relational_compare import compare_relational
    from packages.metricguard_core.relational_evidence import RelationalEvidenceValidator, validate_result
    from packages.metricguard_core.relational_snapshot import validate_manifest
    database, contract, manifest = _snapshot(tmp_path)
    baseline = parse_relational('SELECT SUM(o.amount) FROM orders o', contract)
    target = parse_relational('SELECT SUM(o.amount) FROM orders o JOIN products p ON o.product_id = p.product_id', contract)
    context = {'contract': contract, 'manifest': manifest, 'baseline': baseline, 'target': target}
    result = compare_relational(database, baseline, target, context_for(contract))
    row = deepcopy(result['evidence'][0])
    row['links']['product']['foreign_key'] = 'forged'
    with RelationalEvidenceValidator(context) as validator, pytest.raises(ValueError, match='EVIDENCE_INVALID'):
        validator.add(row)
    with pytest.raises(ValueError, match='RELATIONAL_RESULT_INVALID'):
        validate_result({**result, 'absolute_gap': '0.00'}, context)
    forged = deepcopy(manifest)
    forged['join_proof']['dimensions']['product']['dimension_rows'] = 0
    forged['manifest_hash'] = digest({k: v for k, v in forged.items() if k != 'manifest_hash'})
    with pytest.raises(CoreError):
        validate_manifest(forged, contract)
    forged = deepcopy(target)
    forged['predicates'] = [{'table': [], 'column': 'category', 'op': 'eq', 'values': ['books']}]
    forged['ir_hash'] = digest({k: v for k, v in forged.items() if k != 'ir_hash'})
    with pytest.raises(CoreError):
        validate_ir(forged, contract)


def test_star_governance_checks_explicit_lookup_with_incomplete_coverage(tmp_path):
    from services.api.models import RelationalMetricContractExpectations
    from services.api.relational_governance import relational_checks, validate_proposal
    from services.api.store import DomainError

    _, contract, manifest = _snapshot(tmp_path)
    ir = parse_relational('SELECT SUM(o.amount) FROM orders o '
                          'LEFT JOIN customers c ON o.customer_id = c.customer_id '
                          'JOIN products p ON o.product_id = p.product_id', contract)
    expected = RelationalMetricContractExpectations.model_validate({
        'schema_version': 2, 'owner_label': 'Analyst', 'meaning': 'Orders by product', 'unit_label': 'INR',
        'fact': {k: contract['fact'][k] for k in ('table', 'key', 'grain')},
        'dimension': {k: contract['dimensions'][1][k] for k in ('table', 'key', 'grain')},
        'relationship': {'fact_foreign_key': 'product_id', 'dimension_key': 'product_id',
                         'cardinality': 'many_to_one', 'allowed_join_kinds': ['inner'],
                         'missing_dimension_policy': 'preserve_and_report'},
        'expected_fields': [{'table': 'products', 'column': 'category', 'type': 'string'}],
        'source_expectations': {'mode': 'relational_snapshot', 'execution_policy': 'relational_snapshot_v1',
                                'contract_hash': contract['contract_hash']},
    }).model_dump()
    validate_proposal(expected, contract, DomainError)
    metric = {'display_name': 'Orders', 'importance': 'standard'}
    version = {'id': 'v1', 'ir': ir, 'ir_hash': ir['ir_hash'], 'raw_hash': 'a' * 64,
               'contract_hash': contract['contract_hash'], 'execution_policy': 'relational_snapshot_v1',
               'support_state': 'executable'}
    revision = {'expectations': expected, 'metric_identity': metric,
                'approved_definition': {'version_id': 'v1', 'raw_hash': version['raw_hash'],
                                        'ir_hash': ir['ir_hash'], 'contract_hash': contract['contract_hash']}}
    snapshot = {'manifest': manifest, 'manifest_hash': manifest['manifest_hash'],
                'contract_hash': contract['contract_hash'], 'execution_policy': 'relational_snapshot_v1'}
    checks, observed, assurance = relational_checks(revision, metric, version, {'source_label': 'Manual'},
                                                   snapshot, {'approved_version_id': 'v1'}, contract, DomainError)
    by_field = {item['field']: item for item in checks}
    assert by_field['dimension.table']['observed'] == 'products'
    assert by_field['relationship.join_kind']['observed'] == 'inner'
    assert by_field['relationship.fact_foreign_key']['observed'] == 'product_id'
    assert by_field['lookup_governance_coverage']['status'] == 'unverified'
    assert by_field['lookup_governance_coverage']['observed']['uncovered_tables'] == ['customers']
    assert not any(item['status'] == 'mismatch' for item in checks)
    assert observed['governance_scope'] == 'selected_lookup_only'
    assert len(observed['all_join_proof']['dimensions']) == 2
    assert assurance['structural_join_proof']['status'] == 'observed_preserved'
    # A schema-2 declaration cannot silently bind the fact table as its dimension.
    invalid = deepcopy(expected)
    invalid['dimension']['table'] = 'orders'
    with pytest.raises(DomainError):
        validate_proposal(invalid, contract, DomainError)


class _Done:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class _InProcess:
    def start(self, input_dir, output_dir, command=None):
        (ingest_job if command == 'ingest' else run_job)(input_dir, output_dir)
        return _Done()


@pytest.mark.parametrize('variant', ['missing', 'extra', 'duplicate'])
def test_star_upload_refuses_missing_extra_or_duplicate_files(tmp_path, variant):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        response = client.post('/api/v1/relational/contracts', json=CONTRACT)
        assert response.status_code == 201, response.text
        files = [('fact_file', ('orders.csv', _csv(ORDERS), 'text/csv')),
                 ('lookup_customer', ('customers.csv', _csv(CUSTOMERS), 'text/csv')),
                 ('lookup_product', ('products.csv', _csv(PRODUCTS), 'text/csv'))]
        if variant == 'missing':
            files.pop()
        elif variant == 'extra':
            files.append(('lookup_unknown', ('unknown.csv', b'id\n1\n', 'text/csv')))
        else:
            files.append(files[1])
        uploaded = client.post('/api/v1/relational/snapshots', data={'contract_id': response.json()['id']}, files=files)
        assert uploaded.status_code == 422, uploaded.text
        assert not (tmp_path / 'uploads').exists()


def test_star_http_run_evidence_breakdown_and_replay(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), _InProcess(), tmp_path / 'jobs', 'star-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/relational/contracts', json=CONTRACT)
        assert contract.status_code == 201, contract.text
        contract = contract.json()
        uploaded = client.post('/api/v1/relational/snapshots', data={'contract_id': contract['id'], 'name': 'Star'},
                               files={'fact_file': ('orders.csv', _csv(ORDERS), 'text/csv'),
                                      'lookup_customer': ('customers.csv', _csv(CUSTOMERS), 'text/csv'),
                                      'lookup_product': ('products.csv', _csv(PRODUCTS), 'text/csv')})
        assert uploaded.status_code == 202, uploaded.text
        assert worker.run_once()
        imported = client.get(f"/api/v1/snapshot-imports/{uploaded.json()['id']}").json()
        assert imported['state'] == 'validated', imported['error']
        snapshot_id = imported['snapshot_id']
        ids = []
        for name, sql in [('All paid', "SELECT SUM(o.amount) FROM orders o WHERE o.status = 'paid'"),
                          ('Retail books', 'SELECT SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.customer_id '
                                           "JOIN products p ON o.product_id = p.product_id WHERE p.category = 'books'")]:
            response = client.post('/api/v1/relational/definitions', json={'name': name, 'raw_sql': sql, 'contract_id': contract['id']})
            assert response.status_code == 201, response.text
            ids.append(response.json()['versions'][0]['id'])
        context = {'mode': 'relational_snapshot', 'scope': 'entire_supplied_snapshot',
                   'contract_hash': contract['contract_hash'], 'unit_label': 'INR', 'values_comparable': True}
        queued = client.post('/api/v1/relational/comparisons', headers={'Idempotency-Key': 'star-run'}, json={
            'baseline_version_id': ids[0], 'target_version_id': ids[1], 'snapshot_id': snapshot_id, 'context': context})
        assert queued.status_code == 202, queued.text
        assert worker.run_once()
        run = client.get(f"/api/v1/comparisons/{queued.json()['id']}").json()
        assert run['state'] == 'succeeded', (run['state'], run['error'])
        # Paid: O1 100 + O2 50 + O4 20 + O5 40 + O6 10 = 220.
        # Customer and product both matched, category books: O1 100 (O4 has no customer) -> 100.
        assert (run['result']['baseline_value'], run['result']['target_value'],
                run['result']['delta_target_minus_baseline']) == ('220.00', '100.00', '-120.00')
        page = client.get(f"/api/v1/comparisons/{run['id']}/evidence?limit=10").json()
        rows = {r['fact_key']: r for r in page['items']}
        assert rows['O4']['links']['customer']['membership'] == 'null_foreign_key'
        assert rows['O5']['links']['customer']['membership'] == 'missing_dimension'
        assert rows['O6']['links']['product']['membership'] == 'null_foreign_key'
        joined = client.get(f"/api/v1/comparisons/{run['id']}/join-evidence").json()
        assert joined['join_evidence']['summary']['relationship_counts']['customer']['matched'] == 4
        split = client.get(f"/api/v1/comparisons/{run['id']}/evidence-breakdown?column=product.category").json()
        # O2 toys -50; O4 books -20; O5 (no product match) -40 and O6 (no product) -10 -> blank -50.
        assert split['available'] and {g['value']: g['change'] for g in split['groups']} == {'books': '-20.00', 'toys': '-50.00', None: '-50.00'}
        for fmt in ('json', 'html'):
            assert client.get(f"/api/v1/comparisons/{run['id']}/export?format={fmt}").status_code == 200
        saved = client.post('/api/v1/investigations', json={'title': 'Star', 'run_id': run['id'],
                                                           'decision': 'Joins drop orders', 'actor_label': 'Tester'})
        assert saved.status_code == 201, saved.text
        replay = client.post(f"/api/v1/investigations/{saved.json()['id']}/replays", headers={'Idempotency-Key': 'star-replay'})
        assert replay.status_code == 202, replay.text
        assert worker.run_once()
        replayed = client.get(f"/api/v1/comparisons/{replay.json()['id']}").json()
        assert replayed['state'] == 'succeeded', replayed['error']
        assert replayed['result']['evidence_artifact']['sha256'] == run['result']['evidence_artifact']['sha256']


V1_CONTRACT = {
    'version': 1, 'mode': 'relational_snapshot', 'policy': 'relational-snapshot-v1', 'name': 'Orders with customers',
    'fact': {'table': 'orders', 'key': 'order_id', 'grain': 'one row per order', 'categories': ['status']},
    'dimension': {'table': 'customers', 'key': 'customer_id', 'grain': 'one row per customer', 'categories': ['segment']},
    'relationship': {'fact_foreign_key': 'customer_id', 'dimension_key': 'customer_id', 'cardinality': 'many_to_one',
                     'missing_dimension_policy': 'preserve_and_report'},
    'measure': {'column': 'amount', 'precision': 12, 'scale': 2}, 'measure_meaning': 'Order amount',
    'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}


def test_one_lookup_contract_v1_still_runs_and_replays(tmp_path):
    """Regression guard: contract v1 keeps its own IR, evidence rows and join-evidence policy."""
    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), _InProcess(), tmp_path / 'jobs', 'v1-worker')
    orders = [[c for i, c in enumerate(row) if i != 2] for row in ORDERS]  # drop product_id
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/relational/contracts', json=V1_CONTRACT)
        assert contract.status_code == 201, contract.text
        contract = contract.json()
        assert contract['version'] == 1 and 'dimensions' not in contract
        uploaded = client.post('/api/v1/relational/snapshots', data={'contract_id': contract['id'], 'name': 'V1'},
                               files={'fact_file': ('orders.csv', _csv(orders), 'text/csv'),
                                      'dimension_file': ('customers.csv', _csv(CUSTOMERS), 'text/csv')})
        assert uploaded.status_code == 202, uploaded.text
        assert worker.run_once()
        imported = client.get(f"/api/v1/snapshot-imports/{uploaded.json()['id']}").json()
        assert imported['state'] == 'validated', imported['error']
        ids = []
        for name, sql in [('Paid', "SELECT SUM(o.amount) FROM orders o LEFT JOIN customers c ON o.customer_id = c.customer_id WHERE o.status = 'paid'"),
                          ('Retail', "SELECT SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.customer_id WHERE c.segment = 'retail'")]:
            response = client.post('/api/v1/relational/definitions', json={'name': name, 'raw_sql': sql, 'contract_id': contract['id']})
            assert response.status_code == 201, response.text
            assert response.json()['versions'][0]['ir']['join'] in ('left', 'inner')
            ids.append(response.json()['versions'][0]['id'])
        context = {'mode': 'relational_snapshot', 'scope': 'entire_supplied_snapshot',
                   'contract_hash': contract['contract_hash'], 'unit_label': 'INR', 'values_comparable': True}
        queued = client.post('/api/v1/relational/comparisons', headers={'Idempotency-Key': 'v1-run'}, json={
            'baseline_version_id': ids[0], 'target_version_id': ids[1], 'snapshot_id': imported['snapshot_id'], 'context': context})
        assert queued.status_code == 202, queued.text
        assert worker.run_once()
        run = client.get(f"/api/v1/comparisons/{queued.json()['id']}").json()
        assert run['state'] == 'succeeded', (run['state'], run['error'])
        # Paid (LEFT): O1 100 + O2 50 + O4 20 + O5 40 + O6 10 = 220. Retail (INNER): O1 100 + O3 30 = 130.
        assert (run['result']['baseline_value'], run['result']['target_value']) == ('220.00', '130.00')
        joined = client.get(f"/api/v1/comparisons/{run['id']}/join-evidence").json()
        assert joined['join_evidence']['summary']['policy'] == 'relational-join-evidence-v1'
        assert 'dimension_matched' in joined['items'][0]
        split = client.get(f"/api/v1/comparisons/{run['id']}/evidence-breakdown?column=dimension.segment").json()
        assert split['available']
        assert client.get(f"/api/v1/comparisons/{run['id']}/export?format=html").status_code == 200


def test_prepared_star_keeps_readable_column_names_and_exact_totals(tmp_path):
    """Preparing a star for large comparisons keeps source names; a shared name gets its role prefix."""
    from packages.metricguard_core.flat import compare_flat, parse_flat
    from packages.metricguard_core.relational_prepared import prepare
    contract = deepcopy(CONTRACT)
    contract['dimensions'][0]['categories'] = ['segment', 'region']
    contract['dimensions'][1]['categories'] = ['category', 'region']
    tables = {'fact': ORDERS,
              'customer': [['customer_id', 'segment', 'region'], ['C1', 'retail', 'north'], ['C2', 'trade', 'south']],
              'product': [['product_id', 'category', 'region'], ['P1', 'books', 'east'], ['P2', 'toys', 'west']]}
    database, _, _ = _snapshot(tmp_path, contract, tables)
    flat_contract, manifest = prepare(database, tmp_path / 'prepared.duckdb', name='Orders prepared',
                                      joins={'customer': 'left', 'product': 'left'},
                                      selected_columns=['fact.status', 'customer.segment', 'customer.region',
                                                        'product.category', 'product.region'])
    mapping = manifest['relational_preparation']['column_mapping']
    assert mapping == {'customer.region': 'customer_region', 'customer.segment': 'segment',
                       'fact.status': 'status', 'product.category': 'category', 'product.region': 'product_region'}
    assert set(flat_contract['categories']) == set(mapping.values())
    a = parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'paid'", flat_contract)
    b = parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'paid' AND segment = 'retail' "
                   "AND product_region = 'east'", flat_contract)
    context = {'mode': 'flat_snapshot', 'scope': flat_contract['scope'], 'contract_hash': flat_contract['contract_hash'],
               'unit_label': flat_contract['unit_label'], 'values_comparable': True,
               'manifest_hash': manifest['manifest_hash']}
    result = compare_flat(tmp_path / 'prepared.duckdb', a, b, context)
    # paid: O1 100 + O2 50 + O4 20 + O5 40 + O6 10 = 220; retail and product east (P1): O1 only = 100
    assert (result['baseline_value'], result['target_value']) == ('220.00', '100.00')


def test_star_not_equal_and_not_in_follow_sql_null_rules(tmp_path):
    """<> and NOT IN on lookup and fact columns; a blank (NULL) value matches neither, as in SQL."""
    from packages.metricguard_core.relational import context_for
    from packages.metricguard_core.relational_compare import compare_relational
    from packages.metricguard_core.relational_evidence import RelationalEvidenceValidator
    database, contract, manifest = _snapshot(tmp_path)
    baseline = parse_relational('SELECT SUM(o.amount) FROM orders o', contract)
    target = parse_relational('SELECT SUM(o.amount) FROM orders o LEFT JOIN customers c ON o.customer_id = c.customer_id '
                              "WHERE c.segment <> 'trade' AND o.status NOT IN ('refunded', 'void')", contract)
    assert {p['op'] for p in target['predicates']} == {'neq', 'not_in'}
    result = compare_relational(database, baseline, target, context_for(contract))
    # Only O1 (retail, paid) stays: O2/O6 are trade, O3 refunded, O4 has no customer and O5 an unknown one (NULL segment).
    assert (result['baseline_value'], result['target_value'], result['delta_target_minus_baseline']) == ('250.00', '100.00', '-150.00')
    context = {'contract': contract, 'manifest': manifest, 'baseline': baseline, 'target': target}
    with RelationalEvidenceValidator(context, include_join_evidence=True) as validator:
        for row in result['evidence']:
            validator.add(row)
        validator.finish(result)
