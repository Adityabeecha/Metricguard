"""Verifier for COUNT(DISTINCT) entity evidence and its record membership file."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .flat_distinct import DISTINCT_POLICY

ROW_FIELDS = {'order_id', 'baseline_included', 'target_included', 'baseline_contribution', 'target_contribution',
              'delta', 'baseline_null_term', 'target_null_term', 'membership',
              'baseline_supporting_rows', 'target_supporting_rows'}
MAX_MEMBER_LINE_BYTES = 2048
MAX_MEMBERS_BYTES = 64 * 1024 * 1024


def _invalid():
    raise ValueError('EVIDENCE_INVALID')


def binding(context):
    baseline, target = context['baseline'], context['target']
    return {'policy': DISTINCT_POLICY, 'contract_hash': context['contract']['contract_hash'],
            'entity_column': baseline['measure'],
            'baseline_ir_hash': baseline['ir_hash'], 'target_ir_hash': target['ir_hash']}


class DistinctEvidenceValidator:
    def __init__(self, context, expected_result=None):
        baseline, target = context['baseline'], context['target']
        if (baseline.get('aggregate_policy') != DISTINCT_POLICY or target.get('aggregate_policy') != DISTINCT_POLICY or
                baseline['measure'] != target['measure']):
            _invalid()
        self.column = baseline['measure']
        self.previous = None
        self.counts = {'baseline': 0, 'target': 0, 'gained': 0, 'lost': 0, 'kept': 0}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def add(self, row):
        if not isinstance(row, dict) or set(row) != ROW_FIELDS or not isinstance(row['order_id'], str):
            _invalid()
        entity = row['order_id']
        if self.previous is not None and entity <= self.previous:
            _invalid()
        self.previous = entity
        b, t = row['baseline_included'], row['target_included']
        if any(type(row[k]) is not bool for k in ('baseline_included', 'target_included', 'baseline_null_term', 'target_null_term')):
            _invalid()
        if row['baseline_null_term'] or row['target_null_term']:
            _invalid()
        for side, included in (('baseline', b), ('target', t)):
            rows = row[f'{side}_supporting_rows']
            if type(rows) is not int or rows < 0 or (rows > 0) != included:
                _invalid()
            if row[f'{side}_contribution'] != ('1' if included else '0'):
                _invalid()
        if row['delta'] != str(int(t) - int(b)):
            _invalid()
        membership = ('included_in_both' if b and t else 'baseline_only' if b else 'target_only' if t else 'included_in_neither')
        if row['membership'] != membership:
            _invalid()
        self.counts['baseline'] += b
        self.counts['target'] += t
        self.counts['gained'] += t and not b
        self.counts['lost'] += b and not t
        self.counts['kept'] += b and t

    def finish(self, expected_result):
        if not isinstance(expected_result, dict) or expected_result.get('aggregate') != 'count_distinct':
            _invalid()
        if (expected_result.get('baseline_value') != str(self.counts['baseline']) or
                expected_result.get('target_value') != str(self.counts['target']) or
                expected_result.get('delta_target_minus_baseline') != str(self.counts['target'] - self.counts['baseline'])):
            _invalid()
        summary = expected_result.get('distinct') or {}
        if (summary.get('policy') != DISTINCT_POLICY or summary.get('entity_column') != self.column or
                any(summary.get(k) != self.counts[k] for k in ('gained', 'lost', 'kept'))):
            _invalid()


def write_members(path, members):
    """Runner side: one line per snapshot record, in record-key order."""
    from .disk_evidence import STORAGE_POLICY, MAX_DISK_BYTES, MAX_DISK_ROWS
    byte_limit = MAX_DISK_BYTES if getattr(members, 'artifact_policy', None) == STORAGE_POLICY else MAX_MEMBERS_BYTES
    digest, size, count = hashlib.sha256(), 0, 0
    with Path(path).open('xb', buffering=4 * 1024 * 1024) as out:
        for item in members:
            line = (json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
            if len(line) > MAX_MEMBER_LINE_BYTES or size + len(line) > byte_limit or count >= MAX_DISK_ROWS:
                raise ValueError('MEMBERS_LIMIT')
            out.write(line)
            digest.update(line)
            size += len(line)
            count += 1
    return {'sha256': digest.hexdigest(), 'row_count': count, 'byte_count': size}


def validate_members(members_path, evidence_path, result, row_count=None):
    """Supervisor side: the membership list must reproduce every entity's supporting
    record counts and the NULL-entity counts stored with the result."""
    from .disk_evidence import bounded_database, MAX_DISK_ROWS
    from services.supervisor.evidence_artifact import artifact_limits
    artifact = result.get('evidence_artifact') or {}
    _, evidence_byte_limit = artifact_limits(artifact, result)
    byte_limit = evidence_byte_limit if artifact.get('storage_policy') else MAX_MEMBERS_BYTES
    with bounded_database() as con:
        con.execute('CREATE TABLE support(e TEXT PRIMARY KEY, b INTEGER, t INTEGER, cb INTEGER DEFAULT 0, '
                    'ct INTEGER DEFAULT 0) WITHOUT ROWID')
        with Path(evidence_path).open('rb') as ev:
            for line in iter(lambda: ev.readline(65537), b''):
                if len(line) > 65536 or not line.endswith(b'\n'):
                    _invalid()
                row = json.loads(line)
                con.execute('INSERT INTO support(e,b,t) VALUES (?,?,?)',
                            (row['order_id'], row['baseline_supporting_rows'], row['target_supporting_rows']))
        nulls = [0, 0]
        digest, size, count, previous = hashlib.sha256(), 0, 0, None
        with Path(members_path).open('rb') as source:
            for line in iter(lambda: source.readline(MAX_MEMBER_LINE_BYTES + 1), b''):
                size += len(line)
                if len(line) > MAX_MEMBER_LINE_BYTES or size > byte_limit or not line.endswith(b'\n'):
                    _invalid()
                item = json.loads(line)
                if (not isinstance(item, dict) or set(item) != {'k', 'e', 'b', 't'} or not isinstance(item['k'], str) or
                        type(item['b']) is not bool or type(item['t']) is not bool or
                        not (item['e'] is None or isinstance(item['e'], str))):
                    _invalid()
                if previous is not None and item['k'] <= previous:
                    _invalid()
                previous = item['k']
                if item['e'] is None:
                    nulls[0] += item['b']
                    nulls[1] += item['t']
                else:
                    updated = con.execute('UPDATE support SET cb=cb+?, ct=ct+? WHERE e=?',
                                          (item['b'], item['t'], item['e']))
                    if updated.rowcount != 1:
                        _invalid()
                digest.update(line)
                count += 1
                if count > MAX_DISK_ROWS or (row_count is not None and count > row_count):
                    _invalid()
        if con.execute('SELECT 1 FROM support WHERE b<>cb OR t<>ct LIMIT 1').fetchone():
            _invalid()
        summary = result.get('distinct') or {}
        if nulls != [summary.get('baseline_null_entity_rows'), summary.get('target_null_entity_rows')]:
            _invalid()
        if row_count is not None and count != row_count:
            _invalid()
        return {'sha256': digest.hexdigest(), 'row_count': count, 'byte_count': size}
