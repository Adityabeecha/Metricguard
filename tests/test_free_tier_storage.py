"""Free-tier durable compression and recoverable disk cache behavior."""
import gzip
import hashlib
import multiprocessing

import pytest

from services import durable


def _file(root, key, content=b'x' * 4096):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _pin_in_process(data, bucket, path, ready, release):
    store = durable.Durable(data, durable.DirBackend(bucket))
    with store.pin([path]):
        ready.set()
        release.wait(20)


def test_jsonl_compressed_round_trip_preserves_original_hash_and_removes_both_keys(tmp_path):
    data, bucket = tmp_path / 'data', tmp_path / 'bucket'
    store = durable.Durable(data, durable.DirBackend(bucket))
    raw = b'{"amount":"12.00"}\r\n' * 1000 + b'{"last":true}\n'
    path = _file(data, 'evidence/run/rows.jsonl', raw)
    expected = hashlib.sha256(raw).hexdigest()
    store.push(path)
    compressed = bucket / 'evidence/run/rows.jsonl.gz'
    assert gzip.decompress(compressed.read_bytes()) == raw
    assert not (bucket / 'evidence/run/rows.jsonl').exists()
    path.unlink()
    assert store.ensure(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    _file(bucket, 'evidence/run/rows.jsonl', b'legacy')
    store.remove(path)
    assert not compressed.exists()
    assert not (bucket / 'evidence/run/rows.jsonl').exists()


def test_jsonl_reads_legacy_plain_object_and_failed_gzip_is_never_published(tmp_path):
    data, bucket = tmp_path / 'data', tmp_path / 'bucket'
    store = durable.Durable(data, durable.DirBackend(bucket))
    _file(bucket, 'evidence/old.jsonl', b'{"legacy":1}\n')
    path = data / 'evidence/old.jsonl'
    assert store.ensure(path)
    assert path.read_bytes() == b'{"legacy":1}\n'
    path.unlink()
    _file(bucket, 'evidence/old.jsonl.gz', b'bad gzip')
    with pytest.raises(durable.StorageError):
        store.ensure(path)
    assert not path.exists()
    assert not list(path.parent.glob('.*.download'))


def test_cache_only_evicts_verified_pushed_idle_files_and_restores(tmp_path, monkeypatch):
    data, bucket = tmp_path / 'data', tmp_path / 'bucket'
    store = durable.Durable(data, durable.DirBackend(bucket))
    oldest = _file(data, 'snapshots/old.duckdb')
    recent = _file(data, 'evidence/recent.jsonl')
    modified = _file(data, 'snapshots/modified.duckdb')
    uploaded = _file(data, 'uploads/source.csv')
    job = _file(data, 'jobs/running.json')
    for path in [oldest, recent, modified, uploaded, job]:
        store.push(path)
    modified.write_bytes(b'y' * 4096)  # same size: only digest verification catches the edit
    unpushed = _file(data, 'snapshots/unpushed.duckdb', b'unpushed')
    assert store.ensure(oldest)  # make recent the least recently used verified copy
    monkeypatch.setenv('METRICGUARD_CACHE_MAX_MB', '0.011')
    second_process_store = durable.Durable(data, durable.DirBackend(bucket))
    assert second_process_store.cleanup_cache() == 1
    assert oldest.exists() and not recent.exists()
    assert all(path.exists() for path in [modified, uploaded, job, unpushed])
    assert second_process_store.ensure(recent)
    assert recent.read_bytes() == b'x' * 4096


def test_cache_disabled_without_limit_or_backend_and_push_protects_new_copy(tmp_path, monkeypatch):
    data, bucket = tmp_path / 'data', tmp_path / 'bucket'
    store = durable.Durable(data, durable.DirBackend(bucket))
    path = _file(data, 'evidence/new.jsonl')
    monkeypatch.delenv('METRICGUARD_CACHE_MAX_MB', raising=False)
    store.push(path)
    assert store.cleanup_cache() == 0 and path.exists()
    monkeypatch.setenv('METRICGUARD_CACHE_MAX_MB', '0.001')
    store.push(path)
    assert path.exists()
    assert durable.Durable(data).cleanup_cache() == 0 and path.exists()
    assert store.cleanup_cache(protected=[path]) == 0 and path.exists()
    assert store.cleanup_cache() == 1 and not path.exists()


def test_ensure_restores_evicted_manifest_when_snapshot_is_still_local(tmp_path):
    data, bucket = tmp_path / 'data', tmp_path / 'bucket'
    store = durable.Durable(data, durable.DirBackend(bucket))
    path = _file(data, 'snapshots/a.duckdb')
    side = _file(data, 'snapshots/a.duckdb.manifest.json', b'{"hash":"frozen"}')
    store.push(path)
    side.unlink()
    assert store.ensure(path)
    assert side.read_bytes() == b'{"hash":"frozen"}'


def test_failed_push_retains_unpushed_file_and_removes_staging(tmp_path, monkeypatch):
    class Unavailable(durable.DirBackend):
        def put(self, key, path):
            raise durable.StorageError('offline')

    data = tmp_path / 'data'
    store = durable.Durable(data, Unavailable(tmp_path / 'bucket'))
    path = _file(data, 'evidence/unpushed.jsonl')
    monkeypatch.setenv('METRICGUARD_CACHE_MAX_MB', '0.001')
    with pytest.raises(durable.StorageError, match='offline'):
        store.push(path)
    assert store.cleanup_cache() == 0
    assert path.exists()
    assert not list(path.parent.glob('.*.upload'))


def test_stored_sizes_counts_original_bytes_after_local_cache_eviction(tmp_path, monkeypatch):
    data, bucket = tmp_path / 'data', tmp_path / 'bucket'
    store = durable.Durable(data, durable.DirBackend(bucket))
    pushed = _file(data, 'evidence/counted.jsonl')
    unpushed = _file(data, 'uploads/unpushed.csv', b'csv')
    store.push(pushed)
    monkeypatch.setenv('METRICGUARD_CACHE_MAX_MB', '0.001')
    assert store.cleanup_cache() == 1
    restarted = durable.Durable(data, durable.DirBackend(bucket))
    assert restarted.stored_sizes([pushed, unpushed, data / 'missing']) == {
        str(pushed.resolve()): 4096, str(unpushed.resolve()): 3,
    }
    assert not pushed.exists()  # usage inspection never downloads the original


def test_killed_process_pin_is_reclaimed(tmp_path, monkeypatch):
    data, bucket = tmp_path / 'data', tmp_path / 'bucket'
    store = durable.Durable(data, durable.DirBackend(bucket))
    path = _file(data, 'snapshots/crashed.duckdb')
    store.push(path)
    context = multiprocessing.get_context('spawn')
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_pin_in_process, args=(data, bucket, path, ready, release))
    process.start()
    try:
        assert ready.wait(15)
    finally:
        process.terminate()
        process.join(15)
    monkeypatch.setenv('METRICGUARD_CACHE_MAX_MB', '0.001')
    assert store.cleanup_cache() == 1 and not path.exists()


def test_cross_process_pin_blocks_cache_eviction_until_released(tmp_path, monkeypatch):
    data, bucket = tmp_path / 'data', tmp_path / 'bucket'
    store = durable.Durable(data, durable.DirBackend(bucket))
    path = _file(data, 'snapshots/pinned.duckdb')
    store.push(path)
    monkeypatch.setenv('METRICGUARD_CACHE_MAX_MB', '0.001')
    context = multiprocessing.get_context('spawn')
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_pin_in_process, args=(data, bucket, path, ready, release))
    process.start()
    try:
        assert ready.wait(15)
        assert store.cleanup_cache() == 0 and path.exists()
    finally:
        release.set()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 0
    assert store.cleanup_cache() == 1 and not path.exists()
