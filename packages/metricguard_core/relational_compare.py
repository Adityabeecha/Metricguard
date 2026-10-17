"""Exact row-grain comparison for the bounded relational policy."""

import time

import duckdb
import sqlglot

from .common import digest
from .numeric import ScaledInteger
from .relational import (POLICY, EXECUTION_POLICY, context_for, validate_ir, quote,
                         compile_predicates, fail)
from .relational_snapshot import open_snapshot, verify_database, join_sql


def compare_relational(database, baseline, target, context):
    c, manifest = open_snapshot(database)
    baseline, target = validate_ir(baseline, c), validate_ir(target, c)
    if context != context_for(c):
        fail('not_comparable', 'Use the exact frozen whole-snapshot scope and unit context.')
    started = time.monotonic()
    fk = 'f.' + quote(c['relationship']['fact_foreign_key'])
    dk = 'd.' + quote(c['dimension']['key'])
    bp, bparams = compile_predicates(baseline, c)
    tp, tparams = compile_predicates(target, c)
    bj, tj = ('TRUE' if ir['join'] == 'left' else dk + ' IS NOT NULL' for ir in (baseline, target))
    fields = ['f.' + quote(c['fact']['key']), fk, dk, 'f.' + quote(c['measure']['column']),
              f'(({bj}) AND {bp}) IS TRUE', f'(({tj}) AND {tp}) IS TRUE',
              *('f.' + quote(n) for n in c['fact']['categories']),
              *('d.' + quote(n) for n in c['dimension']['categories'])]
    sql = 'SELECT ' + ', '.join(fields) + ' ' + join_sql(c) + ' ORDER BY f.' + quote(c['fact']['key'])
    evidence, sums, terms = [], [ScaledInteger.zero(c['measure']['scale']) for _ in range(2)], [0, 0]
    with duckdb.connect(str(database), read_only=True, config={'threads': '1', 'memory_limit': '256MB',
                        'enable_external_access': 'false', 'autoinstall_known_extensions': 'false', 'autoload_known_extensions': 'false'}) as con:
        con.execute('SET lock_configuration = true')
        verify_database(con, c, manifest)
        cursor = con.execute(sql, [*bparams, *tparams])
        while batch := cursor.fetchmany(1000):
            for raw in batch:
                key, foreign_key, dimension_key, measure, bi, ti = raw[:6]
                matched = dimension_key is not None
                measure_str = None if measure is None else format(measure, 'f')
                n = ScaledInteger.zero(c['measure']['scale']) if measure is None else ScaledInteger.parse(measure_str)
                bc, tc = (n if flag and measure is not None else ScaledInteger.zero(c['measure']['scale']) for flag in (bi, ti))
                for i, flag in enumerate((bi, ti)):
                    if flag and measure is not None:
                        sums[i] += n; terms[i] += 1
                split = 6 + len(c['fact']['categories'])
                evidence.append({'order_id': key, 'fact_key': key, 'foreign_key': foreign_key,
                                 'dimension_key': dimension_key, 'dimension_matched': matched,
                                 'join_membership': 'matched' if matched else 'null_foreign_key' if foreign_key is None else 'missing_dimension',
                                 'baseline_join_included': baseline['join'] == 'left' or matched,
                                 'target_join_included': target['join'] == 'left' or matched,
                                 'fact_values': dict(zip(c['fact']['categories'], raw[6:split])),
                                 'dimension_values': dict(zip(c['dimension']['categories'], raw[split:])),
                                 'measure_value': measure_str, 'baseline_included': bi, 'target_included': ti,
                                 'baseline_null_term': bi and measure is None, 'target_null_term': ti and measure is None,
                                 'baseline_contribution': bc.to_fixed_string(), 'target_contribution': tc.to_fixed_string(),
                                 'delta': (tc - bc).to_fixed_string(),
                                 'membership': 'included_in_both' if bi and ti else 'baseline_only' if bi else 'target_only' if ti else 'included_in_neither'})
    for total in sums:
        if abs(total.coeff) >= 10 ** 38:
            fail('arithmetic_overflow', 'Endpoint SUM exceeds the declared DECIMAL(38, scale) capacity.')
    av, bv = (total if count else None for total, count in zip(sums, terms))
    gap = bv - av if av is not None and bv is not None else None
    number = lambda n: None if n is None else n.to_fixed_string()
    step = {'rule': 'join_and_filter_change', 'position': 1,
            'baseline': {'join': baseline['join'], 'predicates': baseline['predicates']},
            'target': {'join': target['join'], 'predicates': target['predicates']}, 'changed': baseline != target,
            'previous_value': number(av), 'next_value': number(bv), 'value_before': number(av), 'value_after': number(bv),
            'contribution': number(gap)}
    return {'baseline_value': number(av), 'target_value': number(bv), 'delta_target_minus_baseline': number(gap),
            'absolute_gap': number(abs(gap)) if gap is not None else None, 'residual': number(ScaledInteger.zero(c['measure']['scale'])) if gap is not None else None,
            'relative_change_percent_display': None, 'currency': None, 'unit_label': c['unit_label'], 'measure_meaning': c['measure_meaning'],
            'numeric_tolerance': '0', 'method': 'relational_join_filter_change',
            'definition_status': 'same_supported_representation' if baseline == target else 'different',
            'value_status': 'defined' if gap is not None else 'undefined',
            'explanation_status': 'complete_join_filter_change' if gap is not None else 'unavailable',
            'steps': [step] if gap is not None else [], 'definition_differences': [step], 'shapley': None, 'evidence': evidence,
            'join_proof': manifest['join_proof'],
            'provenance': {'execution_policy': EXECUTION_POLICY, 'engine_version': POLICY, 'contract_version': 1,
                           'contract_hash': c['contract_hash'], 'baseline_ir_hash': baseline['ir_hash'], 'target_ir_hash': target['ir_hash'],
                           'snapshot_manifest_hash': manifest['manifest_hash'], 'database_hash': manifest['database_hash'],
                           'source_hash': manifest['source_hash'], 'source_hashes': manifest['source_hashes'], 'context': context,
                           'query_hash': digest({'sql': sql, 'params': [*bparams, *tparams]}), 'query_count': 1,
                           'parser_version': sqlglot.__version__, 'duckdb_version': duckdb.__version__,
                           'elapsed_ms': round((time.monotonic() - started) * 1000), 'evidence_unit': 'fact_key'},
            'limitations': ['The uniqueness proof establishes join multiplicity within this snapshot; business grain and source authenticity are not independently established.',
                            'Missing dimension records are reported; they do not establish a zero measure or a valid foreign-key match.',
                            'One whole-definition bridge explains the join/filter change; it is not causal attribution across individual predicates.',
                            'Units are declared by the user. Unknown currency stays unknown; no verified financial loss is claimed.'] +
                           (['An empty or all-NULL endpoint SUM is NULL; gap and residual are not applicable.'] if gap is None else [])}
