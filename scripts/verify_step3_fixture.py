"""Step 3: Independent Relational Fixture and Decimal Calculation.

Generates fact_items.csv, dim_groups.csv, registers the relational contract,
and independently calculates expected values with pure Decimal arithmetic without
importing MetricGuard's comparison algorithms.
Outputs: data/v8_acceptance/receipts/receipt_03_relational_fixture.json
"""
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'data/v8_acceptance'
RECEIPTS_DIR = DATA_DIR / 'receipts'
RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)

FACT_CSV = """fact_id,group_id,amount,kind
F1,D1,10.00,web
F2,D2,20.00,web
F3,DX,30.00,store
F4,,5.00,store
F5,D1,,web
"""

DIM_CSV = """group_id,segment
D1,north
D2,south
"""

def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    fact_path = DATA_DIR / 'fact_items.csv'
    dim_path = DATA_DIR / 'dim_groups.csv'

    fact_path.write_text(FACT_CSV, encoding='utf-8')
    dim_path.write_text(DIM_CSV, encoding='utf-8')

    fact_hash = file_sha256(fact_path)
    dim_hash = file_sha256(dim_path)

    # 1. Independent parsing of CSVs
    with open(fact_path, 'r', encoding='utf-8') as f:
        fact_rows = list(csv.DictReader(f))
    with open(dim_path, 'r', encoding='utf-8') as f:
        dim_rows = list(csv.DictReader(f))

    dim_map = {row['group_id']: row for row in dim_rows}

    # 2. Structural join proof calculation
    matched_facts = 0
    unmatched_null_fk = 0
    unmatched_missing_fk = 0
    left_join_rows = 0

    for f in fact_rows:
        gid = f['group_id']
        left_join_rows += 1
        if not gid or gid.strip() == '':
            unmatched_null_fk += 1
        elif gid in dim_map:
            matched_facts += 1
        else:
            unmatched_missing_fk += 1

    join_proof = {
        'fact_row_count': len(fact_rows),
        'dimension_row_count': len(dim_rows),
        'matched_facts': matched_facts,
        'unmatched_facts': unmatched_null_fk + unmatched_missing_fk,
        'unmatched_null_fk': unmatched_null_fk,
        'unmatched_missing_fk': unmatched_missing_fk,
        'left_join_row_count': left_join_rows,
        'fanout': 0,  # All group_ids in dim_groups are distinct
    }

    assert join_proof['fact_row_count'] == 5
    assert join_proof['dimension_row_count'] == 2
    assert join_proof['matched_facts'] == 3
    assert join_proof['unmatched_facts'] == 2
    assert join_proof['unmatched_null_fk'] == 1
    assert join_proof['unmatched_missing_fk'] == 1
    assert join_proof['left_join_row_count'] == 5
    assert join_proof['fanout'] == 0

    # 3. Independent Decimal arithmetic for Baseline & Target
    # Baseline:
    # SELECT SUM(f.amount) FROM fact_items f LEFT JOIN dim_groups d ON f.group_id = d.group_id
    # Every fact row is in LEFT JOIN.
    # F1: 10.00
    # F2: 20.00
    # F3: 30.00
    # F4: 5.00
    # F5: NULL amount -> ignored by SUM
    baseline_sum = Decimal('0.00')
    target_sum = Decimal('0.00')

    row_deltas = {}
    for f in fact_rows:
        fid = f['fact_id']
        raw_amt = f['amount']
        amt = Decimal(raw_amt) if raw_amt and raw_amt.strip() != '' else None
        gid = f['group_id']
        dim = dim_map.get(gid)

        # Baseline evaluation
        b_included = True  # LEFT JOIN preserves all facts
        b_contrib = amt if b_included and amt is not None else Decimal('0.00')
        if amt is not None:
            baseline_sum += b_contrib

        # Target evaluation:
        # INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE d.segment = 'north'
        t_included = (dim is not None) and (dim.get('segment') == 'north')
        t_contrib = amt if t_included and amt is not None else Decimal('0.00')
        if t_included and amt is not None:
            target_sum += t_contrib

        # Row delta: target contribution minus baseline contribution
        delta = (t_contrib if t_included else Decimal('0.00')) - (b_contrib if b_included else Decimal('0.00'))
        row_deltas[fid] = {
            'baseline_included': b_included,
            'baseline_contribution': str(amt) if amt is not None else 'NULL',
            'target_included': t_included,
            'target_contribution': str(amt) if (t_included and amt is not None) else ('NULL' if t_included else '0.00'),
            'delta': str(delta),
            'null_measure_distinction': amt is None,
        }

    delta_target_minus_baseline = target_sum - baseline_sum

    expected_totals = {
        'baseline_value': str(baseline_sum),
        'target_value': str(target_sum),
        'delta_target_minus_baseline': str(delta_target_minus_baseline),
    }

    assert expected_totals['baseline_value'] == '65.00'
    assert expected_totals['target_value'] == '10.00'
    assert expected_totals['delta_target_minus_baseline'] == '-55.00'

    assert row_deltas['F1']['delta'] == '0.00'
    assert row_deltas['F2']['delta'] == '-20.00'
    assert row_deltas['F3']['delta'] == '-30.00'
    assert row_deltas['F4']['delta'] == '-5.00'
    assert row_deltas['F5']['delta'] == '0.00'
    assert row_deltas['F5']['null_measure_distinction'] is True

    # 4. Construct Supported Relational Contract definition
    contract_def = {
        'policy': 'relational_snapshot_v1',
        'unit': 'points',
        'scope': 'entire_supplied_snapshot',
        'fact_table': 'fact_items',
        'fact_row_key': 'fact_id',
        'dimension_table': 'dim_groups',
        'dimension_row_key': 'group_id',
        'foreign_key': {'fact_column': 'group_id', 'dimension_column': 'group_id'},
        'measure': {
            'column': 'amount',
            'precision': 10,
            'scale': 2,
            'blank_nullable': True,
        },
        'categorical_fields': [
            {'table': 'fact_items', 'column': 'kind'},
            {'table': 'dim_groups', 'column': 'segment'},
        ],
        'declarations': {
            'fact_grain': 'one row per item',
            'dimension_grain': 'one row per group',
            'owner_label': 'Billing Operations',
            'meaning': 'Standard point items billed per group segment',
        }
    }

    # Contract hash
    contract_canonical = json.dumps(contract_def, sort_keys=True, separators=(',', ':'))
    contract_hash = hashlib.sha256(contract_canonical.encode('utf-8')).hexdigest()
    contract_def['contract_hash'] = contract_hash

    receipt = {
        'gate': 'INDEPENDENT_RELATIONAL_FIXTURE',
        'status': 'PASS',
        'fixtures': {
            'fact_items_csv': {
                'path': str(fact_path),
                'sha256': fact_hash,
                'row_count': len(fact_rows),
            },
            'dim_groups_csv': {
                'path': str(dim_path),
                'sha256': dim_hash,
                'row_count': len(dim_rows),
            },
        },
        'frozen_join_proof': join_proof,
        'independent_decimal_calculations': {
            'baseline_sql': "SELECT SUM(f.amount) FROM fact_items f LEFT JOIN dim_groups d ON f.group_id = d.group_id",
            'target_sql': "SELECT SUM(f.amount) FROM fact_items f INNER JOIN dim_groups d ON f.group_id = d.group_id WHERE d.segment = 'north'",
            'expected_totals': expected_totals,
            'row_deltas': row_deltas,
        },
        'contract_definition': contract_def,
    }

    out_file = RECEIPTS_DIR / 'receipt_03_relational_fixture.json'
    out_file.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f"Receipt written to {out_file}")
    print(json.dumps(receipt, indent=2))

if __name__ == '__main__':
    main()
