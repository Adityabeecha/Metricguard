"""Generate a second independent flat snapshot case with different schema.

This case uses an inventory/warehouse domain with different column names
and categories to verify that flat snapshot mode generalizes beyond Olist.
"""

import csv
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]


def generate_inventory_case():
    """Generate inventory snapshot with different columns and categories."""
    output_dir = ROOT / 'data' / 'evaluation' / 'inventory-case'
    output_dir.mkdir(parents=True, exist_ok=True)

    # Different schema: SKU tracking with location and condition categories
    csv_path = output_dir / 'inventory-snapshot.csv'
    manifest_path = output_dir / 'inventory-manifest.json'
    oracle_path = output_dir / 'oracle.json'

    random.seed(20260925)

    # Generate 500 inventory records
    warehouses = ['NORTH', 'SOUTH', 'EAST', 'WEST', 'CENTRAL']
    conditions = ['NEW', 'REFURBISHED', 'DAMAGED', 'RETURNED']

    records = []
    for i in range(500):
        sku_id = f'SKU-{1000 + i:05d}'
        warehouse = random.choice(warehouses)
        condition = random.choice(conditions)

        # Cost varies by condition
        base_cost = Decimal(str(random.randint(50, 500)))
        if condition == 'DAMAGED':
            cost = base_cost * Decimal('0.3')
        elif condition == 'RETURNED':
            cost = base_cost * Decimal('0.7')
        elif condition == 'REFURBISHED':
            cost = base_cost * Decimal('0.85')
        else:  # NEW
            cost = base_cost

        records.append({
            'sku_id': sku_id,
            'warehouse_location': warehouse,
            'item_condition': condition,
            'inventory_cost': f'{cost:.2f}'
        })

    # Write CSV
    with csv_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['sku_id', 'warehouse_location', 'item_condition', 'inventory_cost'])
        writer.writeheader()
        writer.writerows(records)

    # Write lineage file (maps each row to its source)
    lineage_path = output_dir / 'inventory-lineage.csv'
    with lineage_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['sku_id', 'source', 'source_row'])
        writer.writeheader()
        for i, record in enumerate(records):
            writer.writerow({
                'sku_id': record['sku_id'],
                'source': 'synthetic_generator',
                'source_row': i + 1
            })

    # Calculate independent oracle totals
    # Use positive filters (flat snapshot only supports = and IN, not <>)
    baseline_total = sum(Decimal(r['inventory_cost']) for r in records if r['item_condition'] in ['NEW', 'REFURBISHED', 'RETURNED'])
    target_total = sum(Decimal(r['inventory_cost']) for r in records if r['warehouse_location'] in ['NORTH', 'SOUTH'])

    # Calculate hashes
    csv_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    lineage_hash = hashlib.sha256(lineage_path.read_bytes()).hexdigest()

    # Create manifest with all required fields for external_preparation
    # source_hashes must contain SHA256 hashes (64 hex chars) - create a fake source hash for synthetic data
    synthetic_source_hash = hashlib.sha256(b'synthetic_inventory_generator_seed_20260925').hexdigest()

    manifest = {
        'version': 1,
        'classification': 'external_preparation',
        'script': 'scripts/create_inventory_case.py',
        'script_version': '1',
        'transformation': 'Generated synthetic inventory records with random warehouse locations and item conditions',
        'source_hashes': {
            'synthetic_generator': synthetic_source_hash
        },
        'flat_file': 'inventory-snapshot.csv',
        'flat_sha256': csv_hash,
        'lineage_file': 'inventory-lineage.csv',
        'lineage_sha256': lineage_hash,
        'sku_count': len(records),
        'measure_meaning': 'total inventory cost at snapshot date',
        'measure_unit': 'USD',
        'null_semantics': 'blank inventory_cost means cost not recorded',
        'domain': 'inventory_warehouse',
        'purpose': 'Verify flat snapshot generalization beyond Olist retail case',
        'schema_differences': [
            'Different column names: sku_id (not order_id), inventory_cost (not payment_value)',
            'Different categories: warehouse_location (5 values), item_condition (4 values)',
            'Different domain: inventory management, not e-commerce orders'
        ]
    }

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    # Calculate manifest hash
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    # Create oracle with expected totals
    oracle = {
        'baseline': {
            'name': 'Include NEW, REFURBISHED, RETURNED items',
            'sql': "SELECT SUM(inventory_cost) FROM snapshot WHERE item_condition IN ('NEW', 'REFURBISHED', 'RETURNED')",
            'expected': str(baseline_total)
        },
        'target': {
            'name': 'NORTH and SOUTH warehouses only',
            'sql': "SELECT SUM(inventory_cost) FROM snapshot WHERE warehouse_location IN ('NORTH', 'SOUTH')",
            'expected': str(target_total)
        },
        'gap': str(target_total - baseline_total),
        'csv_sha256': csv_hash,
        'manifest_sha256': manifest_hash
    }

    oracle_path.write_text(json.dumps(oracle, indent=2), encoding='utf-8')

    return {
        'csv_path': csv_path,
        'manifest_path': manifest_path,
        'oracle_path': oracle_path,
        'csv_hash': csv_hash,
        'manifest_hash': manifest_hash,
        'row_count': len(records),
        'baseline_total': str(baseline_total),
        'target_total': str(target_total),
        'gap': str(target_total - baseline_total)
    }


if __name__ == '__main__':
    result = generate_inventory_case()
    print(f"Generated inventory case:")
    print(f"  CSV: {result['csv_path']}")
    print(f"  Rows: {result['row_count']}")
    print(f"  CSV hash: {result['csv_hash'][:12]}...")
    print(f"  Baseline total: {result['baseline_total']}")
    print(f"  Target total: {result['target_total']}")
    print(f"  Gap: {result['gap']}")
