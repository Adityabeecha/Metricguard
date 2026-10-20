"""Run the API and its comparison worker(s) in one container (hosted deployments).

    python -m services.serve

Environment:
    PORT / METRICGUARD_PORT    port to listen on (default 8000)
    METRICGUARD_WORKERS        comparison workers to keep running (default 1; 0 = API only)
    METRICGUARD_SEED_DEMO      true to load the demo workspace before starting (default false)
    METRICGUARD_MODE           isolated (default here): comparisons run in the worker

Workers that exit are restarted after a short pause; stopping this process stops them too.
Everything else (database, storage, runner, hosting switches) is read by the API and worker
from the same environment; see docs/deployment-plan.md.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time


def truthy(value: str | None) -> bool:
    return (value or '').strip().lower() in ('1', 'true', 'yes', 'on')


class WorkerPool:
    """Keeps ``count`` worker processes alive until ``stop()``."""

    def __init__(self, count: int, command=None, restart_delay: float = 2.0):
        self.count = count
        self.command = command or [sys.executable, '-m', 'services.supervisor.worker']
        self.restart_delay = restart_delay
        self.processes: list[subprocess.Popen | None] = [None] * count
        self.restarts = 0
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def _spawn(self, slot: int):
        self.processes[slot] = subprocess.Popen(self.command)

    def _watch(self):
        while not self._stopping.is_set():
            for slot, process in enumerate(self.processes):
                if self._stopping.is_set():
                    return
                if process is None or process.poll() is not None:
                    if process is not None:
                        self.restarts += 1
                        print(f'serve: worker {slot} exited ({process.returncode}); restarting', file=sys.stderr, flush=True)
                        if self._stopping.wait(self.restart_delay):
                            return
                    self._spawn(slot)
            self._stopping.wait(1.0)

    def start(self):
        if self.count <= 0:
            return
        self._thread = threading.Thread(target=self._watch, name='worker-pool', daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0):
        self._stopping.set()
        for process in self.processes:
            if process is not None and process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + timeout
        for process in self.processes:
            if process is None:
                continue
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
        if self._thread is not None:
            self._thread.join(timeout=2)


def main():
    os.environ.setdefault('METRICGUARD_MODE', 'isolated')
    port = int(os.environ.get('PORT') or os.environ.get('METRICGUARD_PORT') or '8000')
    workers = int(os.environ.get('METRICGUARD_WORKERS', '1'))
    if truthy(os.environ.get('METRICGUARD_SEED_DEMO')):
        subprocess.run([sys.executable, '-m', 'scripts.seed_demo'], check=True)
    pool = WorkerPool(workers if os.environ['METRICGUARD_MODE'] == 'isolated' else 0)
    pool.start()

    def shutdown(signum, _frame):
        pool.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    try:
        import uvicorn
        uvicorn.run('services.api.app:app', host='0.0.0.0', port=port, proxy_headers=True,
                    forwarded_allow_ips=os.environ.get('METRICGUARD_FORWARDED_ALLOW_IPS', '*'))
    finally:
        pool.stop()


if __name__ == '__main__':
    main()
