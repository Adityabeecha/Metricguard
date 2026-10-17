from pathlib import Path

import pytest

from packages.metricguard_core import CoreError
from packages.metricguard_core import snapshot


def test_ingestion_rejects_source_mutation_between_validation_and_copy(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'MetricGuard_Planning_Pack/examples/orders_golden.csv'
    real_hash = snapshot.file_hash
    source_checks = 0

    def changed_hash(path):
        nonlocal source_checks
        if Path(path) == source:
            source_checks += 1
            return real_hash(path) if source_checks == 1 else '0' * 64
        return real_hash(path)

    monkeypatch.setattr(snapshot, 'file_hash', changed_hash)
    destination = tmp_path / 'changed.duckdb'
    with pytest.raises(CoreError) as error:
        snapshot.ingest_csv(source, destination)
    assert error.value.code == 'source_changed'
    assert not destination.exists()
