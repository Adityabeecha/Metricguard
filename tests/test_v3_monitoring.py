"""V3 Recurring Checks, Monitoring, Materiality & Alerting Tests.

Verifies:
- 3-way change classification: definition_change vs dataset_change vs combined_change.
- Absolute and relative materiality threshold policies.
- Zero-denominator and undefined endpoint handling.
- Intentional variant suppression.
- Deterministic event deduplication.
- In-app alert inbox and acknowledgement workflow.
"""
import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.monitoring import classify_change, evaluate_materiality, compute_event_hash
from scripts.seed_demo import seed


def test_three_way_change_classification():
    """Verify distinct classification between definition change, dataset change, and combined change."""
    ver1 = {'id': 'v1', 'raw_hash': 'h1', 'ir_hash': 'ir1'}
    ver2 = {'id': 'v2', 'raw_hash': 'h2', 'ir_hash': 'ir2'}
    snap1 = {'id': 's1', 'manifest_hash': 'mh1'}
    snap2 = {'id': 's2', 'manifest_hash': 'mh2'}

    # 1. Same snapshot, different definition -> definition_change
    assert classify_change(ver1, ver2, snap1, snap1) == 'definition_change'

    # 2. Same definition, different snapshot -> dataset_change
    assert classify_change(ver1, ver1, snap1, snap2) == 'dataset_change'

    # 3. Different definition, different snapshot -> combined_change
    assert classify_change(ver1, ver2, snap1, snap2) == 'combined_change'

    # 4. Same definition, same snapshot -> unchanged
    assert classify_change(ver1, ver1, snap1, snap1) == 'unchanged'


def test_materiality_evaluation_rules():
    """Verify absolute, relative, zero-denominator, undefined, and variant suppression policies."""
    # Absolute threshold
    res = evaluate_materiality(delta_str='200.00', baseline_val_str='10000.00', target_val_str='10200.00',
                               absolute_threshold='500.00')
    assert res['is_material'] is False

    res = evaluate_materiality(delta_str='600.00', baseline_val_str='10000.00', target_val_str='10600.00',
                               absolute_threshold='500.00')
    assert res['is_material'] is True
    assert res['reason'] == 'exceeds_absolute_threshold'

    # Relative threshold (5%)
    res = evaluate_materiality(delta_str='30.00', baseline_val_str='1000.00', target_val_str='1030.00',
                               relative_threshold=0.05)
    assert res['is_material'] is False

    res = evaluate_materiality(delta_str='60.00', baseline_val_str='1000.00', target_val_str='1060.00',
                               relative_threshold=0.05)
    assert res['is_material'] is True
    assert res['reason'] == 'exceeds_relative_threshold'

    # Zero denominator with non-zero delta
    res = evaluate_materiality(delta_str='50.00', baseline_val_str='0.00', target_val_str='50.00',
                               relative_threshold=0.05)
    assert res['is_material'] is True
    assert res['reason'] == 'zero_denominator_non_zero_delta'

    # Undefined outcome
    res = evaluate_materiality(delta_str=None, baseline_val_str='1000.00', target_val_str=None)
    assert res['is_material'] is True
    assert res['reason'] == 'undefined_endpoint'

    # Intentional variant suppression
    res = evaluate_materiality(delta_str='50000.00', baseline_val_str='1000.00', target_val_str='51000.00',
                               absolute_threshold='100.00', is_intentional_variant=True, suppress_intentional_variants=True)
    assert res['is_material'] is False
    assert res['reason'] == 'suppressed_intentional_variant'


