"""Idempotent document version detection for incremental KG updates."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .models import KGUpdateManifest, LegalDocumentRecord, utc_now_iso


class DocumentVersionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def plan_update(
        self,
        record: LegalDocumentRecord,
        silver_record_path: str | None = None,
    ) -> KGUpdateManifest | None:
        current = self._data.get(record.doc_id)
        if current and current.get("checksum") == record.checksum:
            return None

        previous_version = int(current.get("version", 0)) if current else None
        next_version = (previous_version or 0) + 1
        action = "insert" if current is None else "replace"
        reason = "new_document" if current is None else "checksum_changed"
        return KGUpdateManifest(
            manifest_id=str(uuid4()),
            doc_id=record.doc_id,
            source_id=record.source_id,
            action=action,
            checksum=record.checksum,
            current_version=next_version,
            previous_version=previous_version,
            supersedes_checksum=current.get("checksum") if current else None,
            created_at=utc_now_iso(),
            source_url=record.source_url,
            crawl_run_id=record.crawl_run_id,
            silver_record_path=silver_record_path,
            reason=reason,
        )

    def commit(self, manifest: KGUpdateManifest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data[manifest.doc_id] = {
            "checksum": manifest.checksum,
            "version": manifest.current_version,
            "updated_at": manifest.created_at,
            "source_id": manifest.source_id,
            "source_url": manifest.source_url,
        }
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
