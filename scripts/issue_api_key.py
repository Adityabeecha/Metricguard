"""Trusted local command: create a person, issue their API key and grant workspace roles.

Run it on the machine that hosts the MetricGuard database, with the same
METRICGUARD_DATABASE_URL / METRICGUARD_DATA_DIR as the API. The raw key is printed
once and never stored (only its SHA-256 hash is kept); hand it to the person directly.

    .venv\\Scripts\\python -m scripts.issue_api_key --name "Asha" --email asha@example.com \\
        --workspace local-retail:analyst --workspace demo:reader

For a reviewer who should not see anyone's data, give the key its own empty workspace:

    .venv\\Scripts\\python -m scripts.issue_api_key --name "Reviewer" --email reviewer@example.com \\
        --new-workspace "Reviewer sandbox"

To retire a key that was shared too widely, deactivate it and issue a new one:

    .venv\\Scripts\\python -m scripts.issue_api_key --deactivate asha@example.com
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.api.store import DomainError, Store  # noqa: E402


def _database_url() -> str:
    data_dir = Path(os.environ.get('METRICGUARD_DATA_DIR', ROOT / 'data')).resolve()
    return os.environ.get('METRICGUARD_DATABASE_URL', f"sqlite:///{data_dir / 'metricguard.db'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Issue a MetricGuard API key and grant workspace roles.')
    parser.add_argument('--name')
    parser.add_argument('--email')
    parser.add_argument('--workspace', action='append', default=[], metavar='ID:ROLE',
                        help='Grant a role (owner, analyst or reader) in a workspace; repeatable.')
    parser.add_argument('--new-workspace', metavar='NAME',
                        help='Create a new, empty workspace with this name and make the person its owner.')
    parser.add_argument('--deactivate', metavar='EMAIL', help='Deactivate every key issued to EMAIL instead of issuing one.')
    parser.add_argument('--data-dir', help=r'Data directory of the running app (scripts\mg.ps1 uses data\demo-app).')
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ['METRICGUARD_DATA_DIR'] = args.data_dir
        os.environ.pop('METRICGUARD_DATABASE_URL', None)

    if args.deactivate:
        count = Store(_database_url()).deactivate_users_by_email(args.deactivate)
        print(f'Deactivated {count} key(s) for {args.deactivate}. Requests with them now return 401.')
        return 0 if count else 1
    if not args.name or not args.email:
        parser.error('--name and --email are required to issue a key.')

    grants = []
    for item in args.workspace:
        workspace_id, _, role = item.partition(':')
        if not workspace_id or role not in ('owner', 'analyst', 'reader'):
            parser.error(f"--workspace expects ID:ROLE with role owner, analyst or reader (got '{item}').")
        grants.append((workspace_id, role))

    store = Store(_database_url())
    try:
        for workspace_id, _ in grants:
            store.get_project(workspace_id)
        user, raw_key = store.create_user(args.name, args.email)
        for workspace_id, role in grants:
            store.add_project_member(workspace_id, user['id'], role)
        if args.new_workspace:
            sandbox = store.create_project(args.new_workspace, user['id'])
            grants.append((sandbox['id'], 'owner'))
    except DomainError as error:
        print(f'{error.code}: {error.message}', file=sys.stderr)
        return 2

    print(f"User: {user['name']} <{user['email']}> ({user['id']})")
    for workspace_id, role in grants:
        print(f'  {role} in {workspace_id}')
    if not grants:
        print('  No workspace roles granted yet; the key will sign in but cannot open a workspace.')
    print('\nAPI key (shown once; share it privately, do not commit it):')
    print(raw_key)
    return 0


if __name__ == '__main__':
    sys.exit(main())
