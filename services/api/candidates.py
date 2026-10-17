"""Conservative, reproducible suggestions from latest supported definitions."""
from itertools import combinations
import re


def columns(node):
    if isinstance(node, dict):
        found = {node['name']} if node.get('op') == 'column' else set()
        for value in node.values():
            found |= columns(value)
        return found
    if isinstance(node, list):
        return set().union(*(columns(value) for value in node)) if node else set()
    return set()


def rank_candidates(definitions, reviews):
    current = [(definition, definition['versions'][-1]) for definition in definitions
               if definition['versions'] and definition['versions'][-1]['support_state'] == 'executable'
               and definition['versions'][-1].get('execution_policy') not in ('flat_snapshot_v1', 'relational_snapshot_v1')]
    review_index = {}
    for review in sorted(reviews, key=lambda item: (item['created_at'], item['id'])):
        review_index[frozenset((review['left_definition_id'], review['right_definition_id']))] = review
    items = []
    for (left, a), (right, b) in combinations(current, 2):
        ai, bi = a['ir'], b['ir']
        if ai['aggregate'] != bi['aggregate'] or ai['table'] != bi['table']:
            continue
        score, reasons, cautions = 6, ['Same aggregate family and source table'], []
        if ai['time_column'] == bi['time_column']:
            score += 2
            reasons.append('Same business date column')
        else:
            cautions.append('Different business date columns')
        if ai['expression'] == bi['expression']:
            score += 2
            reasons.append('Same amount expression')
        else:
            cautions.append('Different amount expressions')
        common = sorted(columns(ai['predicate']) & columns(bi['predicate']))
        if common:
            score += 1
            reasons.append('Shared filter columns: ' + ', '.join(common))
        if ai['predicate'] != bi['predicate']:
            cautions.append('Different eligibility filters')
        names = [set(re.findall(r'[a-z0-9]+', d['name'].lower())) - {'dashboard', 'metric', 'kpi'} for d in (left, right)]
        if names[0] & names[1]:
            score += 1
            reasons.append('Shared name terms')
        variants = {'gross', 'net', 'booked', 'recognized'}
        if (names[0] ^ names[1]) & variants:
            cautions.append('Name qualifiers may indicate different metric meanings')
        review = review_index.get(frozenset((left['id'], right['id'])))
        items.append({'left_definition_id': left['id'], 'right_definition_id': right['id'],
            'left_version_id': a['id'], 'right_version_id': b['id'], 'score': score,
            'reasons': reasons, 'cautions': cautions,
            'review_status': review['relation'] if review else 'unreviewed',
            'review_id': review['id'] if review else None})
    return sorted(items, key=lambda item: (-item['score'], item['left_definition_id'], item['right_definition_id']))
