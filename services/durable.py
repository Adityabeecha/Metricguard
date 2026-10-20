"""Durable copies of frozen datasets and evidence files.

The API and the comparison worker read and write files under METRICGUARD_DATA_DIR. On a hosted
container that folder is wiped on every redeploy, so finished files are also kept in an
S3-compatible bucket (Cloudflare R2, AWS S3, MinIO) and fetched back on first use:

* ``push(path)`` copies a finished file to the bucket (and its ``.manifest.json`` sidecar);
* ``ensure(path)`` returns True when the file is on local disk, downloading it first if needed;
* ``remove(path)`` deletes the local file and its durable copy.
* ``pin(paths)`` protects active consumers from cache eviction across processes;
* ``cleanup_cache(protected=())`` evicts only verified pushed snapshot/evidence copies.

Files keep their content hashes, so a downloaded snapshot or evidence file is re-verified by the
same checks as before. With METRICGUARD_STORAGE unset (local use) every call is a plain file
check and nothing leaves the machine.

Configuration:
    METRICGUARD_STORAGE        unset/none | s3 | dir
    METRICGUARD_S3_ENDPOINT    e.g. https://<account-id>.r2.cloudflarestorage.com
    METRICGUARD_S3_BUCKET, METRICGUARD_S3_ACCESS_KEY_ID, METRICGUARD_S3_SECRET_ACCESS_KEY
    METRICGUARD_S3_REGION      default "auto" (R2); e.g. us-east-1 for AWS
    METRICGUARD_S3_PREFIX      optional key prefix, e.g. "prod/"
    METRICGUARD_STORAGE_DIR    folder for the "dir" backend (tests, a mounted volume)
    METRICGUARD_CACHE_MAX_MB   optional local snapshots/evidence cap; evicts to below 80%

JSONL objects use transparent streaming gzip (level 1 for small hosted CPUs) at the
original key plus ``.gz``. Legacy
plain objects remain readable. Local bytes and their evidence hashes do not change.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import hmac
import os
import shutil
import sqlite3
import threading
import time
import uuid
import zlib
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

SIDECAR = '.manifest.json'
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()
BLOCK_SIZE = 1024 * 1024


class StorageError(RuntimeError):
    """A durable copy could not be written or read."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _try_lock(stream) -> bool:
    """A process-owned lock, released by the OS even when a worker is killed."""
    try:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, BlockingIOError):
        return False


class DirBackend:
    """Keeps durable copies in another folder (tests, or a mounted persistent volume)."""

    def __init__(self, root):
        self.root = Path(root)

    def put(self, key: str, path: Path):
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
        try:
            shutil.copyfile(path, staged)
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)

    def get(self, key: str, path: Path) -> bool:
        source = self.root / key
        if not source.is_file():
            return False
        shutil.copyfile(source, path)
        return True

    def delete(self, key: str):
        (self.root / key).unlink(missing_ok=True)


class S3Backend:
    """Minimal S3 client (PUT, GET, DELETE) with AWS Signature Version 4, over httpx.

    Path-style addressing (``endpoint/bucket/key``) works with Cloudflare R2, AWS S3 and MinIO.
    """

    def __init__(self, endpoint, bucket, access_key_id, secret_access_key, region='auto', prefix='', client=None):
        if not (endpoint and bucket and access_key_id and secret_access_key):
            raise StorageError('S3 storage needs METRICGUARD_S3_ENDPOINT, _BUCKET, _ACCESS_KEY_ID and _SECRET_ACCESS_KEY.')
        self.endpoint = endpoint.rstrip('/')
        self.host = self.endpoint.split('://', 1)[-1].split('/', 1)[0]
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region or 'auto'
        self.prefix = prefix or ''
        self._client = client

    def _http(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=httpx.Timeout(30.0, read=300.0, write=300.0))
        return self._client

    def _path(self, key: str) -> str:
        return '/' + quote(self.bucket, safe='') + '/' + quote(self.prefix + key, safe='/-_.~')

    def _headers(self, method: str, path: str, payload_hash: str, now=None) -> dict:
        now = now or _dt.datetime.now(_dt.timezone.utc)
        amz_date, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
        headers = {'host': self.host, 'x-amz-content-sha256': payload_hash, 'x-amz-date': amz_date}
        signed = ';'.join(sorted(headers))
        canonical = '\n'.join([method, path, '', *(f'{k}:{headers[k]}' for k in sorted(headers)), '', signed, payload_hash])
        scope = f'{day}/{self.region}/s3/aws4_request'
        to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        key = ('AWS4' + self.secret_access_key).encode()
        for part in (day, self.region, 's3', 'aws4_request'):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers['authorization'] = (f'AWS4-HMAC-SHA256 Credential={self.access_key_id}/{scope}, '
                                    f'SignedHeaders={signed}, Signature={signature}')
        return headers

    def put(self, key: str, path: Path):
        path_part = self._path(key)
        headers = self._headers('PUT', path_part, _sha256_file(path))
        headers['content-length'] = str(path.stat().st_size)
        with open(path, 'rb') as stream:
            response = self._http().put(self.endpoint + path_part, content=stream, headers=headers)
        if response.status_code >= 300:
            raise StorageError(f'Upload of {key} failed ({response.status_code}).')

    def get(self, key: str, path: Path) -> bool:
        path_part = self._path(key)
        headers = self._headers('GET', path_part, EMPTY_SHA256)
        staged = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.download')
        try:
            with self._http().stream('GET', self.endpoint + path_part, headers=headers) as response:
                if response.status_code == 404:
                    return False
                if response.status_code >= 300:
                    raise StorageError(f'Download of {key} failed ({response.status_code}).')
                with open(staged, 'wb') as out:
                    for chunk in response.iter_bytes(1024 * 1024):
                        out.write(chunk)
            os.replace(staged, path)
            return True
        finally:
            staged.unlink(missing_ok=True)

    def delete(self, key: str):
        path_part = self._path(key)
        response = self._http().delete(self.endpoint + path_part, headers=self._headers('DELETE', path_part, EMPTY_SHA256))
        if response.status_code >= 300 and response.status_code != 404:
            raise StorageError(f'Delete of {key} failed ({response.status_code}).')


