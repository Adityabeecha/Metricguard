"""Entrypoint executed only inside the fixed analytical runner image."""

import json
from pathlib import Path

from services.supervisor.evidence_artifact import aggregate_contexts, write_artifact
from services.supervisor.worker import MAX_OUTPUT_BYTES, file_hash, validate_result


def run_job(input_dir=Path('/job/input'), output_dir=Path('/job/output')):
    from packages.metricguard_core import compare
    from packages.metricguard_core.flat import compare_flat

    input_dir, output_dir = Path(input_dir), Path(output_dir)
    job_path = input_dir / 'job.json'
    if job_path.stat().st_size > 1024 * 1024:
        raise ValueError('JOB_INPUT_TOO_LARGE')
    job = json.loads(job_path.read_text(encoding='utf-8'))
    snapshot = input_dir / 'snapshot.duckdb'
    if file_hash(snapshot) != job['snapshot_hash']:
        raise ValueError('SNAPSHOT_HASH_MISMATCH')
    case_context = None
    relational_context = None
    ratio_context = distinct_context = None
    members = None
    if job.get('execution_policy') == 'relational_snapshot_v1':
        from packages.metricguard_core.relational_compare import compare_relational
        from packages.metricguard_core.relational_snapshot import open_snapshot
        contract, manifest = open_snapshot(snapshot)
        relational_context = {'contract': contract, 'manifest': manifest,
                              'baseline': job['baseline_ir'], 'target': job['target_ir']}
        result = validate_result(compare_relational(snapshot, job['baseline_ir'], job['target_ir'], job['context']),
                                 relational_context=relational_context)
    elif job.get('execution_policy') == 'flat_snapshot_v1':
        if (job['baseline_ir'].get('mode') != 'flat_snapshot' or
                job['target_ir'].get('mode') != 'flat_snapshot'):
            raise ValueError('POLICY_MISMATCH')
        if job['context'].get('comparison') == 'snapshot_change':
            from packages.metricguard_core.flat_change import compare_snapshots
            target_snapshot = input_dir / 'target_snapshot.duckdb'
            if job['baseline_ir'] != job['target_ir'] or file_hash(target_snapshot) != job.get('target_snapshot_hash'):
                raise ValueError('SNAPSHOT_HASH_MISMATCH')
            comparison = compare_snapshots(snapshot, target_snapshot, job['baseline_ir'], job['context'])
        else:
            comparison = compare_flat(snapshot, job['baseline_ir'], job['target_ir'], job['context'])
        manifest = json.loads(snapshot.with_name(snapshot.name + '.manifest.json').read_text(encoding='utf-8'))
        if manifest['contract']['version'] == 7:
            case_context = {'contract': manifest['contract'], 'baseline': job['baseline_ir'],
                            'target': job['target_ir']}
        ratio_context, distinct_context = aggregate_contexts(manifest['contract'], job['baseline_ir'], job['target_ir'])
        members = comparison.pop('members', None)
        if ratio_context is not None or distinct_context is not None or hasattr(comparison['evidence'], 'artifact_policy'):
            # Every row is validated (with the totals) once, by write_artifact below; checking the
            # same rows inline here as well would double the time on large comparisons.
            evidence = comparison['evidence']
            placeholder = {'row_count': len(evidence)}
            if hasattr(evidence, 'artifact_policy'):
                placeholder['storage_policy'] = evidence.artifact_policy
            result = validate_result({**comparison, 'evidence': [], 'evidence_artifact': placeholder}, case_context=case_context,
                                     ratio_context=ratio_context, distinct_context=distinct_context)
            result = {key: value for key, value in result.items() if key != 'evidence_artifact'}
            result['evidence'] = evidence
        else:
            result = validate_result(comparison, case_context=case_context,
                                     ratio_context=ratio_context, distinct_context=distinct_context)
    elif job.get('execution_policy') in (None, 'retail_v1'):
        if (job['baseline_ir'].get('mode') in ('flat_snapshot', 'relational_snapshot') or
                job['target_ir'].get('mode') in ('flat_snapshot', 'relational_snapshot')):
            raise ValueError('POLICY_MISMATCH')
        result = validate_result(compare(snapshot, job['baseline_ir'],
                                         job['target_ir'], job['context']))
    else:
        raise ValueError('POLICY_MISMATCH')
    evidence = result['evidence']
    result = {**result, 'evidence': []}
    output_dir.mkdir(parents=True, exist_ok=True)
    measure_expr = None
    if job.get('execution_policy') == 'flat_snapshot_v1' and case_context is None:
        measure_expr = job.get('baseline_ir', {}).get('measure_expression')
    if ratio_context is not None or distinct_context is not None:
        measure_expr = None
    result['evidence_artifact'] = write_artifact(output_dir / 'evidence.jsonl', evidence, measure_expression=measure_expr, case_context=case_context, relational_context=relational_context,
                                                 ratio_context=ratio_context, distinct_context=distinct_context, expected_result=result,
                                                 check_rows=False)
    if distinct_context is not None:
        # Required for COUNT(DISTINCT): which records carry each entity at each endpoint.
        from packages.metricguard_core.flat_distinct_evidence import write_members
        write_members(output_dir / 'members.jsonl', members or [])
    if job.get('execution_policy') == 'flat_snapshot_v1' and job['context'].get('comparison') != 'snapshot_change':
        # Optional, separately hashed record context (filter-column values per row).
        # Not written for two-snapshot runs: one snapshot cannot describe records of both loads.
        # Best effort: the comparison and its evidence never depend on it.
        try:
            from services.supervisor.row_context import write_entity_context, write_flat_context
            if distinct_context is not None:
                info = write_entity_context(snapshot, manifest['contract'], job['baseline_ir']['measure'],
                                            evidence, output_dir / 'context.jsonl')
            else:
                info = write_flat_context(snapshot, manifest['contract'], evidence, output_dir / 'context.jsonl')
            (output_dir / 'context.json').write_text(json.dumps(info), encoding='utf-8')
        except Exception:
            (output_dir / 'context.jsonl').unlink(missing_ok=True)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError('RUNNER_OUTPUT_TOO_LARGE')
    (output_dir / 'result.json').write_bytes(encoded)


