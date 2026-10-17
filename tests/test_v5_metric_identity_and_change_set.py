"""Focused verification suite for Milestone V5-01:
Stable Metric Identity + dbt Revision Change Set Core.

Covers:
- Metric business identity (create, rename, importance, immutable id, project isolation)
- External metric references (neutral mapping, dbt provider, conflict rejection)
- Metric -> Definition lifecycle binding (one active invariant, candidate, retired history, cross-project protection)
- Version approval within same definition (atomic transition recording, validation against active definition)
- Candidate promotion (candidate -> active, previous -> retired, exact versions and evidence preserved)
- dbt revision change-set core (identical, SQL changed, lineage changed, SQL+lineage, metadata changed,
  support-state changed, added, removed, deterministic repeated computation, DefinitionVersion reuse)
- Metric language invariant (declared metric vs undeclared resource labels, no inferred importance)
- RBAC authorization (readers read-only, analysts/owners mutate, cross-project access forbidden)
"""
import hashlib
import json
import pytest
from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import DomainError, Store


def _sample_manifest(version: int = 12, models: dict | None = None, metrics: dict | None = None,
                     child_map: dict | None = None, invocation_id: str = 'inv-v5-01') -> dict:
    compiled_key = 'compiled_code' if version >= 9 else 'compiled_sql'
    default_models = {
        'model.retail.monthly_revenue': {
            'unique_id': 'model.retail.monthly_revenue',
            'resource_type': 'model',
            'name': 'monthly_revenue',
            'original_file_path': 'models/finance/monthly_revenue.sql',
            'raw_code': 'SELECT SUM(order_amount) FROM {{ ref("orders") }}',
            compiled_key: "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
            'depends_on': {'nodes': ['model.retail.stg_orders']},
            'meta': {'owner': 'finance'},
            'tags': ['finance', 'revenue'],
        },
        'model.retail.stg_orders': {
            'unique_id': 'model.retail.stg_orders',
            'resource_type': 'model',
            'name': 'stg_orders',
            'original_file_path': 'models/staging/stg_orders.sql',
            'raw_code': 'SELECT * FROM raw.orders',
            compiled_key: 'SELECT order_id, amount, status FROM raw_orders',
            'depends_on': {'nodes': ['source.retail.raw_orders']},
            'meta': {},
            'tags': ['staging'],
        }
    }
    return {
        'metadata': {
            'dbt_schema_version': f'https://schemas.getdbt.com/dbt/manifest/v{version}.json',
            'dbt_version': f'1.{version}.0',
            'project_name': 'retail_analytics',
            'generated_at': '2026-09-25T14:00:00Z',
            'invocation_id': invocation_id,
        },
        'nodes': models if models is not None else default_models,
        'metrics': metrics or {},
        'child_map': child_map or {},
    }


# ===========================================================================
# 1. Metric Business Identity & External References
# ===========================================================================

