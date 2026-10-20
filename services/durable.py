"""Durable copies of frozen datasets and evidence files.

The API and the comparison worker read and write files under METRICGUARD_DATA_DIR. On a hosted
container that folder is wiped on every redeploy, so finished files are also kept in an
S3-compatible bucket (Cloudflare R2, AWS S3, MinIO) and fetched back on first use:

* ``push(path)`` copies a finished file to the bucket (and its ``.manifest.json`` sidecar);
* ``ensure(path)`` returns True when the file is on local disk, downloading it first if needed;
* ``remove(path)`` deletes the local file and its durable copy.

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
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import quote

SIDECAR = '.manifest.json'
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()


class StorageError(RuntimeError):
    """A durable copy could not be written or read."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class DirBackend:
    """Keeps durable copies in another folder (tests, or a mounted persistent volume)."""

    def __init__(self, root):
        self.root = Path(root)

    def put(self, key: str, path: Path):
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
        shutil.copyfile(path, staged)
        os.replace(staged, target)

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

    def push(self, path, sidecar: bool = True):
        """Copy a finished file (and its manifest sidecar when present) to durable storage."""
        path = Path(path)
        if self.backend is None:
            return
        key = self.key(path)
        if key is None:
            return
        self.backend.put(key, path)
        side = path.with_name(path.name + SIDECAR)
        if sidecar and side.is_file():
            self.backend.put(key + SIDECAR, side)

    def ensure(self, path, sidecar: bool = True) -> bool:
        """True when ``path`` is on local disk, fetching it (and its sidecar) from storage if needed."""
        if not path:
            return False
        path = Path(path)
        if path.is_file():
            return True
        if self.backend is None:
            return False
        key = self.key(path)
        if key is None:
            return False
        with self._lock(key):
            if path.is_file():
                return True
            path.parent.mkdir(parents=True, exist_ok=True)
            if sidecar and path.suffix == '.duckdb':
                side = path.with_name(path.name + SIDECAR)
                if not side.is_file():
                    self.backend.get(key + SIDECAR, side)
            return self.backend.get(key, path)

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
        if sidecar:
            self.backend.delete(key + SIDECAR)


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
