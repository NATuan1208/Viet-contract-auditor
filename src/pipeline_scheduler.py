"""Lightweight daily scheduler for production-local pipeline profile."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the legal crawl batch on an interval")
    parser.add_argument("--interval-hours", type=float, default=24.0)
    parser.add_argument("--since-days", type=int, default=1)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    while True:
        since = (datetime.now(timezone.utc) - timedelta(days=args.since_days)).date().isoformat()
        command = [
            sys.executable,
            str(ROOT / "src" / "crawl_legal_sources.py"),
            "--since",
            since,
            "--write-lakehouse",
        ]
        if args.dry_run:
            command.append("--dry-run")
        subprocess.run(command, cwd=str(ROOT), check=False)
        if args.once:
            return
        time.sleep(max(args.interval_hours, 0.1) * 3600)


if __name__ == "__main__":
    main()
