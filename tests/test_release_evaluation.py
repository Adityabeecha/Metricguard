import json
from pathlib import Path

from scripts.evaluate_release import evaluate_release


def test_release_evaluation_checks_independent_oracle_and_bounds_report(tmp_path):
    report = evaluate_release(tmp_path, rows=240, seed=19)
    assert report['status'] == 'passed'
    assert report['rows'] == 240
    assert report['oracle_match'] == {'dashboard_a': True, 'dashboard_b': True, 'finance': True}
    assert report['comparisons']['a_to_finance']['delta'] == report['comparisons']['a_to_finance']['expected_delta']
    assert report['comparisons']['a_to_b']['delta'] == report['comparisons']['a_to_b']['expected_delta']
    assert report['timings_seconds']['ingestion'] >= 0
    assert report['timings_seconds']['first_comparison'] >= 0
    assert report['timings_seconds']['repeat_comparison'] >= 0
    assert report['repeat_equal'] is True
    assert report['environment']['execution_mode'] == 'in_process_benchmark'
    saved = json.loads((tmp_path / 'release_report.json').read_text())
    assert saved == report
    encoded = json.dumps(saved)
    assert str(tmp_path) not in encoded
    assert 'customer_id' not in encoded
    assert len(encoded) < 10000


def test_release_evaluation_records_failure_without_claiming_success(tmp_path, monkeypatch):
    from scripts import evaluate_release as module

    def fail(*_args, **_kwargs):
        raise RuntimeError('simulated engine failure')

    monkeypatch.setattr(module, 'compare', fail)
    report = module.evaluate_release(tmp_path, rows=20, seed=11)
    assert report['status'] == 'failed'
    assert report['error']['type'] == 'RuntimeError'
    assert report['error']['message'] == 'simulated engine failure'
    assert report.get('oracle_match') is None
    assert json.loads((tmp_path / 'release_report.json').read_text()) == report
