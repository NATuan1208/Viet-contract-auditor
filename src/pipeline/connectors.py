"""Source connector interfaces and a conservative RSS implementation."""

from __future__ import annotations

import abc
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

from .models import LegalDocumentRecord, RawArtifact, SourceConfig, SourceItem, utc_now_iso
from .normalization import checksum_bytes, clean_text, split_articles, stable_doc_id

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; VietContractAuditor/1.0; "
        "+https://github.com/local/viet-contract-auditor)"
    )
}
THUVIENPHAPLUAT_NEW_DOCUMENTS_URL = "https://thuvienphapluat.vn/van-ban-moi/van-ban-moi"
VANBAN_CHINHPHU_LIST_URL = (
    "https://vanban.chinhphu.vn/he-thong-van-ban?classid=0&maxresults=50&mode=1"
)
DOCUMENT_NUMBER_RE = re.compile(r"\b\d{1,4}/\d{4}/[A-Za-z0-9Đđ\-]+")
SHORT_DOCUMENT_NUMBER_RE = re.compile(r"\b\d{1,4}/[A-Za-zĐđ]+(?:-[A-Za-z0-9Đđ]+)+")
DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
FILE_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")


class LegalSourceConnector(abc.ABC):
    def __init__(self, source: SourceConfig, timeout_seconds: int = 20) -> None:
        self.source = source
        self.timeout_seconds = timeout_seconds

    @abc.abstractmethod
    def discover(self, since: datetime | None = None, limit: int | None = None) -> list[SourceItem]:
        raise NotImplementedError

    def fetch(self, item: SourceItem, crawl_run_id: str | None = None) -> RawArtifact:
        self._assert_allowed_url(item.url)
        response = requests.get(item.url, timeout=self.timeout_seconds, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        content = response.content
        return RawArtifact(
            source_item=item,
            content=content,
            content_type=response.headers.get("content-type", "application/octet-stream"),
            headers=dict(response.headers),
            fetched_at=utc_now_iso(),
            checksum=checksum_bytes(content),
            crawl_run_id=crawl_run_id,
        )

    def parse(self, raw: RawArtifact) -> LegalDocumentRecord:
        text = _html_to_text(raw.content)
        title = raw.source_item.title or _first_line(text) or raw.source_item.url
        metadata = dict(raw.source_item.metadata)
        canonical_number = str(
            metadata.get("canonical_number")
            or metadata.get("external_id")
            or raw.source_item.external_id
            or ""
        )
        issue_date = str(metadata.get("issue_date") or raw.source_item.published_at or "")
        issuer = str(metadata.get("issuer") or self.source.name)
        doc_id = stable_doc_id(self.source.source_id, canonical_number or title, issue_date, issuer)
        return LegalDocumentRecord(
            doc_id=doc_id,
            source_id=self.source.source_id,
            canonical_number=canonical_number,
            issue_date=issue_date,
            issuer=issuer,
            title=title,
            text=text,
            source_url=raw.source_item.url,
            checksum=raw.checksum,
            fetched_at=raw.fetched_at,
            crawl_run_id=raw.crawl_run_id,
            effective_status=str(metadata.get("effective_status") or "unknown"),
            document_type=str(metadata.get("document_type") or _document_type_for_metadata(metadata)),
            articles=split_articles(text),
            metadata={
                **metadata,
                "provenance": {
                    "source_id": self.source.source_id,
                    "source_url": raw.source_item.url,
                    "fetched_at": raw.fetched_at,
                    "checksum": raw.checksum,
                    "crawl_run_id": raw.crawl_run_id,
                    "license_note": self.source.license_note,
                },
                "crawl_run_id": raw.crawl_run_id,
            },
        )

    def _assert_allowed_url(self, url: str) -> None:
        host = urlparse(url).hostname
        if host not in self.source.domain_whitelist:
            raise ValueError(f"{url} is outside whitelist for {self.source.source_id}")


class RssLegalConnector(LegalSourceConnector):
    def discover(self, since: datetime | None = None, limit: int | None = None) -> list[SourceItem]:
        items: list[SourceItem] = []
        for feed in self.source.rss_feeds:
            feed_url = feed["url"]
            self._assert_allowed_url(feed_url)
            response = requests.get(feed_url, timeout=self.timeout_seconds, headers=DEFAULT_HEADERS)
            response.raise_for_status()
            items.extend(parse_rss_items(response.text, self.source, feed.get("id", feed_url), since))
            if limit is not None and len(items) >= limit:
                return items[:limit]
        return items


class UnsupportedConnector(LegalSourceConnector):
    def discover(self, since: datetime | None = None, limit: int | None = None) -> list[SourceItem]:
        return []


class ThuvienPhapLuatDiscoveryConnector(LegalSourceConnector):
    """Discovery-only connector for commercial metadata signals."""

    def discover(self, since: datetime | None = None, limit: int | None = None) -> list[SourceItem]:
        self._assert_allowed_url(THUVIENPHAPLUAT_NEW_DOCUMENTS_URL)
        response = requests.get(
            THUVIENPHAPLUAT_NEW_DOCUMENTS_URL,
            timeout=self.timeout_seconds,
            headers=DEFAULT_HEADERS,
        )
        response.raise_for_status()
        items = parse_thuvienphapluat_new_documents(response.text, self.source, since)
        return items[:limit] if limit is not None else items

    def fetch(self, item: SourceItem, crawl_run_id: str | None = None) -> RawArtifact:
        raise ValueError("thuvienphapluat_discovery is discovery-only; full-text fetch is disabled")


class VanBanChinhPhuConnector(LegalSourceConnector):
    def discover(self, since: datetime | None = None, limit: int | None = None) -> list[SourceItem]:
        self._assert_allowed_url(VANBAN_CHINHPHU_LIST_URL)
        response = requests.get(
            VANBAN_CHINHPHU_LIST_URL,
            timeout=self.timeout_seconds,
            headers=DEFAULT_HEADERS,
        )
        response.raise_for_status()
        items = parse_vanban_chinhphu_listing(response.text, self.source, since)
        return items[:limit] if limit is not None else items

    def parse(self, raw: RawArtifact) -> LegalDocumentRecord:
        return parse_vanban_chinhphu_detail_record(raw, self.source)


def connector_for(source: SourceConfig) -> LegalSourceConnector:
    if source.source_id == "thuvienphapluat_discovery":
        return ThuvienPhapLuatDiscoveryConnector(source)
    if source.source_id == "vanban_chinhphu":
        return VanBanChinhPhuConnector(source)
    if "rss" in source.crawl_methods:
        return RssLegalConnector(source)
    return UnsupportedConnector(source)


def parse_rss_items(
    rss_xml: str,
    source: SourceConfig,
    feed_id: str,
    since: datetime | None = None,
) -> list[SourceItem]:
    root = ET.fromstring(rss_xml)
    parsed: list[SourceItem] = []
    for item in _iter_rss_items(root):
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        if not link:
            continue
        published_at = _parse_date(_child_text(item, "pubDate") or _child_text(item, "updated"))
        if since and published_at and published_at < since:
            continue
        parsed.append(
            SourceItem(
                source_id=source.source_id,
                url=link,
                title=clean_text(title),
                published_at=published_at.isoformat() if published_at else None,
                external_id=_child_text(item, "guid") or link,
                metadata={"feed_id": feed_id},
            )
        )
    return parsed


def parse_thuvienphapluat_new_documents(
    html: str,
    source: SourceConfig,
    since: datetime | None = None,
) -> list[SourceItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[SourceItem] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if "/van-ban/" not in href or ".aspx" not in href:
            continue
        url = urljoin(source.base_url, href)
        if url in seen:
            continue
        title = clean_text(anchor.get_text(" "))
        if not _looks_like_legal_title(title):
            continue
        container_text = _container_text(anchor)
        issue_date = _extract_labeled_date(container_text, ("Ban hành", "Ban hanh"))
        published_at = _published_at(issue_date)
        if since and published_at and published_at < since:
            continue
        canonical_number = _extract_document_number(title)
        parsed = urlparse(url)
        items.append(
            SourceItem(
                source_id=source.source_id,
                url=url,
                title=title,
                published_at=published_at.isoformat() if published_at else None,
                external_id=_path_like_id(parsed.path),
                metadata={
                    "source_use": "discovery_only",
                    "canonical_number": canonical_number,
                    "issue_date": issue_date,
                    "canonical_source_required": True,
                    "commercial_source_url": url,
                },
            )
        )
        seen.add(url)
    return items


def parse_vanban_chinhphu_listing(
    html: str,
    source: SourceConfig,
    since: datetime | None = None,
) -> list[SourceItem]:
    soup = BeautifulSoup(html, "html.parser")
    items_by_url: dict[str, SourceItem] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if "docid=" not in href or "pageid=27160" not in href:
            continue
        url = urljoin(source.base_url, href)
        docid = _query_value(url, "docid")
        text = _container_text(anchor)
        title = _best_title_for_listing(anchor, text)
        canonical_number = _extract_document_number(text) or docid
        issue_date = _extract_date(text)
        published_at = _published_at(issue_date)
        if since and published_at and published_at < since:
            continue
        current = items_by_url.get(url)
        if current and len(current.title) >= len(title):
            continue
        items_by_url[url] = SourceItem(
            source_id=source.source_id,
            url=url,
            title=title,
            published_at=published_at.isoformat() if published_at else None,
            external_id=docid,
            metadata={
                "crawl_method": "official_listing",
                "canonical_number": canonical_number,
                "issue_date": issue_date,
                "document_type": "LegalDocument",
                "listing_url": VANBAN_CHINHPHU_LIST_URL,
            },
        )
    return list(items_by_url.values())


def parse_vanban_chinhphu_detail_record(
    raw: RawArtifact,
    source: SourceConfig,
) -> LegalDocumentRecord:
    soup = BeautifulSoup(raw.content, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = _text_lines(soup)
    page_text = clean_text("\n".join(lines))
    fields = _labeled_fields(lines)
    metadata = dict(raw.source_item.metadata)

    canonical_number = (
        fields.get("So ky hieu")
        or fields.get("Số ký hiệu")
        or metadata.get("canonical_number")
        or _extract_document_number(page_text)
        or raw.source_item.external_id
        or ""
    )
    issue_date = _normalize_date(
        fields.get("Ngay ban hanh") or fields.get("Ngày ban hành") or str(metadata.get("issue_date") or "")
    )
    issuer = (
        fields.get("Co quan ban hanh")
        or fields.get("Cơ quan ban hành")
        or metadata.get("issuer")
        or source.name
    )
    document_type = fields.get("Loai van ban") or fields.get("Loại văn bản") or "LegalDocument"
    summary = fields.get("Trich yeu") or fields.get("Trích yếu") or ""
    effective_date = _normalize_date(
        fields.get("Ngay co hieu luc") or fields.get("Ngày có hiệu lực") or ""
    )
    title = raw.source_item.title or _first_line(page_text) or raw.source_item.url
    doc_id = stable_doc_id(source.source_id, str(canonical_number), issue_date, str(issuer))
    attachments = _attachment_links(soup, raw.source_item.url)
    record_text = "\n".join(
        line
        for line in (
            title,
            f"So ky hieu: {canonical_number}" if canonical_number else "",
            f"Ngay ban hanh: {issue_date}" if issue_date else "",
            f"Ngay co hieu luc: {effective_date}" if effective_date else "",
            f"Co quan ban hanh: {issuer}" if issuer else "",
            f"Trich yeu: {summary}" if summary else "",
            page_text,
        )
        if line
    )
    return LegalDocumentRecord(
        doc_id=doc_id,
        source_id=source.source_id,
        canonical_number=str(canonical_number),
        issue_date=issue_date,
        issuer=str(issuer),
        title=title,
        text=record_text,
        source_url=raw.source_item.url,
        checksum=raw.checksum,
        fetched_at=raw.fetched_at,
        crawl_run_id=raw.crawl_run_id,
        effective_status="unknown",
        document_type="LegalDocument",
        articles=split_articles(record_text),
        metadata={
            **metadata,
            "document_type_label": document_type,
            "effective_date": effective_date,
            "summary": summary,
            "attachments": attachments,
            "crawl_run_id": raw.crawl_run_id,
            "provenance": {
                "source_id": source.source_id,
                "source_url": raw.source_item.url,
                "fetched_at": raw.fetched_at,
                "checksum": raw.checksum,
                "crawl_run_id": raw.crawl_run_id,
                "license_note": source.license_note,
            },
        },
    )


def _iter_rss_items(root: ET.Element) -> Iterable[ET.Element]:
    for item in root.findall(".//item"):
        yield item
    for item in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        yield item


def _child_text(item: ET.Element, name: str) -> str:
    child = item.find(name)
    if child is None:
        child = item.find(f"{{http://www.w3.org/2005/Atom}}{name}")
    return (child.text or "").strip() if child is not None else ""


def _container_text(anchor) -> str:
    container = anchor.find_parent(["tr", "article", "li", "div"]) or anchor.parent or anchor
    return clean_text(container.get_text(" "))


def _looks_like_legal_title(title: str) -> bool:
    if len(title) < 20:
        return False
    normalized = title.lower()
    if normalized in {"tieng anh", "tiếng anh", "van ban goc", "văn bản gốc"}:
        return False
    return bool(_extract_document_number(title)) or any(
        keyword in normalized
        for keyword in (
            "nghi dinh",
            "nghị định",
            "thong tu",
            "thông tư",
            "quyet dinh",
            "quyết định",
            "nghi quyet",
            "nghị quyết",
            "luat ",
            "luật ",
        )
    )


def _extract_document_number(text: str) -> str:
    match = DOCUMENT_NUMBER_RE.search(text)
    if match:
        return match.group(0)
    match = SHORT_DOCUMENT_NUMBER_RE.search(text)
    return match.group(0) if match else ""


def _extract_labeled_date(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        pattern = re.compile(rf"{re.escape(label)}\s*:?\s*(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})", re.I)
        match = pattern.search(text)
        if match:
            return _normalize_date(match.group(1))
    return _extract_date(text)


def _extract_date(text: str) -> str:
    match = DATE_RE.search(text)
    return _normalize_date(match.group(0)) if match else ""


def _normalize_date(raw: str) -> str:
    match = DATE_RE.search(raw or "")
    if not match:
        return ""
    day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return f"{year:04d}-{month:02d}-{day:02d}"


def _published_at(date_value: str) -> datetime | None:
    if not date_value:
        return None
    try:
        return datetime.fromisoformat(f"{date_value}T00:00:00+00:00")
    except ValueError:
        return None


def _query_value(url: str, key: str) -> str:
    values = parse_qs(urlparse(url).query).get(key, [])
    return values[0] if values else ""


def _path_like_id(path: str) -> str:
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    return leaf.removesuffix(".aspx") or path


def _best_title_for_listing(anchor, container_text: str) -> str:
    anchor_text = clean_text(anchor.get_text(" "))
    if len(anchor_text) >= 20 and not re.fullmatch(r"[\d/.\-A-Za-zĐđ]+", anchor_text):
        return anchor_text
    without_dates = DATE_RE.sub("", container_text)
    return clean_text(without_dates) or anchor_text


def _text_lines(soup: BeautifulSoup) -> list[str]:
    return [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]


def _labeled_fields(lines: list[str]) -> dict[str, str]:
    labels = (
        "So ky hieu",
        "Số ký hiệu",
        "Ngay ban hanh",
        "Ngày ban hành",
        "Ngay co hieu luc",
        "Ngày có hiệu lực",
        "Loai van ban",
        "Loại văn bản",
        "Co quan ban hanh",
        "Cơ quan ban hành",
        "Nguoi ky",
        "Người ký",
        "Trich yeu",
        "Trích yếu",
    )
    fields: dict[str, str] = {}
    for index, line in enumerate(lines):
        for label in labels:
            if line == label and index + 1 < len(lines):
                fields.setdefault(label, lines[index + 1])
            elif line.startswith(label):
                value = line[len(label) :].strip(" :")
                if value:
                    fields.setdefault(label, value)
    return fields


def _attachment_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        path = urlparse(href).path.lower()
        if not path.endswith(FILE_EXTENSIONS):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        attachments.append({"url": url, "title": clean_text(anchor.get_text(" ")) or url.rsplit("/", 1)[-1]})
        seen.add(url)
    return attachments


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _html_to_text(content: bytes) -> str:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean_text(soup.get_text("\n"))


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _document_type_for_metadata(metadata: dict) -> str:
    if metadata.get("feed_id") == "cong_bao_moi_dang":
        return "OfficialGazetteIssue"
    return "LegalDocument"
