"""Reproducible, local flat comparison timings; no API or warehouse connection.

Run: python -m scripts.benchmark_flat_comparisons --rows 1000 10000 --repetitions 3
Optional --docker-image NAME additionally measures complete isolated import/run jobs.
Comparison time includes evidence construction; it cannot be reported separately.
These synthetic receipts measure the named paths, not UI latency or a scale guarantee.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import time

import duckdb
import sqlglot

from packages.metricguard_core.common import file_hash
from packages.metricguard_core.flat import MAX_FLAT_ROWS, compare_flat, ingest_flat, parse_flat, validate_contract
from services.supervisor.evidence_artifact import aggregate_contexts, expected_artifact_delta, verify_artifact, write_artifact
from services.supervisor.row_context import write_flat_context
from services.supervisor.worker import DockerLauncher, INGEST_SECONDS, RUN_SECONDS, isolated_ingest, validate_result

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    'sum_filter_change': ("SELECT SUM(amount) FROM snapshot WHERE status = 'paid'",
                          "SELECT SUM(amount) FROM snapshot WHERE status IN ('paid', 'refunded')"),
    'average_filter_change': ("SELECT AVG(amount) FROM snapshot WHERE status = 'paid'",
                              "SELECT AVG(amount) FROM snapshot WHERE status IN ('paid', 'refunded')"),
}


def _source_identity():
    files = sorted([*ROOT.glob('packages/metricguard_core/*.py'),
                    *ROOT.glob('services/supervisor/*.py'), Path(__file__)])
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _timed(timings, name, action):
    started = time.perf_counter()
    try:
        return action()
    finally:
        timings[name] = round(time.perf_counter() - started, 6)


def _fixture(path, count):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['record_id', 'amount', 'status', 'region'])
        for index in range(count):
            cents = index % 20001
            amount = '' if index % 97 == 0 else f'{cents // 100}.{cents % 100:02d}'
            writer.writerow([f'R{index:09d}', amount,
                             'paid' if index % 10 < 7 else 'refunded' if index % 10 < 9 else 'void',
                             'north' if index % 2 else 'south'])


def _contract():
    return validate_contract({
        'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot',
        'name': 'bounded synthetic timing fixture', 'row_key': 'record_id', 'measure': 'amount',
        'precision': 18, 'scale': 2, 'categories': ['status', 'region'],
        'dimension_types': {'status': 'string', 'region': 'string'},
        'measure_meaning': 'synthetic amount', 'unit_label': 'units', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})


def _docker_run(launcher, folder, database, baseline, target, context):
    input_dir, output_dir = folder / 'docker-input', folder / 'docker-output'
    input_dir.mkdir()
    output_dir.mkdir()
    shutil.copyfile(database, input_dir / 'snapshot.duckdb')
    shutil.copyfile(database.with_name(database.name + '.manifest.json'), input_dir / 'snapshot.duckdb.manifest.json')
    (input_dir / 'job.json').write_text(json.dumps({
        'execution_policy': 'flat_snapshot_v1', 'snapshot_hash': file_hash(database),
        'baseline_ir': baseline, 'target_ir': target, 'context': context}), encoding='utf-8')
    started = time.perf_counter()
    process = launcher.start(input_dir, output_dir)
    try:
        _, stderr = process.communicate(timeout=RUN_SECONDS)
    except subprocess.TimeoutExpired:
        launcher.stop(process)
        raise ValueError('RUN_TIMEOUT')
    elapsed = round(time.perf_counter() - started, 6)
    if process.returncode:
        detail = (output_dir / 'error.json')
        raise ValueError(detail.read_text(encoding='utf-8') if detail.exists() else stderr.decode(errors='replace')[-1500:])
    result = json.loads((output_dir / 'result.json').read_text(encoding='utf-8'))
    return elapsed, result


def benchmark(rows, repetitions, output, docker_image=None):
    identity = _source_identity()
    report = {
        'schema': 'metricguard_flat_benchmark_v1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'python_version': platform.python_version(), 'platform': platform.platform(),
        'duckdb_version': duckdb.__version__, 'sqlglot_version': sqlglot.__version__,
        'source_sha256_start': identity, 'requested_rows': rows, 'repetitions': repetitions,
        'fixture': 'deterministic record keys; 70% paid, 20% refunded, 10% void; every 97th amount NULL',
        'scope': 'Local synthetic core stages; comparison includes row evidence construction. '
                 'Optional Docker timing includes container startup and runner work; excludes API/UI and publication.',
        'runs': []}
    launcher = DockerLauncher(docker_image) if docker_image else None
    work_root = ROOT / '.test-tmp' / 'flat-benchmark'
    work_root.mkdir(parents=True, exist_ok=True)
    if launcher:
        report['docker'] = {'requested_image': docker_image, 'run_timeout_seconds': RUN_SECONDS,
                            'ingest_timeout_seconds': INGEST_SECONDS,
                            'image_id': subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', docker_image],
                                                               text=True, timeout=15).strip()}
    for count in rows:
        for repetition in range(1, repetitions + 1):
            with tempfile.TemporaryDirectory(prefix='metricguard-timing-', dir=work_root) as temporary:
                folder = Path(temporary)
                source, database = folder / 'source.csv', folder / 'snapshot.duckdb'
                timings = {}
                _timed(timings, 'fixture_generation', lambda: _fixture(source, count))
                contract = _contract()
                manifest = _timed(timings, 'local_ingestion', lambda: ingest_flat(source, database, contract))
                if launcher:
                    isolated_database = folder / 'isolated.duckdb'
                    _timed(timings, 'isolated_ingestion_total', lambda: isolated_ingest(
                        source, isolated_database, {'flat_contract': contract}, launcher, folder / 'jobs'))
                context = {'mode': 'flat_snapshot', 'scope': contract['scope'], 'contract_hash': contract['contract_hash'],
                           'unit_label': contract['unit_label'], 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}
                for name, queries in CASES.items():
                    stage = {}
                    case = {'rows': count, 'repetition': repetition, 'case': name, 'sql': list(queries),
                            'shared_timings_seconds': timings, 'timings_seconds': stage, 'source_hash': manifest['source_hash']}
                    report['runs'].append(case)
                    case_folder = folder / name
                    case_folder.mkdir()
                    try:
                        baseline, target = _timed(stage, 'parse', lambda: [parse_flat(sql, contract) for sql in queries])
                        result = _timed(stage, 'compare_and_evidence', lambda: compare_flat(database, baseline, target, context))
                        ratio, distinct = aggregate_contexts(contract, baseline, target)
                        evidence = result.pop('evidence')
                        # Match the runner's validation order: aggregate and on-disk rows are checked once on artifact write.
                        deferred = ratio or distinct or hasattr(evidence, 'artifact_policy')
                        result['evidence'] = [] if deferred else evidence
                        if deferred:
                            result['evidence_artifact'] = {'row_count': len(evidence),
                                                           **({'storage_policy': evidence.artifact_policy}
                                                              if hasattr(evidence, 'artifact_policy') else {})}
                        _timed(stage, 'result_validation', lambda: validate_result(result, ratio_context=ratio, distinct_context=distinct))
                        result.pop('evidence_artifact', None)
                        result['evidence'] = []
                        artifact = case_folder / 'evidence.jsonl'
                        artifact_info = _timed(stage, 'artifact_write_and_row_validation', lambda: write_artifact(
                            artifact, evidence, ratio_context=ratio, distinct_context=distinct, expected_result=result))
                        _timed(stage, 'artifact_verification', lambda: verify_artifact(
                            artifact, artifact_info, expected_artifact_delta(result, ratio), ratio_context=ratio,
                            distinct_context=distinct, expected_result=result))
                        _timed(stage, 'row_context', lambda: write_flat_context(database, contract, evidence, case_folder / 'context.jsonl'))
                        if launcher:
                            elapsed, isolated_result = _docker_run(launcher, case_folder, database, baseline, target, context)
                            stage['isolated_comparison_total'] = elapsed
                            for field in ('baseline_value', 'target_value', 'delta_target_minus_baseline', 'residual'):
                                if isolated_result[field] != result[field]:
                                    raise ValueError(f'ISOLATED_RESULT_MISMATCH: {field}')
                        case.update(status='measured', baseline_value=result['baseline_value'], target_value=result['target_value'],
                                    delta=result['delta_target_minus_baseline'], residual=result['residual'],
                                    evidence_records=len(evidence), evidence_bytes=artifact_info['byte_count'])
                    except Exception as error:
                        case.update(status='error', error_code=getattr(error, 'code', type(error).__name__), error=str(error))
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
    report['source_sha256_end'] = _source_identity()
    report['stable_sources'] = identity == report['source_sha256_end']
    report['summary'] = []
    for count in rows:
        for name in CASES:
            matching = [run for run in report['runs'] if run['rows'] == count and run['case'] == name and run['status'] == 'measured']
            stages = sorted({stage for run in matching for stage in run['timings_seconds']})
            report['summary'].append({'rows': count, 'case': name, 'measured_repetitions': len(matching),
                'median_seconds': {stage: round(statistics.median(run['timings_seconds'][stage] for run in matching), 6) for stage in stages}})
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, nargs='+', default=[1000, 10000])
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/evaluation/flat_comparison_timings.json')
    parser.add_argument('--docker-image', help='Explicit image already built from the source being measured')
    args = parser.parse_args()
    if any(not 1 <= count <= MAX_FLAT_ROWS for count in args.rows) or not 1 <= args.repetitions <= 20:
        parser.error(f'rows must be 1..{MAX_FLAT_ROWS}; repetitions must be 1..20')
    report = benchmark(args.rows, args.repetitions, args.output, args.docker_image)
    print(json.dumps({'output': str(args.output), 'stable_sources': report['stable_sources'], 'summary': report['summary']}, indent=2))
    if not report['stable_sources'] or any(run['status'] != 'measured' for run in report['runs']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
