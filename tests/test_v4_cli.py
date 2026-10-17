"""V4-03 Headless CLI / CI Automation – correctness-critical test suite.

These tests cover the exact exit-code semantics, deterministic report
schema, materiality correctness, definition/snapshot resolution rules,
dbt-aware support-state gating, API key redaction, RBAC enforcement,
and pinned-snapshot replay determinism.
"""
from __future__ import annotations

import json
import os
import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

import subprocess
import sys

from scripts.seed_demo import seed
from services.api.store import DomainError, Store
from services.cli import (
    CIOutcome,
    REPORT_SCHEMA,
    ResolvedInputs,
    authenticate_ci_caller,
    determine_exit_code,
    execute_headless_comparison,
    generate_markdown_report,
    generate_terminal_summary,
    redact_api_key,
    resolve_snapshot,
    resolve_version,
    run_cli,
    safe_env_api_key,
    _check_support_state,
    _compute_request_hash,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

CONTEXT = {
    'start': '2026-08-01',
    'end_exclusive': '2026-09-01',
    'timezone': 'Asia/Kolkata',
    'currency': 'INR',
    'unit': 'currency',
    'grain': 'one_row_per_order_id',
    'scope': 'synthetic_retail_all_orders',
}


@pytest.fixture
def workspace(tmp_path):
    """Create a seeded workspace with definitions, snapshot, and golden DuckDB."""
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path}'
    store = seed(database_url=url, data_dir=tmp_path)
    bootstrap = store.bootstrap()
    return {
        'store': store,
        'url': url,
        'data_dir': tmp_path,
        'bootstrap': bootstrap,
        'definitions': bootstrap['definitions'],
        'snapshots': bootstrap['snapshots'],
    }


def _get_versions(workspace):
    """Get the baseline and target definition versions from the seeded demo."""
    defs = workspace['definitions']
    baseline_v = defs[0]['versions'][0]  # Dashboard A
    target_v = defs[2]['versions'][0]    # Finance (uses payment_date → different results)
    return baseline_v, target_v


def _get_snapshot(workspace):
    """Get the golden snapshot from the seeded demo."""
    return workspace['snapshots'][0]


def _build_inputs(workspace, *, mode='advisory', materiality_policy=None, relationship='candidate'):
    """Build a standard ResolvedInputs for testing."""
    store = workspace['store']
    baseline_v, target_v = _get_versions(workspace)
    snapshot = _get_snapshot(workspace)

    baseline = store.version(baseline_v['id'])
    target = store.version(target_v['id'])
    snap = store.snapshot(snapshot['id'])

    context = {**CONTEXT}
    context['contract_hash'] = snap.get('contract_hash')
    context['mapping_hash'] = snap.get('mapping_hash')

    return ResolvedInputs(
        project_id=store.project_id,
        baseline_version=baseline,
        target_version=target,
        snapshot=snap,
        context=context,
        mode=mode,
        materiality_policy=materiality_policy,
        relationship=relationship,
    )


# ===================================================================
# 1. Exit-code semantics – the correctness boundary
# ===================================================================

class TestExitCodeSemantics:
    """Verify the exact exit-code mapping."""

    def test_supported_advisory_no_drift_exits_0(self):
        assert determine_exit_code(CIOutcome.SUPPORTED, 'advisory', False) == 0

    def test_supported_advisory_material_drift_exits_0(self):
        """Advisory mode: material drift still exits 0."""
        assert determine_exit_code(CIOutcome.SUPPORTED, 'advisory', True) == 0

    def test_supported_enforcing_no_drift_exits_0(self):
        assert determine_exit_code(CIOutcome.SUPPORTED, 'enforcing', False) == 0

    def test_supported_enforcing_material_drift_exits_1(self):
        assert determine_exit_code(CIOutcome.SUPPORTED, 'enforcing', True) == 1

    def test_refused_advisory_exits_2(self):
        assert determine_exit_code(CIOutcome.REFUSED, 'advisory', False) == 2

    def test_refused_enforcing_exits_2(self):
        """Refusal ALWAYS exits 2, regardless of mode."""
        assert determine_exit_code(CIOutcome.REFUSED, 'enforcing', False) == 2

    def test_refused_with_material_exits_2(self):
        """Refusal takes precedence over materiality."""
        assert determine_exit_code(CIOutcome.REFUSED, 'enforcing', True) == 2

    def test_failed_exits_3(self):
        assert determine_exit_code(CIOutcome.FAILED, 'advisory', False) == 3

    def test_failed_enforcing_exits_3(self):
        assert determine_exit_code(CIOutcome.FAILED, 'enforcing', True) == 3


# ===================================================================
# 2. Advisory mode – supported agreement → exit 0
# ===================================================================