def ingest_job(input_dir=Path('/job/input'), output_dir=Path('/job/output')):
    from packages.metricguard_core import ingest_csv, ingest_parquet
    from packages.metricguard_core.v2_contract import ingest_mapped
    from packages.metricguard_core.flat import ingest_flat
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    metadata_path = input_dir / 'metadata.json'
    if metadata_path.stat().st_size > 64 * 1024:
        raise ValueError('METADATA_TOO_LARGE')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    output_dir.mkdir(parents=True, exist_ok=True)
    database = output_dir / 'snapshot.duckdb'
    if 'relational_contract' in metadata:
        from packages.metricguard_core.relational_snapshot import ingest_relational
        manifest = ingest_relational(input_dir / 'source.zip', database, metadata['relational_contract'])
        encoded = json.dumps(manifest, allow_nan=False).encode('utf-8')
        if len(encoded) > 1024 * 1024:
            raise ValueError('MANIFEST_TOO_LARGE')
        (output_dir / 'manifest.json').write_bytes(encoded)
        return manifest
    csv_source, parquet_source = input_dir / 'source.csv', input_dir / 'source.parquet'
    if csv_source.is_file() == parquet_source.is_file():
        raise ValueError('INGEST_INPUT_INVALID')
    if 'flat_contract' in metadata:
        manifest = ingest_flat(csv_source if csv_source.is_file() else parquet_source,
                               database, metadata['flat_contract'], metadata.get('flat_preparation'))
    elif 'contract' in metadata:
        if not isinstance(metadata.get('mapping'), dict):
            raise ValueError('MAPPED_INPUT_INVALID')
        manifest = ingest_mapped(csv_source if csv_source.is_file() else parquet_source,
                                 database, metadata['contract'], metadata['mapping'])
    else:
        manifest = (ingest_csv(csv_source, database, metadata) if csv_source.is_file()
                    else ingest_parquet(parquet_source, database, metadata))
    if file_hash(database) != manifest['database_hash']:
        raise ValueError('INGEST_HASH_MISMATCH')
    encoded = json.dumps(manifest, allow_nan=False).encode('utf-8')
    if len(encoded) > 1024 * 1024:
        raise ValueError('MANIFEST_TOO_LARGE')
    (output_dir / 'manifest.json').write_bytes(encoded)
    return manifest


