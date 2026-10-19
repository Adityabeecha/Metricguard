"""The workspace payload carries run summaries only; full results stay on /comparisons/{id}."""
from services.api.app import RUN_SUMMARY_FIELDS, run_summary


def test_run_summary_keeps_list_fields_and_drops_heavy_ones():
    result = {field: f'value-{field}' for field in RUN_SUMMARY_FIELDS}
    result.update(provenance={'big': 'x' * 1000}, steps=[{}], shapley={}, limitations=['a'],
                  definition_differences=[{}], evidence_artifact={'sha256': 'f' * 64}, evidence=[],
                  ratio={'unit': 'share', 'numerator': {'aggregate': 'count'}, 'denominator': {'aggregate': 'count'},
                         'status': 'defined', 'display_places': 6, 'gap_exact': '1/8', 'corners': {'n0d0': '1'},
                         'effects': [{'rule': 'numerator_effect'}],
                         'baseline': {'undefined_reason': None, 'exact': '1/2', 'numerator': '1'},
                         'target': {'undefined_reason': None, 'exact': '5/8', 'numerator': '5'}},
                  distinct={'entity_column': 'customer', 'gained': 2, 'lost': 1, 'kept': 9, 'policy': 'p'})
    summary = run_summary(result)
    assert {key: summary[key] for key in RUN_SUMMARY_FIELDS} == {key: result[key] for key in RUN_SUMMARY_FIELDS}
    for heavy in ('provenance', 'steps', 'shapley', 'limitations', 'definition_differences', 'evidence_artifact', 'evidence'):
        assert heavy not in summary
    # What the lists use to choose a unit (resultUnit) and to show a ratio's change.
    assert summary['ratio']['unit'] == 'share' and summary['ratio']['gap_exact'] == '1/8'
    assert summary['ratio']['baseline'] == {'undefined_reason': None, 'exact': '1/2'}
    assert 'corners' not in summary['ratio'] and 'effects' not in summary['ratio']
    assert summary['distinct'] == {'entity_column': 'customer', 'gained': 2, 'lost': 1, 'kept': 9}
    assert run_summary(None) is None
