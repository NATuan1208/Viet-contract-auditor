"""Daily legal data pipeline scaffolding for official-first RAG updates."""

from __future__ import annotations

from .models import (
    KGUpdateManifest,
    LegalDocumentRecord,
    RawArtifact,
    SourceConfig,
    SourceItem,
)

__all__ = [
    "KGUpdateManifest",
    "LegalDocumentRecord",
    "RawArtifact",
    "SourceConfig",
    "SourceItem",
]
