"""Bounded JSONL row evidence shared by the isolated runner and local API."""

from contextlib import nullcontext
import hashlib
import json
from pathlib import Path

from packages.metricguard_core.numeric import ScaledInteger, MAX_NUMERIC_STR_LEN, MAX_NUMERIC_SCALE
from packages.metricguard_core.flat_case_evidence import (
    CASE_FIELDS, CaseEvidenceValidator, case_binding, validate_case_evidence_row,
)
from packages.metricguard_core.flat_change import CHANGE_FIELDS, check_change_row
from packages.metricguard_core.relational_evidence import (
    RelationalEvidenceValidator, binding as relational_binding,
)
from packages.metricguard_core.relational_explanation import explain_row


MAX_EVIDENCE_BYTES = 256 * 1024 * 1024
MAX_EVIDENCE_ROWS = 1_000_000
MAX_EVIDENCE_ROW_BYTES = 64 * 1024


def artifact_limits(manifest, expected_result=None):
    """Opt-in flat storage capability; historical artifacts retain their old bounds."""
    from packages.metricguard_core.disk_evidence import STORAGE_POLICY, MAX_DISK_ROWS, MAX_DISK_BYTES
    policy = manifest.get('storage_policy')
    if policy is None:
        return MAX_EVIDENCE_ROWS, MAX_EVIDENCE_BYTES
    provenance = (expected_result or {}).get('provenance') or {}
    if (policy != STORAGE_POLICY or provenance.get('execution_policy') != 'flat-snapshot-v1' or
            provenance.get('evidence_storage_policy') != STORAGE_POLICY):
        raise ValueError('EVIDENCE_INVALID')
    return MAX_DISK_ROWS, MAX_DISK_BYTES
# Large write buffer: each write call into a Docker Desktop bind mount is slow on Windows.
WRITE_BUFFER = 4 * 1024 * 1024
ROW_FIELDS = {'order_id', 'baseline_included', 'target_included',
              'baseline_contribution', 'target_contribution', 'delta',
              'baseline_null_term', 'target_null_term', 'membership'}


def _case_binding(context):
    return case_binding(context)


ALLOWED_EXPRESSION_FIELDS = {'baseline_operands', 'target_operands',
                             'baseline_expression_value', 'target_expression_value'}


def _decimal(value):
    try:
        return ScaledInteger.parse(value, max_len=MAX_NUMERIC_STR_LEN, max_scale=MAX_NUMERIC_SCALE)
    except ValueError as error:
        raise ValueError('EVIDENCE_INVALID') from error


def aggregate_contexts(contract, baseline_ir, target_ir):
    """(ratio_context, distinct_context) for single-table runs, from frozen definitions only."""
    policy = (baseline_ir or {}).get('aggregate_policy')
    if policy != (target_ir or {}).get('aggregate_policy'):
        raise ValueError('POLICY_MISMATCH')
    context = {'contract': contract, 'baseline': baseline_ir, 'target': target_ir}
    return (context if policy == 'flat-ratio-v1' else None,
            context if policy == 'flat-distinct-v1' else None)


def expected_artifact_delta(result, ratio_context):
    """The exact total the evidence rows must add up to."""
    if ratio_context is not None:
        from packages.metricguard_core.flat_ratio_evidence import expected_delta
        return expected_delta(result)
    return result['delta_target_minus_baseline']


def _aggregate_checker(ratio_context, distinct_context, expected_result):
    """Validator for ratio or COUNT(DISTINCT) evidence rows, or None for other artifacts."""
    if ratio_context is not None and distinct_context is not None:
        raise ValueError('EVIDENCE_INVALID')
    if ratio_context is not None:
        from packages.metricguard_core.flat_ratio_evidence import RatioEvidenceValidator
        return RatioEvidenceValidator(ratio_context, expected_result)
    if distinct_context is not None:
        from packages.metricguard_core.flat_distinct_evidence import DistinctEvidenceValidator
        return DistinctEvidenceValidator(distinct_context, expected_result)
    return None


def _aggregate_bindings(ratio_context, distinct_context):
    from packages.metricguard_core import flat_distinct_evidence, flat_ratio_evidence
    return {'ratio_binding': None if ratio_context is None else flat_ratio_evidence.binding(ratio_context),
            'distinct_binding': None if distinct_context is None else flat_distinct_evidence.binding(distinct_context)}


