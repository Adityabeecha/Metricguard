"""Live Docker check of runner failure modes the normal live check does not cover.

Uses the real runner image and the supervisor's own container flags, but replaces
the job program with a deliberately misbehaving one:

  timeout   sleeps past the run deadline  -> INGEST_TIMEOUT, container killed
  oom       allocates beyond the 1 GiB cap -> INGEST_FAILED (exit 137)
  crash     exits non-zero mid-run         -> INGEST_FAILED
  network   tries to open a socket         -> no network inside the container

After each case no container named metricguard-* may remain.

    .venv\\Scripts\\python -m scripts.verify_runner_failure_modes [--output data/evaluation/runner_failure_modes.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.supervisor import worker  # noqa: E402

PROGRAMS = {
    'timeout': 'import time; time.sleep(120)',
    'oom': "chunks=[]\nwhile True: chunks.append(bytearray(64*1024*1024))",
    'crash': 'import sys; sys.exit(3)',
    'network': ("import socket, pathlib\n"
                "try:\n socket.create_connection(('1.1.1.1', 53), timeout=3); r='connected'\n"
                "except OSError as e:\n r='blocked: ' + type(e).__name__\n"
                "pathlib.Path('/job/output/network.txt').write_text(r)\nraise SystemExit(4)"),
}


class FaultLauncher(worker.DockerLauncher):
    """Same isolation flags as production; only the entrypoint and program differ."""

    def __init__(self, program: str):
        super().__init__()
        self.program = program
        self.names: list[str] = []

    def start(self, input_dir, output_dir, command=None):
        original = subprocess.Popen

        def popen(args, *a, **kw):
            image_at = args.index(self.image)
            patched = [*args[:image_at], '--entrypoint', 'python', self.image, '-c', self.program]
            return original(patched, *a, **kw)

        subprocess.Popen = popen
        try:
            process = super().start(input_dir, output_dir, command)
        finally:
            subprocess.Popen = original
        self.names.append(process.container_name)
        return process


def leftover(names: list[str]) -> list[str]:
    out = subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}'], capture_output=True, text=True, timeout=20).stdout.split()
    return [n for n in names if n in out]


def run_case(name: str, run_seconds: int) -> dict:
    launcher = FaultLauncher(PROGRAMS[name])
    with tempfile.TemporaryDirectory(prefix='mg-fault-', dir=ROOT / 'data') as tmp:
        tmp = Path(tmp)
        source = tmp / 'source.csv'
        source.write_text('row_id,amount\nA,1.00\n', encoding='utf-8')
        started = time.monotonic()
        outcome = 'completed'
        worker.RUN_SECONDS, saved = run_seconds, worker.RUN_SECONDS
        network_note = None
        try:
            if name == 'network':
                # Run directly so the output folder can be read before cleanup.
                out_dir, in_dir = tmp / 'out', tmp / 'in'
                out_dir.mkdir(); in_dir.mkdir(); out_dir.chmod(0o777)
                proc = launcher.start(in_dir, out_dir, 'ingest')
                proc.wait(timeout=60)
                note = out_dir / 'network.txt'
                network_note = note.read_text() if note.is_file() else 'no output'
                outcome = f'exit {proc.returncode}'
            else:
                worker.isolated_ingest(source, tmp / 'snap.duckdb', {'name': 'fault'}, launcher=launcher, job_root=tmp / 'jobs')
        except ValueError as error:
            outcome = str(error)
        finally:
            worker.RUN_SECONDS = saved
        elapsed = round(time.monotonic() - started, 1)
        time.sleep(2)
        remaining = leftover(launcher.names)
    expected = {'timeout': 'INGEST_TIMEOUT', 'oom': 'INGEST_FAILED', 'crash': 'INGEST_FAILED'}.get(name)
    passed = (outcome == expected) if expected else (network_note or '').startswith('blocked')
    return {'case': name, 'status': 'passed' if passed and not remaining else 'failed', 'outcome': outcome,
            'expected': expected or 'socket blocked', 'network': network_note, 'seconds': elapsed, 'containers_left': remaining}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/evaluation/runner_failure_modes.json')
    parser.add_argument('--run-seconds', type=int, default=8, help='Deadline used for the timeout case.')
    args = parser.parse_args(argv)
    results = [run_case(name, args.run_seconds) for name in PROGRAMS]  # sequential: one container at a time
    report = {'status': 'passed' if all(r['status'] == 'passed' for r in results) else 'failed',
              'image': worker.DockerLauncher().image, 'cases': results}
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
