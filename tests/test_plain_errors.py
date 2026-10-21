import pytest

from packages.metricguard_core.common import CoreError
from services.api.store import DomainError


@pytest.mark.parametrize('error_class', [CoreError, DomainError])
@pytest.mark.parametrize('message', ['Flat IR hash differs', 'Ratio IR requires FlatContract v5 or v8',
    'Both definitions and the snapshot must use flat-snapshot policy v1.',
    'CASE definitions must match the frozen v7 contract and execution policy.'])
def test_internal_validation_words_are_actionable(error_class, message):
    error = error_class('INVALID_IR', message)
    assert str(error) == error.message
    for word in (' IR', 'contract v', 'FlatContract', 'execution policy', 'flat-snapshot policy'):
        assert word.lower() not in error.message.lower()
    assert any(word in error.message.lower() for word in ('add', 'choose', 'try', 'again', 'enable'))


def test_legacy_ratio_refusal_points_to_upgrade_action():
    from packages.metricguard_core.flat_ratio import parse_ratio
    with pytest.raises(CoreError) as caught:
        parse_ratio(None, None, {'version': 4}, None, None)
    assert "Enable averages and ratios" in caught.value.message
    assert 'contract v5' not in caught.value.message
