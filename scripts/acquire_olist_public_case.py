"""Acquire only the two Olist CSVs used by the public payment-status case."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
import zipfile


DATASET_URL = 'https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce'
DOWNLOAD_URL = 'https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce'
METADATA_URL = 'https://www.kaggle.com/api/v1/datasets/view/olistbr/brazilian-ecommerce'
NAMES = ('olist_orders_dataset.csv', 'olist_order_payments_dataset.csv')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    with urlopen(url, timeout=180) as source, destination.open('wb') as output:
        while block := source.read(1024 * 1024):
            output.write(block)


def acquire(directory: Path, fetch: bool) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / 'source.zip'
    metadata_path = directory / 'dataset-metadata.json'
    if fetch:
        if not archive.exists():
            download(DOWNLOAD_URL, archive)
        if not metadata_path.exists():
            download(METADATA_URL, metadata_path)
    if not archive.exists() or not metadata_path.exists():
        raise FileNotFoundError('Missing source.zip or dataset-metadata.json; rerun with --download')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    files = {}
    with zipfile.ZipFile(archive) as source:
        for name in NAMES:
            entry = source.getinfo(name)
            if entry.is_dir() or entry.filename != name:
                raise ValueError(f'Unexpected archive entry for {name}')
            destination = directory / name
            if not destination.exists():
                with source.open(entry) as input_stream, destination.open('wb') as output:
                    while block := input_stream.read(1024 * 1024):
                        output.write(block)
            archive_digest = hashlib.sha256()
            with source.open(entry) as input_stream:
                while block := input_stream.read(1024 * 1024):
                    archive_digest.update(block)
            destination_hash = sha256(destination)
            if destination_hash != archive_digest.hexdigest():
                raise ValueError(f'{name} differs from the downloaded archive')
            with destination.open(encoding='utf-8-sig', newline='') as stream:
                headers = next(csv.reader(stream))
            files[name] = {'sha256': destination_hash, 'bytes': destination.stat().st_size,
                           'headers': headers}
    previous_path = directory / 'source-manifest.json'
    previous = json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else {}
    archive_hash = sha256(archive)
    retrieved_at = (previous.get('retrieved_at_utc') if previous.get('archive_sha256') == archive_hash
                    else datetime.now(timezone.utc).isoformat())
    result = {
        'source_page': DATASET_URL, 'download_url': DOWNLOAD_URL,
        'metadata_url': METADATA_URL,
        'retrieved_at_utc': retrieved_at,
        'dataset_version': metadata.get('currentVersionNumber'),
        'license': metadata.get('licenseName'),
        'dataset_last_updated': metadata.get('lastUpdated'),
        'archive_sha256': archive_hash, 'files': files,
    }
    (directory / 'source-manifest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('data/public-case-olist'))
    parser.add_argument('--download', action='store_true', help='Fetch missing public Kaggle ZIP and metadata')
    args = parser.parse_args()
    print(json.dumps(acquire(args.directory, args.download), indent=2))