class Durable:
    """Mirrors files under ``data_dir`` to a backend. ``backend=None`` means local only."""

    def __init__(self, data_dir, backend=None):
        self.data_dir = Path(data_dir).resolve()
        self.backend = backend
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.backend is not None

    def key(self, path) -> str | None:
        """Bucket key for a file under data_dir, or None for files outside it."""
        try:
            return Path(path).resolve().relative_to(self.data_dir).as_posix()
        except ValueError:
            return None

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    @contextmanager
    def _metadata(self):
        root = self.data_dir / '.durable-cache'
        root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(root / 'index.sqlite3', timeout=30)
        try:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS copies '
                               '(key TEXT PRIMARY KEY, size INTEGER, digest TEXT, accessed INTEGER)')
            connection.execute('CREATE TABLE IF NOT EXISTS pins '
                               '(token TEXT, key TEXT, PRIMARY KEY(token, key))')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _keys(self, paths):
        if isinstance(paths, (str, Path)):
            paths = [paths]
        keys = set()
        for path in paths:
            if path and (key := self.key(path)) is not None:
                keys.update((key, key + SIDECAR))
        return keys

    @contextmanager
    def pin(self, paths):
        """Protect files and their sidecars from eviction across API/worker processes.

        Enter before ensure/read and hold until the consumer has finished. Pin records are
        serialized with eviction; an OS lock makes crashed processes' records reclaimable.
        """
        keys = self._keys(paths)
        if self.backend is None or not keys:
            yield
            return
        root = self.data_dir / '.durable-cache'
        root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        lock_path = root / (token + '.pin')
        stream = open(lock_path, 'w+b')
        try:
            stream.write(b'0')
            stream.flush()
            if not _try_lock(stream):
                raise StorageError('Could not protect the local durable cache.')
            with self._metadata() as connection:
                connection.executemany('INSERT INTO pins VALUES (?, ?)', [(token, key) for key in keys])
            try:
                yield
            finally:
                with self._metadata() as connection:
                    connection.execute('DELETE FROM pins WHERE token = ?', (token,))
        finally:
            stream.close()
            lock_path.unlink(missing_ok=True)

    def _record(self, key, path, digest=None):
        with self._metadata() as connection:
            connection.execute('INSERT OR REPLACE INTO copies VALUES (?, ?, ?, ?)',
                               (key, path.stat().st_size, digest or _sha256_file(path), time.time_ns()))

    def _touch(self, key):
        if self.backend is not None and key is not None:
            with self._metadata() as connection:
                connection.execute('UPDATE copies SET accessed = ? WHERE key = ?', (time.time_ns(), key))

    def stored_sizes(self, paths) -> dict[str, int]:
        """Logical original byte sizes for owned paths, including evicted durable copies.

        Unknown local files use stat; missing unknown paths are omitted. This method
        does not download objects, enumerate the bucket, or change their LRU order.
        """
        resolved = {str(Path(path).resolve()): self.key(path) for path in paths if path}
        sizes = {}
        if self.backend is not None and resolved:
            with self._metadata() as connection:
                for path, key in resolved.items():
                    known = connection.execute('SELECT size FROM copies WHERE key = ?', (key,)).fetchone()
                    if known:
                        sizes[path] = known[0]
        for path in resolved:
            if path not in sizes:
                try:
                    local = Path(path)
                    if local.is_file():
                        sizes[path] = local.stat().st_size
                except FileNotFoundError:
                    pass
        return sizes

    def _active_keys(self, connection):
        active = set()
        for (token,) in connection.execute('SELECT DISTINCT token FROM pins').fetchall():
            lock_path = self.data_dir / '.durable-cache' / (token + '.pin')
            try:
                with open(lock_path, 'r+b') as stream:
                    stale = _try_lock(stream)
            except FileNotFoundError:
                stale = True
            except OSError:
                stale = False  # uncertain protection must never permit eviction
            if stale:
                connection.execute('DELETE FROM pins WHERE token = ?', (token,))
                lock_path.unlink(missing_ok=True)
            else:
                active.update(row[0] for row in connection.execute('SELECT key FROM pins WHERE token = ?', (token,)))
        return active

    def cleanup_cache(self, protected=()) -> int:
        """Evict only verified durable copies in snapshots/evidence, oldest access first.

        The cap counts all local files in these two cache trees; unpushed/changed files
        and active consumers are always retained even if the target cannot be reached.
        Unset METRICGUARD_CACHE_MAX_MB, zero, and local-only storage disable cleanup.
        """
        configured = os.environ.get('METRICGUARD_CACHE_MAX_MB')
        if self.backend is None or not configured:
            return 0
        try:
            value = Decimal(configured)
            if not value.is_finite() or value < 0:
                raise ValueError
            cap = int(value * 1024 * 1024)
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise StorageError('METRICGUARD_CACHE_MAX_MB must be a non-negative number.') from exc
        if cap == 0:
            return 0
        removed = 0
        with self._metadata() as connection:
            excluded = self._keys(protected) | self._active_keys(connection)
            files = {}
            for folder in ('snapshots', 'evidence'):
                for path in (self.data_dir / folder).rglob('*'):
                    if path.is_file() and not path.is_symlink():
                        key = self.key(path)
                        if key and key.split('/')[0] == folder:
                            try:
                                files[key] = path.stat().st_size
                            except FileNotFoundError:
                                pass
            total = sum(files.values())
            if total <= cap:
                return 0
            candidates = connection.execute('SELECT key, size, digest FROM copies ORDER BY accessed').fetchall()
            for key, size, digest in candidates:
                if total < cap * .8:
                    break
                if key not in files or key in excluded:
                    continue
                path = self.data_dir / key
                try:
                    before = path.stat()
                    if before.st_size != size or _sha256_file(path) != digest:
                        continue
                    after = path.stat()
                    if (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (
                            after.st_mtime_ns, after.st_ctime_ns, after.st_size):
                        continue
                    path.unlink()
                    total -= files[key]
                    removed += 1
                except FileNotFoundError:
                    continue
        return removed

    def _push_one(self, path, key):
        """Stage bounded chunks so the uploaded bytes have a verified local original."""
        staged = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.upload')
        compressed = path.name.endswith('.jsonl')
        digest = hashlib.sha256()
        before = path.stat()
        try:
            with open(path, 'rb') as source, open(staged, 'wb') as target:
                if compressed:
                    with gzip.GzipFile(filename='', mode='wb', fileobj=target, mtime=0, compresslevel=1) as out:
                        for chunk in iter(lambda: source.read(BLOCK_SIZE), b''):
                            digest.update(chunk)
                            out.write(chunk)
                else:
                    for chunk in iter(lambda: source.read(BLOCK_SIZE), b''):
                        digest.update(chunk)
                        target.write(chunk)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns) or _sha256_file(path) != digest.hexdigest():
                raise StorageError(f'File changed while copying {key}.')
            self.backend.put(key + '.gz' if compressed else key, staged)
            self._record(key, path, digest.hexdigest())
        finally:
            staged.unlink(missing_ok=True)

    def push(self, path, sidecar: bool = True):
        """Copy a finished file (and its manifest sidecar when present) to durable storage."""
        path = Path(path)
        if self.backend is None:
            return
        key = self.key(path)
        if key is None:
            return
        side = path.with_name(path.name + SIDECAR)
        with self.pin([path, side]):
            self._push_one(path, key)
            if sidecar and side.is_file():
                self._push_one(side, key + SIDECAR)
            self.cleanup_cache(protected=[path, side])

    def _download(self, key, path):
        staged = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.download')
        packed = staged.with_suffix('.gz.download')
        try:
            compressed = key.endswith('.jsonl') and self.backend.get(key + '.gz', packed)
            if compressed:
                try:
                    with gzip.open(packed, 'rb') as source, open(staged, 'wb') as target:
                        shutil.copyfileobj(source, target, BLOCK_SIZE)
                except (OSError, EOFError, zlib.error) as exc:
                    raise StorageError(f'Invalid compressed durable object {key}.') from exc
            elif not self.backend.get(key, staged):
                return False
            digest = _sha256_file(staged)
            with self._metadata() as connection:
                known = connection.execute('SELECT digest FROM copies WHERE key = ?', (key,)).fetchone()
                if known and known[0] != digest:
                    raise StorageError(f'Durable object {key} does not match its original SHA-256.')
            os.replace(staged, path)
            self._record(key, path, digest)
            return True
        finally:
            staged.unlink(missing_ok=True)
            packed.unlink(missing_ok=True)

    def ensure(self, path, sidecar: bool = True) -> bool:
        """True when ``path`` is on local disk, fetching it (and its sidecar) from storage if needed."""
        if not path:
            return False
        path = Path(path)
        if self.backend is None:
            return path.is_file()
        key = self.key(path)
        if key is None:
            return path.is_file()
        with self.pin([path]), self._lock(key):
            path.parent.mkdir(parents=True, exist_ok=True)
            if sidecar and path.suffix == '.duckdb':
                side = path.with_name(path.name + SIDECAR)
                if not side.is_file():
                    self._download(key + SIDECAR, side)
                else:
                    self._touch(key + SIDECAR)
            if path.is_file():
                self._touch(key)
                return True
            return self._download(key, path)

    def remove(self, path, sidecar: bool = True):
        path = Path(path)
        path.unlink(missing_ok=True)
        side = path.with_name(path.name + SIDECAR)
        if sidecar:
            side.unlink(missing_ok=True)
        if self.backend is None:
            return
        key = self.key(path)
        if key is None:
            return
        self.backend.delete(key)
        if path.name.endswith('.jsonl'):
            self.backend.delete(key + '.gz')
        if sidecar:
            self.backend.delete(key + SIDECAR)
        with self._metadata() as connection:
            connection.execute('DELETE FROM copies WHERE key = ?', (key,))
            if sidecar:
                connection.execute('DELETE FROM copies WHERE key = ?', (key + SIDECAR,))


