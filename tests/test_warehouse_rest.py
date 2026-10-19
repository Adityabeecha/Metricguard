"""Snowflake and BigQuery sources, against faithful fakes of their REST APIs (no live account)."""
import csv
import json

import httpx
import pytest

from services.supervisor.collector import CollectorError
from services.supervisor import warehouse_rest as wr

TOKEN = 'pat-SECRET-0123456789'
COLUMNS = [('order_id', 'string'), ('amount', 'amount'), ('status', 'string'), ('ordered_on', 'date'), ('paid', 'boolean')]
ROWS = [['O1', '19.990', 'paid', '2026-09-01', 'true'], ['O2', None, 'paid', '2026-09-02', None],
        ['O3', '250', 'refunded', '2026-09-03', 'false'], ['O4', '-0.50', 'paid', None, 'true']]


def _selection(kind, **extra):
    common = dict(kind=kind, token=TOKEN, table='orders', columns=COLUMNS, row_key='order_id', amount_scales={'amount': 2})
    if kind == 'snowflake':
        common.update(account='myorg-acct', database='analytics', schema='sales', warehouse='compute_wh')
    else:
        common.update(project='my-analytics-123', dataset='sales', location='US')
    return wr.WarehouseSelection(**common, **extra)


def _read(path):
    with open(path, encoding='utf-8', newline='') as stream:
        return list(csv.reader(stream))


EXPECTED = [['order_id', 'amount', 'status', 'ordered_on', 'paid'], ['O1', '19.99', 'paid', '2026-09-01', 'true'],
            ['O2', '', 'paid', '2026-09-02', ''], ['O3', '250', 'refunded', '2026-09-03', 'false'], ['O4', '-0.50', 'paid', '', 'true']]


def snowflake_fake(requests, rows=ROWS, names=None):
    names = names or [c.upper() for c, _ in COLUMNS]

    def handle(request):
        requests.append(request)
        assert request.headers['Authorization'] == f'Bearer {TOKEN}'
        assert request.headers['X-Snowflake-Authorization-Token-Type'] == 'PROGRAMMATIC_ACCESS_TOKEN'
        if request.method == 'POST':
            body = json.loads(request.content)
            assert body['statement'].startswith('SELECT ') and 'FROM analytics.sales.orders' in body['statement']
            return httpx.Response(202, json={'statementHandle': 'abc-123', 'message': 'Asynchronous execution in progress.'})
        partition = int(request.url.params.get('partition', '0'))
        meta = {'numRows': len(rows), 'rowType': [{'name': n} for n in names], 'partitionInfo': [{}, {}]}
        if partition == 0:  # first poll completes; data of partition 0 comes with it
            return httpx.Response(200, json={'statementHandle': 'abc-123', 'resultSetMetaData': meta, 'data': rows[:2]})
        return httpx.Response(200, json={'data': rows[2:]})
    return httpx.Client(transport=httpx.MockTransport(handle))


def bigquery_fake(requests, rows=ROWS):
    pages = [rows[:3], rows[3:]]

    def page(index, complete=True):
        body = {'jobReference': {'jobId': 'job_1', 'location': 'US'}, 'jobComplete': complete}
        if complete:
            body.update(schema={'fields': [{'name': c} for c, _ in COLUMNS]},
                        rows=[{'f': [{'v': v} for v in r]} for r in pages[index]], **({'pageToken': 'next'} if index == 0 else {}))
        return httpx.Response(200, json=body)

    def handle(request):
        requests.append(request)
        assert request.headers['Authorization'] == f'Bearer {TOKEN}'
        if request.method == 'POST':
            body = json.loads(request.content)
            assert body['useLegacySql'] is False and 'FROM `my-analytics-123.sales.orders`' in body['query']
            return page(0, complete=False)
        return page(1 if request.url.params.get('pageToken') == 'next' else 0)
    return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(wr, 'POLL_SECONDS', 0)


@pytest.mark.parametrize('kind,fake', [('snowflake', snowflake_fake), ('bigquery', bigquery_fake)])
def test_rows_are_written_as_exact_text_across_pages(tmp_path, kind, fake):
    requests = []
    rows = wr.extract_to_csv(_selection(kind), tmp_path / 'out.csv', client=fake(requests))
    assert rows == 4 and _read(tmp_path / 'out.csv') == EXPECTED
    assert len(requests) >= 3  # submit, poll, next page or partition


