"""
Test Matrix Verification for MetricGuard Data Sources Flow.
Exercises Cases A, B, C, and D against the live API on port 8001.
"""
import io
import json
import time
import urllib.request
import urllib.parse

BASE_URL = 'http://127.0.0.1:8001/api/v1'


def register_contract(name: str, key: str, measure: str, categories: list[str], unit: str, null_policy: str = 'blank_nullable'):
    payload = {
        'version': 3,
        'mode': 'flat_snapshot',
        'name': name,
        'row_key': key,
        'measure': measure,
        'precision': 18,
        'scale': 2,
        'categories': categories,
        'measure_meaning': f'Row {measure}',
        'unit_label': unit,
        'values_comparable': True,
        'scope': 'entire_supplied_snapshot',
        'null_policy': null_policy,
    }
    req = urllib.request.Request(
        f'{BASE_URL}/flat/contracts',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())


def upload_snapshot(filename: str, csv_bytes: bytes, name: str, contract_id: str):
    boundary = '----MetricGuardMatrixBoundary12345'
    body = io.BytesIO()
    body.write(b'--' + boundary.encode('utf-8') + b'\r\n')
    body.write(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n'.encode('utf-8'))
    body.write(csv_bytes)
    body.write(b'\r\n--' + boundary.encode('utf-8') + b'\r\n')
    body.write(f'Content-Disposition: form-data; name="name"\r\n\r\n{name}\r\n'.encode('utf-8'))
    body.write(b'--' + boundary.encode('utf-8') + b'\r\n')
    body.write(f'Content-Disposition: form-data; name="contract_id"\r\n\r\n{contract_id}\r\n'.encode('utf-8'))
    body.write(b'--' + boundary.encode('utf-8') + b'--\r\n')

    req = urllib.request.Request(
        f'{BASE_URL}/flat/snapshots',
        data=body.getvalue(),
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
    )
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())


def poll_import(import_id: str, max_wait_sec: float = 25.0):
    start = time.time()
    while time.time() - start < max_wait_sec:
        req = urllib.request.Request(f'{BASE_URL}/snapshot-imports/{import_id}')
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read())
            if data.get('state') in ('validated', 'failed'):
                return data
        time.sleep(0.5)
    raise TimeoutError(f'Import {import_id} did not finish within {max_wait_sec}s')


def get_bootstrap():
    req = urllib.request.Request(f'{BASE_URL}/bootstrap')
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())


def test_case_a():
    print('\n==================================================')
    print('CASE A: Normal 15-row Orders CSV')
    print('==================================================')
    csv_data = b'''order_id,order_ts,region,status,amount
301,2026-01-01T10:00:00,South,completed,120
302,2026-01-02T11:00:00,South,completed,80
303,2026-01-03T12:00:00,North,completed,200
304,2026-01-04T13:00:00,South,cancelled,150
305,2026-01-05T14:00:00,West,completed,175
306,2026-01-06T15:00:00,South,completed,220
307,2026-01-07T16:00:00,East,completed,90
308,2026-01-08T17:00:00,North,completed,130
309,2026-01-09T18:00:00,South,completed,160
310,2026-01-10T19:00:00,South,refunded,110
311,2026-01-11T20:00:00,West,completed,140
312,2026-01-12T21:00:00,South,completed,100
313,2026-01-13T22:00:00,East,completed,210
314,2026-01-14T23:00:00,South,completed,190
315,2026-01-15T00:00:00,North,completed,250'''

    contract = register_contract('Orders 15 Row Case A', 'order_id', 'amount', ['order_ts', 'region', 'status'], 'INR')
    print(f'Contract registered: {contract["id"]}')

    result = upload_snapshot('orders_case_a.csv', csv_data, 'Orders 15 Row Case A', contract['id'])
    print(f'Upload result state: {result.get("state")}')

    if 'state' in result:
        final_import = poll_import(result['id'])
        print(f'Poll completed: state={final_import["state"]}, snapshot_id={final_import.get("snapshot_id")}')
        assert final_import['state'] == 'validated', f'Expected validated, got {final_import["state"]}'
        snapshot_id = final_import['snapshot_id']
    else:
        snapshot_id = result['id']

    bootstrap = get_bootstrap()
    found = next((s for s in bootstrap['snapshots'] if s['id'] == snapshot_id), None)
    assert found is not None, f'Snapshot {snapshot_id} not found in bootstrap!'
    assert found['row_count'] == 15, f'Expected 15 rows, got {found["row_count"]}'
    print(f'Case A PASSED: Snapshot {found["name"]} ({found["id"]}) validated with {found["row_count"]} rows.')


