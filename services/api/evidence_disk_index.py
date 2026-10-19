"""Disposable local indexes. Only hashes of fully verified evidence authorize reads."""
import hashlib
import json
import sqlite3
import tempfile
from decimal import Decimal
from pathlib import Path


class DiskIndex:
    def __init__(self, path, key_of):
        self.directory = tempfile.TemporaryDirectory(prefix='metricguard-index-')
        self.path = Path(self.directory.name) / 'index.sqlite'
        digest = hashlib.sha256()
        self.count = changed = blank = position = 0
        top = []
        try:
            with sqlite3.connect(self.path) as db, Path(path).open('rb') as source:
                db.execute('PRAGMA cache_size=-8192')
                db.execute('PRAGMA temp_store=FILE')
                db.execute('CREATE TABLE rows (i INTEGER PRIMARY KEY, pos INTEGER, hash TEXT, key TEXT, changed INTEGER, exponent INTEGER, digits TEXT)')
                batch = []
                for i, line in enumerate(source):
                    digest.update(line)
                    row = json.loads(line)
                    magnitude = Decimal(row.get('delta') or '0').copy_abs()
                    if not magnitude.is_finite():
                        raise ValueError('EVIDENCE_INVALID')
                    tup = magnitude.as_tuple()
                    digits = ''.join(map(str, tup.digits)).rstrip('0') or '0'
                    exponent = magnitude.adjusted() if magnitude else -1000000
                    batch.append((i, position, hashlib.sha256(line).hexdigest(), key_of(row).lower(), bool(magnitude), exponent, digits))
                    position += len(line)
                    changed += bool(magnitude)
                    blank += bool(row.get('baseline_null_term') or row.get('target_null_term'))
                    self.count += 1
                    if magnitude:
                        top.append((magnitude, i, row))
                        if len(top) > 80:
                            top.sort(key=lambda item: (item[0].copy_negate(), item[1]))
                            del top[20:]
                    if len(batch) == 1000:
                        db.executemany('INSERT INTO rows VALUES (?,?,?,?,?,?,?)', batch)
                        batch.clear()
                if batch:
                    db.executemany('INSERT INTO rows VALUES (?,?,?,?,?,?,?)', batch)
                db.execute('CREATE INDEX impact ON rows(exponent DESC, digits DESC, i)')
            top.sort(key=lambda item: (item[0].copy_negate(), item[1]))
            self.summary = {'total': self.count, 'changed': changed, 'blank_amount_rows': blank,
                            'top_changes': [row for _, _, row in top[:20]]}
            self.digest = digest.hexdigest()
        except BaseException:
            self.directory.cleanup()
            raise

    def __len__(self):
        return self.count

    def select(self, offset, limit, changed_only, query, order):
        where = 'WHERE (? = 0 OR changed = 1) AND (? = \'\' OR instr(key, ?) > 0)'
        params = (int(changed_only), query.strip().lower(), query.strip().lower())
        ordering = 'exponent DESC, digits DESC, i' if order == 'impact' else 'i'
        with sqlite3.connect(self.path) as db:
            count = db.execute(f'SELECT COUNT(*) FROM rows {where}', params).fetchone()[0]
            items = db.execute(f'SELECT pos, hash FROM rows {where} ORDER BY {ordering} LIMIT ? OFFSET ?',
                               (*params, limit, offset)).fetchall()
        return items, count

    def hashes(self):
        with sqlite3.connect(self.path) as db:
            for row in db.execute('SELECT hash FROM rows ORDER BY i'):
                yield row[0]

    def __del__(self):
        self.directory.cleanup()
