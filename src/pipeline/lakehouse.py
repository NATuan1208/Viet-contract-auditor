"""Filesystem-backed lakehouse writer used by local dry-runs and tests.

The production target is MinIO + Iceberg, configured in config/legal_sources.yml.
This writer creates the same bronze/silver/gold boundaries locally so crawler
and KG update logic can be verified before wiring a live Iceberg catalog.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import KGUpdateManifest, LegalDocumentRecord, RawArtifact


class LocalLakehouse:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.bronze_dir = self.root / "bronze"
        self.silver_dir = self.root / "silver"
        self.gold_dir = self.root / "gold"

    def ensure_dirs(self) -> None:
        for path in (self.bronze_dir, self.silver_dir, self.gold_dir):
            path.mkdir(parents=True, exist_ok=True)

    def write_bronze(self, raw: RawArtifact) -> Path:
        self.ensure_dirs()
        run_id = raw.crawl_run_id or "adhoc"
        target_dir = self.bronze_dir / raw.source_item.source_id / raw.fetched_at[:10] / run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        body_path = target_dir / f"{raw.checksum}.bin"
        meta_path = target_dir / f"{raw.checksum}.json"
        body_path.write_bytes(raw.content)
        meta_path.write_text(
            json.dumps(raw.to_dict(include_content=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return body_path

    def write_silver(self, record: LegalDocumentRecord) -> Path:
        self.ensure_dirs()
        target_dir = self.silver_dir / record.source_id / record.doc_id.replace(":", "_")
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{record.checksum}.json"
        path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def append_gold_manifest(self, manifest: KGUpdateManifest) -> Path:
        self.ensure_dirs()
        path = self.gold_dir / "kg_update_manifest.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest.to_dict(), ensure_ascii=False) + "\n")
        return path

    def backlog_count(self) -> int:
        path = self.gold_dir / "kg_update_manifest.jsonl"
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
