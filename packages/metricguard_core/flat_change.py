"""One flat definition evaluated on two snapshots of the same dataset contract.

The baseline and target are two data loads (for example September and October) frozen
under one contract. Every record is joined on the contract row key; its contribution in
each load and its change label explain the whole difference, which must reconcile exactly.
"""

import json
import time
from decimal import Decimal, localcontext
from pathlib import Path

import duckdb
import sqlglot

from .free_tier import check_rows
from .common import CoreError, digest, file_hash
from .flat import (FULL_EVIDENCE_ROWS, MEASURE_POLICY_V8, POLICY, _check_ir, _connect, _query,
                   amount_column, validate_contract)
from .flat_boolean import PREDICATE_POLICY
from .snapshot import manifest_path

CHANGE_POLICY = 'flat-snapshot-change-v1'
CHANGE_KINDS = ('added', 'removed', 'value_changed', 'entered_filter', 'left_filter', 'unchanged')
SUPPORTED_CONTRACT_VERSIONS = (3, 4, 5, 8)
CHANGE_FIELDS = {'baseline_present', 'target_present', 'change'}


def _error(code, message):
    raise CoreError(code, message)


def classify(baseline_present, target_present, baseline_included, target_included, baseline_contribution,
             target_contribution, baseline_null_term=False, target_null_term=False):
    """The change label of one record; shared by the engine and the evidence validator."""
    if not baseline_present:
        return 'added'
    if not target_present:
        return 'removed'
    if baseline_included and not target_included:
        return 'left_filter'
    if target_included and not baseline_included:
        return 'entered_filter'
    if baseline_contribution != target_contribution or baseline_null_term != target_null_term:
        return 'value_changed'
    return 'unchanged'


def check_change_row(row, baseline_contribution, target_contribution):
    """Validate the snapshot-change fields of one evidence row (ValueError when inconsistent)."""
    if not CHANGE_FIELDS.issubset(row):
        raise ValueError('EVIDENCE_INVALID')
    bp, tp = row['baseline_present'], row['target_present']
    if type(bp) is not bool or type(tp) is not bool or not (bp or tp) or row['change'] not in CHANGE_KINDS:
        raise ValueError('EVIDENCE_INVALID')
    # A record absent from a load cannot be counted or carry a value in that load.
    if ((not bp and (row['baseline_included'] or baseline_contribution != 0)) or
            (not tp and (row['target_included'] or target_contribution != 0))):
        raise ValueError('EVIDENCE_INVALID')
    expected = classify(bp, tp, row['baseline_included'], row['target_included'], baseline_contribution,
                        target_contribution, row['baseline_null_term'], row['target_null_term'])
    if row['change'] != expected:
        raise ValueError('EVIDENCE_INVALID')


def open_snapshot(database):
    """Integrity-checked manifest of one frozen flat snapshot."""
    database = Path(database)
    try:
        manifest = json.loads(manifest_path(database).read_text(encoding='utf-8'))
        expected = manifest.pop('manifest_hash')
        if (digest(manifest) != expected or file_hash(database) != manifest['database_hash'] or
                manifest.get('mode') != 'flat_snapshot' or manifest.get('policy') != POLICY):
            _error('snapshot_integrity', 'Flat snapshot or manifest changed')
        manifest['manifest_hash'] = expected
    except (OSError, ValueError, KeyError) as exc:
        if isinstance(exc, CoreError):
            raise
        raise CoreError('snapshot_missing', 'Flat snapshot or manifest is unavailable') from exc
    return manifest


class _Load:
    """Streams one load's (key, included, contribution, null term) rows in key order and adds up its total."""

    def __init__(self, con, ir, contract):
        measure = ir['measure'] if contract['version'] == 8 else contract.get('measure')
        sql, params = _query(ir, contract['row_key'], measure, contract=contract)
        self.query_hash = digest({'sql': sql, 'params': params})
        self.cursor = con.execute(sql, params)
        self.batch, self.position = [], 0
        self.total, self.terms, self.rows, self.previous = Decimal(0), 0, 0, None

    def next(self):
        if self.position == len(self.batch):
            self.batch, self.position = self.cursor.fetchmany(10_000), 0
            if not self.batch:
                return None
        key, flag, amount = self.batch[self.position]
        self.position += 1
        if self.previous is not None and key <= self.previous:
            _error('snapshot_integrity', 'Snapshot rows are not in strict row-key order')
        self.previous = key
        self.rows += 1
        included = flag is True
        if included and amount is not None:
            amount = Decimal(amount)
            self.total += amount
            self.terms += 1
        return key, (included, amount if included and amount is not None else Decimal(0), included and amount is None)

    def value(self, ir, contract):
        counting = ir.get('aggregate') == 'count'
        value = self.total if self.terms else (Decimal(0) if counting else None)
        if contract['version'] == 8 and value is not None and not counting:
            scale = amount_column(contract, ir['measure'])['scale']
            if abs(value * (Decimal(10) ** scale)) > (Decimal(10) ** 38 - 1):
                _error('arithmetic_overflow', f'Endpoint aggregate exceeds DECIMAL(38, {scale}) capacity')
        return value