def preview_job(input_dir=Path('/job/input'), output_dir=Path('/job/output')):
    from packages.metricguard_core.v2_contract import preview_source

    input_dir, output_dir = Path(input_dir), Path(output_dir)
    csv_source, parquet_source = input_dir / 'source.csv', input_dir / 'source.parquet'
    if csv_source.is_file() == parquet_source.is_file():
        raise ValueError('PREVIEW_INPUT_INVALID')
    preview = preview_source(csv_source if csv_source.is_file() else parquet_source)
    output_dir.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(preview, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > 64 * 1024:
        raise ValueError('PREVIEW_TOO_LARGE')
    (output_dir / 'preview.json').write_bytes(encoded)
    return preview


if __name__ == '__main__':
    import sys
    output = Path('/job/output')
    if len(sys.argv) > 1 and sys.argv[1] in {'ingest', 'preview'}:
        from packages.metricguard_core import CoreError
        try:
            ingest_job() if sys.argv[1] == 'ingest' else preview_job()
        except CoreError as error:
            output.mkdir(parents=True, exist_ok=True)
            (output / 'error.json').write_text(json.dumps({'code': error.code, 'message': error.message}), encoding='utf-8')
            sys.exit(2)
        except ValueError as error:
            output.mkdir(parents=True, exist_ok=True)
            (output / 'error.json').write_text(json.dumps({'code': 'INGEST_FAILED', 'message': str(error)}), encoding='utf-8')
            sys.exit(2)
        except Exception:
            output.mkdir(parents=True, exist_ok=True)
            (output / 'error.json').write_text(json.dumps({'code': 'INTERNAL_ERROR', 'message': 'Internal runner ingestion error'}), encoding='utf-8')
            sys.exit(1)
    else:
        from packages.metricguard_core import CoreError
        try:
            run_job()
        except CoreError as error:
            output.mkdir(parents=True, exist_ok=True)
            (output / 'error.json').write_text(json.dumps({'code': error.code, 'message': error.message}), encoding='utf-8')
            sys.exit(2)
        except ValueError as error:
            output.mkdir(parents=True, exist_ok=True)
            code = str(error)
            msg = 'Validation error during runner execution'
            if code == 'POLICY_MISMATCH':
                msg = 'Policy mismatch between job and IR'
            elif code == 'SNAPSHOT_HASH_MISMATCH':
                msg = 'Snapshot hash mismatch'
            elif code in ('JOB_INPUT_TOO_LARGE', 'RUNNER_OUTPUT_TOO_LARGE'):
                msg = 'Runner job or output exceeded byte limits'
            elif code == 'EVIDENCE_INVALID':
                msg = 'Evidence artifact validation failed'
            elif 'reconcile' in code:
                msg = 'Comparison or evidence delta does not reconcile'
            (output / 'error.json').write_text(json.dumps({'code': 'RUNNER_VALIDATION_FAILED', 'message': msg}), encoding='utf-8')
            sys.exit(2)
        except Exception:
            output.mkdir(parents=True, exist_ok=True)
            (output / 'error.json').write_text(json.dumps({'code': 'INTERNAL_ERROR', 'message': 'Internal runner execution error'}), encoding='utf-8')
            sys.exit(1)
