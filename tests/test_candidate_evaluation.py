from pathlib import Path

from scripts.evaluate_candidates import evaluate_corpus, summarize_split


CORPUS = Path(__file__).resolve().parents[1] / 'evaluation/candidate_corpus.json'


def test_candidate_corpus_has_100_labeled_pairs_and_disjoint_concepts():
    result = evaluate_corpus(CORPUS)
    assert result['pair_count'] >= 100
    assert result['splits']['development']['pair_count'] == 66
    assert result['splits']['holdout']['pair_count'] == 66
    assert set(result['splits']['development']['concepts']).isdisjoint(
        result['splits']['holdout']['concepts'])
    assert result['splits']['holdout']['label_counts']['intentional_variant'] > 0
    assert result['splits']['holdout']['label_counts']['unrelated'] > 0
    assert result['splits']['holdout']['label_counts']['unknown'] > 0
    assert 0 <= result['splits']['holdout']['top1_precision'] <= 1


def test_top_ranked_precision_and_recall_do_not_count_unknown_as_negative():
    truths = {frozenset(('a', 'b')): 'expected_agreement',
              frozenset(('a', 'c')): 'unrelated',
              frozenset(('b', 'c')): 'unknown'}
    ranked = [{'left_definition_id': 'a', 'right_definition_id': 'c', 'score': 12},
              {'left_definition_id': 'a', 'right_definition_id': 'b', 'score': 10}]
    result = summarize_split(['a', 'b', 'c'], truths, ranked)
    assert result['pair_precision'] == 0.5
    assert result['pair_recall'] == 1.0
    assert result['top1_precision'] == 1 / 3
    assert result['top1_abstention_rate'] == 0.0
    assert result['unknown_suggestions'] == 0
