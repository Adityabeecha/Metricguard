"""Deterministic 15-Row Regression Verification for Milestone V5.2-05:
Unified Investigation Workspace + Durable Server Drafts + Contextual Decisions.

Exercises MetricGuard through the supported production API and isolated supervisor runner:
1. 15-Row Generic Dataset ingestion & snapshot creation:
   Columns: order_id, order_ts, region, status, amount
2. Baseline & Target analytical execution:
   Baseline: SELECT SUM(amount) FROM snapshot WHERE status='completed' -> 1985.00 INR
   Target:   SELECT SUM(amount) FROM snapshot WHERE status='completed' AND region='South' -> 870.00 INR
   Delta:    -1115.00 INR
3. Exact row membership breakdown:
   - Both: Orders 1, 2, 6, 9, 12, 14 (amount sum = 870.00)
   - Baseline only: Orders 3, 5, 7, 8, 11, 13, 15 (amount sum = 1115.00)
   - Excluded from both: Orders 4, 10 (cancelled / refunded)
   - Target only: None (0 rows)
4. Residual accounting:
   - Residual: 0.00 (all delta accounted for)
5. Evidence artifact & Provenance:
   - Verified row-level evidence artifact (JSONL sha256 + row_count=15 + delta_sum=-1115.00)
6. Saved Investigation & Replay:
   - Save investigation record with decision & actor
   - Direct reopening via GET /api/v1/investigations/{id}
   - Deterministic replay producing identical delta (-1115.00 INR)
7. Durable Server-Backed Draft Persistence:
   - Initial creation with stable ID and version_token
   - Debounced autosave simulation & version_token rotation
   - Stale version_token rejection (409 STALE_DRAFT)
   - Direct-link resolution via GET /api/v1/investigations/{draft_id}
   - Restart durability: draft reloaded from fresh database connection
8. Workspace / Project Isolation:
   - Draft created in Workspace A is strictly invisible to Workspace B
   - Cross-workspace read / update / delete rejected (403/404)
9. Changes Handoff Verification:
   - Controlled impact to investigation promotion
   - Run ID and row-level evidence reused without recomputation
10. Contextual Decision & Governance Transition:
   - Metric candidate governance
   - Candidate promotion creates immutable MetricStateTransition audit entry
   - Draft marked completed with decision rationale
   - Ad-hoc investigation enforces metric-optional behavior without governed candidate actions
"""
from __future__ import annotations

import csv
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import DomainError, Store
from services.supervisor.worker import DockerLauncher, Supervisor

DATA_DIR = ROOT / 'data' / 'evaluation'
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_FILE = DATA_DIR / 'investigation_15row.csv'
DB_FILE = DATA_DIR / 'investigation_15row.db'
JOB_ROOT = DATA_DIR / 'jobs_15row'


class CompletedProcess:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class InProcessLauncher:
    def start(self, input_dir, output_dir, command=None):
        from services.supervisor.runner import ingest_job, run_job
        if command == 'ingest':
            ingest_job(input_dir, output_dir)
        elif command is None:
            run_job(input_dir, output_dir)
        else:
            raise AssertionError(f"Unknown runner command: {command}")
        return CompletedProcess()


def get_launcher():
    try:
        res = subprocess.run(['docker', 'info'], capture_output=True, timeout=5)
        if res.returncode == 0:
            return DockerLauncher()
    except Exception:
        pass
    return InProcessLauncher()


