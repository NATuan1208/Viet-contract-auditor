"""Small JSONL trace helpers for retrieval and rerank benchmarking.

Tracing is opt-in because queries may contain private contract text. When
enabled, text previews are truncated and can be disabled with
LIGHTRAG_TRACE_PREVIEW_CHARS=0.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any

_TRACE_LOCK = threading.Lock()
_DEFAULT_TRACE_PATH = Path("reports/benchmarks/retrieval_trace.jsonl")


def trace_enabled() -> bool:
    return _env_bool("LIGHTRAG_BENCHMARK_TRACE_ENABLED", False)


def trace_path() -> Path:
    raw = os.getenv("LIGHTRAG_BENCHMARK_TRACE_PATH", "").strip()
    return Path(raw) if raw else _DEFAULT_TRACE_PATH


def preview_chars() -> int:
    return max(0, int(os.getenv("LIGHTRAG_TRACE_PREVIEW_CHARS", "180")))


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def text_preview(text: str, limit: int | None = None) -> str:
    limit = preview_chars() if limit is None else max(0, limit)
    if limit == 0:
        return ""
    compact = " ".join(str(text or "").split())
    return compact[:limit]


def score_summary(scores: list[float]) -> dict[str, float | int | None]:
    if not scores:
        return {
            "count": 0,
            "min": None,
            "p50": None,
            "p90": None,
            "max": None,
            "mean": None,
        }

    ordered = sorted(float(score) for score in scores)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": _percentile(ordered, 0.50),
        "p90": _percentile(ordered, 0.90),
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


def write_trace_event(event_type: str, payload: dict[str, Any]) -> None:
    if not trace_enabled():
        return

    path = trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        **payload,
    }
    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with _TRACE_LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _percentile(ordered: list[float], q: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    weight = pos - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
