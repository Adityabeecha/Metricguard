"""Claim runs and launch the fixed, restricted analytical container."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from decimal import Decimal, InvalidOperation, localcontext

from packages.metricguard_core.numeric import ScaledInteger, MAX_NUMERIC_STR_LEN
from services.supervisor.evidence_artifact import (
    aggregate_contexts, expected_artifact_delta, verify_artifact, _row_delta,
)
from packages.metricguard_core.flat_case_evidence import CaseEvidenceValidator, validate_case_result


MAX_OUTPUT_BYTES = 25 * 1024 * 1024
# Comparisons and imports of large extracts (millions of rows) need longer than small ones.
RUN_SECONDS = int(os.environ.get('METRICGUARD_RUN_SECONDS', '600'))
INGEST_SECONDS = int(os.environ.get('METRICGUARD_INGEST_SECONDS', '1200'))


class IngestValidationError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(code)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_aggregate(result, ratio_context, distinct_context):
    """Ratio and COUNT(DISTINCT) results need the trusted definitions; inline evidence is fully checked."""
    artifact = result.get('evidence_artifact') or {}
    claims_ratio = (result.get('aggregate') == 'ratio' or result.get('method') == 'flat_ratio_shapley' or
                    'ratio' in result or 'ratio_binding' in artifact)
    claims_distinct = (result.get('aggregate') == 'count_distinct' or 'distinct' in result or
                       result.get('method') == 'flat_distinct_membership_change' or 'distinct_binding' in artifact)
    if claims_ratio != (ratio_context is not None) or claims_distinct != (distinct_context is not None):
        raise ValueError('POLICY_MISMATCH')
    if ratio_context is None and distinct_context is None:
        return
    from packages.metricguard_core.flat_distinct_evidence import DistinctEvidenceValidator
    from packages.metricguard_core.flat_ratio_evidence import RatioEvidenceValidator
    checker = (RatioEvidenceValidator(ratio_context, result) if ratio_context is not None
               else DistinctEvidenceValidator(distinct_context, result))
    if 'evidence_artifact' in result:
        return  # the artifact's rows and totals are proven by verify_artifact
    for row in result['evidence']:
        checker.add(row)
    checker.finish(result)


def validate_result(result, case_context=None, relational_context=None, ratio_context=None, distinct_context=None):
    if not isinstance(result, dict):
        raise ValueError('result must be an object')
    required = ('baseline_value', 'target_value', 'delta_target_minus_baseline',
                'absolute_gap', 'residual', 'steps', 'evidence', 'provenance')
    if any(key not in result for key in required):
        raise ValueError('result structure incomplete')
    if not isinstance(result['steps'], list) or not isinstance(result['evidence'], list):
        raise ValueError('invalid result arrays')
    if not isinstance(result['provenance'], dict):
        raise ValueError('invalid provenance')
    if 'evidence_artifact' in result and (not isinstance(result['evidence_artifact'], dict) or result['evidence']):
        raise ValueError('invalid evidence artifact summary')
    coverage = result.get('evidence_coverage')
    if coverage is not None:
        from packages.metricguard_core.flat import MAX_FLAT_ROWS, MAX_LISTED_RECORDS
        from packages.metricguard_core.disk_evidence import STORAGE_POLICY
        storage = result['provenance'].get('evidence_storage_policy')
        listed_limit = MAX_FLAT_ROWS if storage == STORAGE_POLICY else MAX_LISTED_RECORDS
        if storage is not None and (storage != STORAGE_POLICY or
                result.get('evidence_artifact', {}).get('storage_policy') != storage):
            raise ValueError('invalid evidence storage policy')
        count_fields = {'total_records', 'listed_records', 'unchanged_records',
                        'unchanged_included_in_both', 'unchanged_included_in_neither'}
        if (not isinstance(coverage, dict) or set(coverage) != count_fields | {'mode'} or
                coverage['mode'] != 'changed_records' or
                result['provenance'].get('evidence_coverage') != 'changed_records' or
                result['provenance'].get('execution_policy') != 'flat-snapshot-v1' or
                any(type(coverage[field]) is not int or coverage[field] < 0 for field in count_fields) or
                coverage['total_records'] > MAX_FLAT_ROWS or coverage['listed_records'] > listed_limit or
                coverage['unchanged_records'] != coverage['unchanged_included_in_both'] + coverage['unchanged_included_in_neither'] or
                coverage['total_records'] != coverage['listed_records'] + coverage['unchanged_records'] or
                coverage['listed_records'] != (result['evidence_artifact'].get('row_count')
                                               if 'evidence_artifact' in result else len(result['evidence']))):
            raise ValueError('invalid evidence coverage')
    elif result['provenance'].get('evidence_coverage') == 'changed_records':
        raise ValueError('missing evidence coverage')
    if relational_context is not None:
        from packages.metricguard_core.relational_evidence import RelationalEvidenceValidator, validate_result as validate_relational_result
        if case_context is not None:
            raise ValueError('POLICY_MISMATCH')
        validate_relational_result(result, relational_context)
        if 'evidence_artifact' not in result:
            with RelationalEvidenceValidator(relational_context) as checker:
                for row in result['evidence']:
                    checker.add(row)
                checker.finish(result)
    elif (result['provenance'].get('execution_policy') == 'relational_snapshot_v1' or
          result.get('method') == 'relational_join_filter_change' or
          'relational_binding' in result.get('evidence_artifact', {})):
        raise ValueError('Relational result requires trusted definition context')
    if case_context is not None:
        validate_case_result(result, case_context)
        # Artifact-backed results are checked in verify_artifact. Inline runner
        # output uses the same bounded native-predicate verification, not one
        # connection (or one Python predicate interpreter) per row.
        if 'evidence_artifact' not in result:
            with CaseEvidenceValidator(case_context) as checker:
                for row in result['evidence']:
                    _row_delta(row, case_context=case_context, case_validator=checker)
                checker.finish(result)
    elif (result['provenance'].get('contract_version') == 7 or
          result['provenance'].get('measure_policy') == 'flat-case-v1' or
          result.get('method') == 'flat_case_change' or
          'case_binding' in result.get('evidence_artifact', {})):
        raise ValueError('CASE result requires trusted definition context')
    _validate_aggregate(result, ratio_context, distinct_context)
    if result['baseline_value'] is None or result['target_value'] is None:
        if (result['delta_target_minus_baseline'] is not None or
                result['absolute_gap'] is not None or result['residual'] is not None or
                result['steps']):
            raise ValueError('undefined endpoint has derived values')
        return result
    try:
        for field in ('baseline_value', 'target_value', 'delta_target_minus_baseline', 'absolute_gap', 'residual'):
            v_str = result[field]
            if not isinstance(v_str, str) or not v_str or len(v_str) > MAX_NUMERIC_STR_LEN or 'e' in v_str or 'E' in v_str:
                raise ValueError('invalid numeric representation')
        baseline = ScaledInteger.parse(result['baseline_value'], max_len=MAX_NUMERIC_STR_LEN)
        target = ScaledInteger.parse(result['target_value'], max_len=MAX_NUMERIC_STR_LEN)
        delta = ScaledInteger.parse(result['delta_target_minus_baseline'], max_len=MAX_NUMERIC_STR_LEN)
        gap = ScaledInteger.parse(result['absolute_gap'], max_len=MAX_NUMERIC_STR_LEN)
        residual = ScaledInteger.parse(result['residual'], max_len=MAX_NUMERIC_STR_LEN)
    except KeyError as exc:
        raise ValueError('invalid numeric value') from exc
    if target - baseline != delta:
        raise ValueError('delta does not reconcile')
    if abs(delta) != gap:
        raise ValueError('absolute gap does not reconcile')
    if result['steps']:
        previous = baseline
        for step in result['steps']:
            if not isinstance(step, dict):
                raise ValueError('invalid numeric value')
            for fld in ('previous_value', 'next_value', 'contribution'):
                if fld not in step or not isinstance(step[fld], str) or 'e' in step[fld] or 'E' in step[fld] or len(step[fld]) > MAX_NUMERIC_STR_LEN:
                    raise ValueError('invalid numeric value')
            step_prev = ScaledInteger.parse(step['previous_value'], max_len=MAX_NUMERIC_STR_LEN)
            step_next = ScaledInteger.parse(step['next_value'], max_len=MAX_NUMERIC_STR_LEN)
            ScaledInteger.parse(step['contribution'], max_len=MAX_NUMERIC_STR_LEN)
            if step_prev != previous:
                raise ValueError('bridge steps are not chained')
            previous = step_next
        if result.get('explanation_status') == 'complete_ordered_bridge' and previous != target:
            raise ValueError('bridge does not reach target')
        contributions = sum((ScaledInteger.parse(step['contribution'], max_len=MAX_NUMERIC_STR_LEN) for step in result['steps']), ScaledInteger.zero())
        if delta - contributions != residual:
            raise ValueError('bridge residual does not reconcile')
    if result.get('explanation_status') == 'complete_ordered_bridge' and residual != 0:
        raise ValueError('complete bridge has residual')
    if result['evidence'] and ratio_context is None:  # ratio rows hold exact shares, checked above
        try:
            evidence_delta = ScaledInteger.zero()
            for row in result['evidence']:
                if not isinstance(row, dict) or 'delta' not in row or not isinstance(row['delta'], str) or 'e' in row['delta'] or 'E' in row['delta'] or len(row['delta']) > MAX_NUMERIC_STR_LEN:
                    raise ValueError('invalid evidence delta')
                evidence_delta += ScaledInteger.parse(row['delta'], max_len=MAX_NUMERIC_STR_LEN)
        except (ValueError, TypeError, KeyError) as exc:
            if str(exc) in ('invalid evidence delta', 'evidence does not reconcile'):
                raise
            raise ValueError('invalid evidence delta') from exc
        if evidence_delta != delta:
            raise ValueError('evidence does not reconcile')
    return result


class DockerLauncher:
    def __init__(self, image=None):
        self.image = image or os.environ.get('METRICGUARD_RUNNER_IMAGE', 'metricguard-runner:1.0')

    def start(self, input_dir, output_dir, command=None):
        args, name = self.arguments(input_dir, output_dir, [self.image, *([] if command is None else [command])])
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.container_name = name
        return process

    def arguments(self, input_dir, output_dir, image_and_command):
        """The isolated `docker run` command line; shared with scripts.verify_live_runner."""
        input_dir = Path(input_dir).resolve()
        output_dir = Path(output_dir).resolve()
        # Large comparisons spool evidence to temporary files (disk_evidence, sqlite). The
        # 64 MiB in-memory /tmp counts against the 1 GiB memory limit, so temporary files go
        # to a fresh anonymous volume: disk-backed inside the Docker VM (a host bind mount is
        # several times slower on Docker Desktop) and removed with the container.
        name = f'metricguard-{uuid.uuid4().hex}'
        args = [
            'docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--name', name,
            '--user', '10001:10001', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--memory', '1g',
            '--cpus', '1', '--pids-limit', '64', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
            '--mount', f'type=bind,src={input_dir},dst=/job/input,readonly',
            '--mount', f'type=bind,src={output_dir},dst=/job/output',
            '--mount', 'type=volume,dst=/job/scratch',
            '--env', 'TMPDIR=/job/scratch', '--env', 'SQLITE_TMPDIR=/job/scratch',
            *image_and_command,
        ]
        return args, name

    def stop(self, process):
        name = getattr(process, 'container_name', None)
        if name:
            subprocess.run(['docker', 'kill', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
            subprocess.run(['docker', 'rm', '-f', '-v', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
        process.kill()
        process.wait(timeout=5)


class Supervisor:
    def __init__(self, store, launcher, job_root, worker_id, data_dir=None):
        self.store = store
        self.launcher = launcher
        self.job_root = Path(job_root)
        self.worker_id = worker_id
        self.data_dir = Path(data_dir) if data_dir is not None else self.job_root.parent

    def run_once(self):
        if hasattr(self.store, 'worker_seen'):
            self.store.worker_seen(self.worker_id)
        if hasattr(self.store, 'claim_import'):
            pending = self.store.claim_import(self.worker_id)
            if pending is not None:
                if pending['data'].get('metadata', {}).get('purpose') == 'preview':
                    self._preview(pending)
                else:
                    self._ingest(pending)
                return True
        run = self.store.claim(self.worker_id)
        if run is None or run.get('execution_mode') != 'isolated':
            return False
        run_id, attempt = run['id'], run['attempt']
        try:
            self._execute(run)
        except Exception as exc:
            allowed = {'SNAPSHOT_HASH_MISMATCH', 'RUN_TIMEOUT', 'RUNNER_FAILED',
                       'RUNNER_OUTPUT_INVALID'}
            if isinstance(exc, IngestValidationError):
                code, message = exc.code, exc.message
            else:
                code = str(exc) if isinstance(exc, ValueError) and str(exc) in allowed else 'RUNNER_ERROR'
                message = 'Isolated execution did not complete.'
            self.store.finish(run_id, self.worker_id, attempt,
                              error={'code': code, 'message': message})
        return True

    def _ingest(self, pending):
        ident, attempt = pending['id'], pending['attempt']
        source = Path(pending['source_path'])
        destination = self.data_dir / 'snapshots' / f'{ident}-{attempt}.duckdb'
        try:
            if file_hash(source) != pending['data']['source_hash']:
                raise ValueError('SOURCE_HASH_MISMATCH')
            def progress():
                self.store.worker_seen(self.worker_id)
                return self.store.heartbeat_import(ident, self.worker_id, attempt)
            manifest = isolated_ingest(source, destination, pending['data']['metadata'], self.launcher, self.job_root, progress)
            if manifest['source_hash'] != pending['data']['source_hash']:
                raise ValueError('SOURCE_HASH_MISMATCH')
            finished = self.store.finish_import(ident, self.worker_id, attempt, manifest=manifest, artifact_path=destination)
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                'SOURCE_HASH_MISMATCH', 'INGEST_TIMEOUT', 'INGEST_LEASE_LOST', 'INGEST_OUTPUT_INVALID',
                'INGEST_HASH_MISMATCH', 'INGEST_MANIFEST_MISMATCH'} else 'INGEST_FAILED'
            if isinstance(exc, IngestValidationError):
                code, message = exc.code, exc.message
            else:
                message = ('Relational import failed. Check both CSV files, registered keys, decimal measure and NULL policy.'
                           if 'relational_contract' in pending['data'].get('metadata', {}) else
                           'Flat extract validation failed. Check the registered key, decimal measure, categories, and NULL policy.'
                           if 'flat_contract' in pending['data'].get('metadata', {}) else
                           'Dataset validation or isolated ingestion failed. Check the fixed orders schema and metadata.')
            finished = self.store.finish_import(ident, self.worker_id, attempt,
                error={'code': code, 'message': message})
            destination.unlink(missing_ok=True)
            destination.with_name(destination.name + '.manifest.json').unlink(missing_ok=True)
        if finished:
            source.unlink(missing_ok=True)
        else:
            destination.unlink(missing_ok=True)
            destination.with_name(destination.name + '.manifest.json').unlink(missing_ok=True)

    def _preview(self, pending):
        ident, attempt = pending['id'], pending['attempt']
        source = Path(pending['source_path'])
        try:
            expected = pending['data']['source_hash']
            if file_hash(source) != expected:
                raise ValueError('SOURCE_HASH_MISMATCH')
            self.job_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='metricguard-preview-', dir=self.job_root) as workspace:
                input_dir, output_dir = Path(workspace) / 'input', Path(workspace) / 'output'
                input_dir.mkdir()
                output_dir.mkdir()
                output_dir.chmod(0o777)
                shutil.copyfile(source, input_dir / f'source{source.suffix.lower()}')
                process = self.launcher.start(input_dir, output_dir, 'preview')
                started = last_beat = time.monotonic()
                first = True
                while process.poll() is None:
                    now = time.monotonic()
                    if first or now - last_beat >= 1:
                        first, last_beat = False, now
                        self.store.worker_seen(self.worker_id)
                        if not self.store.heartbeat_import(ident, self.worker_id, attempt):
                            self.launcher.stop(process)
                            raise ValueError('PREVIEW_LEASE_LOST')
                    if now - started >= RUN_SECONDS:
                        self.launcher.stop(process)
                        raise ValueError('PREVIEW_TIMEOUT')
                    time.sleep(0.1)
                if process.wait(timeout=5) != 0:
                    error_path = output_dir / 'error.json'
                    if error_path.is_file() and error_path.stat().st_size <= 8192:
                        detail = json.loads(error_path.read_text(encoding='utf-8'))
                        if (detail.get('code') in {'invalid_schema', 'invalid_row', 'invalid_csv', 'invalid_xlsx',
                                'invalid_parquet', 'too_many_rows', 'file_too_large', 'preview_too_large'} and
                                isinstance(detail.get('message'), str) and len(detail['message']) <= 500):
                            raise IngestValidationError(detail['code'].upper(), detail['message'])
                    raise ValueError('PREVIEW_FAILED')
                output = output_dir / 'preview.json'
                if not output.is_file() or output.stat().st_size > 64 * 1024:
                    raise ValueError('PREVIEW_OUTPUT_INVALID')
                preview = json.loads(output.read_text(encoding='utf-8'))
                if (not isinstance(preview, dict) or preview.get('source_hash') != expected or
                        not isinstance(preview.get('headers'), list) or
                        not isinstance(preview.get('sample_rows'), list) or
                        len(preview['sample_rows']) > 5 or
                        len(json.dumps(preview, ensure_ascii=False).encode('utf-8')) > 64 * 1024):
                    raise ValueError('PREVIEW_OUTPUT_INVALID')
            finished = self.store.finish_preview(ident, self.worker_id, attempt, preview=preview)
        except Exception as exc:
            code = exc.code if isinstance(exc, IngestValidationError) else (
                str(exc) if isinstance(exc, ValueError) and str(exc) in {
                    'SOURCE_HASH_MISMATCH', 'PREVIEW_LEASE_LOST', 'PREVIEW_TIMEOUT',
                    'PREVIEW_OUTPUT_INVALID'} else 'PREVIEW_FAILED')
            message = exc.message if isinstance(exc, IngestValidationError) else 'Isolated source preview failed.'
            finished = self.store.finish_preview(ident, self.worker_id, attempt,
                                                 error={'code': code, 'message': message})
        if finished:
            source.unlink(missing_ok=True)

    @staticmethod
    def _entity_context(output_dir, contract):
        """For a distinct-count run: (offered columns, excluded columns); None for record context.

        The per-entity context offers only columns with one value per counted item (never
        the counted column itself); the others are listed with the reason (display only).
        """
        path = output_dir / 'context.json'
        if not path.is_file() or path.stat().st_size > 64 * 1024:
            return None
        meta = json.loads(path.read_text(encoding='utf-8'))
        if 'excluded_columns' not in meta:
            return None
        excluded, columns = meta.get('excluded_columns') or {}, meta.get('columns') or []
        categories = contract.get('categories', [])
        if (not isinstance(excluded, dict) or any(k not in categories for k in excluded) or
                any(v != 'multi_valued_per_entity' for v in excluded.values()) or
                not isinstance(columns, list) or any(c not in categories or c in excluded for c in columns)):
            raise ValueError('CONTEXT_INVALID')
        return [c for c in categories if c in columns], excluded

    def _publish_context(self, output_dir, published_evidence, contract, evidence_dir, run_id, attempt):
        """Validate and publish the optional record-context file; never fails the run."""
        source = output_dir / 'context.jsonl'
        if not source.is_file():
            return None
        target = evidence_dir / f'{run_id}-{attempt}.context.jsonl'
        try:
            from services.supervisor.row_context import validate_context
            info = validate_context(source, published_evidence, list(contract.get('categories', [])))
            entity = self._entity_context(output_dir, contract)
            if entity is not None:
                columns, excluded = entity
                info = {**info, 'columns': columns, 'excluded_columns': excluded}
            staged = evidence_dir / f'.{run_id}-{attempt}-{uuid.uuid4().hex}.context.tmp'
            shutil.copyfile(source, staged)
            if file_hash(staged) != info['sha256']:
                staged.unlink(missing_ok=True)
                return None
            os.replace(staged, target)
            return info
        except Exception:
            target.unlink(missing_ok=True)
            return None

    def _execute(self, run):
        run_id, attempt = run['id'], run['attempt']
        snapshot = self.store.snapshot(run['snapshot_id'])
        source = Path(snapshot['artifact_path']).resolve(strict=True)
        expected = snapshot['manifest']['database_hash']
        if file_hash(source) != expected:
            raise ValueError('SNAPSHOT_HASH_MISMATCH')
        self.job_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='metricguard-', dir=self.job_root) as workspace:
            input_dir = Path(workspace) / 'input'
            output_dir = Path(workspace) / 'output'
            input_dir.mkdir()
            output_dir.mkdir()
            output_dir.chmod(0o777)
            copied = input_dir / 'snapshot.duckdb'
            shutil.copyfile(source, copied)
            if file_hash(copied) != expected:
                raise ValueError('SNAPSHOT_HASH_MISMATCH')
            sidecar = source.with_name(source.name + '.manifest.json')
            destination_sidecar = copied.with_name(copied.name + '.manifest.json')
            if sidecar.is_file():
                shutil.copyfile(sidecar, destination_sidecar)
            else:
                destination_sidecar.write_text(json.dumps(snapshot['manifest'], allow_nan=False), encoding='utf-8')
            snapshot_change = (run.get('context') or {}).get('comparison') == 'snapshot_change'
            target_snapshot = None
            if snapshot_change:
                # Two data loads of one dataset: the target load travels as a second, hash-checked file.
                target_snapshot = self.store.snapshot(run['target_snapshot_id'])
                target_source = Path(target_snapshot['artifact_path']).resolve(strict=True)
                target_expected = target_snapshot['manifest']['database_hash']
                if (run.get('execution_policy') != 'flat_snapshot_v1' or
                        target_snapshot['manifest']['manifest_hash'] != run['context'].get('target_manifest_hash') or
                        snapshot['manifest']['manifest_hash'] != run['context'].get('manifest_hash')):
                    raise ValueError('SNAPSHOT_HASH_MISMATCH')
                target_copy = input_dir / 'target_snapshot.duckdb'
                shutil.copyfile(target_source, target_copy)
                if file_hash(target_copy) != target_expected:
                    raise ValueError('SNAPSHOT_HASH_MISMATCH')
                target_sidecar = target_source.with_name(target_source.name + '.manifest.json')
                target_copy_sidecar = target_copy.with_name(target_copy.name + '.manifest.json')
                if target_sidecar.is_file():
                    shutil.copyfile(target_sidecar, target_copy_sidecar)
                else:
                    target_copy_sidecar.write_text(json.dumps(target_snapshot['manifest'], allow_nan=False), encoding='utf-8')
            baseline = self.store.version(run['baseline_version_id'])
            target = self.store.version(run['target_version_id'])
            case_context = None
            relational_context = None
            contract = snapshot['manifest'].get('contract', {})
            if run.get('execution_policy') == 'relational_snapshot_v1':
                if (run.get('baseline_ir_hash') != baseline.get('ir_hash') or
                        run.get('target_ir_hash') != target.get('ir_hash') or
                        run.get('snapshot_manifest_hash') != snapshot['manifest']['manifest_hash']):
                    raise ValueError('SNAPSHOT_HASH_MISMATCH')
                relational_context = {'contract': contract, 'manifest': snapshot['manifest'],
                                      'baseline': baseline['ir'], 'target': target['ir']}
            elif contract.get('version') == 7:
                case_context = {'contract': contract, 'baseline': baseline['ir'], 'target': target['ir']}
            ratio_context = distinct_context = None
            if run.get('execution_policy') == 'flat_snapshot_v1':
                ratio_context, distinct_context = aggregate_contexts(contract, baseline['ir'], target['ir'])
            job = {'snapshot_hash': expected, 'baseline_ir': baseline['ir'],
                   'target_ir': target['ir'], 'context': run['context'],
                   'execution_policy': run.get('execution_policy', 'retail_v1')}
            if snapshot_change:
                job['target_snapshot_hash'] = target_snapshot['manifest']['database_hash']
            (input_dir / 'job.json').write_text(json.dumps(job, allow_nan=False), encoding='utf-8')
            process = self.launcher.start(input_dir, output_dir)
            started = last_beat = time.monotonic()
            first = True
            # Notice the container's exit within 0.1 s; heartbeat and cancel checks stay at about 1 s.
            while process.poll() is None:
                now = time.monotonic()
                if first or now - last_beat >= 1:
                    first, last_beat = False, now
                    if hasattr(self.store, 'worker_seen'):
                        self.store.worker_seen(self.worker_id)
                    if not self.store.heartbeat(run_id, self.worker_id, attempt):
                        self.launcher.stop(process)
                        return
                    if self.store.get_run(run_id).get('cancel_requested'):
                        self.launcher.stop(process)
                        self.store.finish(run_id, self.worker_id, attempt,
                                          error={'code': 'CANCELED', 'message': 'Canceled by user'})
                        return
                if now - started >= RUN_SECONDS:
                    self.launcher.stop(process)
                    raise ValueError('RUN_TIMEOUT')
                time.sleep(0.1)
            if process.wait(timeout=5) != 0:
                error_path = output_dir / 'error.json'
                if error_path.is_file() and error_path.stat().st_size <= 8192:
                    try:
                        detail = json.loads(error_path.read_text(encoding='utf-8'))
                        if detail.get('code') and isinstance(detail.get('message'), str):
                            raise IngestValidationError(detail['code'].upper(), detail['message'])
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        pass
                raise ValueError('RUNNER_FAILED')
            output = output_dir / 'result.json'
            if not output.is_file() or output.stat().st_size > MAX_OUTPUT_BYTES:
                raise ValueError('RUNNER_OUTPUT_INVALID')
            with output.open(encoding='utf-8') as stream:
                result = json.load(stream, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
            try:
                result = validate_result(result, case_context=case_context, relational_context=relational_context,
                                         ratio_context=ratio_context, distinct_context=distinct_context)
                if 'evidence_artifact' not in result:
                    raise ValueError('missing evidence artifact')
                if (result.get('evidence_coverage') is not None and
                        result['evidence_coverage']['total_records'] != snapshot['manifest'].get('row_count')):
                    raise ValueError('evidence coverage differs from frozen snapshot')
                if snapshot_change or result.get('method') == 'flat_snapshot_change':
                    expected_provenance = {
                        'comparison': 'snapshot_change' if snapshot_change else None,
                        'database_hash': expected, 'snapshot_manifest_hash': snapshot['manifest']['manifest_hash'],
                        'target_database_hash': target_snapshot['manifest']['database_hash'] if snapshot_change else None,
                        'target_snapshot_manifest_hash': target_snapshot['manifest']['manifest_hash'] if snapshot_change else None}
                    if (result.get('method') != 'flat_snapshot_change' or
                            any(result['provenance'].get(k) != v for k, v in expected_provenance.items())):
                        raise ValueError('Snapshot-change result binding differs')
                artifact = output_dir / 'evidence.jsonl'
                measure_expr = None if case_context is not None else baseline.get('ir', {}).get('measure_expression')
                if ratio_context is not None or distinct_context is not None:
                    expected_provenance = {'database_hash': expected,
                                           'source_hash': snapshot['manifest']['source_hash'],
                                           'snapshot_manifest_hash': snapshot['manifest']['manifest_hash']}
                    if any(result['provenance'].get(k) != v for k, v in expected_provenance.items()):
                        raise ValueError('Result snapshot binding differs')
                if case_context is not None:
                    expected_provenance = {'database_hash': expected,
                                           'source_hash': snapshot['manifest']['source_hash'],
                                           'snapshot_manifest_hash': snapshot['manifest']['manifest_hash']}
                    if any(result['provenance'].get(k) != v for k, v in expected_provenance.items()):
                        raise ValueError('CASE result snapshot binding differs')
                verify_artifact(artifact, result['evidence_artifact'], expected_artifact_delta(result, ratio_context),
                                measure_expression=measure_expr, case_context=case_context, expected_result=result,
                                relational_context=relational_context, ratio_context=ratio_context,
                                distinct_context=distinct_context)
                members_info = None
                if distinct_context is not None:
                    from packages.metricguard_core.flat_distinct_evidence import validate_members
                    members_info = validate_members(output_dir / 'members.jsonl', artifact, result,
                                                    row_count=snapshot['manifest'].get('row_count'))
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ValueError('RUNNER_OUTPUT_INVALID') from error
            evidence_dir = self.data_dir / 'evidence'
            evidence_dir.mkdir(parents=True, exist_ok=True)
            published = evidence_dir / f'{run_id}-{attempt}.jsonl'
            staged = evidence_dir / f'.{run_id}-{attempt}-{uuid.uuid4().hex}.tmp'
            did_publish = False
            try:
                if published.exists():
                    raise ValueError('RUNNER_OUTPUT_INVALID')
                shutil.copyfile(artifact, staged)
                # The source bytes were fully verified above; the copy only has to be
                # byte-identical to them, which its SHA-256 and size establish.
                if (file_hash(staged) != result['evidence_artifact']['sha256'] or
                        staged.stat().st_size != result['evidence_artifact']['byte_count']):
                    raise ValueError('RUNNER_OUTPUT_INVALID')
                os.replace(staged, published)
                did_publish = True
                members_path = None
                if members_info is not None:
                    members_path = evidence_dir / f'{run_id}-{attempt}.members.jsonl'
                    shutil.copyfile(output_dir / 'members.jsonl', staged)
                    if file_hash(staged) != members_info['sha256']:
                        raise ValueError('RUNNER_OUTPUT_INVALID')
                    os.replace(staged, members_path)
                context_info = self._publish_context(output_dir, published, contract, evidence_dir, run_id, attempt)
                if not self.store.finish(run_id, self.worker_id, attempt, result=result):
                    published.unlink(missing_ok=True)
                    if members_path is not None:
                        members_path.unlink(missing_ok=True)
                    did_publish = False
                elif hasattr(self.store, 'record'):
                    try:
                        if members_info is not None:
                            self.store.record('evidence_members', {'run_id': run_id, 'attempt': attempt, **members_info},
                                              project_id=run.get('project_id'))
                        if context_info is not None:
                            self.store.record('evidence_context', {'run_id': run_id, 'attempt': attempt, **context_info},
                                              project_id=run.get('project_id'))
                    except Exception:
                        pass
            except Exception:
                if did_publish:
                    published.unlink(missing_ok=True)
                    (evidence_dir / f'{run_id}-{attempt}.members.jsonl').unlink(missing_ok=True)
                raise
            finally:
                staged.unlink(missing_ok=True)


def isolated_ingest(source, destination, metadata, launcher=None, job_root=Path('./data/jobs'), progress=None):
    from packages.metricguard_core.common import SCHEMA, digest
    source, destination, job_root = Path(source), Path(destination), Path(job_root)
    suffix = source.suffix.lower()
    relational = 'relational_contract' in metadata
    allowed = {'.zip'} if relational else {'.csv', '.parquet', '.xlsx'} if 'flat_contract' in metadata else {'.csv', '.parquet'}
    if suffix not in allowed:
        raise ValueError('UNSUPPORTED_FORMAT')
    sidecar_destination = destination.with_name(destination.name + '.manifest.json')
    if destination.exists() or sidecar_destination.exists():
        raise ValueError('IMMUTABLE_SNAPSHOT_EXISTS')
    from packages.metricguard_core.flat import MAX_FLAT_BYTES, MAX_FLAT_ROWS
    byte_limit = MAX_FLAT_BYTES if 'flat_contract' in metadata else 25 * 1024 * 1024 + (4096 * 5 if relational else 0)
    if source.stat().st_size > byte_limit:
        raise ValueError('UPLOAD_TOO_LARGE')
    launcher = launcher or DockerLauncher()
    job_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='metricguard-ingest-', dir=job_root) as workspace:
        input_dir, output_dir = Path(workspace) / 'input', Path(workspace) / 'output'
        input_dir.mkdir()
        output_dir.mkdir()
        output_dir.chmod(0o777)
        shutil.copyfile(source, input_dir / f'source{suffix}')
        (input_dir / 'metadata.json').write_text(json.dumps(metadata, allow_nan=False), encoding='utf-8')
        process = launcher.start(input_dir, output_dir, 'ingest')
        try:
            if progress is None:
                code = process.wait(timeout=INGEST_SECONDS)
            else:
                started = last_beat = time.monotonic()
                first = True
                while process.poll() is None:
                    now = time.monotonic()
                    if first or now - last_beat >= 1:  # lease heartbeat about once a second
                        first, last_beat = False, now
                        if not progress():
                            launcher.stop(process)
                            raise ValueError('INGEST_LEASE_LOST')
                    if now - started >= INGEST_SECONDS:
                        launcher.stop(process)
                        raise ValueError('INGEST_TIMEOUT')
                    time.sleep(0.1)
                code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            launcher.stop(process)
            raise ValueError('INGEST_TIMEOUT')
        if code != 0:
            error_path = output_dir / 'error.json'
            if error_path.is_file() and error_path.stat().st_size <= 8192:
                try:
                    detail = json.loads(error_path.read_text(encoding='utf-8'))
                    if detail.get('code') in {'invalid_schema', 'invalid_row', 'invalid_csv', 'invalid_xlsx', 'invalid_parquet',
                            'too_many_rows', 'invalid_metadata', 'invalid_contract', 'invalid_mapping',
                            'file_too_large', 'unsupported_format', 'scale_mismatch', 'unsupported_precision',
                            'identical_operands', 'arithmetic_overflow', 'invalid_bundle', 'invalid_keys',
                            'invalid_amount', 'join_fanout'} and \
                            isinstance(detail.get('message'), str) and len(detail['message']) <= 500:
                        raise IngestValidationError(detail['code'].upper(), detail['message'])
                except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                    pass
            raise ValueError('INGEST_FAILED')
        manifest_path, artifact = output_dir / 'manifest.json', output_dir / 'snapshot.duckdb'
        sidecar = output_dir / 'snapshot.duckdb.manifest.json'
        if not manifest_path.is_file() or manifest_path.stat().st_size > 1024 * 1024 or not artifact.is_file() or not sidecar.is_file() or sidecar.stat().st_size > 1024 * 1024:
            raise ValueError('INGEST_OUTPUT_INVALID')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if file_hash(artifact) != manifest.get('database_hash'):
            raise ValueError('INGEST_HASH_MISMATCH')
        claimed_hash = manifest.get('manifest_hash')
        if claimed_hash != digest({key: value for key, value in manifest.items() if key != 'manifest_hash'}):
            raise ValueError('INGEST_MANIFEST_MISMATCH')
        if relational:
            from packages.metricguard_core.relational_snapshot import validate_manifest
            try:
                validate_manifest(manifest, metadata['relational_contract'], file_hash(source))
            except Exception as error:
                raise ValueError('INGEST_MANIFEST_MISMATCH') from error
        elif 'flat_contract' in metadata:
            from packages.metricguard_core.flat import validate_contract, flat_schema
            contract = validate_contract(metadata['flat_contract'])
            expected_schema = flat_schema(contract)
            if (manifest.get('execution_policy') != 'flat_snapshot_v1' or
                    manifest.get('contract_hash') != contract['contract_hash'] or
                    manifest.get('contract') != contract or
                    manifest.get('schema') != expected_schema or
                    manifest.get('primary_key') != contract['row_key'] or
                    manifest.get('scope') != 'entire_supplied_snapshot' or
                    type(manifest.get('row_count')) is not int or
                    not 0 <= manifest['row_count'] <= MAX_FLAT_ROWS):
                raise ValueError('INGEST_MANIFEST_MISMATCH')
        elif manifest.get('schema') != SCHEMA:
            raise ValueError('INGEST_MANIFEST_MISMATCH')
        # warehouse_source is lineage shown with the import (where the CSV came from); the runner never sees or checks it.
        declared = {key: value for key, value in metadata.items()
                    if key not in {'contract', 'mapping', 'flat_contract', 'relational_contract', 'warehouse_source'}}
        if any(manifest.get(key) != value for key, value in declared.items()) or manifest.get('source_hash') != file_hash(source):
            raise ValueError('INGEST_MANIFEST_MISMATCH')
        if 'contract' in metadata and (manifest.get('contract_hash') != metadata['contract']['contract_hash'] or
                                      manifest.get('mapping_hash') != metadata['mapping']['mapping_hash']):
            raise ValueError('INGEST_MANIFEST_MISMATCH')
        if json.loads(sidecar.read_text(encoding='utf-8')) != manifest:
            raise ValueError('INGEST_MANIFEST_MISMATCH')
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(artifact, destination)
        try:
            os.replace(sidecar, sidecar_destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return manifest


def main():
    from services.api.store import Store
    database_url = os.environ.get('METRICGUARD_DATABASE_URL', 'sqlite:///data/metricguard.db')
    data_dir = Path(os.environ.get('METRICGUARD_DATA_DIR', './data'))
    # Temporary job folders can live outside synced folders (e.g. OneDrive), which
    # makes the Docker bind-mount traffic much faster. Results still go to data_dir.
    job_root = Path(os.environ.get('METRICGUARD_JOB_DIR') or data_dir / 'jobs')
    store = Store(database_url)
    supervisor = Supervisor(store, DockerLauncher(), job_root,
                            worker_id=f'local-{os.getpid()}', data_dir=data_dir)
    from sqlalchemy.exc import OperationalError
    while True:
        # Several workers may share one queue (scripts\mg.ps1 -Workers); a briefly locked
        # SQLite file is retried rather than ending this worker. Leases recover any claim.
        try:
            store.worker_seen(supervisor.worker_id)
            worked = supervisor.run_once()
        except OperationalError as exc:
            print(f'{supervisor.worker_id}: database busy, retrying ({exc.__class__.__name__})', file=sys.stderr, flush=True)
            worked = False
            time.sleep(1)
        if not worked:
            time.sleep(0.25)


if __name__ == '__main__':
    main()