def test_queries_convert_every_value_to_text_and_bind_the_cutoff():
    sql, bindings = wr.snowflake_query(_selection('snowflake', cutoff_column='ordered_on', cutoff_type='date', cutoff_value='2026-09-30', sample_rows=1000))
    assert "TO_VARCHAR(ordered_on, 'YYYY-MM-DD') AS \"ordered_on\"" in sql and 'TO_VARCHAR(amount) AS "amount"' in sql
    assert sql.endswith('WHERE ordered_on <= TO_DATE(?) ORDER BY order_id LIMIT 1000')
    assert bindings == {'1': {'type': 'TEXT', 'value': '2026-09-30'}}
    sql, parameters = wr.bigquery_query(_selection('bigquery', cutoff_column='ordered_on', cutoff_type='date', cutoff_value='2026-09-30'))
    assert 'CAST(`amount` AS STRING) AS `amount`' in sql and sql.endswith('WHERE `ordered_on` <= CAST(@cutoff AS DATE) ORDER BY `order_id`')
    assert parameters[0]['parameterValue'] == {'value': '2026-09-30'}  # bound, never pasted into SQL


def test_extra_digits_are_kept_for_the_import_to_refuse():
    assert wr.canonical_amount('19.990', 2) == '19.99'
    assert wr.canonical_amount('19.991', 2) == '19.991'  # not rounded
    assert wr.canonical_amount('7.000', 0) == '7'
    assert wr.canonical_amount(None, 2) == ''


@pytest.mark.parametrize('field,value', [('table', 'orders; DROP TABLE x'), ('account', 'evil.com/x?'), ('database', 'a"b'),
                                         ('token', 'has space token'), ('cutoff_value', "2026-09-30' OR 1=1")])
def test_unsafe_inputs_are_refused_before_any_request(tmp_path, field, value):
    extra = {'cutoff_column': 'ordered_on', 'cutoff_type': 'date'} if field == 'cutoff_value' else {}
    selection = _selection('snowflake', **extra)
    setattr(selection, field, value)
    with pytest.raises(CollectorError):
        wr.extract_to_csv(selection, tmp_path / 'out.csv', client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail('no request may be sent'))))


def test_token_never_appears_in_errors(tmp_path):
    def refuse(request):
        return httpx.Response(400, json={'message': f'bad statement near Bearer {TOKEN}'})
    with pytest.raises(CollectorError) as error:
        wr.extract_to_csv(_selection('snowflake'), tmp_path / 'out.csv', client=httpx.Client(transport=httpx.MockTransport(refuse)))
    assert TOKEN not in error.value.message and 'HTTP 400' in error.value.message
    with pytest.raises(CollectorError, match='refused the token'):
        wr.extract_to_csv(_selection('bigquery'), tmp_path / 'out.csv',
                          client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))))


def test_columns_that_differ_from_the_contract_are_refused(tmp_path):
    with pytest.raises(CollectorError, match='different columns'):
        wr.extract_to_csv(_selection('snowflake'), tmp_path / 'out.csv',
                          client=snowflake_fake([], names=['ORDER_ID', 'AMOUNT', 'STATUS', 'ORDERED_ON', 'OTHER']))


def test_route_queues_an_isolated_import_without_storing_the_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from scripts.seed_demo import seed
    from services.api.app import create_app
    from services.api.store import Store
    from services.supervisor.worker import Supervisor
    from tests.test_flat_workflow import InProcessLauncher
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    real_client = httpx.Client
    monkeypatch.setattr(wr.httpx, 'Client', lambda **kwargs: snowflake_fake([]) if not kwargs.get('transport') else real_client(**kwargs))
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/flat/contracts', json={
            'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'Snowflake orders',
            'row_key': 'order_id', 'measure': 'amount', 'precision': 12, 'scale': 2,
            'categories': ['status', 'ordered_on', 'paid'],
            'dimension_types': {'status': 'string', 'ordered_on': 'date', 'paid': 'boolean'},
            'nullable_categories': ['ordered_on', 'paid'], 'measure_meaning': 'Order value', 'unit_label': 'INR',
            'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}).json()
        response = client.post('/api/v1/projects/local-retail/sources/snowflake/extract', json={
            'contract_id': contract['id'], 'snapshot_name': 'Orders from Snowflake', 'token': TOKEN, 'table': 'orders',
            'account': 'myorg-acct', 'database': 'analytics', 'schema_name': 'sales', 'warehouse': 'compute_wh'})
        assert response.status_code == 202, response.text
        queued = response.json()
        assert TOKEN not in json.dumps(queued)  # the token is never stored with the import
        assert queued['metadata']['warehouse_source']['table'] == 'analytics.sales.orders'
        Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'warehouse-test', data_dir=tmp_path).run_once()
        job = client.get(f"/api/v1/snapshot-imports/{queued['id']}").json()
        assert job['state'] == 'validated', job.get('error')
        snapshot = client.get(f"/api/v1/snapshots/{job['snapshot_id']}").json()
        assert snapshot['row_count'] == 4