def _row_delta(row, measure_expression=None, case_context=None, *, case_validator=None, relational_context=None,
               aggregate_validator=False):
    """Validate evidence row schema, exact difference arithmetic, and metadata.

    Carries frozen measure_expression context (minuend, subtrahend identities)
    to enforce ordered signed subtraction (minuend - subtrahend == expression_val)
    independent of JSON key ordering.
    """
    if relational_context is not None or aggregate_validator:
        if measure_expression is not None or case_context is not None or case_validator is None:
            raise ValueError('EVIDENCE_INVALID')
        case_validator.add(row)
        return _decimal(row['delta']).to_decimal()
    if not isinstance(row, dict):
        raise ValueError('EVIDENCE_INVALID')
    row_keys = set(row)
    if not ROW_FIELDS.issubset(row_keys) or (row_keys - ROW_FIELDS - (CASE_FIELDS if case_context is not None else ALLOWED_EXPRESSION_FIELDS | CHANGE_FIELDS)):
        raise ValueError('EVIDENCE_INVALID')
    if (not isinstance(row['order_id'], str) or
            any(type(row[key]) is not bool for key in (
                'baseline_included', 'target_included',
                'baseline_null_term', 'target_null_term'))):
        raise ValueError('EVIDENCE_INVALID')
    baseline, target = row['baseline_included'], row['target_included']
    membership = ('included_in_both' if baseline and target else
                  'baseline_only' if baseline else 'target_only' if target else
                  'included_in_neither')
    if (row['membership'] != membership or
            (row['baseline_null_term'] and not baseline) or
            (row['target_null_term'] and not target)):
        raise ValueError('EVIDENCE_INVALID')

    if case_context is not None:
        if measure_expression is not None or not CASE_FIELDS.issubset(row_keys):
            raise ValueError('EVIDENCE_INVALID')
        if case_validator is not None:
            case_validator.add(row)
        else:
            validate_case_evidence_row(row, case_context)

    delta = _decimal(row.get('delta'))
    target_contrib = _decimal(row.get('target_contribution'))
    baseline_contrib = _decimal(row.get('baseline_contribution'))
    if target_contrib - baseline_contrib != delta:
        raise ValueError('EVIDENCE_INVALID')

    # Null terms on included rows contribute zero
    if row['baseline_included'] and row['baseline_null_term'] and baseline_contrib != 0:
        raise ValueError('EVIDENCE_INVALID')
    if row['target_included'] and row['target_null_term'] and target_contrib != 0:
        raise ValueError('EVIDENCE_INVALID')

    # Filter-excluded rows contribute zero
    if not row['baseline_included'] and baseline_contrib != 0:
        raise ValueError('EVIDENCE_INVALID')
    if not row['target_included'] and target_contrib != 0:
        raise ValueError('EVIDENCE_INVALID')

    # Two-snapshot rows: presence in each load and the change label must agree with the values
    if CHANGE_FIELDS.intersection(row_keys):
        if ALLOWED_EXPRESSION_FIELDS.intersection(row_keys):
            raise ValueError('EVIDENCE_INVALID')
        check_change_row(row, baseline_contrib, target_contrib)

    # Expression metadata validation
    expr_fields_present = ALLOWED_EXPRESSION_FIELDS.intersection(row_keys)
    if expr_fields_present:
        # Exactly all 4 expression fields must be present if any is present
        if expr_fields_present != ALLOWED_EXPRESSION_FIELDS:
            raise ValueError('EVIDENCE_INVALID')

        # Frozen measure_expression context is required to validate expression semantics
        if not isinstance(measure_expression, dict) or measure_expression.get('op') != 'sub':
            raise ValueError('EVIDENCE_INVALID')
        min_col = measure_expression.get('minuend')
        sub_col = measure_expression.get('subtrahend')
        if not isinstance(min_col, str) or not isinstance(sub_col, str) or not min_col or not sub_col:
            raise ValueError('EVIDENCE_INVALID')
        declared_ops = {min_col, sub_col}

        for op_key, expr_key, null_term_key, inc_key, contrib_val in (
            ('baseline_operands', 'baseline_expression_value', 'baseline_null_term', 'baseline_included', baseline_contrib),
            ('target_operands', 'target_expression_value', 'target_null_term', 'target_included', target_contrib),
        ):
            op_dict = row[op_key]
            expr_val = row[expr_key]

            if not isinstance(op_dict, dict):
                raise ValueError('EVIDENCE_INVALID')

            # Exactly the declared operands must be present
            if set(op_dict.keys()) != declared_ops:
                raise ValueError('EVIDENCE_INVALID')

            min_raw = op_dict[min_col]
            sub_raw = op_dict[sub_col]

            min_si = _decimal(min_raw) if min_raw is not None else None
            sub_si = _decimal(sub_raw) if sub_raw is not None else None

            # NULL operands produce NULL expression values
            if min_raw is None or sub_raw is None:
                if expr_val is not None:
                    raise ValueError('EVIDENCE_INVALID')
                if row[inc_key] and not row[null_term_key]:
                    raise ValueError('EVIDENCE_INVALID')
            else:
                # Non-NULL operands
                if expr_val is None:
                    if row[inc_key] and not row[null_term_key]:
                        raise ValueError('EVIDENCE_INVALID')
                else:
                    expr_si = _decimal(expr_val)
                    # Ordered subtraction using frozen identities (independent of dict key order)
                    if min_si - sub_si != expr_si:
                        raise ValueError('EVIDENCE_INVALID')

                    # Included non-NULL contribution equals signed expression value
                    if row[inc_key] and not row[null_term_key]:
                        if contrib_val != expr_si:
                            raise ValueError('EVIDENCE_INVALID')

    return delta.to_decimal()


