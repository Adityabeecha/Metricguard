"""Claim runs and launch the fixed, restricted analytical container."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid
from decimal import Decimal, InvalidOperation, localcontext

from packages.metricguard_core.numeric import ScaledInteger, MAX_NUMERIC_STR_LEN
from services.supervisor.evidence_artifact import verify_artifact, _row_delta
from packages.metricguard_core.flat_case_evidence import CaseEvidenceValidator, validate_case_result


MAX_OUTPUT_BYTES = 25 * 1024 * 1024
RUN_SECONDS = 60


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


def validate_result(result, case_context=None, relational_context=None):
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
    if result['evidence']:
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
        input_dir = Path(input_dir).resolve()
        output_dir = Path(output_dir).resolve()
        name = f'metricguard-{uuid.uuid4().hex}'
        args = [
            'docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--name', name,
            '--user', '10001:10001', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--memory', '1g',
            '--cpus', '1', '--pids-limit', '64', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
            '--mount', f'type=bind,src={input_dir},dst=/job/input,readonly',
            '--mount', f'type=bind,src={output_dir},dst=/job/output',
            self.image, *([] if command is None else [command]),
        ]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.container_name = name
        return process

    def stop(self, process):
        name = getattr(process, 'container_name', None)
        if name:
            subprocess.run(['docker', 'kill', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
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
                started = time.monotonic()
                while process.poll() is None:
                    self.store.worker_seen(self.worker_id)
                    if not self.store.heartbeat_import(ident, self.worker_id, attempt):
                        self.launcher.stop(process)
                        raise ValueError('PREVIEW_LEASE_LOST')
                    if time.monotonic() - started >= RUN_SECONDS:
                        self.launcher.stop(process)
                        raise ValueError('PREVIEW_TIMEOUT')
                    time.sleep(1)
                if process.wait(timeout=5) != 0:
                    error_path = output_dir / 'error.json'
                    if error_path.is_file() and error_path.stat().st_size <= 8192:
                        detail = json.loads(error_path.read_text(encoding='utf-8'))
                        if (detail.get('code') in {'invalid_schema', 'invalid_row', 'invalid_csv',
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

    def _publish_context(self, output_dir, published_evidence, contract, evidence_dir, run_id, attempt):
        """Validate and publish the optional record-context file; never fails the run."""
        source = output_dir / 'context.jsonl'
        if not source.is_file():
            return None
        target = evidence_dir / f'{run_id}-{attempt}.context.jsonl'
        try:
            from services.supervisor.row_context import validate_context
            info = validate_context(source, published_evidence, list(contract.get('categories', [])))
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
            job = {'snapshot_hash': expected, 'baseline_ir': baseline['ir'],
                   'target_ir': target['ir'], 'context': run['context'],
                   'execution_policy': run.get('execution_policy', 'retail_v1')}
            (input_dir / 'job.json').write_text(json.dumps(job, allow_nan=False), encoding='utf-8')
            process = self.launcher.start(input_dir, output_dir)
            started = time.monotonic()
            while process.poll() is None:
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
                if time.monotonic() - started >= RUN_SECONDS:
                    self.launcher.stop(process)
                    raise ValueError('RUN_TIMEOUT')
                time.sleep(1)
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
                result = validate_result(result, case_context=case_context, relational_context=relational_context)
                if 'evidence_artifact' not in result:
                    raise ValueError('missing evidence artifact')
                artifact = output_dir / 'evidence.jsonl'
                measure_expr = None if case_context is not None else baseline.get('ir', {}).get('measure_expression')
                if case_context is not None:
                    expected_provenance = {'database_hash': expected,
                                           'source_hash': snapshot['manifest']['source_hash'],
                                           'snapshot_manifest_hash': snapshot['manifest']['manifest_hash']}
                    if any(result['provenance'].get(k) != v for k, v in expected_provenance.items()):
                        raise ValueError('CASE result snapshot binding differs')
                verify_artifact(artifact, result['evidence_artifact'], result['delta_target_minus_baseline'], measure_expression=measure_expr, case_context=case_context, expected_result=result, relational_context=relational_context)
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
                context_info = self._publish_context(output_dir, published, contract, evidence_dir, run_id, attempt)
                if not self.store.finish(run_id, self.worker_id, attempt, result=result):
                    published.unlink(missing_ok=True)
                    did_publish = False
                elif context_info is not None and hasattr(self.store, 'record'):
                    try:
                        self.store.record('evidence_context', {'run_id': run_id, 'attempt': attempt, **context_info},
                                          project_id=run.get('project_id'))
                    except Exception:
                        pass
            except Exception:
                if did_publish:
                    published.unlink(missing_ok=True)
                raise
            finally:
                staged.unlink(missing_ok=True)


def isolated_ingest(source, destination, metadata, launcher=None, job_root=Path('./data/jobs'), progress=None):
    from packages.metricguard_core.common import SCHEMA, digest
    source, destination, job_root = Path(source), Path(destination), Path(job_root)
    suffix = source.suffix.lower()
    relational = 'relational_contract' in metadata
    if suffix not in ({'.zip'} if relational else {'.csv', '.parquet'}):
        raise ValueError('UNSUPPORTED_FORMAT')
    sidecar_destination = destination.with_name(destination.name + '.manifest.json')
    if destination.exists() or sidecar_destination.exists():
        raise ValueError('IMMUTABLE_SNAPSHOT_EXISTS')
    if source.stat().st_size > 25 * 1024 * 1024 + (4096 if relational else 0):
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
                code = process.wait(timeout=RUN_SECONDS)
            else:
                started = time.monotonic()
                while process.poll() is None:
                    if not progress():
                        launcher.stop(process)
                        raise ValueError('INGEST_LEASE_LOST')
                    if time.monotonic() - started >= RUN_SECONDS:
                        launcher.stop(process)
                        raise ValueError('INGEST_TIMEOUT')
                    time.sleep(1)
                code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            launcher.stop(process)
            raise ValueError('INGEST_TIMEOUT')
        if code != 0:
            error_path = output_dir / 'error.json'
            if error_path.is_file() and error_path.stat().st_size <= 8192:
                try:
                    detail = json.loads(error_path.read_text(encoding='utf-8'))
                    if detail.get('code') in {'invalid_schema', 'invalid_row', 'invalid_csv', 'invalid_parquet',
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
                    not 0 <= manifest['row_count'] <= 100_000):
                raise ValueError('INGEST_MANIFEST_MISMATCH')
        elif manifest.get('schema') != SCHEMA:
            raise ValueError('INGEST_MANIFEST_MISMATCH')
        declared = {key: value for key, value in metadata.items() if key not in {'contract', 'mapping', 'flat_contract', 'relational_contract'}}
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
    while True:
        store.worker_seen(supervisor.worker_id)
        if not supervisor.run_once():
            time.sleep(0.5)


if __name__ == '__main__':
    main()
