"""Check that durable storage (e.g. Cloudflare R2) accepts, returns and deletes a file.

Set the same environment the server uses (METRICGUARD_STORAGE=s3 and METRICGUARD_S3_*), then:

    python -m scripts.check_storage

It writes one small file under <prefix>healthcheck/, reads it back, compares the bytes and
deletes it. Nothing else in the bucket is touched.
"""
import sys
import tempfile
import uuid
from pathlib import Path

from services import durable


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='metricguard-storage-check-') as folder:
        store = durable.from_environment(folder)
        if not store.enabled:
            print('METRICGUARD_STORAGE is not set (or is "none"): nothing to check.')
            return 1
        probe = Path(folder) / 'healthcheck' / f'{uuid.uuid4().hex}.txt'
        probe.parent.mkdir(parents=True)
        payload = f'MetricGuard storage check {uuid.uuid4()}\n'.encode()
        probe.write_bytes(payload)
        try:
            store.push(probe, sidecar=False)
            print(f'upload   ok  ({store.key(probe)})')
            probe.unlink()
            if not store.ensure(probe, sidecar=False) or probe.read_bytes() != payload:
                print('download FAILED: the file did not come back unchanged.')
                return 2
            print('download ok  (bytes match)')
        finally:
            store.remove(probe, sidecar=False)
        if store.ensure(probe, sidecar=False):
            print('delete   FAILED: the file is still there.')
            return 3
        print('delete   ok')
    print('Storage is ready.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