def write_artifact(path, rows, measure_expression=None, case_context=None, relational_context=None,
                   ratio_context=None, distinct_context=None, expected_result=None, *, check_rows=True):
    """Write one JSON object per line without buffering the whole artifact.

    check_rows=False (used inside the isolated runner) skips the per-row checks here:
    the supervisor treats runner output as untrusted and verify_artifact re-checks every
    byte, row and total before anything is published, so checking twice only adds time.
    """
    storage_policy = getattr(rows, 'artifact_policy', None)
    storage = {'storage_policy': storage_policy} if storage_policy is not None else {}
    row_limit, byte_limit = artifact_limits(storage, expected_result)
    binding = _case_binding(case_context) if case_context is not None else None
    if binding is not None and measure_expression is not None:
        raise ValueError('EVIDENCE_INVALID')
    if relational_context is not None and (case_context is not None or measure_expression is not None):
        raise ValueError('EVIDENCE_INVALID')
    aggregate = _aggregate_checker(ratio_context, distinct_context, expected_result) if check_rows else None
    if aggregate is not None and (case_context is not None or relational_context is not None or measure_expression is not None):
        raise ValueError('EVIDENCE_INVALID')
    path = Path(path)
    digest = hashlib.sha256()
    byte_count = row_count = 0
    delta_sum = ScaledInteger.zero()
    try:
        with (nullcontext() if not check_rows else aggregate if aggregate is not None else RelationalEvidenceValidator(relational_context) if relational_context is not None else CaseEvidenceValidator(case_context) if binding is not None else nullcontext()) as checker, path.open('xb', buffering=WRITE_BUFFER) as output:
            for row in rows:
                if check_rows:
                    _row_delta(row, measure_expression=measure_expression, case_context=case_context, case_validator=checker,
                               relational_context=relational_context, aggregate_validator=aggregate is not None)
                delta_sum += ScaledInteger.parse(row['delta'])
                encoded = (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
                if len(encoded) > MAX_EVIDENCE_ROW_BYTES or row_count >= row_limit or byte_count + len(encoded) > byte_limit:
                    raise ValueError('EVIDENCE_LIMIT')
                output.write(encoded)
                digest.update(encoded)
                byte_count += len(encoded)
                row_count += 1
            if aggregate is not None:
                checker.finish(expected_result)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    delta_sum_str = delta_sum.to_fixed_string()
    manifest = {'sha256': digest.hexdigest(), 'row_count': row_count,
                'byte_count': byte_count, 'delta_sum': delta_sum_str, **storage}
    if measure_expression is not None:
        manifest['measure_expression'] = measure_expression
    if binding is not None:
        manifest['case_binding'] = binding
    if relational_context is not None:
        manifest['relational_binding'] = relational_binding(relational_context)
    if ratio_context is not None or distinct_context is not None:
        manifest.update({k: v for k, v in _aggregate_bindings(ratio_context, distinct_context).items() if v is not None})
    return manifest


def verify_artifact(path, manifest, expected_delta, offset=0, limit=0, measure_expression=None, case_context=None, expected_result=None, relational_context=None, *, include_join_evidence=False,
                    ratio_context=None, distinct_context=None):
    """Scan and verify all bytes before returning at most ``limit`` requested rows."""
    if not isinstance(manifest, dict):
        raise ValueError('EVIDENCE_INVALID')
    row_limit, byte_limit = artifact_limits(manifest, expected_result)
    if (not isinstance(manifest, dict) or not isinstance(manifest.get('sha256'), str) or
            type(manifest.get('row_count')) is not int or type(manifest.get('byte_count')) is not int or
            not 0 <= manifest['row_count'] <= row_limit or
            not 0 <= manifest['byte_count'] <= byte_limit or
            offset < 0 or limit < 0 or limit > 100):
        raise ValueError('EVIDENCE_INVALID')
    claimed_sum = _decimal(manifest.get('delta_sum'))
    if include_join_evidence and relational_context is None:
        raise ValueError('EVIDENCE_INVALID')
    if expected_delta is not None and claimed_sum != _decimal(expected_delta):
        raise ValueError('EVIDENCE_INVALID')

    if case_context is not None:
        if (measure_expression is not None or 'measure_expression' in manifest or
                manifest.get('case_binding') != _case_binding(case_context) or
                not isinstance(expected_result, dict)):
            raise ValueError('EVIDENCE_INVALID')
    elif 'case_binding' in manifest:
        raise ValueError('EVIDENCE_INVALID')
    if relational_context is not None:
        if (case_context is not None or measure_expression is not None or 'measure_expression' in manifest or
                manifest.get('relational_binding') != relational_binding(relational_context) or not isinstance(expected_result, dict)):
            raise ValueError('EVIDENCE_INVALID')
    elif 'relational_binding' in manifest:
        raise ValueError('EVIDENCE_INVALID')
    if (ratio_context is not None or distinct_context is not None) and (
            case_context is not None or relational_context is not None or measure_expression is not None or
            'measure_expression' in manifest or not isinstance(expected_result, dict)):
        raise ValueError('EVIDENCE_INVALID')
    for name, value in _aggregate_bindings(ratio_context, distinct_context).items():
        if manifest.get(name) != value:  # a binding must be present exactly when its context is
            raise ValueError('EVIDENCE_INVALID')
    aggregate = _aggregate_checker(ratio_context, distinct_context, expected_result)

    manifest_me = manifest.get('measure_expression')
    if measure_expression is not None and manifest_me is not None and manifest_me != measure_expression:
        raise ValueError('EVIDENCE_INVALID')
    effective_me = measure_expression if measure_expression is not None else manifest_me

    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size != manifest['byte_count']:
            raise ValueError('EVIDENCE_INVALID')
        digest = hashlib.sha256()
        byte_count = row_count = 0
        delta_sum = ScaledInteger.zero()
        page = []
        with (aggregate if aggregate is not None else RelationalEvidenceValidator(relational_context, include_join_evidence=include_join_evidence) if relational_context is not None else CaseEvidenceValidator(case_context) if case_context is not None else nullcontext()) as checker, path.open('rb') as source:
            while line := source.readline(MAX_EVIDENCE_ROW_BYTES + 1):
                if (len(line) > MAX_EVIDENCE_ROW_BYTES or not line.endswith(b'\n') or
                        row_count >= row_limit or byte_count + len(line) > byte_limit):
                    raise ValueError('EVIDENCE_INVALID')
                digest.update(line)
                byte_count += len(line)
                row = json.loads(line.decode('utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
                _row_delta(row, measure_expression=effective_me, case_context=case_context, case_validator=checker,
                           relational_context=relational_context, aggregate_validator=aggregate is not None)
                delta_sum += ScaledInteger.parse(row['delta'])
                if offset <= row_count < offset + limit:
                    page.append(row)
                row_count += 1
            if checker is not None:
                checker.finish(expected_result)
        if (digest.hexdigest() != manifest['sha256'] or byte_count != manifest['byte_count'] or
                row_count != manifest['row_count'] or delta_sum != claimed_sum):
            raise ValueError('EVIDENCE_INVALID')
        if include_join_evidence:
            return {'items': page, 'total': row_count,
                    'join_evidence': {'summary': checker.explanation.report(expected_result, manifest),
                                      'rows': [explain_row(relational_context, row) for row in page]}}
        return page
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('EVIDENCE_INVALID') from error
