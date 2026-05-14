"""Config-driven legal source registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .models import SourceConfig


def load_source_registry(path: str | Path) -> tuple[dict[str, Any], list[SourceConfig]]:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    sources = [SourceConfig(**entry) for entry in raw.get("sources", [])]
    validate_sources(sources)
    return raw, sorted(sources, key=lambda item: item.priority, reverse=True)


def validate_sources(sources: list[SourceConfig]) -> None:
    seen: set[str] = set()
    errors: list[str] = []

    for source in sources:
        if source.source_id in seen:
            errors.append(f"duplicate source_id: {source.source_id}")
        seen.add(source.source_id)

        if not source.domain_whitelist:
            errors.append(f"{source.source_id}: domain_whitelist is required")

        base_host = urlparse(source.base_url).hostname
        if not base_host:
            errors.append(f"{source.source_id}: invalid base_url")
        elif base_host not in source.domain_whitelist:
            errors.append(
                f"{source.source_id}: base_url host {base_host} is not in domain_whitelist"
            )

        if source.robots_policy != "obey":
            errors.append(f"{source.source_id}: robots_policy must be 'obey'")

        if source.discovery_only and "detail" in source.crawl_methods:
            errors.append(f"{source.source_id}: discovery-only sources cannot crawl detail")

    if errors:
        joined = "\n - ".join(errors)
        raise ValueError(f"Invalid legal source registry:\n - {joined}")
