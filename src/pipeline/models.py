"""Typed records shared by crawler, lakehouse, and KG update steps."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    name: str
    tier: int
    role: str
    base_url: str
    domain_whitelist: list[str]
    crawl_methods: list[str]
    rate_limit_per_minute: int
    robots_policy: str
    priority: int
    license_note: str
    rss_feeds: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_official(self) -> bool:
        return self.tier in {0, 1}

    @property
    def discovery_only(self) -> bool:
        return self.tier >= 2 or "discovery_only" in self.role


@dataclass(frozen=True)
class SourceItem:
    source_id: str
    url: str
    title: str
    discovered_at: str = field(default_factory=utc_now_iso)
    published_at: str | None = None
    updated_at: str | None = None
    external_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawArtifact:
    source_item: SourceItem
    content: bytes
    content_type: str
    headers: dict[str, str]
    fetched_at: str
    checksum: str
    crawl_run_id: str | None = None

    def to_dict(self, include_content: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        if include_content:
            payload["content"] = self.content.decode("utf-8", errors="replace")
        else:
            payload.pop("content", None)
        return payload


@dataclass(frozen=True)
class LegalDocumentRecord:
    doc_id: str
    source_id: str
    canonical_number: str
    issue_date: str
    issuer: str
    title: str
    text: str
    source_url: str
    checksum: str
    fetched_at: str
    crawl_run_id: str | None = None
    effective_status: str = "unknown"
    document_type: str = "LegalDocument"
    articles: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KGUpdateManifest:
    manifest_id: str
    doc_id: str
    source_id: str
    action: str
    checksum: str
    current_version: int
    created_at: str
    source_url: str
    crawl_run_id: str | None = None
    silver_record_path: str | None = None
    previous_version: int | None = None
    supersedes_checksum: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
