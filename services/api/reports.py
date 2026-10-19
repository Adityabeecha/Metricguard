"""Bounded, escaped evidence exports, derived only from stored results."""
from html import escape
import json


def _star_join_evidence_html(projection):
    """Verified projection for a fact table with several lookup tables."""
    e = lambda value: escape(str(value)) if value is not None else 'NULL'
    summary = projection['summary']
    c, proof, reconciliation = summary['contract'], summary['join_proof'], summary['reconciliation']
    outcomes = {'included_value': 'Included non-NULL measure', 'included_null_measure': 'Included NULL measure (ignored by SUM)',
                'excluded_join': 'Excluded by an INNER join', 'excluded_filter_false': 'Excluded: WHERE is FALSE',
                'excluded_filter_unknown': 'Excluded: WHERE is UNKNOWN'}
    count_rows = ''.join(f"<tr><td>{e(label)}</td><td>{e(summary['side_counts']['baseline'][key])}</td>"
                         f"<td>{e(summary['side_counts']['target'][key])}</td></tr>" for key, label in outcomes.items())
    lookup_rows = ''.join(
        f"<tr><td>{e(d['role'])} ({e(d['table'])})</td><td>{e(c['fact']['table'])}.{e(d['fact_foreign_key'])} → {e(d['table'])}.{e(d['key'])}</td>"
        f"<td>{e(proof['dimensions'][d['role']]['dimension_rows'])}</td><td>{e(proof['dimensions'][d['role']]['matched_fact_rows'])}</td>"
        f"<td>{e(proof['dimensions'][d['role']]['null_foreign_key_rows'])}</td><td>{e(proof['dimensions'][d['role']]['missing_nonnull_foreign_key_rows'])}</td></tr>"
        for d in c['dimensions'])
    definitions = ''.join(f"<h3>{e(side.title())} definition</h3><pre>{e(json.dumps(summary['definitions'][side], indent=2))}</pre>" for side in ('baseline', 'target'))
    bookkeeping = (f"<p>Full-artifact row contributions: baseline {e(reconciliation['baseline_bookkeeping_sum'])}, "
                   f"target {e(reconciliation['target_bookkeeping_sum'])}, difference {e(reconciliation['row_delta_sum'])}.</p>")
    return (f"<h2>Verified join evidence</h2><p>{e(summary['fact_rows'])} facts checked across the complete artifact. "
            f"{e(summary['changed_inclusion_rows'])} changed inclusion; {e(summary['nonzero_contribution_rows'])} have a nonzero row contribution.</p>"
            '<table><thead><tr><th>Lookup table</th><th>Join</th><th>Rows</th><th>Matched facts</th><th>NULL foreign key</th><th>Unmatched key</th></tr></thead><tbody>'
            + lookup_rows + '</tbody></table>'
            '<table><thead><tr><th>Fact outcome</th><th>Baseline</th><th>Target</th></tr></thead><tbody>' + count_rows + '</tbody></table>'
            + bookkeeping + definitions
            + f"<p>{e(summary['null_semantics'])}</p><pre>{e(json.dumps(summary['pins'], indent=2))}</pre>"
            + f"<p>Explanation policy: {e(summary['policy'])} · summary SHA-256: {e(summary['summary_hash'])}</p>")