def from_environment(data_dir=None) -> Durable:
    data_dir = Path(data_dir or os.environ.get('METRICGUARD_DATA_DIR', './data'))
    kind = (os.environ.get('METRICGUARD_STORAGE') or 'none').strip().lower()
    if kind in ('', 'none', 'local'):
        return Durable(data_dir)
    if kind == 'dir':
        root = os.environ.get('METRICGUARD_STORAGE_DIR')
        if not root:
            raise StorageError('METRICGUARD_STORAGE=dir needs METRICGUARD_STORAGE_DIR.')
        return Durable(data_dir, DirBackend(root))
    if kind == 's3':
        return Durable(data_dir, S3Backend(
            os.environ.get('METRICGUARD_S3_ENDPOINT'), os.environ.get('METRICGUARD_S3_BUCKET'),
            os.environ.get('METRICGUARD_S3_ACCESS_KEY_ID'), os.environ.get('METRICGUARD_S3_SECRET_ACCESS_KEY'),
            os.environ.get('METRICGUARD_S3_REGION', 'auto'), os.environ.get('METRICGUARD_S3_PREFIX', '')))
    raise StorageError(f'Unknown METRICGUARD_STORAGE {kind!r}: use none, s3 or dir.')


_current: Durable | None = None
_current_guard = threading.Lock()


def storage() -> Durable:
    """The process-wide durable store, configured from the environment on first use."""
    global _current
    with _current_guard:
        if _current is None:
            _current = from_environment()
        return _current


def configure(durable: Durable | None):
    """Replace the process-wide store (create_app, the worker and tests call this)."""
    global _current
    with _current_guard:
        _current = durable


def ensure(path, sidecar: bool = True) -> bool:
    return storage().ensure(path, sidecar)


def push(path, sidecar: bool = True):
    storage().push(path, sidecar)


def remove(path, sidecar: bool = True):
    storage().remove(path, sidecar)


def pin(paths):
    return storage().pin(paths)


def cleanup_cache(protected=()) -> int:
    return storage().cleanup_cache(protected)


def stored_sizes(paths) -> dict[str, int]:
    return storage().stored_sizes(paths)