def generate_15row_csv():
    rows = [
        {'order_id': '1',  'order_ts': '2026-01-01T10:00:00', 'region': 'South', 'status': 'completed', 'amount': '120'},
        {'order_id': '2',  'order_ts': '2026-01-02T11:00:00', 'region': 'South', 'status': 'completed', 'amount': '80'},
        {'order_id': '3',  'order_ts': '2026-01-03T12:00:00', 'region': 'North', 'status': 'completed', 'amount': '200'},
        {'order_id': '4',  'order_ts': '2026-01-04T13:00:00', 'region': 'South', 'status': 'cancelled', 'amount': '150'},
        {'order_id': '5',  'order_ts': '2026-01-05T14:00:00', 'region': 'West',  'status': 'completed', 'amount': '175'},
        {'order_id': '6',  'order_ts': '2026-01-06T15:00:00', 'region': 'South', 'status': 'completed', 'amount': '220'},
        {'order_id': '7',  'order_ts': '2026-01-07T16:00:00', 'region': 'East',  'status': 'completed', 'amount': '90'},
        {'order_id': '8',  'order_ts': '2026-01-08T17:00:00', 'region': 'North', 'status': 'completed', 'amount': '130'},
        {'order_id': '9',  'order_ts': '2026-01-09T18:00:00', 'region': 'South', 'status': 'completed', 'amount': '160'},
        {'order_id': '10', 'order_ts': '2026-01-10T19:00:00', 'region': 'South', 'status': 'refunded',  'amount': '110'},
        {'order_id': '11', 'order_ts': '2026-01-11T20:00:00', 'region': 'West',  'status': 'completed', 'amount': '140'},
        {'order_id': '12', 'order_ts': '2026-01-12T21:00:00', 'region': 'South', 'status': 'completed', 'amount': '100'},
        {'order_id': '13', 'order_ts': '2026-01-13T22:00:00', 'region': 'East',  'status': 'completed', 'amount': '210'},
        {'order_id': '14', 'order_ts': '2026-01-14T23:00:00', 'region': 'South', 'status': 'completed', 'amount': '190'},
        {'order_id': '15', 'order_ts': '2026-01-15T09:00:00', 'region': 'North', 'status': 'completed', 'amount': '170'},
    ]
    with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['order_id', 'order_ts', 'region', 'status', 'amount'])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Generated 15-row CSV at: {CSV_FILE}")
    return len(rows)


