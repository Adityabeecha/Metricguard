"""Check R: a saved case restored from backup into a fresh data directory replays through Docker.

1. Back up a workspace from the running app (scripts\\mg.ps1 start -Seed).
2. Restore the archive into a new, empty data directory (as when moving machines).
3. Start a second API and supervisor bound only to the restored copy.
4. Replay every saved investigation there and compare totals and evidence hash
   with the original saved result.

    .venv\\Scripts\\python -m scripts.verify_restored_replay [--api http://127.0.0.1:8000] [--port 8002]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.api.backup import restore_project  # noqa: E402
from services.api.store import Store  # noqa: E402


def _wait_health(url: str, seconds: int = 45) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(f'{url}/api/v1/health', timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.7)
    raise SystemExit(f'Restored API at {url} did not start.')


def _sha(run: dict) -> str | None:
    return ((run.get('result') or {}).get('evidence_artifact') or {}).get('sha256')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', default='http://127.0.0.1:8000')
    parser.add_argument('--workspace', default='local-retail')
    parser.add_argument('--port', type=int, default=8002)
    parser.add_argument('--target-dir', default='data/restore-check')
    parser.add_argument('--report', default='data/evaluation/restored_replay_report.json')
    args = parser.parse_args(argv)

    source = httpx.Client(base_url=args.api, headers={'X-Project-Id': args.workspace}, timeout=120)
    boot = source.get(f'/api/v1/bootstrap?project_id={args.workspace}').raise_for_status().json()
    originals = {i['id']: next(r for r in boot['runs'] if r['id'] == i['run_id']) for i in boot['investigations']}
    if not originals:
        raise SystemExit('No saved investigations to check. Run scripts\\mg.ps1 start -Seed first.')

    target = (ROOT / args.target_dir).resolve()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    archive = target / 'backup.tar.gz'
    archive.write_bytes(source.post(f'/api/v1/projects/{args.workspace}/backup').raise_for_status().content)

    url = f"sqlite:///{(target / 'metricguard.db').as_posix()}"
    restored = restore_project(Store(url), archive, target)

    env = {**os.environ, 'METRICGUARD_MODE': 'isolated', 'METRICGUARD_DATA_DIR': str(target),
           'METRICGUARD_DATABASE_URL': url, 'METRICGUARD_ALLOW_UNVERIFIED_LOCAL': 'true'}
    logs = target / 'logs'
    logs.mkdir()
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'services.api.app:app', '--host', '127.0.0.1', '--port', str(args.port)],
                           cwd=ROOT, env=env, stdout=(logs / 'api.log').open('w'), stderr=subprocess.STDOUT)
    worker = subprocess.Popen([sys.executable, '-m', 'services.supervisor.worker'], cwd=ROOT, env=env,
                              stdout=(logs / 'supervisor.log').open('w'), stderr=subprocess.STDOUT)
    results = []
    try:
        restored_url = f'http://127.0.0.1:{args.port}'
        _wait_health(restored_url)
        client = httpx.Client(base_url=restored_url, headers={'X-Project-Id': args.workspace}, timeout=120)
        for case_id, original in originals.items():
            detail = client.get(f'/api/v1/investigations/{case_id}').raise_for_status().json()
            replay = client.post(f'/api/v1/investigations/{case_id}/replays', headers={'Idempotency-Key': uuid4().hex})
            if replay.status_code != 202:
                results.append({'case': detail['title'], 'status': 'failed', 'error': replay.text[:300]})
                continue
            run = replay.json()
            deadline = time.monotonic() + 120
            while run['state'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(1)
                run = client.get(f"/api/v1/comparisons/{run['id']}").raise_for_status().json()
            o, r = original['result'], run.get('result') or {}
            same = (run['state'] == 'succeeded'
                    and all(o[k] == r.get(k) for k in ('baseline_value', 'target_value', 'delta_target_minus_baseline'))
                    and _sha(original) == _sha(run))
            results.append({'case': detail['title'], 'status': 'passed' if same else 'failed', 'state': run['state'],
                            'original': [o['baseline_value'], o['target_value'], o['delta_target_minus_baseline'], _sha(original)],
                            'replayed': [r.get('baseline_value'), r.get('target_value'), r.get('delta_target_minus_baseline'), _sha(run)],
                            'error': run.get('error')})
    finally:
        for proc in (worker, api):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    report = {'status': 'passed' if results and all(r['status'] == 'passed' for r in results) else 'failed',
              'restored_into': str(target), 'restore': restored, 'cases': results}
    out = ROOT / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2, default=str))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
