"""Normalization helpers for stable document IDs and article structure."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from typing import Any

ARTICLE_RE = re.compile(
    r"(?m)^\s*(?:Dieu|Điều)\s+(\d+[a-zA-Z]?)\s*[\.:]\s*(.*)$",
    flags=re.IGNORECASE,
)


def clean_text(raw_text: str) -> str:
    if not raw_text:
        return ""
    text = html.unescape(html.unescape(raw_text))
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def checksum_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def stable_doc_id(source_id: str, canonical_number: str, issue_date: str, issuer: str) -> str:
    parts = [source_id, canonical_number, issue_date, issuer]
    normalized = "|".join(_normalize_key_part(part) for part in parts)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"{source_id}:{digest}"


def split_articles(text: str) -> list[dict[str, Any]]:
    matches = list(ARTICLE_RE.finditer(text))
    articles: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        article_text = text[start:end].strip()
        articles.append(
            {
                "article_number": match.group(1),
                "article_title": match.group(2).strip(),
                "text": article_text,
                "start_offset": start,
                "end_offset": end,
            }
        )
    return articles


def _normalize_key_part(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    ascii_text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
