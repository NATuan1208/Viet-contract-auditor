"""PyIceberg-backed lakehouse writer for production-local runs."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pyarrow as pa
from pyiceberg.types import StringType

from .models import KGUpdateManifest, LegalDocumentRecord, RawArtifact

NAMESPACE = "legal"
BRONZE_TABLE = f"{NAMESPACE}.bronze_raw_artifacts"
SILVER_TABLE = f"{NAMESPACE}.silver_legal_documents"
GOLD_TABLE = f"{NAMESPACE}.gold_kg_update_manifest"


@dataclass(frozen=True)
class IcebergLakehouseConfig:
    catalog_uri: str
    warehouse: str
    s3_endpoint: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_region: str = "us-east-1"


class IcebergLakehouse:
    def __init__(self, config: IcebergLakehouseConfig) -> None:
        self.config = config
        self._catalog = None

    @classmethod
    def from_registry_config(cls, raw_config: dict) -> "IcebergLakehouse":
        lakehouse = raw_config.get("lakehouse", {})
        catalog = lakehouse.get("catalog", {})
        object_store = lakehouse.get("object_store", {})
        endpoint = os.getenv("LAKEHOUSE_S3_ENDPOINT") or _local_endpoint_if_needed(
            str(object_store.get("endpoint", "http://minio:9000"))
        )
        uri = os.getenv("LAKEHOUSE_CATALOG_URI") or _local_catalog_uri_if_needed(
            _normalize_postgres_uri(str(catalog.get("uri", "")))
        )
        return cls(
            IcebergLakehouseConfig(
                catalog_uri=uri,
                warehouse=os.getenv("LAKEHOUSE_WAREHOUSE") or str(catalog.get("warehouse", "")),
                s3_endpoint=endpoint,
                s3_access_key_id=os.getenv(str(object_store.get("access_key_env", ""))) or "minioadmin",
                s3_secret_access_key=os.getenv(str(object_store.get("secret_key_env", "")))
                or "minioadmin_secure_pwd",
            )
        )

    def catalog(self):
        if self._catalog is None:
            from pyiceberg.catalog import load_catalog

            props = {
                "type": "sql",
                "uri": self.config.catalog_uri,
                "warehouse": self.config.warehouse,
                "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
                "s3.endpoint": self.config.s3_endpoint,
                "s3.access-key-id": self.config.s3_access_key_id,
                "s3.secret-access-key": self.config.s3_secret_access_key,
                "s3.region": self.config.s3_region,
                "s3.anonymous": "false",
                "s3.force-virtual-addressing": "false",
            }
            self._catalog = load_catalog("legal_lakehouse", **props)
        return self._catalog

    def ensure_bucket(self) -> None:
        from pyarrow.fs import S3FileSystem

        parsed = urlparse(self.config.warehouse)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError(f"Warehouse must be an s3:// URI: {self.config.warehouse}")
        endpoint = urlparse(self.config.s3_endpoint)
        filesystem = S3FileSystem(
            access_key=self.config.s3_access_key_id,
            secret_key=self.config.s3_secret_access_key,
            region=self.config.s3_region,
            scheme=endpoint.scheme or "http",
            endpoint_override=endpoint.netloc,
            allow_bucket_creation=True,
            force_virtual_addressing=False,
        )
        filesystem.create_dir(parsed.netloc)

    def ensure_tables(self) -> None:
        self.ensure_bucket()
        catalog = self.catalog()
        catalog.create_namespace_if_not_exists(NAMESPACE)
        catalog.create_table_if_not_exists(BRONZE_TABLE, schema=bronze_schema())
        catalog.create_table_if_not_exists(SILVER_TABLE, schema=silver_schema())
        catalog.create_table_if_not_exists(GOLD_TABLE, schema=gold_schema())
        self._ensure_column(BRONZE_TABLE, "crawl_run_id")
        self._ensure_column(SILVER_TABLE, "crawl_run_id")
        self._ensure_column(GOLD_TABLE, "crawl_run_id")

    def append_bronze(self, raw: RawArtifact) -> None:
        self.catalog().load_table(BRONZE_TABLE).append(
            pa.Table.from_pylist([bronze_row(raw)], schema=bronze_schema())
        )

    def append_silver(self, record: LegalDocumentRecord) -> bool:
        if self._row_exists(SILVER_TABLE, {"doc_id": record.doc_id, "checksum": record.checksum}):
            return False
        self.catalog().load_table(SILVER_TABLE).append(
            pa.Table.from_pylist([silver_row(record)], schema=silver_schema())
        )
        return True

    def append_gold_manifest(self, manifest: KGUpdateManifest) -> bool:
        if self._row_exists(GOLD_TABLE, {"manifest_id": manifest.manifest_id}):
            return False
        self.catalog().load_table(GOLD_TABLE).append(
            pa.Table.from_pylist([gold_row(manifest)], schema=gold_schema())
        )
        return True

    def table_counts(self) -> dict[str, int]:
        catalog = self.catalog()
        counts = {}
        for identifier in (BRONZE_TABLE, SILVER_TABLE, GOLD_TABLE):
            table = catalog.load_table(identifier)
            counts[identifier] = table.scan().to_arrow().num_rows
        return counts

    def _row_exists(self, identifier: str, expected: dict[str, object]) -> bool:
        table = self.catalog().load_table(identifier).scan().to_arrow()
        if table.num_rows == 0:
            return False
        rows = table.select(list(expected)).to_pylist()
        return any(all(row.get(key) == value for key, value in expected.items()) for row in rows)

    def _ensure_column(self, identifier: str, column_name: str) -> None:
        table = self.catalog().load_table(identifier)
        if column_name in {field.name for field in table.schema().fields}:
            return
        table.update_schema().add_column(column_name, StringType(), required=False).commit()


def bronze_schema() -> pa.Schema:
    return pa.schema(
        [
            ("source_id", pa.string()),
            ("crawl_run_id", pa.string()),
            ("source_url", pa.string()),
            ("title", pa.string()),
            ("external_id", pa.string()),
            ("discovered_at", pa.string()),
            ("published_at", pa.string()),
            ("fetched_at", pa.string()),
            ("checksum", pa.string()),
            ("content_type", pa.string()),
            ("content", pa.binary()),
            ("headers_json", pa.string()),
            ("metadata_json", pa.string()),
            ("ingest_date", pa.string()),
        ]
    )


def silver_schema() -> pa.Schema:
    return pa.schema(
        [
            ("doc_id", pa.string()),
            ("crawl_run_id", pa.string()),
            ("source_id", pa.string()),
            ("canonical_number", pa.string()),
            ("issue_date", pa.string()),
            ("issuer", pa.string()),
            ("title", pa.string()),
            ("text", pa.string()),
            ("source_url", pa.string()),
            ("checksum", pa.string()),
            ("fetched_at", pa.string()),
            ("effective_status", pa.string()),
            ("document_type", pa.string()),
            ("articles_json", pa.string()),
            ("relations_json", pa.string()),
            ("metadata_json", pa.string()),
            ("ingest_date", pa.string()),
        ]
    )


def gold_schema() -> pa.Schema:
    return pa.schema(
        [
            ("manifest_id", pa.string()),
            ("crawl_run_id", pa.string()),
            ("doc_id", pa.string()),
            ("source_id", pa.string()),
            ("action", pa.string()),
            ("checksum", pa.string()),
            ("current_version", pa.int64()),
            ("created_at", pa.string()),
            ("source_url", pa.string()),
            ("silver_record_path", pa.string()),
            ("previous_version", pa.int64()),
            ("supersedes_checksum", pa.string()),
            ("reason", pa.string()),
            ("ingest_date", pa.string()),
        ]
    )


def bronze_row(raw: RawArtifact) -> dict:
    item = raw.source_item
    return {
        "source_id": item.source_id,
        "crawl_run_id": raw.crawl_run_id,
        "source_url": item.url,
        "title": item.title,
        "external_id": item.external_id,
        "discovered_at": item.discovered_at,
        "published_at": item.published_at,
        "fetched_at": raw.fetched_at,
        "checksum": raw.checksum,
        "content_type": raw.content_type,
        "content": raw.content,
        "headers_json": _json(raw.headers),
        "metadata_json": _json(item.metadata),
        "ingest_date": raw.fetched_at[:10],
    }


def silver_row(record: LegalDocumentRecord) -> dict:
    return {
        "doc_id": record.doc_id,
        "crawl_run_id": record.crawl_run_id,
        "source_id": record.source_id,
        "canonical_number": record.canonical_number,
        "issue_date": record.issue_date,
        "issuer": record.issuer,
        "title": record.title,
        "text": record.text,
        "source_url": record.source_url,
        "checksum": record.checksum,
        "fetched_at": record.fetched_at,
        "effective_status": record.effective_status,
        "document_type": record.document_type,
        "articles_json": _json(record.articles),
        "relations_json": _json(record.relations),
        "metadata_json": _json(record.metadata),
        "ingest_date": record.fetched_at[:10],
    }


def gold_row(manifest: KGUpdateManifest) -> dict:
    return {
        "manifest_id": manifest.manifest_id,
        "crawl_run_id": manifest.crawl_run_id,
        "doc_id": manifest.doc_id,
        "source_id": manifest.source_id,
        "action": manifest.action,
        "checksum": manifest.checksum,
        "current_version": manifest.current_version,
        "created_at": manifest.created_at,
        "source_url": manifest.source_url,
        "silver_record_path": manifest.silver_record_path,
        "previous_version": manifest.previous_version,
        "supersedes_checksum": manifest.supersedes_checksum,
        "reason": manifest.reason,
        "ingest_date": manifest.created_at[:10],
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _normalize_postgres_uri(uri: str) -> str:
    if uri.startswith("postgresql://"):
        return "postgresql+psycopg2://" + uri.removeprefix("postgresql://")
    return uri


def _local_catalog_uri_if_needed(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.hostname == "postgres" and not _tcp("postgres", parsed.port or 5432):
        return _replace_netloc(uri, "127.0.0.1", 5433)
    return uri


def _local_endpoint_if_needed(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.hostname == "minio" and not _tcp("minio", parsed.port or 9000):
        return _replace_netloc(endpoint, "127.0.0.1", 9000)
    return endpoint


def _replace_netloc(uri: str, host: str, port: int) -> str:
    parsed = urlparse(uri)
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        auth = f"{auth}@"
    return urlunparse(parsed._replace(netloc=f"{auth}{host}:{port}"))


def redact_uri(uri: str) -> str:
    parsed = urlparse(uri)
    if not parsed.username:
        return uri
    auth = parsed.username
    if parsed.password:
        auth = f"{auth}:***"
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=f"{auth}@{host}"))


def _tcp(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()