def test_case_b():
    print('\n==================================================')
    print('CASE B: Web Events / Revenue CSV (Distinct Schema)')
    print('==================================================')
    csv_data = b'''event_id,event_time,user_id,country,device,revenue
e1,2026-02-01T08:00:00,u101,US,desktop,45.50
e2,2026-02-01T09:00:00,u102,GB,mobile,22.00
e3,2026-02-01T10:00:00,u103,US,mobile,110.00
e4,2026-02-01T11:00:00,u104,IN,desktop,85.25
e5,2026-02-01T12:00:00,u105,DE,desktop,19.99
e6,2026-02-01T13:00:00,u106,US,tablet,65.00
e7,2026-02-01T14:00:00,u107,FR,mobile,40.00
e8,2026-02-01T15:00:00,u108,IN,mobile,95.50'''

    contract = register_contract('Web Events Revenue Case B', 'event_id', 'revenue', ['event_time', 'country', 'device'], 'USD')
    print(f'Contract registered: {contract["id"]}')

    result = upload_snapshot('web_events.csv', csv_data, 'Web Events Revenue Case B', contract['id'])
    if 'state' in result:
        final_import = poll_import(result['id'])
        print(f'Poll completed: state={final_import["state"]}, snapshot_id={final_import.get("snapshot_id")}')
        assert final_import['state'] == 'validated', f'Expected validated, got {final_import["state"]}'
        snapshot_id = final_import['snapshot_id']
    else:
        snapshot_id = result['id']

    bootstrap = get_bootstrap()
    found = next((s for s in bootstrap['snapshots'] if s['id'] == snapshot_id), None)
    assert found is not None, f'Snapshot {snapshot_id} not found in bootstrap!'
    assert found['row_count'] == 8, f'Expected 8 rows, got {found["row_count"]}'
    print(f'Case B PASSED: Snapshot {found["name"]} ({found["id"]}) validated with {found["row_count"]} rows.')


def test_case_c():
    print('\n==================================================')
    print('CASE C: Validation Failure (Duplicate Key)')
    print('==================================================')
    csv_data = b'''txn_id,customer,amount
t1,CustA,100.00
t2,CustB,200.00
t1,CustC,300.00''' # Duplicate key t1

    contract = register_contract('Duplicate Txn Case C', 'txn_id', 'amount', ['customer'], 'USD')
    result = upload_snapshot('dup_txn.csv', csv_data, 'Duplicate Txn Case C', contract['id'])
    assert 'state' in result, 'Expected queued import for duplicate file'
    final_import = poll_import(result['id'])
    print(f'Poll completed: state={final_import["state"]}, error={final_import.get("error")}')
    assert final_import['state'] == 'failed', f'Expected failed state, got {final_import["state"]}'
    assert final_import.get('error') is not None, 'Expected error object'
    print(f'Case C PASSED: Import rejected with error: {final_import["error"]}')


def test_case_d():
    print('\n==================================================')
    print('CASE D: Blank/Nullable Fields with NULL Policy')
    print('==================================================')
    csv_data = b'''item_id,category,color,price
item_1,apparel,blue,29.99
item_2,apparel,,19.99
item_3,,red,49.99
item_4,footwear,black,89.00
item_5,,,15.00'''

    contract = register_contract('Nullable Catalog Case D', 'item_id', 'price', ['category', 'color'], 'USD', null_policy='blank_nullable')
    result = upload_snapshot('nullable_catalog.csv', csv_data, 'Nullable Catalog Case D', contract['id'])
    if 'state' in result:
        final_import = poll_import(result['id'])
        print(f'Poll completed: state={final_import["state"]}, snapshot_id={final_import.get("snapshot_id")}')
        assert final_import['state'] == 'validated', f'Expected validated, got {final_import["state"]}'
        snapshot_id = final_import['snapshot_id']
    else:
        snapshot_id = result['id']

    bootstrap = get_bootstrap()
    found = next((s for s in bootstrap['snapshots'] if s['id'] == snapshot_id), None)
    assert found is not None, f'Snapshot {snapshot_id} not found in bootstrap!'
    assert found['row_count'] == 5, f'Expected 5 rows, got {found["row_count"]}'
    print(f'Case D PASSED: Snapshot {found["name"]} ({found["id"]}) validated with {found["row_count"]} rows including nulls.')


if __name__ == '__main__':
    test_case_a()
    test_case_b()
    test_case_c()
    test_case_d()
    print('\nALL 4 TEST MATRIX CASES PASSED!')
