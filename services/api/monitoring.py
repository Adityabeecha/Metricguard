"""V3 Recurring Checks, Change Classification, Materiality and Alerting."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any


def classify_change(
    baseline_version: dict[str, Any],
    target_version: dict[str, Any],
    baseline_snapshot: dict[str, Any],
    target_snapshot: dict[str, Any],
) -> str:
    """Classify the nature of an observed difference between baseline and target runs."""
    def_changed = (
        baseline_version['id'] != target_version['id']
        or baseline_version.get('raw_hash') != target_version.get('raw_hash')
        or baseline_version.get('ir_hash') != target_version.get('ir_hash')
    )
    snap_changed = (
        baseline_snapshot['id'] != target_snapshot['id']
        or baseline_snapshot.get('manifest_hash') != target_snapshot.get('manifest_hash')
    )

    if def_changed and not snap_changed:
        return 'definition_change'
    if not def_changed and snap_changed:
        return 'dataset_change'
    if def_changed and snap_changed:
        return 'combined_change'
    return 'unchanged'


def evaluate_materiality(
    delta_str: str | None,
    baseline_val_str: str | None,
    target_val_str: str | None,
    absolute_threshold: str | None = None,
    relative_threshold: float | None = None,
    is_intentional_variant: bool = False,
    suppress_intentional_variants: bool = True,
) -> dict[str, Any]:
    """Evaluate whether an observed gap is material according to registered policy."""
    if is_intentional_variant and suppress_intentional_variants:
        return {
            'is_material': False,
            'reason': 'suppressed_intentional_variant',
            'details': {'suppressed': True, 'variant': True},
        }

    if delta_str is None or baseline_val_str is None or target_val_str is None:
        return {
            'is_material': True,
            'reason': 'undefined_endpoint',
            'details': {
                'undefined_outcome': True,
                'delta': delta_str,
                'baseline_value': baseline_val_str,
                'target_value': target_val_str,
            },
        }

    try:
        delta = Decimal(str(delta_str))
        baseline = Decimal(str(baseline_val_str))
    except (InvalidOperation, TypeError):
        return {
            'is_material': True,
            'reason': 'unparseable_value',
            'details': {'delta': delta_str, 'baseline_value': baseline_val_str},
        }

    details: dict[str, Any] = {'delta': str(delta), 'baseline_value': str(baseline)}

    # Absolute threshold check
    if absolute_threshold is not None:
        try:
            abs_thresh = Decimal(str(absolute_threshold))
            details['absolute_threshold'] = str(abs_thresh)
            if abs(delta) >= abs_thresh:
                return {
                    'is_material': True,
                    'reason': 'exceeds_absolute_threshold',
                    'details': {**details, 'gap': str(abs(delta))},
                }
        except InvalidOperation:
            pass

    # Relative threshold check
    if relative_threshold is not None:
        rel_thresh = Decimal(str(relative_threshold))
        details['relative_threshold'] = str(rel_thresh)
        if baseline == Decimal('0'):
            if delta != Decimal('0'):
                return {
                    'is_material': True,
                    'reason': 'zero_denominator_non_zero_delta',
                    'details': {**details, 'zero_denominator': True},
                }
        else:
            ratio = abs(delta / baseline)
            details['relative_ratio'] = str(round(ratio, 6))
            if ratio >= rel_thresh:
                return {
                    'is_material': True,
                    'reason': 'exceeds_relative_threshold',
                    'details': details,
                }

    # If neither threshold is configured, any non-zero delta is material
    if absolute_threshold is None and relative_threshold is None:
        is_mat = delta != Decimal('0')
        return {
            'is_material': is_mat,
            'reason': 'non_zero_delta' if is_mat else 'zero_delta',
            'details': details,
        }

    return {
        'is_material': False,
        'reason': 'within_thresholds',
        'details': details,
    }


def compute_event_hash(
    policy_id: str,
    baseline_version_id: str,
    target_version_id: str,
    snapshot_id: str,
    delta: str | None,
) -> str:
    """Deterministic hash to deduplicate equivalent recurring check events."""
    payload = {
        'policy_id': policy_id,
        'baseline_version_id': baseline_version_id,
        'target_version_id': target_version_id,
        'snapshot_id': snapshot_id,
        'delta': str(delta),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
