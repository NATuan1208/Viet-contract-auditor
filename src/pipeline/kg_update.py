"""Apply KG update manifests to LightRAG with explicit document IDs."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

from .models import KGUpdateManifest, LegalDocumentRecord


def load_manifest(path: str | Path) -> list[KGUpdateManifest]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        return []
    items: list[KGUpdateManifest] = []
    seen_updates: set[tuple[str, str, str, int]] = set()
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = KGUpdateManifest(**json.loads(line))
            update_key = (item.doc_id, item.checksum, item.action, item.current_version)
            if update_key in seen_updates:
                continue
            seen_updates.add(update_key)
            items.append(item)
    return items


def latest_manifest_items(items: list[KGUpdateManifest]) -> list[KGUpdateManifest]:
    """Keep only the latest pending manifest per document ID.

    Historical smoke runs can leave multiple manifests for the same doc_id. LightRAG
    receives explicit IDs, so applying all of them in one batch can reinsert the same
    document repeatedly. Prefer the highest version, then the latest file order.
    """
    latest: dict[str, tuple[int, int, KGUpdateManifest]] = {}
    for index, item in enumerate(items):
        current = latest.get(item.doc_id)
        candidate = (item.current_version, index, item)
        if current is None or (candidate[0], candidate[1]) >= (current[0], current[1]):
            latest[item.doc_id] = candidate
    return [entry[2] for entry in sorted(latest.values(), key=lambda entry: entry[1])]


def load_silver_record(path: str | Path) -> LegalDocumentRecord:
    return LegalDocumentRecord(**json.loads(Path(path).read_text(encoding="utf-8")))


async def apply_manifest_items(
    items: list[KGUpdateManifest],
    dry_run: bool = True,
    force_replace: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if dry_run:
        for item in items:
            results.append(
                {
                    "doc_id": item.doc_id,
                    "action": item.action,
                    "version": item.current_version,
                    "status": "dry_run",
                }
            )
        return results

    from core.lightrag_client import get_rag_client

    rag = await get_rag_client()
    for item in items:
        if not item.silver_record_path:
            raise ValueError(f"Manifest {item.manifest_id} has no silver_record_path")
        record = load_silver_record(item.silver_record_path)
        if item.action == "replace" or force_replace:
            await _delete_doc(rag, item.doc_id)
        await _insert_doc(rag, record)
        results.append(
            {
                "doc_id": item.doc_id,
                "action": item.action,
                "version": item.current_version,
                "status": "applied",
            }
        )
    return results


async def _delete_doc(rag: Any, doc_id: str) -> None:
    if hasattr(rag, "adelete_by_doc_id"):
        await rag.adelete_by_doc_id(doc_id)
        return
    if hasattr(rag, "delete_by_doc_id"):
        result = rag.delete_by_doc_id(doc_id)
        if inspect.isawaitable(result):
            await result
        return
    raise RuntimeError("LightRAG client does not expose delete_by_doc_id")


async def _insert_doc(rag: Any, record: LegalDocumentRecord) -> None:
    text = _document_text_with_provenance(record)
    if hasattr(rag, "ainsert"):
        kwargs = _insert_kwargs(rag.ainsert, text, record)
        await rag.ainsert(**kwargs)
        return
    kwargs = _insert_kwargs(rag.insert, text, record)
    result = rag.insert(**kwargs)
    if inspect.isawaitable(result):
        await result


def _insert_kwargs(insert_func: Any, text: str, record: LegalDocumentRecord) -> dict[str, Any]:
    params = inspect.signature(insert_func).parameters
    kwargs: dict[str, Any] = {"input": [text]}
    if "ids" in params:
        kwargs["ids"] = [record.doc_id]
    if "file_paths" in params:
        kwargs["file_paths"] = [record.source_url]
    if "track_id" in params and record.crawl_run_id:
        kwargs["track_id"] = record.crawl_run_id
    return kwargs


def _document_text_with_provenance(record: LegalDocumentRecord) -> str:
    header = "\n".join(
        [
            "PROVENANCE",
            f"doc_id: {record.doc_id}",
            f"source_id: {record.source_id}",
            f"source_url: {record.source_url}",
            f"canonical_number: {record.canonical_number}",
            f"issuer: {record.issuer}",
            f"issue_date: {record.issue_date}",
            f"checksum: {record.checksum}",
            f"crawl_run_id: {record.crawl_run_id or ''}",
            "END_PROVENANCE",
        ]
    )
    return f"{header}\n\n{record.text}"


def apply_manifest_items_sync(
    items: list[KGUpdateManifest],
    dry_run: bool = True,
    force_replace: bool = False,
) -> list[dict[str, Any]]:
    return asyncio.run(
        apply_manifest_items(items, dry_run=dry_run, force_replace=force_replace)
    )