def join_evidence_html(projection):
    """Render the versioned verified projection, including all-row coverage."""
    if projection['summary'].get('schema_version') == 2:
        return _star_join_evidence_html(projection)
    e = lambda value: escape(str(value)) if value is not None else 'NULL'
    summary = projection['summary']
    c, proof, reconciliation = summary['contract'], summary['join_proof'], summary['reconciliation']
    outcomes = {'included_value': 'Included non-NULL measure', 'included_null_measure': 'Included NULL measure (ignored by SUM)',
                'excluded_join': 'Excluded by INNER JOIN', 'excluded_filter_false': 'Excluded: WHERE is FALSE',
                'excluded_filter_unknown': 'Excluded: WHERE is UNKNOWN'}
    count_rows = ''.join(f"<tr><td>{e(label)}</td><td>{e(summary['side_counts']['baseline'][key])}</td>"
                         f"<td>{e(summary['side_counts']['target'][key])}</td></tr>" for key, label in outcomes.items())
    proof_labels = {'fact_rows': 'Fact rows', 'dimension_rows': 'Dimension rows', 'fact_key_unique': 'Fact key unique',
                    'dimension_key_unique': 'Dimension key unique', 'left_join_rows': 'Rows after LEFT JOIN',
                    'matched_fact_rows': 'Matched facts', 'null_foreign_key_rows': 'Facts with NULL foreign key',
                    'missing_nonnull_foreign_key_rows': 'Facts with unmatched non-NULL foreign key',
                    'max_dimension_matches_per_fact': 'Maximum dimension matches per fact', 'fanout_rows': 'Multiplied facts'}
    proof_rows = ''.join(f'<tr><td>{e(label)}</td><td>{e(proof[key])}</td></tr>' for key, label in proof_labels.items())
    definitions = ''.join(f"<h3>{e(side.title())} definition</h3><pre>{e(json.dumps(summary['definitions'][side], indent=2))}</pre>" for side in ('baseline', 'target'))
    reasons = []
    for row in projection['rows']:
        for side in ('baseline', 'target'):
            value = row[side]
            predicates = '; '.join(f"{p['field']} {p['op']} {json.dumps(p['values'])}; observed {json.dumps(p['observed'])}; {p['state']}"
                                   for p in value['predicates'])
            reasons.append(f"<tr><td>{e(row['fact_key'])}</td><td>{e(side)}</td><td>{e(value['join_kind'])}</td>"
                           f"<td>{e(outcomes[value['outcome']])}</td><td>{e(value['where_state'])}</td><td>{e(predicates)}</td></tr>")
    bookkeeping = (f"<p>Full-artifact row contributions: baseline {e(reconciliation['baseline_bookkeeping_sum'])}, "
                   f"target {e(reconciliation['target_bookkeeping_sum'])}, difference {e(reconciliation['row_delta_sum'])}.</p>")
    if reconciliation['status'] == 'reconciled':
        bookkeeping += f"<p>These contributions reconcile to target minus baseline {e(reconciliation['delta_target_minus_baseline'])}; residual {e(reconciliation['residual'])}.</p>"
    else:
        bookkeeping += '<p>At least one endpoint SUM is NULL. The metric gap and residual are not applicable; bookkeeping totals do not establish a zero endpoint.</p>'
    return (f"<h2>Verified join evidence</h2><p>{e(summary['fact_rows'])} facts checked across the complete artifact. "
            f"{e(summary['changed_inclusion_rows'])} changed inclusion; {e(summary['nonzero_contribution_rows'])} have a nonzero row contribution.</p>"
            f"<p>Declared fact grain: {e(c['fact']['grain'])}. Declared dimension grain: {e(c['dimension']['grain'])}. "
            f"Join path: {e(c['fact']['table'])}.{e(c['relationship']['fact_foreign_key'])} → {e(c['dimension']['table'])}.{e(c['dimension']['key'])}.</p>"
            '<p>Uniqueness and multiplicity are proven within this snapshot. Business grain, meaning and source authenticity remain declarations.</p>'
            '<table><thead><tr><th>Structural proof</th><th>Observed</th></tr></thead><tbody>' + proof_rows + '</tbody></table>'
            '<table><thead><tr><th>Fact outcome</th><th>Baseline</th><th>Target</th></tr></thead><tbody>' + count_rows + '</tbody></table>'
            + bookkeeping + definitions + '<h3>Supporting row explanations (exported page only)</h3>'
            '<table><thead><tr><th>Fact key</th><th>Side</th><th>Join</th><th>Outcome</th><th>WHERE state</th><th>Predicate observations</th></tr></thead><tbody>'
            + ''.join(reasons) + '</tbody></table>'
            + f"<p>{e(summary['null_semantics'])}</p><pre>{e(json.dumps(summary['pins'], indent=2))}</pre>"
            + f"<p>Explanation policy: {e(summary['policy'])} · summary SHA-256: {e(summary['summary_hash'])}</p>")