def check_definition(ir, contract):
    """Refuse definitions or datasets this policy does not cover; returns the checked IR."""
    if contract['version'] not in SUPPORTED_CONTRACT_VERSIONS:
        _error('unsupported_snapshot_change',
               'Comparing two data loads supports plain totals (SUM) and record counts (COUNT(*)); '
               'subtraction and CASE datasets are not supported yet.')
    ir = _check_ir(ir, contract)
    if ir.get('aggregate', 'sum') not in ('sum', 'count'):
        _error('unsupported_snapshot_change',
               'Comparing two data loads supports totals (SUM) and record counts (COUNT(*)); '
               'distinct counts, ratios and averages are not supported yet.')
    return ir


def compare_snapshots(baseline_database, target_database, ir, context):
    """Evaluate one flat SUM or COUNT(*) definition on two snapshots and explain the change by record."""
    started = time.monotonic()
    baseline_manifest, target_manifest = open_snapshot(baseline_database), open_snapshot(target_database)
    check_rows(baseline_manifest['row_count'])
    check_rows(target_manifest['row_count'])
    contract = validate_contract(baseline_manifest['contract'])
    if validate_contract(target_manifest['contract'])['contract_hash'] != contract['contract_hash']:
        _error('snapshot_contract_mismatch',
               'The two snapshots were loaded under different dataset contracts (row key, columns or units differ); '
               'compare loads of the same dataset.')
    ir = check_definition(ir, contract)
    if not isinstance(context, dict) or any(context.get(key) != value for key, value in {
            'mode': 'flat_snapshot', 'scope': contract['scope'], 'contract_hash': contract['contract_hash'],
            'unit_label': contract['unit_label'], 'values_comparable': True, 'comparison': 'snapshot_change'}.items()):
        _error('not_comparable', 'Snapshot-change context differs from the contract')
    for key, manifest in (('manifest_hash', baseline_manifest), ('target_manifest_hash', target_manifest)):
        if context.get(key) and context[key] != manifest['manifest_hash']:
            _error('snapshot_integrity', f'{key} differs from the frozen snapshot')
    counting = ir.get('aggregate') == 'count'
    large = any(manifest['row_count'] > FULL_EVIDENCE_ROWS for manifest in (baseline_manifest, target_manifest))
    from .disk_evidence import DiskRows, STORAGE_POLICY
    # Only records that changed are listed; unchanged records add the same amount to both
    # loads and nothing to the change. change_summary still counts every record by kind.
    evidence = DiskRows() if large else []
    absent = (False, Decimal(0), False)
    summary = {kind: {'records': 0, 'delta': Decimal(0)} for kind in CHANGE_KINDS}
    listed_delta = Decimal(0)
    with localcontext() as dc, _connect(baseline_database) as b_con, _connect(target_database) as t_con:
        dc.prec = 100
        try:
            before, after = _Load(b_con, ir, contract), _Load(t_con, ir, contract)
            b_row, t_row = before.next(), after.next()
            while b_row is not None or t_row is not None:
                if t_row is None or (b_row is not None and b_row[0] < t_row[0]):
                    key, (ai, ac, an), (bi, bc, bn), bp, tp = b_row[0], b_row[1], absent, True, False
                    b_row = before.next()
                elif b_row is None or t_row[0] < b_row[0]:
                    key, (ai, ac, an), (bi, bc, bn), bp, tp = t_row[0], absent, t_row[1], False, True
                    t_row = after.next()
                else:
                    key, (ai, ac, an), (bi, bc, bn), bp, tp = b_row[0], b_row[1], t_row[1], True, True
                    b_row, t_row = before.next(), after.next()
                check_rows(sum(item['records'] for item in summary.values()) + 1, exact=False)
                change = classify(bp, tp, ai, bi, ac, bc, an, bn)
                summary[change]['records'] += 1
                summary[change]['delta'] += bc - ac
                if change == 'unchanged':
                    continue
                listed_delta += bc - ac
                membership = ('included_in_both' if ai and bi else 'baseline_only' if ai else
                              'target_only' if bi else 'included_in_neither')
                evidence.append({'order_id': key, 'baseline_included': ai, 'target_included': bi,
                                 'baseline_contribution': format(ac, 'f'), 'target_contribution': format(bc, 'f'),
                                 'delta': format(bc - ac, 'f'), 'baseline_null_term': an, 'target_null_term': bn,
                                 'membership': membership, 'baseline_present': bp, 'target_present': tp,
                                 'change': change})
        except duckdb.Error as exc:
            if 'overflow' in str(exc).lower():
                _error('arithmetic_overflow', f'Row or aggregate arithmetic overflow: {exc}')
            raise
        for load, manifest in ((before, baseline_manifest), (after, target_manifest)):
            if load.rows != manifest['row_count']:
                _error('snapshot_integrity', 'Snapshot row count differs from its manifest')
        av, bv = before.value(ir, contract), after.value(ir, contract)
        baseline_query, target_query = before.query_hash, after.query_hash
        gap = bv - av if av is not None and bv is not None else None
        if summary['unchanged']['delta'] != 0:
            _error('reconciliation_failure', 'An unchanged record changed its contribution')
        if gap is not None and listed_delta != gap:
            _error('reconciliation_failure', 'Snapshot-change evidence does not reconcile')
    number = lambda value: None if value is None else format(value, 'f')
    filter_key = 'predicate_tree' if contract['version'] in (5, 8) else 'predicates'
    step = {'rule': 'snapshot_change', 'baseline': baseline_manifest['manifest_hash'],
            'target': target_manifest['manifest_hash'],
            'changed': baseline_manifest['manifest_hash'] != target_manifest['manifest_hash'],
            'definition': ir[filter_key], 'position': 1, 'previous_value': number(av), 'next_value': number(bv),
            'value_before': number(av), 'value_after': number(bv), 'contribution': number(gap)}
    unit = (contract['unit_label'] if counting or contract['version'] != 8
            else amount_column(contract, ir['measure'])['unit_label'])
    return {'baseline_value': number(av), 'target_value': number(bv), 'delta_target_minus_baseline': number(gap),
            'absolute_gap': number(abs(gap)) if gap is not None else None, 'relative_change_percent_display': None,
            'residual': '0' if gap is not None else None, 'currency': None, 'unit_label': unit,
            'measure_meaning': contract['measure_meaning'], 'numeric_tolerance': '0', 'method': 'flat_snapshot_change',
            'definition_status': 'same_supported_representation',
            'value_status': 'defined' if gap is not None else 'undefined',
            'explanation_status': 'complete_snapshot_change' if gap is not None else 'unavailable',
            'steps': [step] if gap is not None else [], 'shapley': None, 'evidence': evidence,
            'definition_differences': [],
            'change_summary': {kind: {'records': item['records'], 'delta': format(item['delta'], 'f')}
                               for kind, item in summary.items()},
            'provenance': {'execution_policy': POLICY, 'change_policy': CHANGE_POLICY, 'comparison': 'snapshot_change',
                           'baseline_ir_hash': ir['ir_hash'], 'target_ir_hash': ir['ir_hash'],
                           'snapshot_manifest_hash': baseline_manifest['manifest_hash'],
                           'database_hash': baseline_manifest['database_hash'],
                           'source_hash': baseline_manifest['source_hash'],
                           'target_snapshot_manifest_hash': target_manifest['manifest_hash'],
                           'target_database_hash': target_manifest['database_hash'],
                           'target_source_hash': target_manifest['source_hash'],
                           'contract_hash': contract['contract_hash'], 'parser_version': sqlglot.__version__,
                           'duckdb_version': duckdb.__version__, 'engine_version': POLICY,
                           **({'predicate_policy': PREDICATE_POLICY, 'contract_version': contract['version']}
                              if contract['version'] in (5, 8) else {}),
                           **({'measure_policy': MEASURE_POLICY_V8} if contract['version'] == 8 else {}),
                           'context': context, 'baseline_query_hash': baseline_query, 'target_query_hash': target_query,
                           'query_count': 2, 'elapsed_ms': round((time.monotonic() - started) * 1000),
                           'evidence_unit': 'row_key',
                           **({'evidence_storage_policy': STORAGE_POLICY} if large else {})},
            'limitations': ['Records are matched across the two loads by the dataset row key; a record whose key '
                            'changed between loads appears as one removed and one added record.',
                            'The comparison covers exactly these two frozen snapshots.',
                            f'Only the {len(evidence):,} records that were added, removed or changed are listed; the other '
                            f'{summary["unchanged"]["records"]:,} records are unchanged and contribute nothing to the change.',
                            'Unknown currency remains unknown; no verified financial loss is claimed.'] +
                           (['An endpoint SUM is NULL because no non-NULL terms matched; the change is undefined.']
                            if gap is None else []) +
                           (['COUNT(*) counts records; the dataset unit does not apply to these totals.'] if counting else []),
            **({'aggregate': 'count'} if counting else {})}
