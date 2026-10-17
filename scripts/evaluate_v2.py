"""Rehearse mapped investigations on two synthetic source layouts.

These generated cases are development fixtures, not analyst-pilot evidence.
"""

import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path
import tempfile

from packages.metricguard_core import compare, parse_definition
from packages.metricguard_core.common import SCHEMA
from packages.metricguard_core.v2_binding import bind_sql
from packages.metricguard_core.v2_contract import ingest_mapped, validate_contract, validate_mapping


ROOT = Path(__file__).resolve().parents[1]
CASES = (
    {'name': 'Synthetic returns', 'source_columns': (
        'receipt', 'buyer', 'sold_on', 'paid_on', 'state', 'pay_state',
        'gross', 'returned', 'ccy'),
     'rows': (
        ('a1', 'b1', '2026-08-02', '2026-08-03', 'completed', 'paid', '120.00', '0.00', 'INR'),
        ('a2', 'b2', '2026-08-04', '', 'completed', 'unpaid', '80.00', '0.00', 'INR'),
        ('a3', 'b3', '2026-08-05', '2026-08-06', 'canceled', 'paid', '30.00', '0.00', 'INR')),
     'baseline': '200.00', 'target': '150.00', 'delta': '-50.00'},
    {'name': 'Synthetic collections', 'source_columns': (
        'txn_key', 'party', 'event_day', 'settlement_day', 'lifecycle',
        'settlement', 'amount_inr', 'rebate', 'currency_code'),
     'rows': (
        ('c1', 'p1', '2026-08-10', '2026-08-11', 'completed', 'paid', '100.00', '0.00', 'INR'),
        ('c2', 'p2', '2026-08-12', '2026-08-13', 'completed', 'paid', '70.00', '0.00', 'INR'),
        ('c3', 'p3', '2026-08-14', '2026-08-15', 'canceled', 'paid', '20.00', '0.00', 'INR')),
     'baseline': '170.00', 'target': '190.00', 'delta': '20.00'},
)


def evaluate_v2(workdir: Path) -> dict:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, case in enumerate(CASES, 1):
        source = workdir / f'case-{index}.csv'
        with source.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(case['source_columns'])
            writer.writerows(case['rows'])
        contract = validate_contract({
            'version': 2, 'name': case['name'], 'currency': 'INR',
            'timezone': 'Asia/Kolkata', 'scope': f'synthetic_v2_case_{index}',
            'grain': 'one_row_per_sale', 'cutoff_at': '2026-09-02',
            'null_policy': 'blank_nullable', 'row_key': 'order_id',
            'entity_key': 'customer_id', 'decimal_scale': 2, 'schema': SCHEMA})
        mapping = validate_mapping({'columns': dict(zip(SCHEMA, case['source_columns']))}, contract)
        database = workdir / f'case-{index}.duckdb'
        manifest = ingest_mapped(source, database, contract, mapping)
        state_name, payment_name = mapping['columns']['status'], mapping['columns']['payment_status']
        amount_name = mapping['columns']['order_amount']
        source_sql = (
            f'SELECT SUM("{amount_name}") FROM orders WHERE "{state_name}" = \'completed\'',
            f'SELECT SUM("{amount_name}") FROM orders WHERE "{payment_name}" = \'paid\'')
        baseline, target = (parse_definition(bind_sql(sql, mapping), 'order_date') for sql in source_sql)
        context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01',
                   'timezone': 'Asia/Kolkata', 'currency': 'INR', 'unit': 'currency',
                   'grain': contract['grain'], 'scope': contract['scope'],
                   'contract_hash': contract['contract_hash'],
                   'mapping_hash': mapping['mapping_hash']}
        actual = compare(database, baseline, target, context)
        independent_baseline = sum((Decimal(row[6]) for row in case['rows'] if row[4] == 'completed'), Decimal(0))
        independent_target = sum((Decimal(row[6]) for row in case['rows'] if row[5] == 'paid'), Decimal(0))
        assert format(independent_baseline, '.2f') == case['baseline']
        assert format(independent_target, '.2f') == case['target']
        assert format(independent_target - independent_baseline, '.2f') == case['delta']
        assert actual['baseline_value'] == case['baseline']
        assert actual['target_value'] == case['target']
        assert actual['delta_target_minus_baseline'] == case['delta']
        assert actual['residual'] == '0.00'
        results.append({'name': case['name'], 'rows': manifest['row_count'],
                        'source_hash': manifest['source_hash'],
                        'contract_hash': manifest['contract_hash'],
                        'mapping_hash': manifest['mapping_hash'],
                        'baseline': actual['baseline_value'], 'target': actual['target_value'],
                        'delta': actual['delta_target_minus_baseline'], 'residual': actual['residual']})
    return {'status': 'passed', 'evidence_kind': 'synthetic_development_rehearsal',
            'cases': results, 'pilot_acceptance': 'not_evaluated'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    (ROOT / '.test-tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='v2-evaluation-', dir=ROOT / '.test-tmp') as folder:
        report = evaluate_v2(Path(folder))
    encoded = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
    print(encoded, end='')


if __name__ == '__main__':
    main()