def html_report(bundle):
    result = bundle['result']
    e = lambda value: escape(str(value)) if value is not None else 'Undefined'
    flat = result.get('provenance', {}).get('execution_policy') == 'flat-snapshot-v1'
    relational = result.get('provenance', {}).get('execution_policy') == 'relational_snapshot_v1'
    unit = result.get('unit_label') if flat or relational else result.get('currency')
    bridge_title = 'Filter-change explanation' if flat else 'Ordered rule bridge'
    bridge_detail = ('One eligibility-filter change; this is not an operational root-cause claim.' if flat else
                     'Amount expression → eligibility → time attribution.')
    case_mode = result.get('provenance', {}).get('measure_policy') == 'flat-case-v1'
    if case_mode:
        bridge_title = 'Whole-definition contribution change'
        bridge_detail = 'Combined WHERE inclusion and CASE branch effects; no per-predicate causal attribution.'
    steps = ''.join(f"<tr><td>{e(step['rule'])}</td><td>{e(step['previous_value'])}</td><td>{e(step['contribution'])}</td><td>{e(step['next_value'])}</td></tr>" for step in result['steps'])
    ratio = result.get('ratio')
    exact_values = ''
    delta_label = 'Delta'
    if ratio:
        top_unit = ratio['numerator'].get('unit_label', 'records')
        bottom_unit = ratio['denominator'].get('unit_label', 'records')
        unit = 'share' if ratio.get('unit') == 'share' else f'{top_unit} / {bottom_unit}'
        delta_label = 'Scaled share (divide by shared divisor)'
        exact_values = (f"<p>Exact baseline: {e(ratio['baseline']['exact'])}; exact target: {e(ratio['target']['exact'])}; "
                        f"exact change: {e(ratio['gap_exact'])}. Display values are rounded, half to even, "
                        f"to {e(ratio['display_places'])} decimal places.</p>")
        bridge_title = 'Ratio change: numerator and denominator effects'
        effects = '; '.join(f"{item['rule'].replace('_', ' ')} {item['display']} (exact {item['exact']})" for item in ratio['effects'])
        bridge_detail = (f"Exact change {ratio['gap_exact']}. {effects}. Each record's share below is exact; divide it by "
                         f"{ratio['scale_divisor']} for its effect on the ratio." if ratio['status'] == 'defined' else
                         'The ratio is undefined on at least one side; only its parts are compared.')
        rows = ''.join(f"<tr><td>{e(row['order_id'])}</td><td>{e(row['membership'])}</td>"
                       f"<td>{e(row.get('baseline_numerator'))} / {e(row.get('baseline_denominator'))}</td>"
                       f"<td>{e(row.get('target_numerator'))} / {e(row.get('target_denominator'))}</td><td>{e(row['delta'])}</td></tr>"
                       for row in result['evidence'][:100000])
    else:
        rows = ''.join(f"<tr><td>{e(row['order_id'])}</td><td>{e(row['membership'])}</td><td>{e(row['baseline_contribution'])}</td><td>{e(row['target_contribution'])}</td><td>{e(row['delta'])}</td></tr>" for row in result['evidence'][:100000])
    if result.get('aggregate') == 'count_distinct':
        unit = 'distinct ' + result['distinct']['entity_column']
        bridge_title = 'Distinct-value membership change'
        bridge_detail = 'Each counted value adds 1 when at least one included record carries it; the value changes add up to the change.'
    case_details = ''
    join_details = ''
    if relational:
        bridge_title = 'Join and filter contribution change'
        bridge_detail = 'One whole-definition change at the fact grain; no per-predicate causal attribution.'
        def link_cells(row):
            if 'links' in row:  # several lookup tables
                return (f"<td>{e('; '.join(f'{r}: {l['foreign_key'] or 'NULL'}' for r, l in row['links'].items()))}</td>"
                        f"<td>{e('; '.join(f'{r}: {l['dimension_key'] or 'NULL'}' for r, l in row['links'].items()))}</td>"
                        f"<td>{e('; '.join(f'{r}: {l['membership']}' for r, l in row['links'].items()))}</td>")
            return (f"<td>{e(row['foreign_key'] if row['foreign_key'] is not None else 'NULL')}</td><td>{e(row['dimension_key'] if row['dimension_key'] is not None else 'NULL')}</td>"
                    f"<td>{e(row['join_membership'])}</td>")
        join_rows = ''.join(
            f"<tr><td>{e(row['fact_key'])}</td>{link_cells(row)}<td>{e(row['baseline_join_included'])}</td><td>{e(row['target_join_included'])}</td>"
            f"<td>{e(row['measure_value'] if row['measure_value'] is not None else 'NULL')}</td><td>{e(json.dumps({'fact': row['fact_values'], 'dimension': row['dimension_values']}))}</td></tr>"
            for row in result['evidence'][:100000])
        case_details = ('<h2>Join supporting records</h2><table><thead><tr><th>Fact key</th><th>Foreign key</th><th>Dimension key</th>'
                        '<th>Join membership</th><th>Baseline joined</th><th>Target joined</th><th>Measure</th><th>Categories</th>'
                        '</tr></thead><tbody>' + join_rows + '</tbody></table>')
        if bundle.get('join_evidence'):
            join_details = join_evidence_html(bundle['join_evidence'])
    if case_mode:
        case_rows = ''.join(
            f"<tr><td>{e(row['order_id'])}</td><td>{e(side)}</td>"
            f"<td>{e(row.get(side + '_included'))}</td>"
            f"<td>{e(row.get(side + '_where_state'))}</td>"
            f"<td>{e(row.get(side + '_when_state'))}</td>"
            f"<td>{e(row.get(side + '_branch'))}</td>"
            f"<td>{e(row.get('measure_value'))}</td>"
            f"<td>{e(row.get(side + '_case_term'))}</td>"
            f"<td>{e(row.get(side + '_null_term'))}</td></tr>"
            for row in result['evidence'][:100000] for side in ('baseline', 'target'))
        case_details = ('<h2>CASE row terms</h2><table><thead><tr><th>ID</th><th>Side</th>'
                        '<th>WHERE included</th><th>WHERE state</th><th>WHEN state</th><th>Branch</th><th>Measure</th>'
                        '<th>CASE term</th><th>Included NULL term</th></tr></thead><tbody>' + case_rows + '</tbody></table>')
    limits = ''.join(f'<li>{e(item)}</li>' for item in result['limitations'])
    provenance = e(json.dumps(bundle['reproducibility'], indent=2))
    coverage = bundle['evidence_coverage']
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>MetricGuard evidence report</title>
<style>body{{font:15px system-ui;color:#172b36;max-width:1100px;margin:48px auto;padding:0 24px}}h1{{font-size:32px}}table{{width:100%;border-collapse:collapse;margin:24px 0}}th,td{{padding:12px;border-bottom:1px solid #dde5e7;text-align:left;font-variant-numeric:tabular-nums}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7f7;padding:20px}}small{{color:#52636c}}</style>
<small>METRICGUARD / REPRODUCIBLE EVIDENCE</small><h1>{e(bundle['baseline']['name'])} → {e(bundle['target']['name'])}</h1>
<p>Run {e(bundle['id'])} · {e(bundle['execution_mode'])} · {e(bundle['state'])}</p>
<p>Baseline: {e(result['baseline_value'])} {e(unit)} · Target: {e(result['target_value'])} {e(unit)}</p>
<h2>Target minus baseline: {e(result['delta_target_minus_baseline'])} {e(unit)}{' (display difference, rounded)' if ratio else ''}</h2>{exact_values}
<p>Definition: {e(result['definition_status'])}. Values: {e(result['value_status'])}. Explanation: {e(result['explanation_status'])}.</p>
<h2>{bridge_title}</h2><p>{bridge_detail} Residual: {e(result['residual'])}.</p>
<table><thead><tr><th>Rule</th><th>Before</th><th>Contribution</th><th>After</th></tr></thead><tbody>{steps}</tbody></table>
<h2>Evidence ({e(result['provenance'].get('evidence_unit', 'order_id'))})</h2>
<p>Included {e(coverage['included_rows'])} of {e(coverage['total_rows'])} rows; omitted {e(coverage['omitted_rows'])}. Use paginated evidence for all rows.</p>
<table><thead><tr><th>ID</th><th>Membership</th><th>Baseline</th><th>Target</th><th>{delta_label}</th></tr></thead><tbody>{rows}</tbody></table>
{case_details}{join_details}<h2>Limitations</h2><ul>{limits}</ul><h2>Reproducibility</h2><pre>{provenance}</pre></html>'''
