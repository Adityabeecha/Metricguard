import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.store import Store


def test_authorization_matrix_and_idor_isolation(tmp_path):
    """Verify RBAC and cross-project IDOR rejection across all endpoints."""
    url = f"sqlite:///{tmp_path / 'auth_matrix.db'}"
    store = Store(url)

    # Setup: Project A and Project B
    owner_a, key_oa = store.create_user("Owner A", "owner_a@test.org")
    analyst_a, key_aa = store.create_user("Analyst A", "analyst_a@test.org")
    reader_a, key_ra = store.create_user("Reader A", "reader_a@test.org")
    user_b, key_b = store.create_user("User B", "user_b@test.org")

    proj_a = store.create_project("Project A", owner_id=owner_a['id'], project_id="proj-a")
    proj_b = store.create_project("Project B", owner_id=user_b['id'], project_id="proj-b")

    store.add_project_member("proj-a", analyst_a['id'], "analyst")
    store.add_project_member("proj-a", reader_a['id'], "reader")

    # Seed an object in Project A (a definition)
    def_a = store.record_definition(
        name="Net Sales A",
        raw_sql="SELECT sum(order_amount) AS metric FROM orders",
        source_label="BI",
        owner_label="Analyst",
        time_column="order_date",
        project_id="proj-a"
    )
    ver_a_id = def_a['versions'][0]['id']

    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    client = TestClient(app)

    # 1. Cross-Project Isolation (User B cannot read or access Project A's definition)
    res_b_read = client.get(f"/api/v1/definitions/{def_a['id']}", headers={'Authorization': f"Bearer {key_b}"})
    assert res_b_read.status_code == 403, res_b_read.text

    # 2. Reader cannot mutate
    res_reader_mutate = client.post(
        "/api/v1/definitions",
        json={"name": "Illegal Mutate", "raw_sql": "SELECT sum(order_amount) AS metric FROM orders", "source_label": "BI", "owner_label": "Reader", "time_column": "order_date"},
        headers={'Authorization': f"Bearer {key_ra}", 'X-Project-Id': 'proj-a'}
    )
    assert res_reader_mutate.status_code == 403

    # 3. Analyst cannot manage members or make decisions
    res_analyst_member = client.post(
        "/api/v1/projects/proj-a/members",
        json={"user_id": user_b['id'], "role": "reader"},
        headers={'Authorization': f"Bearer {key_aa}"}
    )
    assert res_analyst_member.status_code == 403

    res_analyst_decision = client.post(
        "/api/v1/projects/proj-a/decisions",
        json={"approved_version_id": ver_a_id, "decision": "approved", "rationale": "Analyst illegal decision"},
        headers={'Authorization': f"Bearer {key_aa}"}
    )
    assert res_analyst_decision.status_code == 403

    from scripts.seed_demo import seed
    seed(url, tmp_path)
    snap = store.bootstrap('local-retail')['snapshots'][0]
    snap_a = store.record_snapshot(
        name='Golden Snapshot A', manifest={**snap['manifest'], 'manifest_hash': 'hash-a'},
        artifact_path=snap['artifact_path'], project_id='proj-a'
    )
    snap_b = store.record_snapshot(
        name='Golden Snapshot B', manifest={**snap['manifest'], 'manifest_hash': 'hash-b'},
        artifact_path=snap['artifact_path'], project_id='proj-b'
    )

    # 4. IDOR Check: User B attempts to validate or run comparison in proj-b using proj-a's definition IDs
    idor_val = client.post(
        "/api/v1/comparisons/validate",
        json={
            "baseline_version_id": ver_a_id,
            "target_version_id": ver_a_id,
            "snapshot_id": snap_b['id'],
            "relationship": "expected_agreement",
            "context": {"start": "2026-08-01", "end_exclusive": "2026-08-02", "currency": "INR", "timezone": "Asia/Kolkata", "scope": "synthetic_retail_all_orders", "grain": "one_row_per_order_id", "unit": "currency"}
        },
        headers={'Authorization': f"Bearer {key_b}", 'X-Project-Id': 'proj-b'}
    )
    assert idor_val.status_code == 403, idor_val.text

    idor_compare = client.post(
        "/api/v1/comparisons",
        json={
            "baseline_version_id": ver_a_id,
            "target_version_id": ver_a_id,
            "snapshot_id": snap_b['id'],
            "relationship": "expected_agreement",
            "context": {"start": "2026-08-01", "end_exclusive": "2026-08-02", "currency": "INR", "timezone": "Asia/Kolkata", "scope": "synthetic_retail_all_orders", "grain": "one_row_per_order_id", "unit": "currency"}
        },
        headers={'Authorization': f"Bearer {key_b}", 'X-Project-Id': 'proj-b', 'Idempotency-Key': 'idor-1'}
    )
    assert idor_compare.status_code == 403, idor_compare.text