@pytest.fixture
def monitoring_env(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    store = app.state.store

    owner, owner_key = store.create_user('Mon Owner', 'mon_owner@metricguard.internal')
    analyst, analyst_key = store.create_user('Mon Analyst', 'mon_analyst@metricguard.internal')

    proj = store.create_project('Monitoring Project', owner_id=owner['id'], project_id='proj-mon')
    store.add_project_member('proj-mon', analyst['id'], 'analyst')

    def1 = store.record_definition(
        name='KPI One', source_label='Warehouse', owner_label='Ops',
        raw_sql='SELECT SUM(order_amount) FROM orders', time_column='order_date',
        project_id='proj-mon'
    )
    def2 = store.record_definition(
        name='KPI Two', source_label='Warehouse', owner_label='Ops',
        raw_sql='SELECT SUM(order_amount) FROM orders WHERE payment_status = \'failed\'',
        time_column='order_date', project_id='proj-mon'
    )

    snap = store.bootstrap('local-retail')['snapshots'][0]
    snap_mon = store.record_snapshot(
        name='Snap Mon', manifest=snap['manifest'],
        artifact_path=snap['artifact_path'], project_id='proj-mon'
    )

    context = {
        'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
        'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
        'scope': 'synthetic_retail_all_orders'
    }

    run_req = {
        'baseline_version_id': def1['versions'][0]['id'],
        'target_version_id': def2['versions'][0]['id'],
        'snapshot_id': snap_mon['id'],
        'context': context,
        'relationship': 'candidate'
    }
    run = store.queue(run_req, idempotency_key='mon-run-1', execution_mode='demo', project_id='proj-mon')
    mock_result = {
        'baseline_value': '104000000.00', 'target_value': '98000000.00',
        'delta_target_minus_baseline': '-6000000.00', 'explanation_status': 'complete_ordered_bridge',
        'residual': '0.00', 'evidence': [],
        'provenance': {'baseline_source_hash': 'h1', 'target_source_hash': 'h2', 'context_fingerprint': 'c1',
                       'snapshot_manifest_hash': snap_mon['manifest_hash']}
    }
    run = store.complete_demo(run['id'], mock_result)

    with TestClient(app) as client:
        yield {
            'client': client,
            'store': store,
            'owner_token': owner_key,
            'analyst_token': analyst_key,
            'def1': def1,
            'def2': def2,
            'snap': snap_mon,
            'run': run,
        }


def test_monitoring_check_alerting_deduplication_and_acknowledgement(monitoring_env):
    """Recurring check evaluates materiality, deduplicates alerts, and supports in-app acknowledgement."""
    client = monitoring_env['client']
    a_headers = {'Authorization': f"Bearer {monitoring_env['analyst_token']}", 'X-Project-Id': 'proj-mon'}
    run_id = monitoring_env['run']['id']

    # 1. Analyst creates a monitoring policy with absolute threshold of 1,000,000.00
    pol_res = client.post("/api/v1/projects/proj-mon/monitoring-policies", headers=a_headers, json={
        'name': 'Gross Gap Monitor',
        'baseline_version_id': monitoring_env['def1']['versions'][0]['id'],
        'target_version_id': monitoring_env['def2']['versions'][0]['id'],
        'snapshot_id': monitoring_env['snap']['id'],
        'schedule_type': 'event_triggered',
        'absolute_threshold': '1000000.00',
        'suppress_intentional_variants': True,
        'enabled': True
    })
    assert pol_res.status_code == 201
    policy = pol_res.json()
    policy_id = policy['id']

    # 2. Trigger monitoring check: delta is -6,000,000.00 -> exceeds 1,000,000.00 threshold -> triggers alert
    check_res = client.post(f"/api/v1/projects/proj-mon/monitoring-checks?policy_id={policy_id}&run_id={run_id}", headers=a_headers)
    assert check_res.status_code == 200
    check_data = check_res.json()
    assert check_data['change_type'] == 'definition_change'
    assert check_data['materiality']['is_material'] is True
    assert check_data['is_new_alert'] is True
    alert = check_data['alert']
    assert alert is not None
    assert alert['state'] == 'unacknowledged'

    # 3. Deduplication check: re-running the check for the same run produces NO duplicate alert
    dup_res = client.post(f"/api/v1/projects/proj-mon/monitoring-checks?policy_id={policy_id}&run_id={run_id}", headers=a_headers)
    assert dup_res.status_code == 200
    dup_data = dup_res.json()
    assert dup_data['is_new_alert'] is False
    assert dup_data['alert']['id'] == alert['id']

    # Exactly one alert exists in project
    alerts = client.get("/api/v1/projects/proj-mon/alerts", headers=a_headers).json()
    assert len(alerts) == 1
    assert alerts[0]['id'] == alert['id']
    assert alerts[0]['state'] == 'unacknowledged'

    # 4. Analyst acknowledges the alert
    ack_res = client.post(f"/api/v1/alerts/{alert['id']}/acknowledge", headers=a_headers, json={
        'note': 'Investigated: expected gap due to failed order filter. Review underway.'
    })
    assert ack_res.status_code == 200
    ack_alert = ack_res.json()
    assert ack_alert['state'] == 'acknowledged'
    assert ack_alert['note'] == 'Investigated: expected gap due to failed order filter. Review underway.'
    assert ack_alert['acknowledged_at'] is not None

    # 5. Filter alerts by status
    open_alerts = client.get("/api/v1/projects/proj-mon/alerts?status=unacknowledged", headers=a_headers).json()
    assert len(open_alerts) == 0

    acked_alerts = client.get("/api/v1/projects/proj-mon/alerts?status=acknowledged", headers=a_headers).json()
    assert len(acked_alerts) == 1
    assert acked_alerts[0]['id'] == alert['id']
