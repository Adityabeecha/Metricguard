from decimal import Decimal
import pytest

from services.api.monitoring import classify_change, evaluate_materiality, compute_event_hash


def test_four_way_change_classification_independent():
    """Verify all four change classification combinations independently."""
    v1 = {'id': 'def-v1', 'raw_hash': 'raw-1', 'ir_hash': 'ir-1'}
    v2 = {'id': 'def-v2', 'raw_hash': 'raw-2', 'ir_hash': 'ir-2'}
    s1 = {'id': 'snap-1', 'manifest_hash': 'manifest-1'}
    s2 = {'id': 'snap-2', 'manifest_hash': 'manifest-2'}

    # 1. Definition changed, dataset unchanged
    assert classify_change(
        baseline_version=v1, target_version=v2,
        baseline_snapshot=s1, target_snapshot=s1
    ) == 'definition_change'

    # 2. Dataset changed, definition unchanged
    assert classify_change(
        baseline_version=v1, target_version=v1,
        baseline_snapshot=s1, target_snapshot=s2
    ) == 'dataset_change'

    # 3. Both changed
    assert classify_change(
        baseline_version=v1, target_version=v2,
        baseline_snapshot=s1, target_snapshot=s2
    ) == 'combined_change'

    # 4. Neither changed
    assert classify_change(
        baseline_version=v1, target_version=v1,
        baseline_snapshot=s1, target_snapshot=s1
    ) == 'unchanged'


def test_materiality_threshold_boundaries_and_negatives():
    """Test exact threshold boundary, just below, just above, and negative values."""
    abs_thresh = "100.00"

    # Exact boundary: abs(delta) == 100.00 -> True
    m_exact = evaluate_materiality(
        delta_str="100.00", baseline_val_str="1000.00", target_val_str="1100.00",
        absolute_threshold=abs_thresh
    )
    assert m_exact['is_material'] is True
    assert m_exact['reason'] == 'exceeds_absolute_threshold'

    # Just below boundary: delta = 99.999 -> False
    m_below = evaluate_materiality(
        delta_str="99.999", baseline_val_str="1000.00", target_val_str="1099.999",
        absolute_threshold=abs_thresh
    )
    assert m_below['is_material'] is False
    assert m_below['reason'] == 'within_thresholds'

    # Just above boundary: delta = 100.001 -> True
    m_above = evaluate_materiality(
        delta_str="100.001", baseline_val_str="1000.00", target_val_str="1100.001",
        absolute_threshold=abs_thresh
    )
    assert m_above['is_material'] is True
    assert m_above['reason'] == 'exceeds_absolute_threshold'

    # Negative values: delta = -100.00 -> abs(delta) == 100.00 -> True
    m_neg = evaluate_materiality(
        delta_str="-100.00", baseline_val_str="1000.00", target_val_str="900.00",
        absolute_threshold=abs_thresh
    )
    assert m_neg['is_material'] is True
    assert m_neg['reason'] == 'exceeds_absolute_threshold'

    # Negative just below: delta = -99.99 -> False
    m_neg_below = evaluate_materiality(
        delta_str="-99.99", baseline_val_str="1000.00", target_val_str="900.01",
        absolute_threshold=abs_thresh
    )
    assert m_neg_below['is_material'] is False


def test_materiality_relative_threshold_and_denominator_edge_cases():
    """Test relative threshold, zero baseline, tiny denominator, and zero target."""
    rel_thresh = 0.05  # 5%

    # Relative exact boundary: 50 / 1000 = 0.05 -> True
    m_rel_exact = evaluate_materiality(
        delta_str="50.00", baseline_val_str="1000.00", target_val_str="1050.00",
        relative_threshold=rel_thresh
    )
    assert m_rel_exact['is_material'] is True
    assert m_rel_exact['reason'] == 'exceeds_relative_threshold'

    # Relative just below: 49.99 / 1000 = 0.04999 -> False
    m_rel_below = evaluate_materiality(
        delta_str="49.99", baseline_val_str="1000.00", target_val_str="1049.99",
        relative_threshold=rel_thresh
    )
    assert m_rel_below['is_material'] is False

    # Relative just above: 50.01 / 1000 = 0.05001 -> True
    m_rel_above = evaluate_materiality(
        delta_str="50.01", baseline_val_str="1000.00", target_val_str="1050.01",
        relative_threshold=rel_thresh
    )
    assert m_rel_above['is_material'] is True

    # Zero baseline with non-zero delta -> zero_denominator_non_zero_delta
    m_zero_base = evaluate_materiality(
        delta_str="25.00", baseline_val_str="0.00", target_val_str="25.00",
        relative_threshold=rel_thresh
    )
    assert m_zero_base['is_material'] is True
    assert m_zero_base['reason'] == 'zero_denominator_non_zero_delta'

    # Zero baseline with zero delta -> within_thresholds
    m_zero_both = evaluate_materiality(
        delta_str="0.00", baseline_val_str="0.00", target_val_str="0.00",
        relative_threshold=rel_thresh
    )
    assert m_zero_both['is_material'] is False

    # Zero target: baseline = 100, target = 0, delta = -100 -> ratio 1.0 -> True
    m_zero_target = evaluate_materiality(
        delta_str="-100.00", baseline_val_str="100.00", target_val_str="0.00",
        relative_threshold=rel_thresh
    )
    assert m_zero_target['is_material'] is True

    # Extremely small denominator: baseline = 0.00000001, delta = 0.00000001 -> ratio 1.0 -> True
    m_tiny = evaluate_materiality(
        delta_str="0.00000001", baseline_val_str="0.00000001", target_val_str="0.00000002",
        relative_threshold=rel_thresh
    )
    assert m_tiny['is_material'] is True
    assert Decimal(m_tiny['details']['relative_ratio']) == Decimal('1.0')


def test_materiality_undefined_and_intentional_variant_suppression():
    """Test NULL/undefined outcomes and intentional variant suppression."""
    # NULL values
    assert evaluate_materiality(None, "100", "120")['reason'] == 'undefined_endpoint'
    assert evaluate_materiality("20", None, "120")['reason'] == 'undefined_endpoint'
    assert evaluate_materiality("20", "100", None)['reason'] == 'undefined_endpoint'

    # Intentional variant with suppression active -> False
    m_suppressed = evaluate_materiality(
        delta_str="5000.00", baseline_val_str="1000.00", target_val_str="6000.00",
        absolute_threshold="100.00",
        is_intentional_variant=True,
        suppress_intentional_variants=True
    )
    assert m_suppressed['is_material'] is False
    assert m_suppressed['reason'] == 'suppressed_intentional_variant'

    # Intentional variant with suppression disabled -> evaluated normally -> True
    m_unsuppressed = evaluate_materiality(
        delta_str="5000.00", baseline_val_str="1000.00", target_val_str="6000.00",
        absolute_threshold="100.00",
        is_intentional_variant=True,
        suppress_intentional_variants=False
    )
    assert m_unsuppressed['is_material'] is True
    assert m_unsuppressed['reason'] == 'exceeds_absolute_threshold'

    # Deterministic event hash computation
    h1 = compute_event_hash("pol-1", "b-1", "t-1", "s-1", "100.00")
    h2 = compute_event_hash("pol-1", "b-1", "t-1", "s-1", "100.00")
    h3 = compute_event_hash("pol-1", "b-1", "t-1", "s-1", "100.01")
    assert h1 == h2
    assert h1 != h3
