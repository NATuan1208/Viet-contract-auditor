"""Validate and smoke-test the production-local Iceberg lakehouse."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.iceberg_lakehouse import IcebergLakehouse, redact_uri
from pipeline.registry import load_source_registry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "config" / "legal_sources.yml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate PyIceberg SQL catalog and MinIO warehouse")
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES))
    parser.add_argument("--init-tables", action="store_true")
    parser.add_argument("--counts", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config, _ = load_source_registry(args.sources)
    lakehouse = IcebergLakehouse.from_registry_config(config)
    result = {
        "catalog_uri": redact_uri(lakehouse.config.catalog_uri),
        "warehouse": lakehouse.config.warehouse,
        "s3_endpoint": lakehouse.config.s3_endpoint,
        "init_tables": False,
        "counts": {},
    }
    if args.init_tables:
        lakehouse.ensure_tables()
        result["init_tables"] = True
    if args.counts:
        result["counts"] = lakehouse.table_counts()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
