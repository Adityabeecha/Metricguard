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
    exact_ratio: dict | None = None,
) -> dict[str, Any]:
    """Evaluate whether an observed gap is material according to registered policy."""
    if is_intentional_variant and suppress_intentional_variants:
        return {
            'is_material': False,
            'reason': 'suppressed_intentional_variant',
            'details': {'suppressed': True, 'variant': True},
        }

    if exact_ratio is not None:
        return _ratio_materiality(exact_ratio, absolute_threshold, relative_threshold)

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


def _ratio_materiality(ratio, absolute_threshold, relative_threshold):
    """Gate ratio changes on verified rational values, never rounded display text."""
    from fractions import Fraction
    from packages.metricguard_core.flat_ratio import fraction_text, parse_fraction_text

    if ratio.get('status') != 'defined':
        return {'is_material': True, 'reason': 'undefined_endpoint',
                'details': {'undefined_outcome': True, 'exact_ratio': True}}
    try:
        baseline = parse_fraction_text(ratio['baseline']['exact'])
        target = parse_fraction_text(ratio['target']['exact'])
        delta = parse_fraction_text(ratio['gap_exact'])
        if target - baseline != delta:
            raise ValueError('inconsistent exact ratio')
        absolute = None if absolute_threshold is None else Fraction(str(absolute_threshold))
        relative = None if relative_threshold is None else Fraction(str(relative_threshold))
        if (absolute is not None and absolute < 0) or (relative is not None and relative < 0):
            raise ValueError('invalid threshold')
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return {'is_material': True, 'reason': 'unparseable_value', 'details': {'exact_ratio': True}}
    details = {'exact_ratio': True, 'gap_exact': fraction_text(delta),
               'baseline_exact': fraction_text(baseline), 'target_exact': fraction_text(target)}
    reason, material = 'within_thresholds', False
    if absolute is not None and abs(delta) >= absolute:
        reason, material = 'exceeds_absolute_threshold', True
    elif relative is not None and not baseline and delta:
        reason, material = 'zero_denominator_non_zero_delta', True
    elif relative is not None and baseline and abs(delta / baseline) >= relative:
        reason, material = 'exceeds_relative_threshold', True
    elif absolute is None and relative is None:
        material = bool(delta)
        reason = 'non_zero_delta' if material else 'zero_delta'
    return {'is_material': material, 'reason': reason, 'details': details}


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