def test_metric_create_and_immutable_id(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    # Create Metric with default unclassified importance
    m = store.create_metric(display_name='Gross Revenue', project_id=pid)
    assert m['id'].startswith('metric-')
    assert m['project_id'] == pid
    assert m['display_name'] == 'Gross Revenue'
    assert m['importance'] == 'unclassified'

    # Rename display_name: Metric.id remains immutable
    original_id = m['id']
    updated = store.update_metric(original_id, display_name='Total Revenue', importance='critical', project_id=pid)
    assert updated['id'] == original_id
    assert updated['display_name'] == 'Total Revenue'
    assert updated['importance'] == 'critical'

    # Retrieve and verify persistence
    fetched = store.get_metric(original_id, project_id=pid)
    assert fetched['id'] == original_id
    assert fetched['display_name'] == 'Total Revenue'
    assert fetched['importance'] == 'critical'


def test_importance_validation_and_not_on_definition(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    # Invalid importance rejected
    with pytest.raises(DomainError) as exc:
        store.create_metric(display_name='Test', importance='super_urgent', project_id=pid)
    assert exc.value.code == 'INVALID_INPUT'

    with pytest.raises(DomainError) as exc:
        m = store.create_metric(display_name='Valid', importance='standard', project_id=pid)
        store.update_metric(m['id'], importance='invalid_value', project_id=pid)
    assert exc.value.code == 'INVALID_INPUT'

    # Definition record does NOT have importance
    defn = store.record_definition(
        name='Revenue Defn', source_label='dbt', owner_label='ops',
        raw_sql="SELECT SUM(amount) FROM orders", project_id=pid
    )
    defn_rec = store.definition(defn['id'])
    assert 'importance' not in defn_rec


def test_project_isolation_for_metrics(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid1 = 'local-retail'
    owner, _ = store.create_user('Owner 2', 'o2@metricguard.internal')
    store.create_project('Project 2', owner_id=owner['id'], project_id='proj-2')

    m1 = store.create_metric(display_name='Revenue P1', project_id=pid1)
    m2 = store.create_metric(display_name='Revenue P2', project_id='proj-2')

    # Cannot get m1 from proj-2
    with pytest.raises(DomainError) as exc:
        store.get_metric(m1['id'], project_id='proj-2')
    assert exc.value.code == 'NOT_FOUND'

    # Listing is isolated
    p1_metrics = store.list_metrics(project_id=pid1)
    assert any(m['id'] == m1['id'] for m in p1_metrics)
    assert not any(m['id'] == m2['id'] for m in p1_metrics)


def test_external_metric_reference_mapping_and_conflicts(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    m1 = store.create_metric(display_name='Revenue', importance='critical', project_id=pid)
    m2 = store.create_metric(display_name='Profit', importance='important', project_id=pid)

    # Attach dbt external reference to m1
    ref = store.attach_external_metric_reference(
        metric_id=m1['id'], provider='dbt', external_id='model.retail.monthly_revenue', project_id=pid
    )
    assert ref['metric_id'] == m1['id']
    assert ref['provider'] == 'dbt'
    assert ref['external_id'] == 'model.retail.monthly_revenue'

    # Idempotent re-attach
    ref2 = store.attach_external_metric_reference(
        metric_id=m1['id'], provider='dbt', external_id='model.retail.monthly_revenue', project_id=pid
    )
    assert ref2['id'] == ref['id']

    # Attaching the same external_id to a DIFFERENT metric in the same project is rejected
    with pytest.raises(DomainError) as exc:
        store.attach_external_metric_reference(
            metric_id=m2['id'], provider='dbt', external_id='model.retail.monthly_revenue', project_id=pid
        )
    assert exc.value.code == 'CONFLICT'

    refs = store.list_external_metric_references(m1['id'], project_id=pid)
    assert len(refs) == 1
    assert refs[0]['external_id'] == 'model.retail.monthly_revenue'


# ===========================================================================
# 2. Binding Lifecycle & Invariants
# ===========================================================================

def test_binding_lifecycle_and_one_active_invariant(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    metric = store.create_metric(display_name='Monthly Active Revenue', project_id=pid)
    mid = metric['id']

    # Zero active allowed initially
    fetched = store.get_metric(mid, project_id=pid)
    assert fetched['active_binding'] is None

    # Create two definitions
    def_a = store.record_definition(name='Def A', source_label='dbt', owner_label='ops',
                                   raw_sql="SELECT SUM(amount) FROM orders", project_id=pid)
    ver_a1 = def_a['versions'][0]['id']

    def_b = store.record_definition(name='Def B', source_label='dbt', owner_label='ops',
                                   raw_sql="SELECT SUM(amount) FROM orders WHERE valid = true", project_id=pid)
    ver_b1 = def_b['versions'][0]['id']

    # Candidate binding allowed
    bind_cand = store.bind_metric_definition(metric_id=mid, definition_id=def_a['id'],
                                             status='candidate', approved_version_id=ver_a1, project_id=pid)
    assert bind_cand['status'] == 'candidate'
    assert bind_cand['approved_version_id'] == ver_a1

    # Promote Def A -> Active
    promo1 = store.promote_metric_definition(metric_id=mid, definition_id=def_a['id'],
                                             approved_version_id=ver_a1, project_id=pid)
    active1 = promo1['binding']
    assert active1['status'] == 'active'
    assert active1['definition_id'] == def_a['id']
    assert active1['approved_version_id'] == ver_a1

    # Invariant check: Metric now has Def A active
    metric_state = store.get_metric(mid, project_id=pid)
    assert metric_state['active_binding']['id'] == active1['id']

    # Bind Def B as candidate
    bind_b_cand = store.bind_metric_definition(metric_id=mid, definition_id=def_b['id'],
                                               status='candidate', approved_version_id=ver_b1, project_id=pid)
    assert bind_b_cand['status'] == 'candidate'

    # Promote Def B -> Active
    promo2 = store.promote_metric_definition(metric_id=mid, definition_id=def_b['id'],
                                             approved_version_id=ver_b1, project_id=pid)
    active2 = promo2['binding']
    trans2 = promo2['transition']

    # Invariant: Def A MUST BE RETIRED, Def B MUST BE ACTIVE
    all_bindings = store.list_metric_definition_bindings(mid, project_id=pid)
    active_bindings = [b for b in all_bindings if b['status'] == 'active']
    retired_bindings = [b for b in all_bindings if b['status'] == 'retired']

    assert len(active_bindings) == 1
    assert active_bindings[0]['definition_id'] == def_b['id']
    assert len(retired_bindings) == 1
    assert retired_bindings[0]['definition_id'] == def_a['id']
    assert retired_bindings[0]['retired_at'] is not None

    # Transition evidence captures exact provenance
    assert trans2['transition_kind'] == 'definition_promoted'
    assert trans2['definition_id'] == def_b['id']
    assert trans2['approved_version_id'] == ver_b1
    assert trans2['previous_definition_id'] == def_a['id']
    assert trans2['previous_approved_version_id'] == ver_a1


def test_invalid_cross_project_and_invalid_version_bindings_rejected(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid1 = 'local-retail'
    owner, _ = store.create_user('Owner 2', 'o2@metricguard.internal')
    store.create_project('Project 2', owner_id=owner['id'], project_id='proj-2')

    m1 = store.create_metric(display_name='M1', project_id=pid1)

    # Definition in Project 2
    def_p2 = store.record_definition(name='Def P2', source_label='dbt', owner_label='ops',
                                     raw_sql="SELECT 1", project_id='proj-2')

    # Cross-project definition binding rejected
    with pytest.raises(DomainError) as exc:
        store.bind_metric_definition(metric_id=m1['id'], definition_id=def_p2['id'], project_id=pid1)
    assert exc.value.code == 'CROSS_PROJECT_FORBIDDEN'

    # Definition in Project 1
    def_p1_a = store.record_definition(name='Def P1 A', source_label='dbt', owner_label='ops',
                                       raw_sql="SELECT 1", project_id=pid1)
    def_p1_b = store.record_definition(name='Def P1 B', source_label='dbt', owner_label='ops',
                                       raw_sql="SELECT 2", project_id=pid1)
    ver_b = def_p1_b['versions'][0]['id']

    # Assigning version from Def B to Def A binding is rejected
    with pytest.raises(DomainError) as exc:
        store.bind_metric_definition(metric_id=m1['id'], definition_id=def_p1_a['id'],
                                     approved_version_id=ver_b, project_id=pid1)
    assert exc.value.code == 'INVALID_BINDING'


# ===========================================================================
# 3. Version Approval within Same Definition
# ===========================================================================

def test_version_approval_within_same_definition(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    metric = store.create_metric(display_name='Revenue', project_id=pid)
    mid = metric['id']

    defn = store.record_definition(name='Revenue SQL', source_label='dbt', owner_label='ops',
                                  raw_sql="SELECT SUM(amount) FROM orders", project_id=pid)
    v1_id = defn['versions'][0]['id']

    # Initial promotion of definition to active with V1
    store.promote_metric_definition(metric_id=mid, definition_id=defn['id'], approved_version_id=v1_id, project_id=pid)

    # PR creates V2
    defn_v2 = store.record_definition(name='Revenue SQL', source_label='dbt', owner_label='ops',
                                     raw_sql="SELECT SUM(amount) FROM orders WHERE status = 'paid'",
                                     definition_id=defn['id'], project_id=pid)
    v2_id = defn_v2['versions'][1]['id']

    # Approve V2
    approval = store.approve_metric_version(
        metric_id=mid,
        approved_version_id=v2_id,
        project_id=pid,
        actor_id='analyst-jane'
    )
    assert approval['binding']['approved_version_id'] == v2_id
    trans = approval['transition']
    assert trans['transition_kind'] == 'version_approved'
    assert trans['definition_id'] == defn['id']
    assert trans['approved_version_id'] == v2_id
    assert trans['previous_definition_id'] == defn['id']
    assert trans['previous_approved_version_id'] == v1_id
    assert trans['actor_id'] == 'analyst-jane'

    # Cannot approve a version from a DIFFERENT definition for this metric
    other_defn = store.record_definition(name='Unrelated', source_label='dbt', owner_label='ops',
                                         raw_sql="SELECT 99", project_id=pid)
    other_v_id = other_defn['versions'][0]['id']
    with pytest.raises(DomainError) as exc:
        store.approve_metric_version(metric_id=mid, approved_version_id=other_v_id, project_id=pid)
    assert exc.value.code == 'INVALID_BINDING'

    # Cannot approve version if metric has no active binding
    unbound_m = store.create_metric(display_name='Unbound', project_id=pid)
    with pytest.raises(DomainError) as exc:
        store.approve_metric_version(metric_id=unbound_m['id'], approved_version_id=v1_id, project_id=pid)
    assert exc.value.code == 'INVALID_STATE'


# ===========================================================================
# 4. dbt Revision Change-Set Core & Invariants
# ===========================================================================

def test_change_set_identical_revisions(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    manifest = _sample_manifest(version=12, invocation_id='inv-01')
    rev1 = store.record_dbt_import(json.dumps(manifest).encode(), project_id=pid)
    rev_id = rev1['id']

    # Compare identical revision to itself
    diff = store.compute_dbt_change_set(baseline_revision_id=rev_id, target_revision_id=rev_id, project_id=pid)

    assert diff['schema_version'] == 'metricguard_change_set_v1'
    assert diff['counts']['total_resources'] == 2
    assert diff['counts']['changed_count'] == 0
    assert diff['counts']['unchanged_count'] == 2
    assert diff['counts']['added_count'] == 0
    assert diff['counts']['removed_count'] == 0

    for r in diff['resources']:
        assert r['change_category'] == 'unchanged'
        assert r['change_dimensions'] == []


def test_change_set_sql_and_lineage_and_metadata_dimensions(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    # Rev 1 Baseline
    manifest_v1 = _sample_manifest(version=12, invocation_id='inv-01', models={
        'model.retail.model_sql': {
            'unique_id': 'model.retail.model_sql',
            'resource_type': 'model',
            'name': 'model_sql',
            'original_file_path': 'models/sql.sql',
            'raw_code': 'SELECT 1',
            'compiled_code': "SELECT 1 AS val",
            'depends_on': {'nodes': ['source.retail.raw']},
            'meta': {'tag': 'v1'},
            'tags': ['a'],
        },
        'model.retail.model_lineage': {
            'unique_id': 'model.retail.model_lineage',
            'resource_type': 'model',
            'name': 'model_lineage',
            'original_file_path': 'models/lin.sql',
            'raw_code': 'SELECT 2',
            'compiled_code': "SELECT 2 AS val",
            'depends_on': {'nodes': ['model.retail.model_sql']},
            'meta': {},
            'tags': [],
        },
        'model.retail.model_both': {
            'unique_id': 'model.retail.model_both',
            'resource_type': 'model',
            'name': 'model_both',
            'original_file_path': 'models/both.sql',
            'raw_code': 'SELECT 3',
            'compiled_code': "SELECT 3 AS val",
            'depends_on': {'nodes': ['model.retail.model_sql']},
            'meta': {},
            'tags': [],
        },
        'model.retail.model_meta_only': {
            'unique_id': 'model.retail.model_meta_only',
            'resource_type': 'model',
            'name': 'model_meta_only',
            'original_file_path': 'models/meta.sql',
            'raw_code': 'SELECT 4',
            'compiled_code': "SELECT 4 AS val",
            'depends_on': {'nodes': []},
            'meta': {'team': 'analytics'},
            'tags': ['alpha'],
        },
        'model.retail.model_removed': {
            'unique_id': 'model.retail.model_removed',
            'resource_type': 'model',
            'name': 'model_removed',
            'original_file_path': 'models/rem.sql',
            'raw_code': 'SELECT 5',
            'compiled_code': "SELECT 5 AS val",
            'depends_on': {'nodes': []},
            'meta': {},
            'tags': [],
        }
    })
    rev1 = store.record_dbt_import(
        json.dumps(manifest_v1).encode(),
        source_revision={'git_sha': 'sha-111', 'branch': 'main'},
        project_id=pid
    )

    # Rev 2 Target
    manifest_v2 = _sample_manifest(version=12, invocation_id='inv-02', models={
        # 1. SQL changed only
        'model.retail.model_sql': {
            'unique_id': 'model.retail.model_sql',
            'resource_type': 'model',
            'name': 'model_sql',
            'original_file_path': 'models/sql.sql',
            'raw_code': 'SELECT 1',
            'compiled_code': "SELECT 1 AS val WHERE active = true",
            'depends_on': {'nodes': ['source.retail.raw']},
            'meta': {'tag': 'v1'},
            'tags': ['a'],
        },
        # 2. Lineage changed only (SQL identical)
        'model.retail.model_lineage': {
            'unique_id': 'model.retail.model_lineage',
            'resource_type': 'model',
            'name': 'model_lineage',
            'original_file_path': 'models/lin.sql',
            'raw_code': 'SELECT 2',
            'compiled_code': "SELECT 2 AS val",
            'depends_on': {'nodes': ['source.retail.new_source']},  # Changed upstream!
            'meta': {},
            'tags': [],
        },
        # 3. Both SQL AND Lineage changed
        'model.retail.model_both': {
            'unique_id': 'model.retail.model_both',
            'resource_type': 'model',
            'name': 'model_both',
            'original_file_path': 'models/both.sql',
            'raw_code': 'SELECT 3',
            'compiled_code': "SELECT 300 AS val",
            'depends_on': {'nodes': ['model.retail.model_sql', 'source.retail.extra']},
            'meta': {},
            'tags': [],
        },
        # 4. Metadata changed only
        'model.retail.model_meta_only': {
            'unique_id': 'model.retail.model_meta_only',
            'resource_type': 'model',
            'name': 'model_meta_only',
            'original_file_path': 'models/meta.sql',
            'raw_code': 'SELECT 4',
            'compiled_code': "SELECT 4 AS val",
            'depends_on': {'nodes': []},
            'meta': {'team': 'growth'},  # Changed meta
            'tags': ['alpha', 'beta'],    # Changed tags
        },
        # 5. Added resource (model_removed omitted)
        'model.retail.model_added': {
            'unique_id': 'model.retail.model_added',
            'resource_type': 'model',
            'name': 'model_added',
            'original_file_path': 'models/add.sql',
            'raw_code': 'SELECT 6',
            'compiled_code': "SELECT 6 AS val",
            'depends_on': {'nodes': []},
            'meta': {},
            'tags': [],
        }
    })
    rev2 = store.record_dbt_import(
        json.dumps(manifest_v2).encode(),
        source_revision={'git_sha': 'sha-222', 'branch': 'feat/updates'},
        project_id=pid
    )

    # Compute Change Set
    diff = store.compute_dbt_change_set(
        baseline_revision_id=rev1['id'],
        target_revision_id=rev2['id'],
        project_id=pid
    )

    res_by_id = {r['unique_id']: r for r in diff['resources']}

    # Assert individual change dimensions:
    assert res_by_id['model.retail.model_sql']['change_category'] == 'modified'
    assert res_by_id['model.retail.model_sql']['change_dimensions'] == ['compiled_sql_changed']

    assert res_by_id['model.retail.model_lineage']['change_category'] == 'modified'
    assert res_by_id['model.retail.model_lineage']['change_dimensions'] == ['lineage_changed']
    # Verify SQL unchanged reused DefinitionVersion
    assert res_by_id['model.retail.model_lineage']['old_definition_version_id'] == res_by_id['model.retail.model_lineage']['new_definition_version_id']

    assert res_by_id['model.retail.model_both']['change_category'] == 'modified'
    assert res_by_id['model.retail.model_both']['change_dimensions'] == ['compiled_sql_changed', 'lineage_changed']

    assert res_by_id['model.retail.model_meta_only']['change_category'] == 'modified'
    assert res_by_id['model.retail.model_meta_only']['change_dimensions'] == ['metadata_changed']

    assert res_by_id['model.retail.model_added']['change_category'] == 'added'
    assert res_by_id['model.retail.model_added']['change_dimensions'] == ['added']

    assert res_by_id['model.retail.model_removed']['change_category'] == 'removed'
    assert res_by_id['model.retail.model_removed']['change_dimensions'] == ['removed']

    # Counts
    assert diff['counts']['total_resources'] == 6
    assert diff['counts']['changed_count'] == 4
    assert diff['counts']['added_count'] == 1
    assert diff['counts']['removed_count'] == 1
    assert diff['counts']['unchanged_count'] == 0


def test_change_set_support_state_change(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    # Rev 1: Executable
    manifest_v1 = _sample_manifest(version=12, models={
        'model.retail.state_model': {
            'unique_id': 'model.retail.state_model',
            'resource_type': 'model',
            'name': 'state_model',
            'original_file_path': 'models/sm.sql',
            'raw_code': 'SELECT 1',
            'compiled_code': "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
            'depends_on': {'nodes': []},
        }
    })
    rev1 = store.record_dbt_import(json.dumps(manifest_v1).encode(), project_id=pid)

    # Rev 2: Multiple CTEs force metadata_only support state
    manifest_v2 = _sample_manifest(version=12, models={
        'model.retail.state_model': {
            'unique_id': 'model.retail.state_model',
            'resource_type': 'model',
            'name': 'state_model',
            'original_file_path': 'models/sm.sql',
            'raw_code': 'SELECT 1',
            'compiled_code': "WITH a AS (SELECT order_amount FROM orders), b AS (SELECT order_amount FROM a) SELECT SUM(order_amount) FROM b",
            'depends_on': {'nodes': []},
        }
    })
    rev2 = store.record_dbt_import(json.dumps(manifest_v2).encode(), project_id=pid)

    diff = store.compute_dbt_change_set(rev1['id'], rev2['id'], project_id=pid)
    res = diff['resources'][0]
    assert res['change_category'] == 'modified'
    assert 'support_state_changed' in res['change_dimensions']
    assert res['old_support_state'] == 'executable'
    assert res['new_support_state'] == 'metadata_only'


# ===========================================================================
# 5. Language Invariant & Declared vs Undeclared Distinctions
# ===========================================================================

def test_language_invariant_and_metric_metadata_attachment(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    # Rev 1
    m1 = _sample_manifest(version=12, invocation_id='inv-01')
    rev1 = store.record_dbt_import(json.dumps(m1).encode(), project_id=pid)
    rev_res = rev1['resources'][0]

    # Declare a business Metric "Net Revenue" and bind it to the definition created by dbt
    business_metric = store.create_metric(display_name='Net Revenue', importance='critical', project_id=pid)
    store.promote_metric_definition(
        metric_id=business_metric['id'],
        definition_id=rev_res['definition_id'],
        project_id=pid
    )

    # Rev 2: SQL change
    m2 = _sample_manifest(version=12, invocation_id='inv-02', models={
        'model.retail.monthly_revenue': {
            'unique_id': 'model.retail.monthly_revenue',
            'resource_type': 'model',
            'name': 'monthly_revenue',
            'original_file_path': 'models/finance/monthly_revenue.sql',
            'raw_code': 'SELECT SUM(order_amount) FROM {{ ref("orders") }}',
            'compiled_code': "SELECT SUM(order_amount) FROM orders WHERE status = 'completed' AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
            'depends_on': {'nodes': ['model.retail.stg_orders']},
        },
        'model.retail.stg_orders': {
            'unique_id': 'model.retail.stg_orders',
            'resource_type': 'model',
            'name': 'stg_orders',
            'original_file_path': 'models/staging/stg_orders.sql',
            'raw_code': 'SELECT * FROM raw.orders',
            'compiled_code': 'SELECT order_id, amount, status FROM raw_orders WHERE deleted_at IS NULL',
            'depends_on': {'nodes': ['source.retail.raw_orders']},
        }
    })
    rev2 = store.record_dbt_import(json.dumps(m2).encode(), project_id=pid)

    diff = store.compute_dbt_change_set(rev1['id'], rev2['id'], project_id=pid)
    res_by_id = {r['unique_id']: r for r in diff['resources']}

    # 1. Declared Metric:
    rev_resource = res_by_id['model.retail.monthly_revenue']
    assert rev_resource['metric'] is not None
    assert rev_resource['metric']['id'] == business_metric['id']
    assert rev_resource['metric']['display_name'] == 'Net Revenue'
    assert rev_resource['metric']['importance'] == 'critical'
    # MANDATORY LANGUAGE INVARIANT: "Net Revenue definition changed"
    assert rev_resource['summary_label'] == 'Net Revenue definition changed'

    # 2. Undeclared resource:
    stg_resource = res_by_id['model.retail.stg_orders']
    assert stg_resource['metric'] is None
    # MANDATORY LANGUAGE INVARIANT: "model.retail.stg_orders changed"
    assert stg_resource['summary_label'] == 'model.retail.stg_orders changed'
    # Invariant: NEVER describe an undeclared resource as a business metric, no importance inferred
    assert 'importance' not in stg_resource or stg_resource.get('importance') is None


# ===========================================================================
# 6. API, RBAC, and Project Isolation Verification
# ===========================================================================

def test_api_metric_crud_and_rbac(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'api_test.db'}", data_dir=tmp_path, mode='demo')
    client = TestClient(app)
    store = app.state.store

    # Create Owner, Analyst, Reader
    owner, owner_key = store.create_user('Owner User', 'owner1@local.internal')
    analyst, analyst_key = store.create_user('Analyst User', 'analyst_test1@local.internal')
    reader, reader_key = store.create_user('Reader User', 'reader1@local.internal')

    proj = store.create_project('SaaS Analytics', owner_id=owner['id'], project_id='proj-saas')
    store.add_project_member(proj['id'], analyst['id'], 'analyst')
    store.add_project_member(proj['id'], reader['id'], 'reader')

    pid = proj['id']

    # 1. Analyst creates metric
    resp = client.post(
        f"/api/v1/projects/{pid}/metrics",
        headers={'x-api-key': analyst_key},
        json={'display_name': 'MRR', 'importance': 'critical'}
    )
    assert resp.status_code == 201
    metric_id = resp.json()['id']
    assert resp.json()['display_name'] == 'MRR'
    assert resp.json()['importance'] == 'critical'

    # 2. Reader reads metric (allowed)
    r_resp = client.get(f"/api/v1/projects/{pid}/metrics/{metric_id}", headers={'x-api-key': reader_key})
    assert r_resp.status_code == 200
    assert r_resp.json()['display_name'] == 'MRR'

    # 3. Reader attempts to update metric (forbidden)
    f_resp = client.patch(
        f"/api/v1/projects/{pid}/metrics/{metric_id}",
        headers={'x-api-key': reader_key},
        json={'display_name': 'Hacked MRR'}
    )
    assert f_resp.status_code == 403

    # 4. Analyst updates metric (allowed)
    u_resp = client.patch(
        f"/api/v1/projects/{pid}/metrics/{metric_id}",
        headers={'x-api-key': analyst_key},
        json={'display_name': 'Monthly Recurring Revenue'}
    )
    assert u_resp.status_code == 200
    assert u_resp.json()['display_name'] == 'Monthly Recurring Revenue'

    # 5. External references API
    ref_resp = client.post(
        f"/api/v1/projects/{pid}/metrics/{metric_id}/external-references",
        headers={'x-api-key': analyst_key},
        json={'provider': 'dbt', 'external_id': 'metric.saas.mrr'}
    )
    assert ref_resp.status_code == 201
    assert ref_resp.json()['external_id'] == 'metric.saas.mrr'

    # Reader lists external references
    list_ref_resp = client.get(
        f"/api/v1/projects/{pid}/metrics/{metric_id}/external-references",
        headers={'x-api-key': reader_key}
    )
    assert list_ref_resp.status_code == 200
    assert len(list_ref_resp.json()['items']) == 1


def test_api_promotion_approval_and_change_sets(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'api_test.db'}", data_dir=tmp_path, mode='demo')
    client = TestClient(app)
    store = app.state.store

    owner, owner_key = store.create_user('Owner User', 'owner2@local.internal')
    analyst, analyst_key = store.create_user('Analyst User', 'analyst_test2@local.internal')
    reader, reader_key = store.create_user('Reader User', 'reader2@local.internal')

    proj = store.create_project('Ecom Project', owner_id=owner['id'], project_id='proj-ecom')
    store.add_project_member(proj['id'], analyst['id'], 'analyst')
    store.add_project_member(proj['id'], reader['id'], 'reader')
    pid = proj['id']

    # Analyst imports dbt Rev 1
    m1 = _sample_manifest(version=12, invocation_id='inv-01')
    imp1 = store.record_dbt_import(json.dumps(m1).encode(), project_id=pid)
    rev1_id = imp1['id']
    defn_id = imp1['resources'][0]['definition_id']
    v1_id = imp1['resources'][0]['definition_version_id']

    # Analyst creates Metric
    m_resp = client.post(
        f"/api/v1/projects/{pid}/metrics",
        headers={'x-api-key': analyst_key},
        json={'display_name': 'Revenue', 'importance': 'critical'}
    )
    mid = m_resp.json()['id']

    # Analyst promotes Definition to Active
    promo_resp = client.post(
        f"/api/v1/projects/{pid}/metrics/{mid}/promote",
        headers={'x-api-key': analyst_key},
        json={'definition_id': defn_id, 'approved_version_id': v1_id}
    )
    assert promo_resp.status_code == 200
    assert promo_resp.json()['binding']['status'] == 'active'
    assert promo_resp.json()['transition']['transition_kind'] == 'definition_promoted'

    # Analyst imports dbt Rev 2 (modified SQL)
    m2 = _sample_manifest(version=12, invocation_id='inv-02', models={
        'model.retail.monthly_revenue': {
            'unique_id': 'model.retail.monthly_revenue',
            'resource_type': 'model',
            'name': 'monthly_revenue',
            'original_file_path': 'models/finance/monthly_revenue.sql',
            'raw_code': 'SELECT SUM(order_amount) FROM {{ ref("orders") }}',
            'compiled_code': "SELECT SUM(order_amount) FROM orders WHERE status = 'completed' AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
            'depends_on': {'nodes': ['model.retail.stg_orders']},
        }
    })
    imp2 = store.record_dbt_import(json.dumps(m2).encode(), project_id=pid)
    rev2_id = imp2['id']
    v2_id = imp2['resources'][0]['definition_version_id']

    # Approve V2
    appr_resp = client.post(
        f"/api/v1/projects/{pid}/metrics/{mid}/approve-version",
        headers={'x-api-key': analyst_key},
        json={'approved_version_id': v2_id}
    )
    assert appr_resp.status_code == 200
    assert appr_resp.json()['binding']['approved_version_id'] == v2_id
    assert appr_resp.json()['transition']['transition_kind'] == 'version_approved'

    # Reader views transitions
    trans_resp = client.get(
        f"/api/v1/projects/{pid}/metrics/{mid}/transitions",
        headers={'x-api-key': reader_key}
    )
    assert trans_resp.status_code == 200
    transitions = trans_resp.json()['items']
    assert len(transitions) == 2
    assert transitions[0]['transition_kind'] == 'version_approved'
    assert transitions[1]['transition_kind'] == 'definition_promoted'

    # Reader computes change set (POST and GET)
    diff_resp = client.post(
        f"/api/v1/projects/{pid}/dbt/change-sets",
        headers={'x-api-key': reader_key},
        json={'baseline_revision_id': rev1_id, 'target_revision_id': rev2_id}
    )
    assert diff_resp.status_code == 200
    diff = diff_resp.json()
    assert diff['schema_version'] == 'metricguard_change_set_v1'
    assert diff['counts']['changed_count'] == 1

    diff_get = client.get(
        f"/api/v1/projects/{pid}/dbt/change-sets?baseline_revision_id={rev1_id}&target_revision_id={rev2_id}",
        headers={'x-api-key': reader_key}
    )
    assert diff_get.status_code == 200
    assert diff_get.json() == diff


def test_deterministic_repeated_change_set_computation(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    m1 = _sample_manifest(version=12, invocation_id='inv-01')
    rev1 = store.record_dbt_import(json.dumps(m1).encode(), project_id=pid)

    m2 = _sample_manifest(version=12, invocation_id='inv-02', models={
        'model.retail.monthly_revenue': {
            'unique_id': 'model.retail.monthly_revenue',
            'resource_type': 'model',
            'name': 'monthly_revenue',
            'original_file_path': 'models/finance/monthly_revenue.sql',
            'raw_code': 'SELECT SUM(order_amount) FROM {{ ref("orders") }}',
            'compiled_code': "SELECT SUM(order_amount) FROM orders WHERE status = 'completed' AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
            'depends_on': {'nodes': ['model.retail.stg_orders']},
        }
    })
    rev2 = store.record_dbt_import(json.dumps(m2).encode(), project_id=pid)

    # Compute 5 times in a row: results must be byte-for-byte identical
    diffs = [
        store.compute_dbt_change_set(rev1['id'], rev2['id'], project_id=pid)
        for _ in range(5)
    ]
    for d in diffs[1:]:
        assert d == diffs[0]


def test_evidence_references_retained_in_promotion(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    m = store.create_metric(display_name='Orders Metric', project_id=pid)
    defn = store.record_definition(
        name='Orders SQL', source_label='dbt', owner_label='ops',
        raw_sql="SELECT SUM(order_amount) FROM orders", project_id=pid
    )
    v1_id = defn['versions'][0]['id']

    # Promotion with mock comparison_run_id and review_decision_id
    # Record a review request & decision in store first to satisfy FK
    user, _ = store.create_user('Decider', 'decider@local.internal')
    store.add_project_member(pid, user['id'], 'owner')
    req = store.create_review_request(
        project_id=pid,
        requester_id=user['id'],
        payload={
            'definition_id': defn['id'],
            'target_version_id': v1_id,
            'rationale': 'Initial approval',
        }
    )
    dec = store.record_decision(
        project_id=pid,
        decider_id=user['id'],
        payload={
            'review_request_id': req['id'],
            'approved_version_id': v1_id,
            'decision': 'approved',
            'rationale': 'Looks good',
        }
    )

    promo = store.promote_metric_definition(
        metric_id=m['id'],
        definition_id=defn['id'],
        approved_version_id=v1_id,
        review_decision_id=dec['id'],
        actor_id=user['id'],
        project_id=pid
    )

    trans = promo['transition']
    assert trans['transition_kind'] == 'definition_promoted'
    assert trans['review_decision_id'] == dec['id']
    assert trans['actor_id'] == user['id']


def test_cross_project_dbt_change_set_rejected(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid1 = 'local-retail'
    owner, _ = store.create_user('Owner 2', 'o2@metricguard.internal')
    store.create_project('Project 2', owner_id=owner['id'], project_id='proj-2')

    m1 = _sample_manifest(version=12, invocation_id='inv-01')
    rev1 = store.record_dbt_import(json.dumps(m1).encode(), project_id=pid1)

    m2 = _sample_manifest(version=12, invocation_id='inv-02')
    rev2 = store.record_dbt_import(json.dumps(m2).encode(), project_id='proj-2')

    # Comparing revisions from different projects in project 1 is rejected
    with pytest.raises(DomainError) as exc:
        store.compute_dbt_change_set(
            baseline_revision_id=rev1['id'],
            target_revision_id=rev2['id'],
            project_id=pid1
        )
    assert exc.value.code == 'NOT_FOUND'
