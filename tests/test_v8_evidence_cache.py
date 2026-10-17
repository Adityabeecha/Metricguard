"""Verified-evidence cache: fast pages without weakening integrity."""
import json
import os

import pytest

from services.api import evidence_cache


def _write(path, deltas):
    rows = [{'row_key': f'R{i:03d}', 'delta': d, 'baseline_contribution': '1.00', 'target_contribution': '1.00',
             'baseline_included': True, 'target_included': True, 'baseline_null_term': i == 2, 'target_null_term': False}
            for i, d in enumerate(deltas)]
    path.write_bytes(b''.join((json.dumps(r, separators=(',', ':')) + '\n').encode() for r in rows))
    return rows


def test_full_verification_runs_once_then_pages_are_direct(tmp_path):
    evidence_cache.clear()
    path = tmp_path / 'run-1.jsonl'
    _write(path, ['0.00', '-200.00', '0.00', '-50.00', '-90.00'])
    calls = []
    key = (str(path), 'run', 1, 'sha')
    rows, total = evidence_cache.page(path, key, lambda: calls.append(1), 0, 2)
    assert total == 5 and [r['row_key'] for r in rows] == ['R000', 'R001']
    rows, _ = evidence_cache.page(path, key, lambda: calls.append(1), 3, 2)
    assert [r['row_key'] for r in rows] == ['R003', 'R004']
    assert calls == [1]  # second page did not re-verify the whole file

    summary = evidence_cache.summary(path, key, lambda: calls.append(1))
    assert summary['changed'] == 3 and summary['blank_amount_rows'] == 1
    assert [r['row_key'] for r in summary['top_changes']] == ['R001', 'R004', 'R003']

    changed, count = evidence_cache.page(path, key, lambda: None, 0, 10, changed_only=True)
    assert count == 3 and [r['row_key'] for r in changed] == ['R001', 'R003', 'R004']
    ordered, _ = evidence_cache.page(path, key, lambda: None, 0, 10, order='impact')
    assert [r['row_key'] for r in ordered][:3] == ['R001', 'R004', 'R003']
    found, count = evidence_cache.page(path, key, lambda: None, 0, 10, query='r00 4'.replace(' ', ''))
    assert count == 1 and found[0]['row_key'] == 'R004'


def test_tamper_with_same_size_and_mtime_is_refused(tmp_path):
    evidence_cache.clear()
    path = tmp_path / 'run-2.jsonl'
    _write(path, ['0.00', '-200.00', '0.00'])
    key = (str(path), 'run', 2, 'sha')
    evidence_cache.page(path, key, lambda: None, 0, 1)
    stat = path.stat()
    data = path.read_bytes().replace(b'-200.00', b'-900.00')
    path.write_bytes(data)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    def full_check():  # stands in for verify_artifact, which rejects the altered bytes
        if b'-900.00' in path.read_bytes():
            raise ValueError('EVIDENCE_INVALID')

    # Either the per-line hash (stat unchanged) or full re-verification (stat changed) refuses it.
    with pytest.raises(ValueError):
        evidence_cache.page(path, key, full_check, 1, 1)
    with pytest.raises(ValueError):
        list(evidence_cache.iter_rows(path, key, full_check))


def test_changed_file_forces_full_reverification(tmp_path):
    evidence_cache.clear()
    path = tmp_path / 'run-3.jsonl'
    _write(path, ['0.00', '-1.00'])
    key = (str(path), 'run', 3, 'sha')
    evidence_cache.page(path, key, lambda: None, 0, 1)
    with path.open('ab') as out:
        out.write(b'{"row_key":"X","delta":"5.00"}\n')

    def refuse():
        raise ValueError('EVIDENCE_INVALID')

    with pytest.raises(ValueError):
        evidence_cache.page(path, key, refuse, 0, 1)