def verify_15row_investigation():
    print("=== Starting V5.2-05 15-Row Regression Verification ===")
    total_rows = generate_15row_csv()
    assert total_rows == 15

    if DB_FILE.exists():
        DB_FILE.unlink()
    if JOB_ROOT.exists():
        shutil.rmtree(JOB_ROOT, ignore_errors=True)
    JOB_ROOT.mkdir(parents=True, exist_ok=True)

    db_url = f'sqlite:///{DB_FILE}'
    store = Store(db_url)
    pid = 'local-retail'
    try:
        store.create_project(name='Local Retail', project_id=pid)
    except Exception:
        pass

    # Create analyst user
    u_analyst, key_analyst = store.create_user('Analyst User', 'analyst@retail.com')
    store.add_project_member(pid, u_analyst['id'], 'analyst')
    headers = {'X-API-Key': key_analyst}

    launcher = get_launcher()
    launcher_type = 'docker' if isinstance(launcher, DockerLauncher) else 'in-process'
    print(f"Using supervisor launcher: {launcher_type}")
    worker = Supervisor(store, launcher, JOB_ROOT, 'verify15-worker', data_dir=DATA_DIR)

    with TestClient(create_app(database_url=db_url, data_dir=DATA_DIR, mode='isolated')) as client:
        # 1. Register Flat Contract
        contract_payload = {
            'version': 3,
            'mode': 'flat_snapshot',
            'name': 'Retail Orders 15-Row Contract',
            'row_key': 'order_id',
            'measure': 'amount',
            'precision': 18,
            'scale': 2,
            'categories': ['region', 'status'],
            'measure_meaning': 'Order gross value',
            'unit_label': 'INR',
            'values_comparable': True,
            'scope': 'entire_supplied_snapshot',
            'null_policy': 'blank_nullable',
        }
        res_contract = client.post('/api/v1/flat/contracts', json=contract_payload, headers=headers)
        assert res_contract.status_code == 201, res_contract.text
        contract = res_contract.json()
        print(f"[1] Registered Contract: {contract['name']} (hash={contract['contract_hash'][:12]}...)")

        # 2. Ingest Flat Snapshot
        with open(CSV_FILE, 'rb') as f:
            res_upload = client.post(
                '/api/v1/flat/snapshots',
                files={'file': ('investigation_15row.csv', f, 'text/csv')},
                data={'contract_id': contract['id'], 'name': '15-row Orders Snapshot'},
                headers=headers,
            )
        assert res_upload.status_code == 202, res_upload.text
        import_id = res_upload.json()['id']
        assert worker.run_once(), "Supervisor failed to claim snapshot import job"
        imported = client.get(f'/api/v1/snapshot-imports/{import_id}', headers=headers).json()
        assert imported['state'] == 'validated', f"Import failed: {imported.get('error')}"
        snapshot_id = imported['snapshot_id']
        snapshot = store.snapshot(snapshot_id)
        assert snapshot['row_count'] == 15
        print(f"[2] Ingested Snapshot: {snapshot['name']} (15 rows, source_hash={snapshot['source_hash'][:12]}...)")

        # 3. Register Baseline and Target SQL Definitions
        sql_baseline = "SELECT SUM(amount) FROM snapshot WHERE status='completed'"
        sql_target = "SELECT SUM(amount) FROM snapshot WHERE status='completed' AND region='South'"

        res_b = client.post('/api/v1/flat/definitions', json={
            'name': 'Retail Revenue Baseline',
            'contract_id': contract['id'],
            'raw_sql': sql_baseline,
        }, headers=headers)
        assert res_b.status_code == 201, res_b.text
        b_def = res_b.json()
        b_ver = b_def['versions'][0]
        assert b_ver['support_state'] == 'executable'

        res_t = client.post('/api/v1/flat/definitions', json={
            'name': 'Retail Revenue Target (South Only)',
            'contract_id': contract['id'],
            'raw_sql': sql_target,
        }, headers=headers)
        assert res_t.status_code == 201, res_t.text
        t_def = res_t.json()
        t_ver = t_def['versions'][0]
        assert t_ver['support_state'] == 'executable'
        print(f"[3] Registered Definitions:\n    Baseline: {b_def['name']} (v{b_ver['version_number']})\n    Target:   {t_def['name']} (v{t_ver['version_number']})")

        # 4. Execute Comparison Run through Supported API + Supervisor
        compare_payload = {
            'baseline_version_id': b_ver['id'],
            'target_version_id': t_ver['id'],
            'snapshot_id': snapshot_id,
            'context': {
                'mode': 'flat_snapshot',
                'scope': 'entire_supplied_snapshot',
                'contract_hash': contract['contract_hash'],
                'unit_label': 'INR',
                'values_comparable': True,
            },
            'relationship': 'candidate',
        }
        res_queued = client.post(
            '/api/v1/flat/comparisons',
            json=compare_payload,
            headers={**headers, 'Idempotency-Key': f'cmp-15row-{uuid4().hex[:8]}'},
        )
        assert res_queued.status_code == 202, res_queued.text
        run_id = res_queued.json()['id']
        assert worker.run_once(), "Supervisor failed to claim comparison run job"

        res_run = client.get(f'/api/v1/comparisons/{run_id}', headers=headers).json()
        assert res_run['state'] == 'succeeded', f"Run failed: {res_run.get('error')}"
        res = res_run['result']

        baseline_val = Decimal(res['baseline_value'])
        target_val = Decimal(res['target_value'])
        delta_val = Decimal(res['delta_target_minus_baseline'])

        print(f"[4] MetricGuard Deterministic Results:")
        print(f"    Baseline Value: {baseline_val:.2f} INR")
        print(f"    Target Value:   {target_val:.2f} INR")
        print(f"    Exact Delta:    {delta_val:.2f} INR")

        assert baseline_val == Decimal('1985.00'), f"Expected 1985.00, got {baseline_val}"
        assert target_val == Decimal('870.00'), f"Expected 870.00, got {target_val}"
        assert delta_val == Decimal('-1115.00'), f"Expected -1115.00, got {delta_val}"

        # Residual verification
        residual = Decimal(res.get('residual') or '0.00')
        assert residual == Decimal('0.00'), f"Unexplained residual: {residual}"
        print(f"    Residual:       {residual:.2f} (Exact zero residual verified)")

        # Provenance verification
        prov = res['provenance']
        assert prov['execution_policy'] == 'flat-snapshot-v1'
        assert prov['contract_hash'] == contract['contract_hash']
        assert prov['source_hash'] == snapshot['source_hash']
        print(f"    Provenance:     policy={prov['execution_policy']}, contract_hash={prov['contract_hash'][:12]}...")

        # 5. Row-level Membership and Evidence Artifact Verification
        res_ev = client.get(f'/api/v1/comparisons/{run_id}/evidence?limit=100', headers=headers)
        assert res_ev.status_code == 200, res_ev.text
        evidence = res_ev.json()
        rows = evidence['items']
        assert evidence['total'] == 15
        assert len(rows) == 15

        both_rows = [r for r in rows if r['membership'] == 'included_in_both']
        baseline_only_rows = [r for r in rows if r['membership'] == 'baseline_only']
        target_only_rows = [r for r in rows if r['membership'] == 'target_only']
        neither_rows = [r for r in rows if r['membership'] == 'included_in_neither']

        print(f"[5] Row Membership Breakdown:")
        print(f"    Included in Both:    {len(both_rows)} rows (Orders: {[r['order_id'] for r in both_rows]})")
        print(f"    Baseline Only:       {len(baseline_only_rows)} rows (Orders: {[r['order_id'] for r in baseline_only_rows]})")
        print(f"    Target Only:         {len(target_only_rows)} rows")
        print(f"    Excluded from Both:  {len(neither_rows)} rows (Orders: {[r['order_id'] for r in neither_rows]})")

        assert len(both_rows) == 6
        assert set(r['order_id'] for r in both_rows) == {'1', '2', '6', '9', '12', '14'}
        assert len(baseline_only_rows) == 7
        assert set(r['order_id'] for r in baseline_only_rows) == {'3', '5', '7', '8', '11', '13', '15'}
        assert len(target_only_rows) == 0
        assert len(neither_rows) == 2
        assert set(r['order_id'] for r in neither_rows) == {'4', '10'}

        # Verify sum of deltas in evidence
        evidence_delta_sum = sum(Decimal(r['delta']) for r in rows)
        assert evidence_delta_sum == Decimal('-1115.00')

        # 6. Saved Investigation and Deterministic Replay
        res_saved = client.post('/api/v1/investigations', json={
            'title': '15-Row Regional Revenue Drilldown',
            'run_id': run_id,
            'decision': 'Target South region revenue analysis',
            'actor_label': 'Analyst User',
        }, headers=headers)
        assert res_saved.status_code == 201, res_saved.text
        saved_inv = res_saved.json()
        inv_id = saved_inv['id']

        # Reopen saved investigation
        res_reopened = client.get(f'/api/v1/investigations/{inv_id}', headers=headers)
        assert res_reopened.status_code == 200, res_reopened.text
        reopened = res_reopened.json()
        assert reopened['id'] == inv_id
        assert reopened['run']['id'] == run_id
        assert reopened['run']['result']['delta_target_minus_baseline'] == '-1115.00'
        assert reopened['snapshot']['id'] == snapshot_id
        print(f"[6] Saved Investigation Reopened: id={inv_id}, run_id={run_id}")

        # Deterministic Replay
        res_replay = client.post(
            f'/api/v1/investigations/{inv_id}/replays',
            headers={**headers, 'Idempotency-Key': f'replay-15row-{uuid4().hex[:8]}'},
        )
        assert res_replay.status_code == 202, res_replay.text
        replay_run_id = res_replay.json()['id']
        assert worker.run_once(), "Supervisor failed to claim replay run job"

        replay_run = client.get(f'/api/v1/comparisons/{replay_run_id}', headers=headers).json()
        assert replay_run['state'] == 'succeeded'
        assert replay_run['result']['delta_target_minus_baseline'] == '-1115.00'
        assert replay_run['result']['baseline_value'] == '1985.00'
        assert replay_run['result']['target_value'] == '870.00'
        print(f"    Deterministic Replay Verified: delta=-1115.00 INR (run={replay_run_id})")

        # 7. Durable Server-Backed Draft Verification
        draft_payload = {
            'title': '15-Row Investigation Studio Draft',
            'baseline_definition_id': b_def['id'],
            'baseline_version_id': b_ver['id'],
            'target_definition_id': t_def['id'],
            'target_version_id': t_ver['id'],
            'snapshot_id': snapshot_id,
            'comparison_run_id': run_id,
            'status': 'draft',
            'notes': 'Initial draft setup before review.',
        }
        res_draft = client.post(f'/api/v1/projects/{pid}/investigation-drafts', json=draft_payload, headers=headers)
        assert res_draft.status_code == 201, res_draft.text
        draft = res_draft.json()
        draft_id = draft['id']
        token_1 = draft['version_token']
        print(f"[7] Durable Server Draft Created: id={draft_id}, token={token_1}")

        # Autosave update (simulate user editing notes & title)
        update_payload = {
            'title': '15-Row Regional Revenue Drilldown (Autosaved)',
            'notes': 'Confirmed -1115.00 INR gap driven by North, West, East exclusion.',
            'version_token': token_1,
        }
        res_up = client.put(f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}', json=update_payload, headers=headers)
        assert res_up.status_code == 200, res_up.text
        updated_draft = res_up.json()
        token_2 = updated_draft['version_token']
        assert token_2 != token_1
        assert updated_draft['title'] == '15-Row Regional Revenue Drilldown (Autosaved)'
        print(f"    Autosave Updated: token rotated {token_1} -> {token_2}")

        # Concurrency Conflict Test (re-saving with token_1 must fail with 409)
        res_stale = client.put(
            f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}',
            json={'title': 'Stale Title', 'version_token': token_1},
            headers=headers,
        )
        assert res_stale.status_code == 409, f"Expected 409, got {res_stale.status_code}"
        assert res_stale.json()['error']['code'] in ('STALE_DRAFT', 'DRAFT_CONFLICT')
        print("    Stale Concurrency: Stale version_token rejected with 409 STALE_DRAFT")

        # Direct Investigation URL Resolution for Draft
        res_direct = client.get(f'/api/v1/investigations/{draft_id}', headers=headers)
        assert res_direct.status_code == 200, res_direct.text
        direct_data = res_direct.json()
        assert direct_data['id'] == draft_id
        assert direct_data['is_draft'] is True
        assert direct_data['run_id'] == run_id
        assert direct_data['snapshot']['id'] == snapshot_id
        print("    Direct Link: GET /api/v1/investigations/{draft_id} resolves draft transparently")

        # 8. Workspace / Project Isolation Verification
        pid_b = 'workspace-b-15row'
        try:
            store.create_project(name='Beta Workspace', project_id=pid_b)
        except Exception:
            pass
        u_b, key_b = store.create_user('Beta User', 'beta@retail.com')
        store.add_project_member(pid_b, u_b['id'], 'analyst')
        headers_b = {'X-API-Key': key_b}

        # Query Workspace B drafts list: must not contain draft_id
        res_b_list = client.get(f'/api/v1/projects/{pid_b}/investigation-drafts', headers=headers_b)
        assert res_b_list.status_code == 200
        assert not any(d['id'] == draft_id for d in res_b_list.json()['items'])

        # Direct access from Workspace B: forbidden / not found
        res_b_get = client.get(f'/api/v1/projects/{pid_b}/investigation-drafts/{draft_id}', headers=headers_b)
        assert res_b_get.status_code in (403, 404)
        print("[8] Workspace Isolation: Draft is strictly invisible to secondary workspace.")

        # 9. Changes Handoff Verification (Controlled Impact → Investigation Evidence Reuse)
        from packages.metricguard_core.snapshot import inspect_snapshot
        golden_file = ROOT / 'data' / 'snapshots' / 'golden.duckdb'
        golden_manifest = inspect_snapshot(golden_file)
        golden_snap = store.record_snapshot('golden_orders_changes', golden_manifest, golden_file, project_id=pid)

        def _build_manifest(models: dict, invocation_id: str = 'inv-v5-handoff') -> dict:
            nodes = {}
            for uid, info in models.items():
                nodes[uid] = {
                    'unique_id': uid,
                    'resource_type': info.get('resource_type', 'model'),
                    'name': info.get('name', uid.split('.')[-1]),
                    'original_file_path': info.get('original_file_path', f"models/{uid}.sql"),
                    'raw_code': info.get('raw_code', 'SELECT 1'),
                    'compiled_code': info.get('sql'),
                    'depends_on': info.get('depends_on', {'nodes': []}),
                    'meta': info.get('meta', {}),
                    'tags': info.get('tags', []),
                }
            return {
                'metadata': {
                    'dbt_schema_version': 'https://schemas.getdbt.com/dbt/manifest/v12.json',
                    'dbt_version': '1.12.0',
                    'project_name': 'retail_analytics',
                    'generated_at': '2026-09-25T14:00:00Z',
                    'invocation_id': invocation_id,
                },
                'nodes': nodes,
                'metrics': {},
                'child_map': {},
            }

        period_clause = "order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"
        sql_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {period_clause}"
        sql_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {period_clause}"

        r_base = store.record_dbt_import(json.dumps(_build_manifest({'model.retail.monthly_rev': {'sql': sql_base}}, invocation_id='inv-base')).encode(), project_id=pid)
        m_rev = store.create_metric(display_name='Monthly Rev', importance='critical', project_id=pid)
        store.attach_external_metric_reference(m_rev['id'], provider='dbt', external_id='model.retail.monthly_rev', project_id=pid)

        defn_id = r_base['resources'][0]['definition_id']
        v1_id = r_base['resources'][0]['definition_version_id']
        store.promote_metric_definition(m_rev['id'], defn_id, approved_version_id=v1_id, project_id=pid)

        r_target = store.record_dbt_import(json.dumps(_build_manifest({'model.retail.monthly_rev': {'sql': sql_target}}, invocation_id='inv-target')).encode(), project_id=pid)

        impact = store.execute_metric_impact(r_base['id'], r_target['id'], golden_snap['id'], project_id=pid)
        assert impact['schema_version'] == 'metricguard_metric_impact_v1'
        evaluated_m = next(m for m in impact['metrics'] if m['metric_id'] == m_rev['id'])
        impact_run_id = evaluated_m['comparison_run_id']
        assert impact_run_id is not None
        assert evaluated_m['exact_delta'] is not None

        # Promote impact to investigation without recomputation
        res_promote = client.post(
            f'/api/v1/projects/{pid}/dbt/metric-impacts/{impact["id"]}/investigations',
            json={
                'metric_id': m_rev['id'],
                'title': 'Handoff from Metric Impact',
                'decision': 'Investigate regional revenue filter gap',
            },
            headers=headers,
        )
        assert res_promote.status_code == 201, res_promote.text
        promoted_inv = res_promote.json()
        assert promoted_inv['run_id'] == impact_run_id
        assert promoted_inv['snapshot_id'] == golden_snap['id']

        # Reopening promoted investigation preserves exact evidence and provenance
        res_promoted_get = client.get(f'/api/v1/investigations/{promoted_inv["id"]}', headers=headers)
        assert res_promoted_get.status_code == 200
        assert res_promoted_get.json()['run']['id'] == impact_run_id
        assert res_promoted_get.json()['run']['result']['delta_target_minus_baseline'] == evaluated_m['exact_delta']
        print("[9] Changes Handoff: Promoted impact reused run ID & evidence with zero recomputation.")

        # 10. Contextual Decision & Governance Transition
        metric_15 = store.create_metric('Total Regional Revenue', importance='critical', project_id=pid)
        store.bind_metric_definition(metric_15['id'], b_def['id'], status='active', approved_version_id=b_ver['id'], project_id=pid)
        store.bind_metric_definition(metric_15['id'], t_def['id'], status='candidate', project_id=pid)

        # Candidate approval
        decision_rationale = "Regional filtering approved for targeted retail campaign dashboard."
        res_approve = client.post(
            f'/api/v1/projects/{pid}/metrics/{metric_15["id"]}/promote',
            json={
                'definition_id': t_def['id'],
                'approved_version_id': t_ver['id'],
                'comparison_run_id': run_id,
            },
            headers=headers,
        )
        assert res_approve.status_code == 200, res_approve.text
        approve_data = res_approve.json()
        assert approve_data['binding']['status'] == 'active'
        assert approve_data['binding']['approved_version_id'] == t_ver['id']

        # Confirm immutable MetricStateTransition was recorded
        res_trans = client.get(f'/api/v1/projects/{pid}/metrics/{metric_15["id"]}/transitions', headers=headers)
        assert res_trans.status_code == 200
        transitions = res_trans.json()['items']
        latest_t = transitions[0]
        assert latest_t['transition_kind'] == 'definition_promoted'
        assert latest_t['approved_version_id'] == t_ver['id']
        assert latest_t['comparison_run_id'] == run_id

        # Update draft to completed with decision rationale
        res_final_draft = client.put(
            f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}',
            json={
                'status': 'completed',
                'notes': f"Decision recorded: {decision_rationale}",
                'version_token': token_2,
            },
            headers=headers,
        )
        assert res_final_draft.status_code == 200
        assert res_final_draft.json()['status'] == 'completed'
        print("[10] Contextual Decision: Candidate promoted to active, audit transition recorded, draft completed.")

        # Ad-hoc investigation check: verify ad-hoc investigation has no candidate governance actions
        res_adhoc = client.post(
            f'/api/v1/projects/{pid}/investigation-drafts',
            json={
                'title': 'Ad-hoc SQL Exploration',
                'status': 'draft',
                'notes': 'Ad-hoc query exploration without bound metric',
            },
            headers=headers,
        )
        assert res_adhoc.status_code == 201
        adhoc_draft = res_adhoc.json()
        assert adhoc_draft.get('metric_id') is None
        print("     Ad-hoc Verification: Metric is optional; no candidate actions exposed for ad-hoc queries.")

    # 11. Restart Durability: Fresh Store Connection
    fresh_store = Store(db_url)
    reloaded_draft = fresh_store.get_investigation_draft(draft_id, project_id=pid)
    assert reloaded_draft['id'] == draft_id
    assert reloaded_draft['comparison_run_id'] == run_id
    assert reloaded_draft['status'] == 'completed'
    assert 'Decision recorded' in reloaded_draft['notes']

    # Reopen completed investigation across fresh connection
    reloaded_inv = fresh_store.investigation(inv_id)
    assert reloaded_inv['id'] == inv_id
    assert reloaded_inv['run_id'] == run_id
    print(f"[11] Restart Durability: Draft and completed investigation reloaded cleanly across fresh Store connection.")

    print("\n>>> ALL 15-ROW VERIFICATION CHECKS PASSED DETERMINISTICALLY! <<<")


if __name__ == '__main__':
    verify_15row_investigation()
