"""Discover official legal updates and optionally write lakehouse manifests.

Run examples:
    uv run python src/crawl_legal_sources.py --since 2026-05-01 --dry-run
    uv run python src/crawl_legal_sources.py --since 2026-05-01 --write-lakehouse
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.attachment_extraction import (
    attachment_item,
    extract_attachment_text,
    fetch_attachment,
    merge_attachment_extractions,
)
from pipeline.connectors import connector_for
from pipeline.iceberg_lakehouse import IcebergLakehouse
from pipeline.lakehouse import LocalLakehouse
from pipeline.registry import load_source_registry
from pipeline.versioning import DocumentVersionStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "config" / "legal_sources.yml"
DEFAULT_LAKEHOUSE = ROOT / "data" / "lakehouse"
DEFAULT_STATE = ROOT / "data" / "pipeline_state" / "legal_doc_versions.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily legal-source crawler")
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES), help="Path to legal_sources.yml")
    parser.add_argument("--source-id", action="append", help="Limit to one or more source IDs")
    parser.add_argument("--since", help="UTC date/datetime lower bound, for example 2026-05-01")
    parser.add_argument("--limit", type=int, help="Max discovered items per source")
    parser.add_argument("--dry-run", action="store_true", help="Discover only; do not fetch or write")
    parser.add_argument("--write-lakehouse", action="store_true", help="Fetch, parse, and write bronze/silver/gold")
    parser.add_argument("--iceberg", action="store_true", help="Also append records to the PyIceberg lakehouse")
    parser.add_argument("--extract-attachments", action="store_true", help="Download official attachments and merge extracted text into silver records")
    parser.add_argument("--attachment-limit", type=int, default=3, help="Max attachments to fetch per document")
    parser.add_argument("--min-attachment-chars", type=int, default=200, help="Minimum extracted chars before attachment text can enter silver/KG")
    parser.add_argument("--tika-url", default=os.getenv("TIKA_SERVER_URL"), help="Optional Apache Tika server URL for OCR/legacy Office extraction")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on the first item-level error")
    parser.add_argument("--crawl-run-id", help="Stable ID for this crawl run; generated when omitted")
    parser.add_argument("--lakehouse-root", default=str(DEFAULT_LAKEHOUSE))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE))
    return parser.parse_args()


def main() -> None:
    _configure_stdout()
    args = parse_args()
    since = _parse_since(args.since)
    raw_config, sources = load_source_registry(args.sources)
    if args.source_id:
        wanted = set(args.source_id)
        sources = [source for source in sources if source.source_id in wanted]

    lakehouse = LocalLakehouse(args.lakehouse_root)
    should_write = args.write_lakehouse and not args.dry_run
    crawl_run_id = args.crawl_run_id or _crawl_run_id()
    iceberg = IcebergLakehouse.from_registry_config(raw_config) if args.iceberg and should_write else None
    if iceberg is not None:
        iceberg.ensure_tables()
    version_store = DocumentVersionStore(args.state_path)
    summary: list[dict] = []

    for source in sources:
        connector = connector_for(source)
        items = connector.discover(since=since, limit=args.limit)
        source_summary = {
            "source_id": source.source_id,
            "crawl_run_id": crawl_run_id,
            "role": source.role,
            "discovered": len(items),
            "item_samples": [_item_sample(item) for item in items[:3]],
            "written": 0,
            "kg_updates": 0,
            "kg_skipped": 0,
            "discovery_only_skipped": 0,
            "attachments_attempted": 0,
            "attachments_extracted": 0,
            "attachments_needing_review": 0,
            "iceberg": bool(iceberg),
            "iceberg_silver_appended": 0,
            "iceberg_silver_skipped": 0,
            "errors": 0,
            "error_samples": [],
            "dry_run": bool(args.dry_run or not args.write_lakehouse),
        }

        if should_write:
            if source.discovery_only:
                source_summary["discovery_only_skipped"] = len(items)
                summary.append(source_summary)
                continue
            for item in items:
                try:
                    raw = connector.fetch(item, crawl_run_id=crawl_run_id)
                    lakehouse.write_bronze(raw)
                    if iceberg is not None:
                        iceberg.append_bronze(raw)
                    record = connector.parse(raw)
                    if args.extract_attachments:
                        record, attachment_stats = _extract_attachments_for_record(
                            record=record,
                            allowed_hosts=set(source.domain_whitelist),
                            lakehouse=lakehouse,
                            iceberg=iceberg,
                            crawl_run_id=crawl_run_id,
                            attachment_limit=args.attachment_limit,
                            min_chars=args.min_attachment_chars,
                            tika_server_url=args.tika_url,
                        )
                        source_summary["attachments_attempted"] += attachment_stats["attempted"]
                        source_summary["attachments_extracted"] += attachment_stats["extracted"]
                        source_summary["attachments_needing_review"] += attachment_stats["needs_review"]
                    silver_path = lakehouse.write_silver(record)
                    if iceberg is not None:
                        if iceberg.append_silver(record):
                            source_summary["iceberg_silver_appended"] += 1
                        else:
                            source_summary["iceberg_silver_skipped"] += 1
                    source_summary["written"] += 1
                    if record.document_type != "LegalDocument":
                        source_summary["kg_skipped"] += 1
                    else:
                        manifest = version_store.plan_update(record, silver_record_path=str(silver_path))
                        if manifest is not None:
                            lakehouse.append_gold_manifest(manifest)
                            if iceberg is not None:
                                iceberg.append_gold_manifest(manifest)
                            version_store.commit(manifest)
                            source_summary["kg_updates"] += 1
                except Exception as exc:
                    source_summary["errors"] += 1
                    if len(source_summary["error_samples"]) < 5:
                        source_summary["error_samples"].append(
                            {
                                "url": item.url,
                                "error": str(exc),
                            }
                        )
                    if args.fail_fast:
                        raise

        summary.append(source_summary)

    print(json.dumps({"sources": summary}, ensure_ascii=False, indent=2))


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    if len(value) == 10:
        value = f"{value}T00:00:00+00:00"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _crawl_run_id() -> str:
    return datetime.now(timezone.utc).strftime("crawl-%Y%m%dT%H%M%SZ")


def _extract_attachments_for_record(
    record,
    allowed_hosts: set[str],
    lakehouse: LocalLakehouse,
    iceberg,
    crawl_run_id: str,
    attachment_limit: int,
    min_chars: int,
    tika_server_url: str | None,
):
    attachments = list(record.metadata.get("attachments") or [])[:attachment_limit]
    extractions = []
    attempted = 0
    for index, attachment in enumerate(attachments, start=1):
        url = attachment.get("url")
        if not url:
            continue
        attempted += 1
        item = attachment_item(record, attachment, index)
        raw_attachment = fetch_attachment(item, crawl_run_id=crawl_run_id, allowed_hosts=allowed_hosts)
        lakehouse.write_bronze(raw_attachment)
        if iceberg is not None:
            iceberg.append_bronze(raw_attachment)
        extractions.append(
            extract_attachment_text(
                raw_attachment,
                min_chars=min_chars,
                tika_server_url=tika_server_url,
            )
        )
    merged = merge_attachment_extractions(record, extractions, min_chars=min_chars)
    return merged, {
        "attempted": attempted,
        "extracted": sum(1 for item in extractions if item.status == "extracted" and item.char_count >= min_chars),
        "needs_review": sum(1 for item in extractions if item.needs_review),
    }


def _item_sample(item) -> dict:
    return {
        "title": item.title,
        "url": item.url,
        "external_id": item.external_id,
        "canonical_number": item.metadata.get("canonical_number"),
        "issue_date": item.metadata.get("issue_date"),
    }


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    main()
