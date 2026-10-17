"""A realistic row count must remain pageable from a bounded evidence artifact."""

from services.supervisor.evidence_artifact import verify_artifact, write_artifact


def test_empty_entity_key_is_valid_count_distinct_evidence(tmp_path):
    artifact = tmp_path / 'empty-key.jsonl'
    row = {
        'order_id': '', 'baseline_included': False, 'target_included': True,
        'baseline_contribution': '0', 'target_contribution': '1', 'delta': '1',
        'baseline_null_term': False, 'target_null_term': False,
        'membership': 'target_only',
    }
    manifest = write_artifact(artifact, [row])
    assert verify_artifact(artifact, manifest, '1', offset=0, limit=1) == [row]


def test_one_hundred_thousand_evidence_rows_are_paged_and_reconciled(tmp_path):
    artifact = tmp_path / 'large-evidence.jsonl'

    def rows():
        for index in range(100_000):
            yield {
                'order_id': f'order-{index:06d}',
                'baseline_included': True,
                'target_included': True,
                'baseline_contribution': '0.00',
                'target_contribution': '1.00',
                'delta': '1.00',
                'baseline_null_term': False,
                'target_null_term': False,
                'membership': 'included_in_both',
            }

    manifest = write_artifact(artifact, rows())
    assert manifest['row_count'] == 100_000
    assert manifest['delta_sum'] == '100000.00'
    assert manifest['byte_count'] == artifact.stat().st_size
    page = verify_artifact(artifact, manifest, '100000.00', offset=99_950, limit=100)
    assert len(page) == 50
    assert page[0]['order_id'] == 'order-099950'
    assert page[-1]['order_id'] == 'order-099999'
