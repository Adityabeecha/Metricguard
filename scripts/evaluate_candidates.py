"""Evaluate deterministic candidates against a frozen semantic corpus."""
from __future__ import annotations

import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path

from packages.metricguard_core import parse_definition
from services.api.candidates import rank_candidates


RELATED = {'expected_agreement', 'intentional_variant'}


def summarize_split(ids: list[str], truths: dict[frozenset[str], str], ranked: list[dict]) -> dict:
    candidates = [item for item in ranked if frozenset((item['left_definition_id'],
                  item['right_definition_id'])) in truths]
    labels = Counter(truths.values())
    known = [item for item in candidates if truths[frozenset((item['left_definition_id'],
             item['right_definition_id']))] != 'unknown']
    related_candidates = sum(truths[frozenset((item['left_definition_id'],
                             item['right_definition_id']))] in RELATED for item in known)
    related_total = sum(label in RELATED for label in truths.values())
    top1_known = []
    abstained = 0
    unknown_top1 = 0
    for ident in ids:
        options = [item for item in candidates if ident in (item['left_definition_id'],
                   item['right_definition_id'])]
        options.sort(key=lambda item: (-item['score'], item['left_definition_id'],
                                    item['right_definition_id']))
        if not options:
            abstained += 1
            continue
        label = truths[frozenset((options[0]['left_definition_id'], options[0]['right_definition_id']))]
        if label == 'unknown':
            unknown_top1 += 1
        else:
            top1_known.append(label in RELATED)
    false_positives = [dict(left=item['left_definition_id'], right=item['right_definition_id'],
                            score=item['score'], truth='unrelated') for item in known
                       if truths[frozenset((item['left_definition_id'], item['right_definition_id']))] == 'unrelated']
    return {'pair_count': len(truths), 'label_counts': dict(sorted(labels.items())),
            'suggested_pairs': len(candidates),
            'pair_precision': related_candidates / len(known) if known else 0.0,
            'pair_recall': related_candidates / related_total if related_total else 0.0,
            'top1_precision': sum(top1_known) / len(top1_known) if top1_known else 0.0,
            'top1_known_predictions': len(top1_known),
            'top1_unknown_predictions': unknown_top1,
            'top1_abstention_rate': abstained / len(ids) if ids else 0.0,
            'unknown_suggestions': len(candidates) - len(known),
            'false_positive_examples': false_positives[:10]}


def evaluate_corpus(path: Path) -> dict:
    corpus = json.loads(Path(path).read_text(encoding='utf-8'))
    definitions = corpus['definitions']
    ids = [item['id'] for item in definitions]
    if len(ids) != len(set(ids)):
        raise ValueError('Corpus definition IDs must be unique')
    splits = {key: [item for item in definitions if item['split'] == key]
              for key in ('development', 'holdout')}
    if sum(map(len, splits.values())) != len(definitions):
        raise ValueError('Corpus has an unknown split')
    concepts = {key: {item['concept'] for item in values} for key, values in splits.items()}
    if concepts['development'] & concepts['holdout']:
        raise ValueError('Concept families must be disjoint across splits')
    period = corpus['period']
    results = {}
    for split, items in splits.items():
        parsed = []
        for item in items:
            template = corpus['templates'][item['template']]
            time_column = template['time_column']
            sql = (f"SELECT {template['aggregate']} FROM orders WHERE {template['filter']} "
                   f"AND {time_column} >= DATE '{period['start']}' "
                   f"AND {time_column} < DATE '{period['end_exclusive']}'")
            ir = parse_definition(sql, time_column=time_column)
            parsed.append({'id': item['id'], 'name': item['name'],
                           'versions': [{'id': item['id'] + '-v1', 'support_state': 'executable', 'ir': ir}]})
        truths = {}
        for left, right in combinations(items, 2):
            label = ('unknown' if left.get('ambiguous') or right.get('ambiguous')
                     else 'unrelated' if left['concept'] != right['concept']
                     else 'expected_agreement' if left['variant'] == right['variant']
                     else 'intentional_variant')
            truths[frozenset((left['id'], right['id']))] = label
        report = summarize_split([item['id'] for item in items], truths, rank_candidates(parsed, []))
        report['concepts'] = sorted(concepts[split])
        results[split] = report
    return {'method': 'deterministic_heuristic_v1',
            'corpus': Path(path).name, 'pair_count': sum(report['pair_count'] for report in results.values()),
            'splits': results, 'limitations': [
                'Synthetic metadata and SQL only; labels are declared concept relationships, not observed production intent.',
                'Holdout concept families are disjoint from development; no ranking threshold was tuned on holdout.']}


def main() -> None:
    parser = argparse.ArgumentParser(description='Evaluate candidate ranking on a labeled synthetic corpus')
    parser.add_argument('--corpus', type=Path, default=Path('evaluation/candidate_corpus.json'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = evaluate_corpus(args.corpus)
    encoded = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
    print(encoded, end='')


if __name__ == '__main__':
    main()
