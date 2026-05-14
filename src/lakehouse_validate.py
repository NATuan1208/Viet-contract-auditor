"""Validate source registry and local lakehouse readiness."""

from __future__ import annotations

import argparse
import importlib.util
import json
import socket
from pathlib import Path
from urllib.parse import urlparse

from pipeline.iceberg_lakehouse import IcebergLakehouse, redact_uri
from pipeline.lakehouse import LocalLakehouse
from pipeline.registry import load_source_registry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "config" / "legal_sources.yml"
DEFAULT_LAKEHOUSE = ROOT / "data" / "lakehouse"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate lakehouse and catalog configuration")
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES))
    parser.add_argument("--lakehouse-root", default=str(DEFAULT_LAKEHOUSE))
    parser.add_argument("--check-services", action="store_true", help="Also check Postgres and MinIO TCP ports")
    parser.add_argument("--check-iceberg", action="store_true", help="Check Iceberg table counts")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config, sources = load_source_registry(args.sources)
    lakehouse = LocalLakehouse(args.lakehouse_root)
    lakehouse.ensure_dirs()

    lakehouse_cfg = config.get("lakehouse", {})
    catalog_cfg = lakehouse_cfg.get("catalog", {})
    object_store_cfg = lakehouse_cfg.get("object_store", {})

    pyiceberg_available = importlib.util.find_spec("pyiceberg") is not None
    checks = {
        "source_registry": {"status": "healthy", "source_count": len(sources)},
        "local_lakehouse": {
            "status": "healthy",
            "root": str(Path(args.lakehouse_root).resolve()),
            "backlog": lakehouse.backlog_count(),
        },
        "pyiceberg": {
            "status": "available" if pyiceberg_available else "not_installed",
            "note": "Live Iceberg catalog writes are available."
            if pyiceberg_available
            else "Install pyiceberg before enabling live Iceberg catalog writes.",
        },
        "catalog": _redact_catalog(catalog_cfg),
        "object_store": {
            key: value for key, value in object_store_cfg.items() if "secret" not in key.lower()
        },
    }

    if args.check_services:
        checks["services"] = {
            "postgres": _tcp_check(_host_port_from_uri(catalog_cfg.get("uri", ""))),
            "minio": _tcp_check(_host_port_from_uri(object_store_cfg.get("endpoint", ""))),
        }
    if args.check_iceberg:
        iceberg = IcebergLakehouse.from_registry_config(config)
        iceberg.ensure_tables()
        checks["iceberg"] = {
            "status": "healthy",
            "catalog_uri": redact_uri(iceberg.config.catalog_uri),
            "warehouse": iceberg.config.warehouse,
            "s3_endpoint": iceberg.config.s3_endpoint,
            "counts": iceberg.table_counts(),
        }

    print(json.dumps(checks, ensure_ascii=False, indent=2))


def _host_port_from_uri(uri: str) -> tuple[str, int] | None:
    parsed = urlparse(uri)
    if not parsed.hostname:
        return None
    if parsed.port:
        return parsed.hostname, parsed.port
    return parsed.hostname, 443 if parsed.scheme == "https" else 80


def _tcp_check(target: tuple[str, int] | None) -> dict[str, object]:
    if target is None:
        return {"status": "skipped", "reason": "missing host/port"}
    host, port = target
    fallback = {
        ("postgres", 5432): ("127.0.0.1", 5433),
        ("minio", 9000): ("127.0.0.1", 9000),
    }.get((host, port))
    first = _tcp_connect(host, port)
    if first["status"] == "healthy" or fallback is None:
        return first
    second_host, second_port = fallback
    second = _tcp_connect(second_host, second_port)
    second["fallback_from"] = f"{host}:{port}"
    return second


def _tcp_connect(host: str, port: int) -> dict[str, object]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect((host, port))
        return {"status": "healthy", "host": host, "port": port}
    except OSError as exc:
        return {"status": "error", "host": host, "port": port, "error": str(exc)}
    finally:
        sock.close()


def _redact_catalog(catalog_cfg: dict) -> dict:
    redacted = dict(catalog_cfg)
    if "uri" in redacted:
        redacted["uri"] = redact_uri(str(redacted["uri"]))
    return redacted


if __name__ == "__main__":
    main()
