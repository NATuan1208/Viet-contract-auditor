"""Storage profile switch for dual-mode deployment."""
from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Literal

Profile = Literal["production", "demo"]

_LIGHTRAG_DIR = Path(__file__).resolve().parents[2] / "lightrag_index"
_DEMO_SENTINEL_FILES = ("graph_chunk_entity_relation.graphml", "vdb_entities.json")


def get_profile() -> Profile:
    raw = os.getenv("STORAGE_PROFILE", "production").lower()
    return "demo" if raw == "demo" else "production"


def required_ports() -> list[tuple[str, int]]:
    if get_profile() == "demo":
        return []
    return [("Neo4j", 7687), ("Qdrant", 6333), ("PostgreSQL", 55432)]


def demo_index_ready() -> bool:
    return all((_LIGHTRAG_DIR / f).exists() for f in _DEMO_SENTINEL_FILES)


def storage_status_labels() -> list[tuple[str, bool]]:
    """Return (service_name, is_online) for sidebar display."""
    if get_profile() == "demo":
        return [("LightRAG local", demo_index_ready())]
    status: list[tuple[str, bool]] = []
    for name, port in required_ports():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.8)
        try:
            sock.connect(("127.0.0.1", port))
            status.append((name, True))
        except OSError:
            status.append((name, False))
        finally:
            sock.close()
    return status
