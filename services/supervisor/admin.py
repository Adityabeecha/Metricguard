"""Trusted administrator commands; this interface is never exposed by the API."""

import argparse
import json
import os
from pathlib import Path

from services.supervisor.worker import isolated_ingest


def main(argv=None):
    parser = argparse.ArgumentParser(prog='metricguard-admin')
    subcommands = parser.add_subparsers(dest='command', required=True)
    ingest = subcommands.add_parser('ingest', help='ingest CSV in the fixed isolated runner')
    ingest.add_argument('source', type=Path)
    ingest.add_argument('destination', type=Path)
    ingest.add_argument('--metadata', type=Path)
    ingest.add_argument('--name')
    args = parser.parse_args(argv)
    metadata = json.loads(args.metadata.read_text(encoding='utf-8')) if args.metadata else {}
    manifest = isolated_ingest(args.source, args.destination, metadata)
    if args.name:
        from services.api.store import Store
        store = Store(os.environ.get('METRICGUARD_DATABASE_URL', 'sqlite:///data/metricguard.db'))
        store.record_snapshot(args.name, manifest, str(args.destination.resolve()))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
