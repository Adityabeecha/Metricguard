from scripts.evaluate_v2 import evaluate_v2


def test_two_synthetic_layouts_match_independent_totals(tmp_path):
    report = evaluate_v2(tmp_path)
    assert report['status'] == 'passed'
    assert report['evidence_kind'] == 'synthetic_development_rehearsal'
    assert [case['delta'] for case in report['cases']] == ['-50.00', '20.00']
    assert all(case['residual'] == '0.00' for case in report['cases'])
    assert report['cases'][0]['mapping_hash'] != report['cases'][1]['mapping_hash']
