"""Replayable, bounded temporary rows for large frozen flat comparisons.

Only locally generated JSON is stored here. The public evidence remains ordinary
JSONL and is independently validated by the supervisor. No pickle deserialization.
"""
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
import json
import tempfile
import sqlite3
from contextlib import contextmanager
from itertools import islice

from .common import CoreError

STORAGE_POLICY = 'flat-disk-evidence-v1'
MAX_DISK_ROWS = 5_000_000
MAX_DISK_BYTES = 4 * 1024**3
MAX_SCRATCH_BYTES = 8 * 1024**3
MAX_ROW_BYTES = 64 * 1024


class DiskBudget:
    def __init__(self):
        self.used = 0


def _encode(value):
    if isinstance(value, Decimal):
        return {'__decimal__': str(value)}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _decode(value):
    return Decimal(value['__decimal__']) if set(value) == {'__decimal__'} else value


class DiskRows(Sequence):
    """Small pages and repeated scans without retaining an evidence-sized list.

    Explicit close is supported; temporary files are also closed on object release.
    Random indexing scans the file, so consumers should iterate or request pages.
    """
    artifact_policy = STORAGE_POLICY

    def __init__(self, rows=(), *, budget=None):
        self.budget = budget if budget is not None else DiskBudget()
        self.file = tempfile.TemporaryFile(mode='w+b')
        self.count = self.byte_count = 0
        self.closed = False
        try:
            for row in rows:
                self.append(row)
        except BaseException:
            self.close()
            raise

    def append(self, row):
        line = (json.dumps(row, default=_encode, ensure_ascii=False, allow_nan=False,
                           separators=(',', ':')) + '\n').encode('utf-8')
        if (len(line) > MAX_ROW_BYTES or self.count >= MAX_DISK_ROWS or
                self.byte_count + len(line) > MAX_DISK_BYTES or
                self.budget.used + len(line) > MAX_SCRATCH_BYTES):
            raise CoreError('evidence_limit', 'Flat evidence exceeds the bounded disk row or byte budget')
        try:
            self.file.seek(0, 2)
            self.file.write(line)
        except OSError as exc:
            raise CoreError('evidence_limit', 'Temporary evidence storage is unavailable or full') from exc
        self.count += 1
        self.byte_count += len(line)
        self.budget.used += len(line)

    def __iter__(self):
        # Each iterator tracks its own cursor, allowing repeated/nested scans.
        offset = 0
        for _ in range(self.count):
            self.file.seek(offset)
            line = self.file.readline(MAX_ROW_BYTES + 1)
            offset += len(line)
            yield json.loads(line, object_hook=_decode)

    def __len__(self):
        return self.count

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(self.count)
            if step <= 0:
                raise ValueError('Disk evidence only supports forward pages')
            return list(islice(self, start, stop, step))
        index = key if key >= 0 else self.count + key
        if not 0 <= index < self.count:
            raise IndexError(key)
        return next(row for i, row in enumerate(self) if i == index)

    def close(self):
        if not self.closed:
            self.file.close()
            self.budget.used -= self.byte_count
            self.closed = True

    def __del__(self):
        self.close()


def cursor_rows(cursor):
    while batch := cursor.fetchmany(10_000):
        yield from batch


@contextmanager
def bounded_database():
    """One disposable 4 GiB SQLite index, with a 16 MiB page cache."""
    with tempfile.TemporaryDirectory(prefix='metricguard-evidence-') as folder:
        con = sqlite3.connect(folder + '/index.sqlite')
        try:
            con.execute('PRAGMA journal_mode=OFF')
            con.execute('PRAGMA synchronous=OFF')
            con.execute('PRAGMA cache_size=-16384')
            con.execute('PRAGMA temp_store=FILE')
            con.execute(f'PRAGMA max_page_count={MAX_DISK_BYTES // 4096}')
            yield con
        except (sqlite3.Error, OSError) as exc:
            raise CoreError('evidence_limit', 'Temporary evidence index storage is unavailable or full') from exc
        finally:
            con.close()