class TestAdvisoryAgreement:
    def test_advisory_same_definition_agreement_exit_0(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        snapshot = _get_snapshot(workspace)
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(snapshot['id'])

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=bv,   # same definition → delta 0
            snapshot=snap,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['exit_code'] == 0
        assert report['outcome'] == 'supported'
        assert report['result']['delta_target_minus_baseline'] == '0.00'
        assert report['result']['value_status'] == 'equal_on_snapshot'


# ===================================================================
# 3. Advisory mode – supported material drift → exit 0
# ===================================================================

class TestAdvisoryMaterialDrift:
    def test_advisory_drift_exits_0_but_reports_materiality(self, workspace):
        inputs = _build_inputs(workspace, mode='advisory',
                                materiality_policy={'absolute_threshold': '100'})
        report = execute_headless_comparison(workspace['store'], inputs)
        assert report['exit_code'] == 0
        assert report['outcome'] == 'supported'
        # There IS material drift, but advisory mode still exits 0
        assert report['materiality']['is_material'] is True
        # The delta should be non-zero
        delta = Decimal(report['result']['delta_target_minus_baseline'])
        assert delta != 0


# ===================================================================
# 4. Enforcing mode – agreement → exit 0
# ===================================================================

class TestEnforcingAgreement:
    def test_enforcing_agreement_exits_0(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        snapshot = _get_snapshot(workspace)
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(snapshot['id'])

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=bv,
            snapshot=snap,
            context=context,
            mode='enforcing',
            materiality_policy={'absolute_threshold': '100'},
        )
        report = execute_headless_comparison(store, inputs)
        assert report['exit_code'] == 0
        assert report['outcome'] == 'supported'
        assert report['materiality']['is_material'] is False


# ===================================================================
# 5. Enforcing mode – material drift → exit 1
# ===================================================================

class TestEnforcingDrift:
    def test_enforcing_material_drift_exits_1(self, workspace):
        inputs = _build_inputs(workspace, mode='enforcing',
                                materiality_policy={'absolute_threshold': '100'})
        report = execute_headless_comparison(workspace['store'], inputs)
        assert report['exit_code'] == 1
        assert report['outcome'] == 'supported'
        assert report['materiality']['is_material'] is True
        assert report['materiality']['reason'] == 'exceeds_absolute_threshold'


# ===================================================================
# 6. Unsupported SQL → exit 2
# ===================================================================

class TestUnsupportedSQL:
    def test_unsupported_baseline_exits_2(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        snapshot = _get_snapshot(workspace)
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(snapshot['id'])

        # Corrupt the baseline to be unsupported
        bv_unsupported = {**bv, 'support_state': 'unsupported',
                           'support_reason': 'SQL contains unsupported constructs'}

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv_unsupported,
            target_version=bv,
            snapshot=snap,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['exit_code'] == 2
        assert report['outcome'] == 'refused'
        assert 'refusal' in report
        assert 'unsupported' in report['refusal']['diagnostic'].lower()


# ===================================================================
# 7. metadata_only dbt definition → exit 2
# ===================================================================

class TestMetadataOnlyDbt:
    def test_metadata_only_baseline_exits_2(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        snapshot = _get_snapshot(workspace)
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(snapshot['id'])

        # Simulate a metadata_only dbt resource
        bv_metadata = {
            **bv,
            'support_state': 'metadata_only',
            'support_reason': 'CTE_DETECTED: Common Table Expressions must be flattened',
            '_dbt_resource_revision': {
                'unique_id': 'model.test.kpi',
                'support_state': 'metadata_only',
                'diagnostics': ['CTE_DETECTED: Common Table Expressions must be flattened'],
            },
        }

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv_metadata,
            target_version=bv,
            snapshot=snap,
            context=context,
            mode='enforcing',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        # Must exit 2 in BOTH advisory and enforcing modes
        assert report['exit_code'] == 2
        assert report['outcome'] == 'refused'
        assert 'metadata_only' in report['refusal']['diagnostic']
        assert 'CTE_DETECTED' in report['refusal']['diagnostic']

    def test_metadata_only_advisory_also_exits_2(self, workspace):
        """Unsupported analysis must NEVER become a successful pass."""
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        snapshot = _get_snapshot(workspace)
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(snapshot['id'])

        bv_metadata = {**bv, 'support_state': 'metadata_only'}

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv_metadata,
            target_version=bv,
            snapshot=snap,
            context=context,
            mode='advisory',   # advisory mode should NOT mask a refusal
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['exit_code'] == 2
        assert report['outcome'] == 'refused'


# ===================================================================
# 8. Operational – missing snapshot → exit 3
# ===================================================================

class TestOperationalMissingSnapshot:
    def test_missing_snapshot_file_exits_3(self, workspace):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snapshot = _get_snapshot(workspace)
        bv = store.version(baseline_v['id'])
        tv = store.version(target_v['id'])
        snap = store.snapshot(snapshot['id'])

        # Point to a non-existent file
        snap_missing = {**snap, 'artifact_path': '/nonexistent/path/snapshot.duckdb'}

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=tv,
            snapshot=snap_missing,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['exit_code'] == 3
        assert report['outcome'] == 'failed'
        assert 'failure' in report
        assert 'missing' in report['failure']['diagnostic'].lower()


# ===================================================================
# 9. Corrupted snapshot → exit 3
# ===================================================================

class TestCorruptedSnapshot:
    def test_corrupted_snapshot_hash_exits_3(self, workspace, tmp_path):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snapshot = _get_snapshot(workspace)
        bv = store.version(baseline_v['id'])
        tv = store.version(target_v['id'])
        snap = store.snapshot(snapshot['id'])

        # Create a corrupted artifact
        corrupted = tmp_path / 'corrupted.duckdb'
        corrupted.write_bytes(b'CORRUPTED DATA PAYLOAD')

        snap_corrupted = {
            **snap,
            'artifact_path': str(corrupted),
            'database_hash': 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef',
        }

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=tv,
            snapshot=snap_corrupted,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['exit_code'] == 3
        assert report['outcome'] == 'failed'
        assert 'integrity' in report['failure']['diagnostic'].lower()


# ===================================================================
# 10. Wrong project object → denied
# ===================================================================

class TestCrossProjectDenied:
    def test_version_from_wrong_project_rejected(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        bv = store.version(baseline_v['id'])
        # Attempt to resolve in a different project
        with pytest.raises(DomainError) as exc:
            resolve_version(store, baseline_v['id'], 'nonexistent-project')
        assert exc.value.status == 403

    def test_snapshot_from_wrong_project_rejected(self, workspace):
        store = workspace['store']
        snapshot = _get_snapshot(workspace)
        with pytest.raises(DomainError) as exc:
            resolve_snapshot(store, snapshot['id'], 'nonexistent-project')
        assert exc.value.status == 403


# ===================================================================
# 11. Reader key → denied; Analyst/owner permitted
# ===================================================================

class TestRBAC:
    def test_reader_denied_ci_comparison(self, workspace):
        store = workspace['store']
        user, key = store.create_user('CI Reader', 'reader@ci.test')
        project = store.create_project('ci-test-project', owner_id=user['id'])
        # Downgrade to reader
        store.add_project_member(project['id'], user['id'], 'reader')

        with pytest.raises(DomainError) as exc:
            authenticate_ci_caller(store, project['id'], api_key=key)
        assert exc.value.code == 'FORBIDDEN'
        assert 'reader' in exc.value.message.lower()

    def test_analyst_permitted_ci_comparison(self, workspace):
        store = workspace['store']
        user, key = store.create_user('CI Analyst', 'analyst@ci.test')
        project = store.create_project('ci-test-analyst', owner_id=user['id'])
        store.add_project_member(project['id'], user['id'], 'analyst')

        result = authenticate_ci_caller(store, project['id'], api_key=key)
        assert result['role'] == 'analyst'
        assert result['project_id'] == project['id']

    def test_owner_permitted_ci_comparison(self, workspace):
        store = workspace['store']
        user, key = store.create_user('CI Owner', 'owner@ci.test')
        project = store.create_project('ci-test-owner', owner_id=user['id'])

        result = authenticate_ci_caller(store, project['id'], api_key=key)
        assert result['role'] == 'owner'

    def test_invalid_api_key_rejected(self, workspace):
        store = workspace['store']
        with pytest.raises(DomainError) as exc:
            authenticate_ci_caller(store, store.project_id, api_key='invalid-key-12345')
        assert exc.value.code == 'UNAUTHORIZED'

    def test_unverified_local_rejected_when_disabled(self, workspace, monkeypatch):
        store = workspace['store']
        monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'false')
        with pytest.raises(DomainError) as exc:
            authenticate_ci_caller(store, store.project_id, api_key=None)
        assert exc.value.code == 'UNAUTHORIZED'

    def test_unverified_local_allowed_on_default_project(self, workspace, monkeypatch):
        store = workspace['store']
        monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'true')
        result = authenticate_ci_caller(store, store.project_id, api_key=None)
        assert result['is_unverified'] is True
        assert result['role'] == 'owner'

    def test_unverified_local_denied_on_other_project(self, workspace, monkeypatch):
        store = workspace['store']
        monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'true')
        with pytest.raises(DomainError) as exc:
            authenticate_ci_caller(store, 'other-project-id', api_key=None)
        assert exc.value.code == 'FORBIDDEN'


# ===================================================================
# 12. API key redaction
# ===================================================================

class TestAPIKeyRedaction:
    def test_redact_api_key_in_string(self):
        text = 'Connection failed with api_key=mg_secret123abc'
        redacted = redact_api_key(text)
        assert 'mg_secret123abc' not in redacted
        assert '[REDACTED]' in redacted

    def test_redact_bearer_token(self):
        text = 'Authorization: Bearer super_secret_token_123'
        redacted = redact_api_key(text)
        assert 'super_secret_token_123' not in redacted

    def test_redact_password_in_exception(self):
        text = 'password=my_database_password_123'
        redacted = redact_api_key(text)
        assert 'my_database_password_123' not in redacted

    def test_report_never_contains_api_key(self, workspace, monkeypatch):
        """The report must never contain the API key value."""
        monkeypatch.setenv('METRICGUARD_API_KEY', 'mg_super_secret_key_12345')
        inputs = _build_inputs(workspace, mode='advisory')
        report = execute_headless_comparison(workspace['store'], inputs)
        report_json = json.dumps(report, default=str)
        assert 'mg_super_secret_key_12345' not in report_json

    def test_safe_env_api_key_reads_env(self, monkeypatch):
        monkeypatch.setenv('METRICGUARD_API_KEY', 'test_key_value')
        assert safe_env_api_key() == 'test_key_value'

    def test_safe_env_api_key_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv('METRICGUARD_API_KEY', raising=False)
        assert safe_env_api_key() is None


# ===================================================================
# 13. Deterministic report contents
# ===================================================================

class TestDeterministicReport:
    def test_report_schema_and_version(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        assert report['schema'] == 'metricguard_ci_report_v1'
        assert report['version'] == 1

    def test_report_contains_immutable_input_references(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        assert report['baseline']['definition_version_id'] == inputs.baseline_version['id']
        assert report['target']['definition_version_id'] == inputs.target_version['id']
        assert report['snapshot']['snapshot_id'] == inputs.snapshot['id']
        assert report['snapshot']['manifest_hash'] == inputs.snapshot.get('manifest_hash')
        assert report['context'] == inputs.context
        assert report['project_id'] == inputs.project_id

    def test_report_has_request_hash(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        assert report['request_hash'] == inputs.request_hash
        assert len(report['request_hash']) == 64  # SHA-256 hex digest

    def test_same_inputs_produce_same_request_hash(self, workspace):
        inputs_a = _build_inputs(workspace)
        inputs_b = _build_inputs(workspace)
        assert inputs_a.request_hash == inputs_b.request_hash

    def test_different_snapshot_produces_different_hash(self, workspace):
        inputs = _build_inputs(workspace)
        # Alter snapshot ID to simulate different input
        altered_snap = {**inputs.snapshot, 'id': 'different-snap-id'}
        inputs2 = ResolvedInputs(
            project_id=inputs.project_id,
            baseline_version=inputs.baseline_version,
            target_version=inputs.target_version,
            snapshot=altered_snap,
            context=inputs.context,
            mode=inputs.mode,
            materiality_policy=inputs.materiality_policy,
        )
        assert inputs.request_hash != inputs2.request_hash

    def test_report_bridge_summary_present(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        if report['outcome'] == 'supported':
            assert 'bridge_summary' in report
            for step in report['bridge_summary']:
                assert 'position' in step
                assert 'rule' in step
                assert 'contribution' in step

    def test_report_evidence_summary_present(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        if report['outcome'] == 'supported':
            assert 'evidence_summary' in report
            summary = report['evidence_summary']
            assert 'total_rows' in summary
            assert summary['total_rows'] > 0

    def test_report_provenance_present(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        if report['outcome'] == 'supported':
            prov = report['provenance']
            assert prov['baseline_ir_hash'] == inputs.baseline_version['ir_hash']
            assert prov['target_ir_hash'] == inputs.target_version['ir_hash']
            assert prov['database_hash'] == (inputs.snapshot.get('database_hash')
                                               or inputs.snapshot.get('manifest', {}).get('database_hash'))
            assert prov['engine_version'] == 'metricguard-core-v1'


# ===================================================================
# 14. Exact decimal serialization – no floating-point ambiguity
# ===================================================================

class TestExactDecimalSerialization:
    def test_no_float_in_result_values(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        result = report.get('result', {})

        # All financial/metric values must be exact decimal strings, not floats
        for field in ('baseline_value', 'target_value', 'delta_target_minus_baseline',
                       'absolute_gap', 'residual', 'numeric_tolerance'):
            value = result.get(field)
            if value is not None:
                assert isinstance(value, str), f'{field} must be a string, got {type(value)}'
                # Verify it's a valid decimal string
                Decimal(value)

    def test_bridge_contributions_are_exact_strings(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        for step in report.get('bridge_summary', []):
            val = step.get('contribution')
            if val is not None:
                assert isinstance(val, str)
                Decimal(val)


# ===================================================================
# 15. Ambiguous selector rejection
# ===================================================================

class TestAmbiguousSelectors:
    def test_nonexistent_version_id_raises(self, workspace):
        store = workspace['store']
        with pytest.raises(DomainError) as exc:
            resolve_version(store, 'nonexistent-version-id', store.project_id)
        assert exc.value.status == 404

    def test_nonexistent_snapshot_id_raises(self, workspace):
        store = workspace['store']
        with pytest.raises(DomainError) as exc:
            resolve_snapshot(store, 'nonexistent-snapshot-id', store.project_id)
        assert exc.value.status == 404

    def test_dbt_prefix_with_unknown_unique_id_raises(self, workspace):
        store = workspace['store']
        with pytest.raises(DomainError) as exc:
            resolve_version(store, 'dbt:model.unknown.kpi', store.project_id)
        assert exc.value.status == 404


# ===================================================================
# 16. Pinned snapshot/definition replay produces identical result
# ===================================================================

class TestReplayDeterminism:
    def test_repeated_execution_produces_identical_result(self, workspace):
        inputs = _build_inputs(workspace)
        store = workspace['store']

        report1 = execute_headless_comparison(store, inputs)
        report2 = execute_headless_comparison(store, inputs)

        assert report1['exit_code'] == report2['exit_code']
        assert report1['outcome'] == report2['outcome']
        assert report1['request_hash'] == report2['request_hash']

        # Comparison results must be identical
        if report1['outcome'] == 'supported':
            r1, r2 = report1['result'], report2['result']
            assert r1['baseline_value'] == r2['baseline_value']
            assert r1['target_value'] == r2['target_value']
            assert r1['delta_target_minus_baseline'] == r2['delta_target_minus_baseline']
            assert r1['residual'] == r2['residual']
            assert r1['value_status'] == r2['value_status']

    def test_same_result_across_modes(self, workspace):
        """Mode affects exit code but not the comparison result values."""
        store = workspace['store']
        advisory = _build_inputs(workspace, mode='advisory',
                                  materiality_policy={'absolute_threshold': '100'})
        enforcing = _build_inputs(workspace, mode='enforcing',
                                   materiality_policy={'absolute_threshold': '100'})

        r_adv = execute_headless_comparison(store, advisory)
        r_enf = execute_headless_comparison(store, enforcing)

        # Results are identical
        assert r_adv['result'] == r_enf['result']
        # But exit codes differ
        if r_adv['materiality']['is_material']:
            assert r_adv['exit_code'] == 0   # advisory
            assert r_enf['exit_code'] == 1   # enforcing


# ===================================================================
# 17. Materiality correctness
# ===================================================================

class TestMaterialityCorrectness:
    def test_absolute_threshold_exact_boundary(self, workspace):
        """Verify exact boundary: abs(delta) == threshold is material."""
        store = workspace['store']
        inputs = _build_inputs(workspace, mode='enforcing')
        report = execute_headless_comparison(store, inputs)

        if report['outcome'] != 'supported':
            pytest.skip('Cannot test materiality without supported analysis')

        delta = abs(Decimal(report['result']['delta_target_minus_baseline']))
        # Set threshold exactly equal to delta
        inputs_exact = _build_inputs(
            workspace, mode='enforcing',
            materiality_policy={'absolute_threshold': str(delta)},
        )
        report_exact = execute_headless_comparison(store, inputs_exact)
        assert report_exact['materiality']['is_material'] is True
        assert report_exact['exit_code'] == 1

    def test_absolute_threshold_just_above_boundary(self, workspace):
        """abs(delta) < threshold → not material."""
        store = workspace['store']
        inputs = _build_inputs(workspace, mode='enforcing')
        report = execute_headless_comparison(store, inputs)

        if report['outcome'] != 'supported':
            pytest.skip('Cannot test materiality without supported analysis')

        delta = abs(Decimal(report['result']['delta_target_minus_baseline']))
        # Set threshold just above delta
        threshold = str(delta + Decimal('0.01'))
        inputs_above = _build_inputs(
            workspace, mode='enforcing',
            materiality_policy={'absolute_threshold': threshold},
        )
        report_above = execute_headless_comparison(store, inputs_above)
        assert report_above['materiality']['is_material'] is False
        assert report_above['exit_code'] == 0

    def test_zero_delta_within_thresholds(self, workspace):
        """Zero delta should not be material."""
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        snapshot = _get_snapshot(workspace)
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(snapshot['id'])

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=bv,   # same → delta 0
            snapshot=snap,
            context=context,
            mode='enforcing',
            materiality_policy={'absolute_threshold': '100'},
        )
        report = execute_headless_comparison(store, inputs)
        assert report['materiality']['is_material'] is False
        assert report['exit_code'] == 0

    def test_no_threshold_any_nonzero_is_material(self, workspace):
        """When no thresholds configured, any non-zero delta is material."""
        inputs = _build_inputs(workspace, mode='enforcing', materiality_policy=None)
        report = execute_headless_comparison(workspace['store'], inputs)
        if report['outcome'] == 'supported':
            delta = Decimal(report['result']['delta_target_minus_baseline'])
            if delta != 0:
                assert report['materiality']['is_material'] is True


# ===================================================================
# 18. Support-state gating
# ===================================================================

class TestSupportStateGating:
    def test_executable_is_supported(self):
        supported, diag = _check_support_state({'support_state': 'executable'}, 'Test')
        assert supported is True
        assert diag is None

    def test_metadata_only_is_refused(self):
        supported, diag = _check_support_state({'support_state': 'metadata_only'}, 'Test')
        assert supported is False
        assert 'metadata_only' in diag

    def test_needs_context_is_refused(self):
        supported, diag = _check_support_state({
            'support_state': 'needs_context',
            'support_reason': 'Confirm a business date column',
        }, 'Test')
        assert supported is False
        assert 'context' in diag.lower()

    def test_unsupported_is_refused(self):
        supported, diag = _check_support_state({
            'support_state': 'unsupported',
            'support_reason': 'Multi-statement SQL',
        }, 'Test')
        assert supported is False
        assert 'Multi-statement' in diag


# ===================================================================
# 19. dbt-aware behavior with V4-01 integration
# ===================================================================

class TestDbtIntegration:
    def test_dbt_resolve_executable_resource(self, workspace):
        """Importing a dbt manifest with executable SQL links to a DefinitionVersion."""
        import io
        store = workspace['store']
        pid = store.project_id

        # Minimal manifest v11 with executable SQL (v9+ uses compiled_code, URL ends with v11.json)
        manifest = {
            'metadata': {'dbt_schema_version': 'https://schemas.getdbt.com/dbt/manifest/v11.json',
                         'invocation_id': 'ci-test-001', 'dbt_version': '1.7.0',
                         'generated_at': '2026-09-25T10:00:00Z', 'project_name': 'test_dbt'},
            'nodes': {
                'model.test.revenue_kpi': {
                    'unique_id': 'model.test.revenue_kpi',
                    'resource_type': 'model',
                    'name': 'revenue_kpi',
                    'compiled_code': "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                    'original_file_path': 'models/revenue_kpi.sql',
                    'depends_on': {'nodes': []},
                },
            },
            'child_map': {},
        }
        manifest_bytes = json.dumps(manifest).encode()

        result = store.record_dbt_import(manifest_bytes, project_id=pid)
        assert not result['is_reimport']
        assert len(result['resources']) == 1

        resource = result['resources'][0]
        assert resource['support_state'] in ('executable', 'needs_context')

        if resource['definition_version_id']:
            # Should resolve via dbt: prefix
            version = resolve_version(store, f"dbt:{resource['unique_id']}", pid)
            assert version['id'] == resource['definition_version_id']

    def test_dbt_resolve_metadata_only_resource_is_refused(self, workspace):
        """A metadata_only dbt resource must refuse resolution."""
        store = workspace['store']
        pid = store.project_id

        # Manifest with CTE (will be classified as metadata_only)
        manifest = {
            'metadata': {'dbt_schema_version': 'https://schemas.getdbt.com/dbt/manifest/v11.json',
                         'invocation_id': 'ci-test-002', 'dbt_version': '1.7.0',
                         'generated_at': '2026-09-25T10:01:00Z', 'project_name': 'test_dbt'},
            'nodes': {
                'model.test.cte_model': {
                    'unique_id': 'model.test.cte_model',
                    'resource_type': 'model',
                    'name': 'cte_model',
                    'compiled_code': 'WITH base AS (SELECT * FROM orders) SELECT order_id, order_amount FROM base',
                    'original_file_path': 'models/cte_model.sql',
                    'depends_on': {'nodes': []},
                },
            },
            'child_map': {},
        }
        manifest_bytes = json.dumps(manifest).encode()

        result = store.record_dbt_import(manifest_bytes, project_id=pid)
        resource = result['resources'][0]
        assert resource['support_state'] == 'metadata_only'
        assert resource['definition_version_id'] is None

        # Attempting to resolve must raise with appropriate diagnostics
        with pytest.raises(DomainError) as exc:
            resolve_version(store, 'dbt:model.test.cte_model', pid)
        assert exc.value.code == 'UNSUPPORTED_CONSTRUCT'
        assert 'metadata_only' in exc.value.message

    def test_dbt_anchor_appears_in_report(self, workspace):
        """When a dbt resource revision is attached, the report includes it."""
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        snapshot = _get_snapshot(workspace)
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(snapshot['id'])

        # Attach a fake dbt revision
        bv_with_dbt = {
            **bv,
            '_dbt_resource_revision': {
                'unique_id': 'model.retail.dashboard_a',
                'resource_type': 'model',
                'support_state': 'executable',
                'import_revision_id': 'dbt-rev-test123',
                'diagnostics': [],
            },
        }

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv_with_dbt,
            target_version=bv,
            snapshot=snap,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['baseline']['dbt'] is not None
        assert report['baseline']['dbt']['unique_id'] == 'model.retail.dashboard_a'
        assert report['baseline']['dbt']['import_revision_id'] == 'dbt-rev-test123'
        assert report['target']['dbt'] is None  # no dbt revision attached


# ===================================================================
# 20. CLI entry point (minimal proof-of-contract)
# ===================================================================

class TestCLIEntryPoint:
    def test_cli_compare_advisory_success(self, workspace, tmp_path, monkeypatch):
        from services.cli import run_cli

        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snapshot = _get_snapshot(workspace)
        snap_data = store.snapshot(snapshot['id'])

        context = {**CONTEXT}
        context['contract_hash'] = snap_data.get('contract_hash')
        context['mapping_hash'] = snap_data.get('mapping_hash')

        output_file = tmp_path / 'report.json'

        monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'true')

        exit_code = run_cli([
            'compare',
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', target_v['id'],
            '--snapshot', snapshot['id'],
            '--mode', 'advisory',
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
            '--output', str(output_file),
        ])

        assert exit_code == 0
        report = json.loads(output_file.read_text(encoding='utf-8'))
        assert report['schema'] == 'metricguard_ci_report_v1'
        assert report['outcome'] == 'supported'

    def test_cli_compare_enforcing_drift_exit_1(self, workspace, tmp_path, monkeypatch):
        from services.cli import run_cli

        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snapshot = _get_snapshot(workspace)
        snap_data = store.snapshot(snapshot['id'])

        context = {**CONTEXT}
        context['contract_hash'] = snap_data.get('contract_hash')
        context['mapping_hash'] = snap_data.get('mapping_hash')

        output_file = tmp_path / 'report.json'
        monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'true')

        exit_code = run_cli([
            'compare',
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', target_v['id'],
            '--snapshot', snapshot['id'],
            '--mode', 'enforcing',
            '--absolute-threshold', '100',
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
            '--output', str(output_file),
        ])

        assert exit_code == 1
        report = json.loads(output_file.read_text(encoding='utf-8'))
        assert report['outcome'] == 'supported'
        assert report['materiality']['is_material'] is True

    def test_cli_no_project_id_exits_3(self, tmp_path, monkeypatch):
        from services.cli import run_cli

        monkeypatch.delenv('METRICGUARD_PROJECT_ID', raising=False)
        exit_code = run_cli([
            'compare',
            '--baseline', 'x',
            '--target', 'y',
            '--snapshot', 'z',
            '--context-json', '{}',
        ])
        assert exit_code == 3


# ===================================================================
# 21. Outcome model distinguishes supported/refused/failed
# ===================================================================

class TestOutcomeModel:
    def test_supported_report_has_result(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        assert report['outcome'] == 'supported'
        assert 'result' in report
        assert 'refusal' not in report
        assert 'failure' not in report

    def test_refused_report_has_refusal(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(_get_snapshot(workspace)['id'])

        bv_bad = {**bv, 'support_state': 'metadata_only'}
        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv_bad,
            target_version=bv,
            snapshot=snap,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['outcome'] == 'refused'
        assert 'refusal' in report
        assert 'result' not in report
        assert 'failure' not in report

    def test_failed_report_has_failure(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        snap_broken = {**snap, 'artifact_path': '/nonexistent'}

        context = {**CONTEXT}
        context['contract_hash'] = snap.get('contract_hash')
        context['mapping_hash'] = snap.get('mapping_hash')

        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=bv,
            snapshot=snap_broken,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['outcome'] == 'failed'
        assert 'failure' in report
        assert 'result' not in report
        assert 'refusal' not in report


# ===================================================================
# 22. Terminal Summary presentation
# ===================================================================

class TestTerminalSummary:
    def test_terminal_summary_supported_agreement(self, workspace):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}
        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=bv,
            snapshot=snap,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        summary = generate_terminal_summary(report)

        assert 'METRICGUARD CI COMPARISON' in summary
        assert 'Status:          SUPPORTED' in summary
        assert 'Exit code: 0' in summary
        assert 'CI State:        PASS (Supported Agreement)' in summary
        assert 'Baseline Value:' in summary
        assert 'Target Value:' in summary
        assert 'Delta (Target - Base):' in summary
        assert 'Row Evidence Summary' in summary
        assert 'Total Evaluated Rows:' in summary
        assert 'Final CI Exit Code: 0' in summary

    def test_terminal_summary_enforcing_material_drift(self, workspace):
        inputs = _build_inputs(workspace, mode='enforcing', materiality_policy={'absolute_threshold': '100.0'})
        report = execute_headless_comparison(workspace['store'], inputs)
        assert report['exit_code'] == 1
        summary = generate_terminal_summary(report)

        assert 'Status:          SUPPORTED' in summary
        assert 'Exit code: 1' in summary
        assert 'CI State:        FAIL (Material Drift Exceeded in Enforcing Mode)' in summary
        assert 'Materiality Verdict:      MATERIAL DRIFT' in summary
        assert 'Final CI Exit Code: 1' in summary

    def test_terminal_summary_refused(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        bv_bad = {**bv, 'support_state': 'metadata_only'}
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}
        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv_bad,
            target_version=bv,
            snapshot=snap,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['outcome'] == 'refused'
        summary = generate_terminal_summary(report)

        assert 'Status:          REFUSED' in summary
        assert 'Exit code: 2' in summary
        assert 'REFUSED (Quantitative Analysis Refused - Exit Code 2)' in summary
        assert 'Refusal Diagnostic' in summary
        assert 'metadata_only' in summary

    def test_terminal_summary_failed(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        snap_broken = {**snap, 'artifact_path': '/nonexistent/file.duckdb'}
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}
        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=bv,
            snapshot=snap_broken,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['outcome'] == 'failed'
        summary = generate_terminal_summary(report)

        assert 'Status:          FAILED' in summary
        assert 'Exit code: 3' in summary
        assert 'FAILED (Operational Failure - Exit Code 3)' in summary
        assert 'Operational Failure' in summary

    def test_terminal_summary_redacts_credentials(self):
        fake_report = {
            'outcome': 'failed',
            'exit_code': 3,
            'execution_mode': 'advisory',
            'project_id': 'proj-secret-key=supersecretpass123',
            'diagnostic': 'Connection to postgresql://user:my_secret_pw@db:5432/test failed with api_key=mg_key_abc',
        }
        summary = generate_terminal_summary(fake_report)
        assert 'supersecretpass123' not in summary
        assert 'my_secret_pw' not in summary
        assert 'mg_key_abc' not in summary
        assert '[REDACTED]' in summary


# ===================================================================
# 23. Markdown Report presentation
# ===================================================================

class TestMarkdownReport:
    def test_markdown_report_supported(self, workspace):
        inputs = _build_inputs(workspace, mode='enforcing', materiality_policy={'relative_threshold': 0.05})
        report = execute_headless_comparison(workspace['store'], inputs)
        md = generate_markdown_report(report)

        assert '# MetricGuard CI Comparison Report' in md
        assert '| **Status** |' in md
        assert '## 1. Comparison Identity' in md
        assert '## 2. Metric Results & Materiality' in md
        assert '## 3. Evidence & Row Attribution' in md
        assert '## 4. Bridge Attribution Summary' in md
        assert '## 5. Execution Provenance' in md
        assert report['baseline']['definition_version_id'] in md
        assert report['snapshot']['snapshot_id'] in md
        assert report['result']['baseline_value'] in md

    def test_markdown_report_refused(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        bv_bad = {**bv, 'support_state': 'metadata_only'}
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}
        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv_bad,
            target_version=bv,
            snapshot=snap,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        md = generate_markdown_report(report)

        assert '# MetricGuard CI Comparison Report' in md
        assert 'REFUSED' in md
        assert '## 2. Refusal Diagnostic' in md
        assert 'metadata_only' in md

    def test_markdown_report_failed(self, workspace):
        store = workspace['store']
        baseline_v = workspace['definitions'][0]['versions'][0]
        bv = store.version(baseline_v['id'])
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        snap_broken = {**snap, 'artifact_path': '/nonexistent/file.duckdb'}
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}
        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=bv,
            snapshot=snap_broken,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        md = generate_markdown_report(report)

        assert '# MetricGuard CI Comparison Report' in md
        assert 'FAILED' in md
        assert '## 2. Operational Failure Diagnostic' in md
        assert 'missing or corrupted' in md

    def test_markdown_report_with_dbt_metadata(self, workspace):
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        report['baseline']['dbt'] = {
            'unique_id': 'model.retail.orders_v1',
            'import_revision_id': 'dbt-imp-123',
            'observed_lineage': ['model.raw_orders', 'model.stg_orders'],
        }
        md = generate_markdown_report(report)
        assert '### dbt Provenance Metadata' in md
        assert 'model.retail.orders_v1' in md
        assert 'dbt-imp-123' in md
        assert 'model.raw_orders, model.stg_orders' in md

    def test_markdown_report_redacts_credentials(self):
        fake_report = {
            'outcome': 'failed',
            'exit_code': 3,
            'diagnostic': 'Error: invalid password=super_secret_pw in connection',
        }
        md = generate_markdown_report(fake_report)
        assert 'super_secret_pw' not in md
        assert '[REDACTED]' in md


# ===================================================================
# 24. CLI Subprocess Matrix (Real subprocess execution)
# ===================================================================

class TestCLISubprocessMatrix:
    def _run_cli_sub(self, cmd_args, env_overrides=None):
        env = os.environ.copy()
        env['METRICGUARD_ALLOW_UNVERIFIED_LOCAL'] = 'true'
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, '-m', 'services.cli', 'compare'] + cmd_args,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_subprocess_advisory_agreement_exits_0(self, workspace):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', baseline_v['id'],
            '--snapshot', snap['id'],
            '--mode', 'advisory',
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
        ])
        assert proc.returncode == 0
        assert 'METRICGUARD CI COMPARISON' in proc.stdout
        assert 'PASS (Supported Agreement)' in proc.stdout

    def test_subprocess_advisory_material_drift_exits_0(self, workspace):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', target_v['id'],
            '--snapshot', snap['id'],
            '--mode', 'advisory',
            '--absolute-threshold', '10.0',
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
        ])
        assert proc.returncode == 0
        assert 'PASS (Advisory Drift' in proc.stdout

    def test_subprocess_enforcing_agreement_exits_0(self, workspace):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', baseline_v['id'],
            '--snapshot', snap['id'],
            '--mode', 'enforcing',
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
        ])
        assert proc.returncode == 0
        assert 'PASS (Supported Agreement)' in proc.stdout

    def test_subprocess_enforcing_material_drift_exits_1(self, workspace):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', target_v['id'],
            '--snapshot', snap['id'],
            '--mode', 'enforcing',
            '--absolute-threshold', '10.0',
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
        ])
        assert proc.returncode == 1
        assert 'FAIL (Material Drift Exceeded in Enforcing Mode)' in proc.stdout

    def test_subprocess_unsupported_analysis_exits_2(self, workspace):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        pid = store.project_id

        manifest = {
            'metadata': {
                'dbt_schema_version': 'https://schemas.getdbt.com/dbt/manifest/v12.json',
                'dbt_version': '1.8.0',
                'generated_at': '2026-09-25T12:00:00Z',
                'invocation_id': 'inv-unsupported-sub',
            },
            'nodes': {
                'model.test.cte_sub': {
                    'unique_id': 'model.test.cte_sub',
                    'resource_type': 'model',
                    'name': 'cte_sub',
                    'schema': 'analytics',
                    'alias': 'cte_sub',
                    'raw_code': 'WITH cte AS (SELECT * FROM orders) SELECT * FROM cte',
                    'compiled_code': 'WITH cte AS (SELECT * FROM orders) SELECT * FROM cte',
                    'original_file_path': 'models/cte_sub.sql',
                    'depends_on': {'nodes': []},
                },
            },
            'child_map': {},
        }
        store.record_dbt_import(json.dumps(manifest).encode(), project_id=pid)

        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', 'dbt:model.test.cte_sub',
            '--target', target_v['id'],
            '--snapshot', snap['id'],
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
        ])
        assert proc.returncode == 2
        assert 'REFUSED' in proc.stdout


    def test_subprocess_missing_snapshot_exits_3(self, workspace):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', target_v['id'],
            '--snapshot', 'missing-snapshot-uuid-1234',
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
        ])
        assert proc.returncode == 3
        assert 'FAILED' in proc.stdout

    def test_subprocess_malformed_input_exits_3(self, workspace):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', target_v['id'],
            '--snapshot', snap['id'],
            '--context-json', '{malformed: not valid json}',
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
        ])
        assert proc.returncode == 3
        assert 'Error:' in proc.stderr

    def test_subprocess_auth_failure_missing_key_exits_3(self, workspace):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        proc = self._run_cli_sub(
            [
                '--project-id', store.project_id,
                '--baseline', baseline_v['id'],
                '--target', target_v['id'],
                '--snapshot', snap['id'],
                '--context-json', json.dumps(context),
                '--database-url', workspace['url'],
                '--data-dir', str(workspace['data_dir']),
            ],
            env_overrides={'METRICGUARD_ALLOW_UNVERIFIED_LOCAL': 'false', 'METRICGUARD_API_KEY': ''},
        )
        assert proc.returncode == 3
        assert 'FAILED' in proc.stdout

    def test_subprocess_authorization_failure_reader_role_exits_3(self, workspace):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        reader_user, reader_key = store.create_user('Reader User', 'reader@example.com')
        store.add_project_member(store.project_id, reader_user['id'], role='reader')

        proc = self._run_cli_sub(
            [
                '--project-id', store.project_id,
                '--baseline', baseline_v['id'],
                '--target', target_v['id'],
                '--snapshot', snap['id'],
                '--context-json', json.dumps(context),
                '--database-url', workspace['url'],
                '--data-dir', str(workspace['data_dir']),
            ],
            env_overrides={
                'METRICGUARD_ALLOW_UNVERIFIED_LOCAL': 'false',
                'METRICGUARD_API_KEY': reader_key,
            },
        )
        assert proc.returncode == 3
        assert 'FAILED' in proc.stdout


    def test_subprocess_json_and_markdown_file_generation(self, workspace, tmp_path):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        json_out = tmp_path / 'artifacts' / 'report.json'
        md_out = tmp_path / 'artifacts' / 'report.md'

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', baseline_v['id'],
            '--snapshot', snap['id'],
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
            '--output', str(json_out),
            '--markdown-output', str(md_out),
        ])
        assert proc.returncode == 0
        assert json_out.is_file()
        assert md_out.is_file()

        data = json.loads(json_out.read_text(encoding='utf-8'))
        assert data['schema'] == 'metricguard_ci_report_v1'
        assert data['outcome'] == 'supported'

        md_content = md_out.read_text(encoding='utf-8')
        assert '# MetricGuard CI Comparison Report' in md_content
        assert '## 1. Comparison Identity' in md_content

    def test_subprocess_file_overwrite_behavior(self, workspace, tmp_path):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        json_out = tmp_path / 'report.json'
        md_out = tmp_path / 'report.md'
        json_out.write_text('stale_dummy_json', encoding='utf-8')
        md_out.write_text('stale_dummy_markdown', encoding='utf-8')

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', baseline_v['id'],
            '--snapshot', snap['id'],
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
            '--output', str(json_out),
            '--markdown-output', str(md_out),
        ])
        assert proc.returncode == 0
        assert 'stale_dummy_json' not in json_out.read_text(encoding='utf-8')
        assert 'stale_dummy_markdown' not in md_out.read_text(encoding='utf-8')

    def test_subprocess_output_directory_failure_exits_3(self, workspace, tmp_path):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        bad_dir = tmp_path / 'blocking_file'
        bad_dir.write_text('I am a file, not a directory', encoding='utf-8')
        impossible_output = bad_dir / 'subdir' / 'report.json'

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', baseline_v['id'],
            '--snapshot', snap['id'],
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
            '--output', str(impossible_output),
        ])
        assert proc.returncode == 3
        assert 'Error:' in proc.stderr

    def test_subprocess_secret_redaction_across_all_surfaces(self, workspace, tmp_path):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        json_out = tmp_path / 'report.json'
        md_out = tmp_path / 'report.md'
        analyst_user, secret_key = store.create_user('Analyst CI', 'analyst_ci@example.com')
        store.add_project_member(store.project_id, analyst_user['id'], role='analyst')

        proc = self._run_cli_sub(
            [
                '--project-id', store.project_id,
                '--baseline', baseline_v['id'],
                '--target', baseline_v['id'],
                '--snapshot', snap['id'],
                '--context-json', json.dumps(context),
                '--database-url', workspace['url'],
                '--data-dir', str(workspace['data_dir']),
                '--output', str(json_out),
                '--markdown-output', str(md_out),
            ],
            env_overrides={'METRICGUARD_API_KEY': secret_key},
        )
        assert proc.returncode == 0
        assert secret_key not in proc.stdout
        assert secret_key not in proc.stderr
        assert secret_key not in json_out.read_text(encoding='utf-8')
        assert secret_key not in md_out.read_text(encoding='utf-8')


    def test_subprocess_format_options(self, workspace):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        # 1. Format JSON
        proc_json = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', baseline_v['id'],
            '--snapshot', snap['id'],
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
            '--format', 'json',
        ])
        assert proc_json.returncode == 0
        parsed = json.loads(proc_json.stdout)
        assert parsed['schema'] == 'metricguard_ci_report_v1'

        # 2. Quiet
        proc_quiet = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', baseline_v['id'],
            '--snapshot', snap['id'],
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
            '--quiet',
        ])
        assert proc_quiet.returncode == 0
        assert proc_quiet.stdout.strip() == ''

    def test_subprocess_context_file_input(self, workspace, tmp_path):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        ctx_file = tmp_path / 'my_context.json'
        ctx_file.write_text(json.dumps(context), encoding='utf-8')

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', baseline_v['id'],
            '--snapshot', snap['id'],
            '--context', str(ctx_file),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
        ])
        assert proc.returncode == 0
        assert 'PASS (Supported Agreement)' in proc.stdout

    def test_subprocess_materiality_policy_file_input(self, workspace, tmp_path):
        store = workspace['store']
        baseline_v, target_v = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        policy_file = tmp_path / 'policy.json'
        policy_file.write_text(json.dumps({'absolute_threshold': '10.0'}), encoding='utf-8')

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', target_v['id'],
            '--snapshot', snap['id'],
            '--mode', 'enforcing',
            '--materiality-policy', str(policy_file),
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
        ])
        assert proc.returncode == 1
        assert 'FAIL (Material Drift Exceeded in Enforcing Mode)' in proc.stdout

    def test_subprocess_windows_paths_and_execution(self, workspace, tmp_path):
        store = workspace['store']
        baseline_v, _ = _get_versions(workspace)
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}

        spaced_dir = tmp_path / 'path with spaces and backslashes'
        spaced_dir.mkdir(parents=True, exist_ok=True)
        json_out = spaced_dir / 'output report.json'

        proc = self._run_cli_sub([
            '--project-id', store.project_id,
            '--baseline', baseline_v['id'],
            '--target', baseline_v['id'],
            '--snapshot', snap['id'],
            '--context-json', json.dumps(context),
            '--database-url', workspace['url'],
            '--data-dir', str(workspace['data_dir']),
            '--output', str(json_out),
        ])
        assert proc.returncode == 0
        assert json_out.is_file()


