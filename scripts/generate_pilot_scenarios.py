"""Generate synthetic pilot scenarios that simulate realistic analyst investigations.

Creates multiple cases with:
- Realistic business context and KPI disagreements
- Varying complexity levels
- Common error patterns analysts might encounter
- Different schema layouts
- Documented expected conclusions
"""

import csv
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]


def generate_retail_returns_case():
    """Scenario: Revenue reporting disagreement due to return handling.

    Business context: Finance excludes returned orders, Marketing includes gross sales.
    Realistic error: Analyst might not notice the filter difference initially.
    """
    output_dir = ROOT / 'data' / 'pilot' / 'scenario-returns'
    output_dir.mkdir(parents=True, exist_ok=True)

    random.seed(20260925001)

    # Generate 800 orders with some returns
    records = []
    for i in range(800):
        order_id = f'ORD-{2024001 + i}'
        status = random.choices(['delivered', 'returned'], weights=[0.85, 0.15])[0]
        amount = Decimal(str(random.randint(20, 500)))

        records.append({
            'order_id': order_id,
            'order_status': status,
            'revenue': f'{amount:.2f}'
        })

    csv_path = output_dir / 'orders-snapshot.csv'
    with csv_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['order_id', 'order_status', 'revenue'])
        writer.writeheader()
        writer.writerows(records)

    # Calculate oracle
    finance_total = sum(Decimal(r['revenue']) for r in records if r['order_status'] == 'delivered')
    marketing_total = sum(Decimal(r['revenue']) for r in records)

    # Create lineage
    lineage_path = output_dir / 'orders-lineage.csv'
    with lineage_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['order_id', 'source', 'source_row'])
        writer.writeheader()
        for i, record in enumerate(records):
            writer.writerow({'order_id': record['order_id'], 'source': 'retail_orders_table', 'source_row': i + 1})

    csv_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    lineage_hash = hashlib.sha256(lineage_path.read_bytes()).hexdigest()
    source_hash = hashlib.sha256(b'retail_orders_synthetic_20260925001').hexdigest()

    manifest = {
        'version': 1,
        'classification': 'external_preparation',
        'script': 'scripts/generate_pilot_scenarios.py',
        'script_version': '1',
        'transformation': 'Extract orders with status and revenue from retail orders table',
        'source_hashes': {'retail_orders': source_hash},
        'flat_file': 'orders-snapshot.csv',
        'flat_sha256': csv_hash,
        'lineage_file': 'orders-lineage.csv',
        'lineage_sha256': lineage_hash
    }

    manifest_path = output_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    oracle = {
        'scenario': 'Revenue reporting disagreement',
        'business_context': 'Finance excludes returned orders from revenue, Marketing reports gross sales',
        'expected_confusion': 'Analyst might not immediately notice the status filter difference',
        'baseline': {
            'name': 'Finance revenue (delivered only)',
            'sql': "SELECT SUM(revenue) FROM snapshot WHERE order_status = 'delivered'",
            'expected': str(finance_total)
        },
        'target': {
            'name': 'Marketing revenue (all orders)',
            'sql': "SELECT SUM(revenue) FROM snapshot WHERE order_status IN ('delivered', 'returned')",
            'expected': str(marketing_total)
        },
        'gap': str(marketing_total - finance_total),
        'correct_conclusion': 'Marketing includes returned order revenue, Finance excludes it',
        'row_count': len(records),
        'csv_sha256': csv_hash,
        'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    }

    (output_dir / 'oracle.json').write_text(json.dumps(oracle, indent=2), encoding='utf-8')

    return {
        'name': 'Retail Returns Case',
        'dir': output_dir,
        'rows': len(records),
        'gap': str(marketing_total - finance_total)
    }


def generate_regional_exclusion_case():
    """Scenario: Regional sales discrepancy.

    Business context: One report excludes test markets, another includes them.
    Realistic error: Test market exclusion not documented in dashboard.
    """
    output_dir = ROOT / 'data' / 'pilot' / 'scenario-regions'
    output_dir.mkdir(parents=True, exist_ok=True)

    random.seed(20260925002)

    regions = ['NORTH', 'SOUTH', 'EAST', 'WEST', 'TEST_A', 'TEST_B']
    records = []

    for i in range(600):
        sale_id = f'SALE-{5000 + i}'
        region = random.choices(regions, weights=[0.25, 0.25, 0.25, 0.20, 0.03, 0.02])[0]
        amount = Decimal(str(random.randint(100, 2000)))

        records.append({
            'sale_id': sale_id,
            'region': region,
            'sale_amount': f'{amount:.2f}'
        })

    csv_path = output_dir / 'sales-snapshot.csv'
    with csv_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['sale_id', 'region', 'sale_amount'])
        writer.writeheader()
        writer.writerows(records)

    # Calculate oracle
    prod_regions = ['NORTH', 'SOUTH', 'EAST', 'WEST']
    prod_total = sum(Decimal(r['sale_amount']) for r in records if r['region'] in prod_regions)
    all_total = sum(Decimal(r['sale_amount']) for r in records)

    lineage_path = output_dir / 'sales-lineage.csv'
    with lineage_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['sale_id', 'source', 'source_row'])
        writer.writeheader()
        for i, record in enumerate(records):
            writer.writerow({'sale_id': record['sale_id'], 'source': 'sales_fact_table', 'source_row': i + 1})

    csv_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    lineage_hash = hashlib.sha256(lineage_path.read_bytes()).hexdigest()
    source_hash = hashlib.sha256(b'sales_fact_synthetic_20260925002').hexdigest()

    manifest = {
        'version': 1,
        'classification': 'external_preparation',
        'script': 'scripts/generate_pilot_scenarios.py',
        'script_version': '1',
        'transformation': 'Extract sales with region from sales fact table',
        'source_hashes': {'sales_fact': source_hash},
        'flat_file': 'sales-snapshot.csv',
        'flat_sha256': csv_hash,
        'lineage_file': 'sales-lineage.csv',
        'lineage_sha256': lineage_hash
    }

    manifest_path = output_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    oracle = {
        'scenario': 'Regional sales discrepancy',
        'business_context': 'Production report excludes TEST_A and TEST_B regions, executive dashboard includes all',
        'expected_confusion': 'Test region exclusion not documented in dashboard SQL',
        'baseline': {
            'name': 'Production regions only',
            'sql': "SELECT SUM(sale_amount) FROM snapshot WHERE region IN ('NORTH', 'SOUTH', 'EAST', 'WEST')",
            'expected': str(prod_total)
        },
        'target': {
            'name': 'All regions including test',
            'sql': "SELECT SUM(sale_amount) FROM snapshot WHERE region IN ('NORTH', 'SOUTH', 'EAST', 'WEST', 'TEST_A', 'TEST_B')",
            'expected': str(all_total)
        },
        'gap': str(all_total - prod_total),
        'correct_conclusion': 'Executive dashboard includes test market sales that production excludes',
        'row_count': len(records),
        'csv_sha256': csv_hash,
        'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    }

    (output_dir / 'oracle.json').write_text(json.dumps(oracle, indent=2), encoding='utf-8')

    return {
        'name': 'Regional Exclusion Case',
        'dir': output_dir,
        'rows': len(records),
        'gap': str(all_total - prod_total)
    }


def generate_status_filter_case():
    """Scenario: Transaction status filtering difference.

    Business context: One dashboard shows 'completed', another shows 'completed' + 'pending'.
    Realistic error: Pending transactions not settled but included in forecast.
    """
    output_dir = ROOT / 'data' / 'pilot' / 'scenario-status'
    output_dir.mkdir(parents=True, exist_ok=True)

    random.seed(20260925003)

    statuses = ['completed', 'pending', 'failed', 'cancelled']
    records = []

    for i in range(1000):
        txn_id = f'TXN-{10000 + i}'
        status = random.choices(statuses, weights=[0.70, 0.15, 0.10, 0.05])[0]
        amount = Decimal(str(random.randint(50, 800)))

        records.append({
            'transaction_id': txn_id,
            'status': status,
            'amount': f'{amount:.2f}'
        })

    csv_path = output_dir / 'transactions-snapshot.csv'
    with csv_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['transaction_id', 'status', 'amount'])
        writer.writeheader()
        writer.writerows(records)

    # Calculate oracle
    completed_total = sum(Decimal(r['amount']) for r in records if r['status'] == 'completed')
    completed_pending = sum(Decimal(r['amount']) for r in records if r['status'] in ['completed', 'pending'])

    lineage_path = output_dir / 'transactions-lineage.csv'
    with lineage_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['transaction_id', 'source', 'source_row'])
        writer.writeheader()
        for i, record in enumerate(records):
            writer.writerow({'transaction_id': record['transaction_id'], 'source': 'transaction_log', 'source_row': i + 1})

    csv_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    lineage_hash = hashlib.sha256(lineage_path.read_bytes()).hexdigest()
    source_hash = hashlib.sha256(b'transaction_log_synthetic_20260925003').hexdigest()

    manifest = {
        'version': 1,
        'classification': 'external_preparation',
        'script': 'scripts/generate_pilot_scenarios.py',
        'script_version': '1',
        'transformation': 'Extract transactions with status from transaction log',
        'source_hashes': {'transaction_log': source_hash},
        'flat_file': 'transactions-snapshot.csv',
        'flat_sha256': csv_hash,
        'lineage_file': 'transactions-lineage.csv',
        'lineage_sha256': lineage_hash
    }

    manifest_path = output_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    oracle = {
        'scenario': 'Transaction status filtering',
        'business_context': 'Accounting shows completed only, Forecast includes pending settlements',
        'expected_confusion': 'Pending transactions shown in forecast but not finalized',
        'baseline': {
            'name': 'Accounting (completed only)',
            'sql': "SELECT SUM(amount) FROM snapshot WHERE status = 'completed'",
            'expected': str(completed_total)
        },
        'target': {
            'name': 'Forecast (completed + pending)',
            'sql': "SELECT SUM(amount) FROM snapshot WHERE status IN ('completed', 'pending')",
            'expected': str(completed_pending)
        },
        'gap': str(completed_pending - completed_total),
        'correct_conclusion': 'Forecast includes pending transactions not yet settled in accounting',
        'row_count': len(records),
        'csv_sha256': csv_hash,
        'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    }

    (output_dir / 'oracle.json').write_text(json.dumps(oracle, indent=2), encoding='utf-8')

    return {
        'name': 'Status Filter Case',
        'dir': output_dir,
        'rows': len(records),
        'gap': str(completed_pending - completed_total)
    }


if __name__ == '__main__':
    print("Generating synthetic pilot scenarios...")
    print()

    cases = [
        generate_retail_returns_case(),
        generate_regional_exclusion_case(),
        generate_status_filter_case()
    ]

    print("=" * 60)
    print("SYNTHETIC PILOT SCENARIOS GENERATED")
    print("=" * 60)
    for case in cases:
        print(f"\n{case['name']}:")
        print(f"  Directory: {case['dir']}")
        print(f"  Rows: {case['rows']}")
        print(f"  Gap: {case['gap']}")

    print("\n\nEach scenario includes:")
    print("  - Realistic business context")
    print("  - Common analyst confusion points")
    print("  - Complete CSV + manifest + lineage")
    print("  - Oracle with expected conclusion")
