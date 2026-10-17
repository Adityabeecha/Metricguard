import hashlib
import time
from pathlib import Path
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store, DomainError
from services.supervisor.worker import Supervisor
from scripts.seed_demo import seed


class FakeCompleted:
    def poll(self): return 0
    def wait(self, timeout=None): return 0


class FakeLauncher:
    def start(self, input_dir, output_dir, command=None):
        return FakeCompleted()


def test_immutable_review_and_evidence_history(tmp_path):
    """Verify that decisions and historical evidence cannot be overwritten and are immutable."""
    url = f"sqlite:///{tmp_path / 'immutable.db'}"
    seed(url, tmp_path)
    store = Store(url)

    owner, key_o = store.create_user("Owner", "owner@imm.org")
    analyst, key_a = store.create_user("Analyst", "analyst@imm.org")
    proj = store.create_project("Project Imm", owner_id=owner['id'], project_id="proj-imm")
    store.add_project_member("proj-imm", analyst['id'], "analyst")

    def1 = store.record_definition(
        name="Revenue", source_label="BI", owner_label="Analyst",
        raw_sql="SELECT sum(order_amount) AS metric FROM orders",
        time_column="order_date", project_id="proj-imm"
    )
    v1_id = def1['versions'][0]['id']

    # New version v2
    def2 = store.record_definition(
        name="Revenue", source_label="BI", owner_label="Analyst",
        raw_sql="SELECT sum(order_amount) AS metric FROM orders WHERE payment_status = 'captured'",
        time_column="order_date", definition_id=def1['id'], project_id="proj-imm"
    )
    v2_id = def2['versions'][1]['id']

    # Create review request
    rr = store.create_review_request("proj-imm", analyst['id'], {
        "definition_id": def1['id'],
        "baseline_version_id": v1_id,
        "target_version_id": v2_id,
        "rationale": "Initial variant proposal",
        "is_intentional_variant": True,
        "variant_rationale": "Captured only",
        "effective_time": "2026-09-01T00:00:00Z"
    })

    # Record first decision
    dec1 = store.record_decision("proj-imm", owner['id'], {
        "review_request_id": rr['id'],
        "approved_version_id": v2_id,
        "supersedes_version_id": v1_id,
        "decision": "variant_accepted",
        "rationale": "Approved as variant"
    })
    time.sleep(0.01)

    # Record a second decision (superseding previous decision)
    dec2 = store.record_decision("proj-imm", owner['id'], {
        "approved_version_id": v1_id,
        "supersedes_version_id": v2_id,
        "decision": "superseded",
        "rationale": "Reverting back to v1"
    })

    # Verify both decisions exist independently and dec1 was NOT mutated
    all_decisions = store.list_decisions("proj-imm")
    assert len(all_decisions) == 2
    assert all_decisions[1]['id'] == dec1['id']
    assert all_decisions[1]['decision'] == 'variant_accepted'
    assert all_decisions[1]['rationale'] == 'Approved as variant'
    assert all_decisions[0]['id'] == dec2['id']
    assert all_decisions[0]['decision'] == 'superseded'

    # Verify versions are untouched
    assert store.version(v1_id)['version_number'] == 1
    assert store.version(v2_id)['version_number'] == 2


def test_scheduler_lease_recovery_and_stale_worker_rejection(tmp_path):
    """Verify that expired worker leases are recovered and stale workers cannot commit."""
    url = f"sqlite:///{tmp_path / 'lease.db'}"
    seed(url, tmp_path)
    store = Store(url)

    boot = store.bootstrap('local-retail')
    v1 = boot['definitions'][0]['versions'][0]['id']
    v2 = boot['definitions'][0]['versions'][1]['id']
    s1 = boot['snapshots'][0]['id']
    req = {
        'baseline_version_id': v1,
        'target_version_id': v2,
        'snapshot_id': s1,
        'context': {'period': '2026-08'}
    }
    # Queue an isolated run
    run = store.queue(req, idempotency_key='lease-test-1', execution_mode='isolated', project_id='local-retail')
    run_id = run['id']

    # Worker 1 claims with very short lease (1 second)
    claimed1 = store.claim('worker-1', lease_seconds=1)
    assert claimed1 is not None
    assert claimed1['id'] == run_id
    assert claimed1['attempt'] == 1

    # Worker 2 attempts to claim immediately (lease still active) -> None available
    assert store.claim('worker-2', lease_seconds=5) is None

    # Wait for lease to expire
    time.sleep(1.1)

    # Worker 2 now claims expired run -> succeeds with attempt 2
    claimed2 = store.claim('worker-2', lease_seconds=5)
    assert claimed2 is not None
    assert claimed2['id'] == run_id
    assert claimed2['attempt'] == 2

    # Stale Worker 1 wakes up and tries to finish attempt 1 -> rejected!
    fake_result = {
        'baseline_value': '100', 'target_value': '120', 'delta_target_minus_baseline': '20',
        'absolute_gap': '20', 'residual': '0', 'steps': [], 'evidence': [], 'provenance': {},
        'explanation_status': 'complete_ordered_bridge'
    }
    stale_finish = store.finish(run_id, 'worker-1', attempt=1, result=fake_result)
    assert stale_finish is False

    # Worker 2 finishes attempt 2 -> succeeds!
    valid_finish = store.finish(run_id, 'worker-2', attempt=2, result=fake_result)
    assert valid_finish is True

    final_run = store.get_run(run_id)
    assert final_run['state'] == 'succeeded'
    assert final_run['attempt'] == 2


def test_alert_event_deduplication_including_acknowledged(tmp_path):
    """Verify that duplicate equivalent events do not produce duplicate alerts even after acknowledgement."""
    url = f"sqlite:///{tmp_path / 'alert_dedup.db'}"
    store = Store(url)

    owner, key_o = store.create_user("Owner", "owner@dedup.org")
    proj = store.create_project("Project Dedup", owner_id=owner['id'], project_id="proj-dedup")

    pol = store.create_monitoring_policy("proj-dedup", owner['id'], {
        "name": "Daily Revenue Policy",
        "baseline_version_id": "b1",
        "target_version_id": "t1",
        "absolute_threshold": "100.00"
    })

    event_hash = "deterministic-hash-12345"
    alert_data = {"delta": "150.00", "severity": "warning"}

    # Tick 1: First occurrence
    alert1, created1 = store.record_alert("proj-dedup", pol['id'], event_hash, alert_data)
    assert created1 is True
    assert alert1['state'] == 'unacknowledged'

    # Tick 2: Repeated equivalent event before acknowledgement -> deduplicated
    alert2, created2 = store.record_alert("proj-dedup", pol['id'], event_hash, alert_data)
    assert created2 is False
    assert alert2['id'] == alert1['id']

    # Analyst acknowledges alert
    ack = store.acknowledge_alert(alert1['id'], owner['id'], note="Investigating root cause")
    assert ack['state'] == 'acknowledged'

    # Tick 3: Repeated scheduler tick after acknowledgement -> still deduplicated! No duplicate alert!
    alert3, created3 = store.record_alert("proj-dedup", pol['id'], event_hash, alert_data)
    assert created3 is False
    assert alert3['id'] == alert1['id']

    # Verify inbox has exactly 1 alert in total
    all_alerts = store.list_alerts("proj-dedup")
    assert len(all_alerts) == 1
