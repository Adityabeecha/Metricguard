"""MetricGuard CLI entry point for local development, CI, and pilot automation."""
from __future__ import annotations

import sys
from services.cli import run_cli

if __name__ == '__main__':
    sys.exit(run_cli())