# ===================================================================
# 25. V4-01 Integration Preservation
# ===================================================================

class TestV401IntegrationPreservation:
    def test_cli_preserves_dbt_import_and_resource_revisions(self, workspace):
        store = workspace['store']
        pid = store.project_id
        manifest = {
            'metadata': {
                'dbt_schema_version': 'https://schemas.getdbt.com/dbt/manifest/v12.json',
                'dbt_version': '1.8.0',
                'generated_at': '2026-09-25T12:00:00Z',
                'invocation_id': 'inv-v401-check',
            },
            'nodes': {
                'model.retail.executable_kpi': {
                    'unique_id': 'model.retail.executable_kpi',
                    'resource_type': 'model',
                    'name': 'executable_kpi',
                    'schema': 'analytics',
                    'alias': 'executable_kpi',
                    'raw_code': 'SELECT SUM(order_amount) FROM orders',
                    'compiled_code': "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                    'original_file_path': 'models/clean_model.sql',
                    'depends_on': {'nodes': ['source.retail.raw_orders']},
                },
            },
            'child_map': {},
        }
        res = store.record_dbt_import(
            json.dumps(manifest).encode(),
            project_id=pid,
            source_revision={'git_sha': 'git-commit-v401-abc'},
        )
        assert res['source_revision']['git_sha'] == 'git-commit-v401-abc'

        version = resolve_version(store, 'dbt:model.retail.executable_kpi', pid)
        assert version['_dbt_resource_revision']['unique_id'] == 'model.retail.executable_kpi'
        assert version['_dbt_resource_revision']['observed_lineage']['upstream_nodes'] == ['source.retail.raw_orders']

        snap = store.snapshot(_get_snapshot(workspace)['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}
        inputs = ResolvedInputs(
            project_id=pid,
            baseline_version=version,
            target_version=version,
            snapshot=snap,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        dbt_anchor = report['baseline']['dbt']
        assert dbt_anchor is not None
        assert dbt_anchor['unique_id'] == 'model.retail.executable_kpi'
        assert dbt_anchor['observed_lineage']['upstream_nodes'] == ['source.retail.raw_orders']
        assert dbt_anchor['source_revision'] == {'git_sha': 'git-commit-v401-abc'}
        assert dbt_anchor['dbt_version'] == '1.8.0'




# ===================================================================
# 26. V4-02 Integration Preservation
# ===================================================================

class TestV402IntegrationPreservation:
    def test_headless_comparison_does_not_connect_to_postgres(self, workspace, monkeypatch):
        """Zero PostgreSQL calls during comparison execution."""
        def fail_on_connect(*args, **kwargs):
            raise AssertionError("PostgreSQL connection attempted during headless comparison!")

        monkeypatch.setattr('psycopg.connect', fail_on_connect)
        inputs = _build_inputs(workspace)
        report = execute_headless_comparison(workspace['store'], inputs)
        assert report['outcome'] == 'supported'

    def test_headless_comparison_validates_duckdb_database_hash(self, workspace):
        """Tampered snapshot DuckDB file fails integrity and exits with operational failure."""
        store = workspace['store']
        snap = store.snapshot(_get_snapshot(workspace)['id'])
        tampered_snap = {
            **snap,
            'database_hash': '0000000000000000000000000000000000000000000000000000000000000000',
        }
        baseline_v, _ = _get_versions(workspace)
        bv = store.version(baseline_v['id'])
        context = {**CONTEXT, 'contract_hash': snap.get('contract_hash'), 'mapping_hash': snap.get('mapping_hash')}
        inputs = ResolvedInputs(
            project_id=store.project_id,
            baseline_version=bv,
            target_version=bv,
            snapshot=tampered_snap,
            context=context,
            mode='advisory',
            materiality_policy=None,
        )
        report = execute_headless_comparison(store, inputs)
        assert report['outcome'] == 'failed'
        assert report['exit_code'] == 3
        assert 'Snapshot integrity failure' in report['failure']['diagnostic']

    def test_same_snapshot_deterministic_replay(self, workspace):
        """Replaying with the same snapshot produces identical results and request hashes."""
        inputs1 = _build_inputs(workspace)
        inputs2 = _build_inputs(workspace)
        assert inputs1.request_hash == inputs2.request_hash

        report1 = execute_headless_comparison(workspace['store'], inputs1)
        report2 = execute_headless_comparison(workspace['store'], inputs2)

        assert report1['result']['delta_target_minus_baseline'] == report2['result']['delta_target_minus_baseline']
        assert report1['evidence_summary'] == report2['evidence_summary']
        assert report1['request_hash'] == report2['request_hash']

