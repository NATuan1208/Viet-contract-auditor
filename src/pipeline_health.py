"""Production-local health check for storage, lakehouse, crawler state, and KG backlog."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

from pipeline.kg_update import load_manifest
from pipeline.lakehouse import LocalLakehouse
from pipeline.registry import load_source_registry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "config" / "legal_sources.yml"
DEFAULT_LAKEHOUSE = ROOT / "data" / "lakehouse"
DEFAULT_STATE = ROOT / "data" / "pipeline_state" / "legal_doc_versions.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline health check")
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES))
    parser.add_argument("--lakehouse-root", default=str(DEFAULT_LAKEHOUSE))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE))
    return parser.parse_args()


def main() -> None:
    _configure_stdout()
    args = parse_args()
    _, sources = load_source_registry(args.sources)
    lakehouse = LocalLakehouse(args.lakehouse_root)
    status = {
        "source_registry": {"status": "healthy", "source_count": len(sources)},
        "storage_ports": {
            "neo4j": _tcp("127.0.0.1", 7687),
            "qdrant": _tcp("127.0.0.1", 6333),
            "postgres": _tcp("127.0.0.1", 5433),
            "minio": _tcp("127.0.0.1", 9000),
            "tika": _tcp("127.0.0.1", 9998),
        },
        "lakehouse": {
            "root": str(Path(args.lakehouse_root).resolve()),
            "exists": Path(args.lakehouse_root).exists(),
            "kg_update_backlog_raw": lakehouse.backlog_count(),
            "kg_update_backlog_unique": _unique_backlog_count(args.lakehouse_root),
        },
        "crawler_state": {
            "path": str(Path(args.state_path).resolve()),
            "exists": Path(args.state_path).exists(),
        },
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))


def _tcp(host: str, port: int) -> dict[str, object]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.8)
    try:
        sock.connect((host, port))
        return {"status": "healthy", "host": host, "port": port}
    except OSError as exc:
        return {"status": "error", "host": host, "port": port, "error": str(exc)}
    finally:
        sock.close()


def _unique_backlog_count(lakehouse_root: str | Path) -> int:
    return len(load_manifest(Path(lakehouse_root) / "gold" / "kg_update_manifest.jsonl"))


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    main()
