"""Bounded, reproducible local v1 scale and numeric evaluation."""
from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal
import hashlib
import json
import platform
from pathlib import Path
import time

import duckdb
import sqlglot

from packages.metricguard_core import compare, ingest_csv, inspect_snapshot, parse_definition
from scripts.scale_fixture import generate_fixture


ROOT = Path(__file__).resolve().parents[1]
REPORT_NAME = 'release_report.json'


def _independent_totals(path: Path) -> dict[str, str]:
    """Read source records without the generator, SQL parser, or comparison engine."""
    totals = {name: Decimal(0) for name in ('dashboard_a', 'dashboard_b', 'finance')}
    august_start, september_start = date(2026, 8, 1), date(2026, 9, 1)
    with path.open(newline='', encoding='utf-8') as source:
        for row in csv.DictReader(source):
            amount = Decimal(row['order_amount'])
            order_date = date.fromisoformat(row['order_date'])
            if august_start <= order_date < september_start:
                if row['status'] == 'Completed':
                    totals['dashboard_a'] += amount
                if row['status'] not in ('Cancelled', ''):
                    totals['dashboard_b'] += amount
            if row['payment_status'] == 'Paid' and row['payment_date']:
                payment_date = date.fromisoformat(row['payment_date'])
                if august_start <= payment_date < september_start:
                    totals['finance'] += amount - Decimal(row['refund_amount'] or '0')
    return {key: f'{value:.2f}' for key, value in totals.items()}


def _seconds(start: float) -> float:
    return round(time.perf_counter() - start, 4)


def evaluate_release(output_dir: Path, rows: int = 100000, seed: int = 20260923) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path, oracle_path = output_dir / 'orders_scale.csv', output_dir / 'orders_scale_oracle.json'
    snapshot_path = output_dir / 'orders_scale.duckdb'
    report_path = output_dir / REPORT_NAME
    report = {'status': 'failed', 'rows': rows, 'seed': seed,
              'target_seconds': 30,
              'environment': {'python': platform.python_version(),
                              'duckdb': duckdb.__version__, 'sqlglot': sqlglot.__version__,
                              'platform': platform.system(), 'machine': platform.machine(),
                              'logical_cpus': __import__('os').cpu_count(),
                              'execution_mode': 'in_process_benchmark'},
              'limitations': ['In-process measurement does not prove Docker isolation or worker latency.',
                              'Synthetic workload and local hardware do not establish enterprise throughput.']}
    try:
        started = time.perf_counter()
        if csv_path.exists() or oracle_path.exists():
            if not (csv_path.exists() and oracle_path.exists()):
                raise ValueError('Fixture and oracle must both exist or both be absent')
            oracle = json.loads(oracle_path.read_text(encoding='utf-8'))
            if oracle['rows'] != rows or oracle['seed'] != seed:
                raise ValueError('Existing fixture has different row count or seed')
            if hashlib.sha256(csv_path.read_bytes()).hexdigest() != oracle['source_hash']:
                raise ValueError('Existing fixture hash differs from oracle')
        else:
            oracle = generate_fixture(csv_path, oracle_path, rows=rows, seed=seed)
        report['timings_seconds'] = {'generation_or_reuse': _seconds(started)}
        report['source_hash'] = oracle['source_hash']
        independent = _independent_totals(csv_path)
        if independent != oracle['august_totals']:
            raise ValueError('Direct CSV oracle does not match generated oracle')
        report['independent_oracle'] = independent

        started = time.perf_counter()
        metadata = {'scope': oracle['scope'], 'cutoff_at': oracle['cutoff_at']}
        manifest = inspect_snapshot(snapshot_path) if snapshot_path.exists() else ingest_csv(csv_path, snapshot_path, metadata)
        if manifest['row_count'] != rows or manifest['source_hash'] != oracle['source_hash']:
            raise ValueError('Snapshot does not match fixture')
        report['timings_seconds']['ingestion'] = _seconds(started)
        report['snapshot_manifest_hash'] = manifest['manifest_hash']
        report['snapshot_database_hash'] = manifest['database_hash']

        examples = ROOT / 'MetricGuard_Planning_Pack' / 'examples'
        a, b, finance = (parse_definition((examples / f'{name}.sql').read_text(encoding='utf-8'))
                         for name in ('dashboard_a', 'dashboard_b', 'finance'))
        context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01',
                   'timezone': oracle['timezone'], 'currency': oracle['currency'],
                   'unit': 'currency', 'grain': oracle['grain'], 'scope': oracle['scope'],
                   'source_hash': oracle['source_hash'], 'manifest_hash': manifest['manifest_hash']}
        started = time.perf_counter()
        a_finance = compare(snapshot_path, a, finance, context)
        report['timings_seconds']['first_comparison'] = _seconds(started)
        started = time.perf_counter()
        a_b = compare(snapshot_path, a, b, context)
        report['timings_seconds']['second_comparison'] = _seconds(started)
        started = time.perf_counter()
        repeat = compare(snapshot_path, a, finance, context)
        report['timings_seconds']['repeat_comparison'] = _seconds(started)

        observed = {'dashboard_a': a_finance['baseline_value'],
                    'dashboard_b': a_b['target_value'], 'finance': a_finance['target_value']}
        matches = {name: observed[name] == independent[name] for name in observed}
        report['oracle_match'] = matches
        report['observed_totals'] = observed
        report['repeat_equal'] = all(a_finance[key] == repeat[key] for key in
                                     ('baseline_value', 'target_value', 'delta_target_minus_baseline',
                                      'steps', 'evidence', 'definition_differences'))
        report['comparisons'] = {}
        for name, result, target in [('a_to_finance', a_finance, 'finance'), ('a_to_b', a_b, 'dashboard_b')]:
            expected_delta = Decimal(independent[target]) - Decimal(independent['dashboard_a'])
            report['comparisons'][name] = {
                'delta': result['delta_target_minus_baseline'],
                'expected_delta': format(expected_delta, 'f'),
                'residual': result['residual'], 'evidence_rows': len(result['evidence']),
                'serialized_result_bytes': len(json.dumps(result, separators=(',', ':')).encode('utf-8')),
                'explanation_status': result['explanation_status']}
        report['runner_output_within_25_mib'] = all(
            item['serialized_result_bytes'] <= 25 * 1024 * 1024
            for item in report['comparisons'].values())
        report['under_30_second_target'] = report['timings_seconds']['first_comparison'] <= 30
        if (all(matches.values()) and report['repeat_equal'] and
                all(item['delta'] == item['expected_delta'] and Decimal(item['residual']) == 0
                    for item in report['comparisons'].values())):
            report['status'] = 'passed'
        else:
            report['error'] = {'type': 'EvaluationMismatch', 'message': 'Numeric or repeat check failed'}
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)[:300]}
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description='Evaluate v1 on a deterministic synthetic workload')
    parser.add_argument('--output-dir', type=Path, default=Path('data/evaluation'))
    parser.add_argument('--rows', type=int, default=100000)
    parser.add_argument('--seed', type=int, default=20260923)
    args = parser.parse_args()
    report = evaluate_release(args.output_dir, args.rows, args.seed)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
