"""Saved warehouse connections never echo secrets; the inspect step lists tables/columns; private hosts are refused."""
import httpx
import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store, warehouse_connections
from services.supervisor import warehouse_inspect as wi
from services.supervisor.collector import CollectorError

SECRET_URI = 'postgresql://reader:VeryS3cretPass@db.example.com:5432/analytics'


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv('METRICGUARD_ORIGIN_SECRET', raising=False)
    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    owner, owner_key = store.create_user('Owner', 'owner@example.com')
    pid = store.create_project('Team', owner['id'], project_id='team')['id']
    analyst, analyst_key = store.create_user('Analyst', 'analyst@example.com')
    store.add_project_member(pid, analyst['id'], 'analyst')
    client = TestClient(create_app(database_url=url, data_dir=tmp_path))
    return client, store, pid, {'X-API-Key': owner_key, 'X-Project-Id': pid}, {'X-API-Key': analyst_key, 'X-Project-Id': pid}


def test_saving_needs_the_server_key(setup, monkeypatch):
    client, _, pid, owner, _ = setup
    monkeypatch.delenv('METRICGUARD_SECRET_KEY', raising=False)
    with client:
        refused = client.post(f'/api/v1/projects/{pid}/warehouse-connections', headers=owner,
                              json={'kind': 'postgres', 'label': 'Prod', 'source_uri': SECRET_URI})
        assert refused.status_code == 409 and 'type the credentials each time' in refused.json()['error']['message']
        assert client.get(f'/api/v1/projects/{pid}/warehouse-connections', headers=owner).json()['saving_available'] is False


def test_saved_connection_is_encrypted_never_echoed_and_owner_managed(setup, monkeypatch):
    client, store, pid, owner, analyst = setup
    monkeypatch.setenv('METRICGUARD_SECRET_KEY', 'k' * 40)
    seen = {}
    monkeypatch.setattr(wi, 'inspect_postgres', lambda uri, schema, table=None: seen.update(uri=uri, schema=schema, table=table)
                        or {'tables': ['orders'], 'columns': [{'name': 'amount', 'type': 'numeric', 'kind': 'amount'}]})
    with client:
        base = f'/api/v1/projects/{pid}/warehouse-connections'
        assert client.post(base, headers=analyst, json={'kind': 'postgres', 'label': 'Prod', 'source_uri': SECRET_URI}).status_code == 403
        created = client.post(base, headers=owner, json={'kind': 'postgres', 'label': 'Prod', 'source_uri': SECRET_URI})
        assert created.status_code == 201, created.text
        assert created.json()['public'] == {'host': 'db.example.com', 'port': '5432', 'dbname': 'analytics', 'user': 'reader'}
        listing = client.get(base, headers=analyst)
        assert listing.status_code == 200
        for response in (created, listing):
            assert 'VeryS3cretPass' not in response.text and 'secret' not in response.json().get('items', [{}])[0]
        with store.engine.connect() as conn:
            stored = conn.execute(warehouse_connections.select()).mappings().one()
        assert 'VeryS3cretPass' not in stored['secret']  # encrypted at rest
        inspected = client.post(f'/api/v1/projects/{pid}/sources/postgres/inspect', headers=analyst,
                                json={'connection_id': created.json()['id'], 'schema_name': 'public', 'table': 'orders'})
        assert inspected.status_code == 200, inspected.text
        assert seen == {'uri': SECRET_URI, 'schema': 'public', 'table': 'orders'}
        assert 'VeryS3cretPass' not in inspected.text
        wrong_kind = client.post(f'/api/v1/projects/{pid}/sources/snowflake/inspect', headers=analyst,
                                 json={'connection_id': created.json()['id'], 'schema_name': 'public'})
        assert wrong_kind.status_code == 422
        assert client.delete(f"{base}/{created.json()['id']}", headers=analyst).status_code == 403
        assert client.delete(f"{base}/{created.json()['id']}", headers=owner).status_code == 200
        assert client.get(base, headers=owner).json()['items'] == []
        # Another workspace cannot use or see it.
        assert client.post(f'/api/v1/projects/{pid}/sources/postgres/inspect', headers=analyst,
                           json={'connection_id': created.json()['id']}).status_code == 404


def test_private_hosts_are_refused_when_hosted(monkeypatch):
    monkeypatch.setenv('METRICGUARD_BLOCK_PRIVATE_SOURCES', 'true')
    for uri in ('postgresql://u:p@127.0.0.1/db', 'postgresql://u:p@10.0.0.5/db', 'postgresql://u:p@169.254.169.254/db',
                'postgresql://u:p@[::1]/db', 'postgresql:///db?host=/var/run/postgresql'):
        with pytest.raises(CollectorError) as refused:
            wi.guarded_conninfo(uri)
        assert refused.value.code == 'PRIVATE_SOURCE'
    pinned = wi.guarded_conninfo('postgresql://u:p@8.8.8.8:5432/db')
    assert 'hostaddr=8.8.8.8' in pinned
    monkeypatch.setenv('METRICGUARD_BLOCK_PRIVATE_SOURCES', 'false')
    assert wi.guarded_conninfo('postgresql://u:p@127.0.0.1/db') == 'postgresql://u:p@127.0.0.1/db'
    monkeypatch.delenv('METRICGUARD_BLOCK_PRIVATE_SOURCES')
    monkeypatch.setenv('METRICGUARD_ORIGIN_SECRET', 'x')  # hosted behind the proxy: blocked by default
    assert wi.private_sources_blocked() is True


def test_snowflake_and_bigquery_inspect_list_tables_and_typed_columns():
    def handle(request):
        body = request.content.decode()
        if 'snowflakecomputing.com' in str(request.url):
            if 'INFORMATION_SCHEMA.TABLES' in body:
                return httpx.Response(200, json={'data': [['ORDERS'], ['REFUNDS']]})
            return httpx.Response(200, json={'data': [['ORDER_ID', 'TEXT'], ['AMOUNT', 'NUMBER'], ['ORDER_DATE', 'DATE']]})
        if request.url.path.endswith('/tables'):
            return httpx.Response(200, json={'tables': [{'tableReference': {'tableId': 'orders'}}]})
        return httpx.Response(200, json={'schema': {'fields': [{'name': 'amount', 'type': 'NUMERIC'}, {'name': 'paid', 'type': 'BOOL'}]}})
    client = httpx.Client(transport=httpx.MockTransport(handle))
    snow = wi.inspect_snowflake('myorg-acct', 'token-123456', 'PROGRAMMATIC_ACCESS_TOKEN', 'SALES', 'PUBLIC', 'ORDERS', client=client)
    assert snow['tables'] == ['orders', 'refunds']
    assert [(c['name'], c['kind']) for c in snow['columns']] == [('order_id', 'text'), ('amount', 'amount'), ('order_date', 'date')]
    big = wi.inspect_bigquery('my-analytics-123', 'token-123456', 'sales', 'orders', client=client)
    assert big['tables'] == ['orders'] and [(c['name'], c['kind']) for c in big['columns']] == [('amount', 'amount'), ('paid', 'boolean')]
    with pytest.raises(CollectorError):
        wi.inspect_snowflake('bad account!', 'token-123456', 'OAUTH', 'SALES', 'PUBLIC', client=client)