def test_legacy_local_retail_compatibility_bounds(tmp_path, monkeypatch):
    """Verify security bounds of unauthenticated legacy access to local-retail."""
    url = f"sqlite:///{tmp_path / 'compat.db'}"
    store = Store(url)
    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    client = TestClient(app)

    # 1. Unauthenticated request to local-retail bootstrap succeeds (backward compatibility)
    res_local = client.get("/api/v1/bootstrap")
    assert res_local.status_code == 200

    # 2. Cannot grant access to another project via header or query
    res_other = client.get("/api/v1/bootstrap", headers={'X-Project-Id': 'non-existent-or-other'})
    assert res_other.status_code == 403

    res_other_q = client.get("/api/v1/bootstrap?project_id=secret-project")
    assert res_other_q.status_code == 403

    # 3. Unauthenticated caller CANNOT create projects
    res_create_proj = client.post("/api/v1/projects", json={"name": "Illegal Unauth Project"})
    assert res_create_proj.status_code == 403

    # 4. Unauthenticated caller CANNOT manage members on local-retail
    res_member = client.post("/api/v1/projects/local-retail/members", json={"user_id": "u1", "role": "analyst"})
    assert res_member.status_code == 403

    # 5. Unauthenticated caller CANNOT record decisions
    res_dec = client.post(
        "/api/v1/projects/local-retail/decisions",
        json={"approved_version_id": "v1", "decision": "approved", "rationale": "unverified decision"}
    )
    assert res_dec.status_code == 403

    # 6. Unauthenticated caller CANNOT set quotas
    res_quota = client.post(
        "/api/v1/projects/local-retail/quotas",
        json={"max_storage_bytes": 1000}
    )
    assert res_quota.status_code == 403

    # 7. Unauthenticated caller CANNOT view audit logs
    res_audit = client.get("/api/v1/projects/local-retail/audit-logs")
    assert res_audit.status_code == 403

    # 8. Explicit deployment boundary: when METRICGUARD_ALLOW_UNVERIFIED_LOCAL=false, unauthenticated requests are rejected
    monkeypatch.setenv("METRICGUARD_ALLOW_UNVERIFIED_LOCAL", "false")
    res_disabled = client.get("/api/v1/bootstrap")
    assert res_disabled.status_code == 401
    assert "credentials required" in res_disabled.json()['error']['message'].lower()


def test_revocation_blocks_all_access_immediately(tmp_path):
    """Verify that membership revocation immediately severs all access paths."""
    url = f"sqlite:///{tmp_path / 'revocation.db'}"
    store = Store(url)

    owner, key_o = store.create_user("Owner", "owner@rev.org")
    analyst, key_a = store.create_user("Analyst", "analyst@rev.org")
    proj = store.create_project("Project Rev", owner_id=owner['id'], project_id="proj-rev")
    store.add_project_member("proj-rev", analyst['id'], "analyst")

    defn = store.record_definition(
        name="Rev Test",
        raw_sql="SELECT sum(order_amount) AS metric FROM orders",
        source_label="BI",
        owner_label="Analyst",
        time_column="order_date",
        project_id="proj-rev"
    )

    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    client = TestClient(app)

    # Prior to revocation, analyst can access
    res_before = client.get(f"/api/v1/definitions/{defn['id']}", headers={'Authorization': f"Bearer {key_a}"})
    assert res_before.status_code == 200

    # Revoke analyst
    res_revoke = client.post(
        f"/api/v1/projects/proj-rev/members/{analyst['id']}/revoke",
        headers={'Authorization': f"Bearer {key_o}"}
    )
    assert res_revoke.status_code == 200

    # Immediately blocked on:
    # 1. Object read with known ID
    assert client.get(f"/api/v1/definitions/{defn['id']}", headers={'Authorization': f"Bearer {key_a}"}).status_code == 403
    # 2. Project bootstrap
    assert client.get("/api/v1/bootstrap", headers={'Authorization': f"Bearer {key_a}", 'X-Project-Id': 'proj-rev'}).status_code == 403
    # 3. New mutation
    assert client.post("/api/v1/definitions", json={"name": "Blocked", "raw_sql": "SELECT sum(order_amount) AS metric FROM orders", "source_label": "BI", "owner_label": "Analyst", "time_column": "order_date"}, headers={'Authorization': f"Bearer {key_a}", 'X-Project-Id': 'proj-rev'}).status_code == 403
    # 4. Review request creation
    assert client.post(
        "/api/v1/projects/proj-rev/review-requests",
        json={"definition_id": defn['id'], "rationale": "blocked"},
        headers={'Authorization': f"Bearer {key_a}"}
    ).status_code == 403
    # 5. Alert listing
    assert client.get("/api/v1/projects/proj-rev/alerts", headers={'Authorization': f"Bearer {key_a}"}).status_code == 403
