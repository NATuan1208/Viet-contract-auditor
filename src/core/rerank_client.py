"""Local reranker adapter for LightRAG retrieval.

LightRAG expects a coroutine with signature:
    rerank(query: str, documents: list[str], top_n: int | None) -> list[dict]

The return format is index based, so LightRAG can map scores back to the
original retrieved chunks: [{"index": int, "relevance_score": float}, ...].
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from typing import Any

from core.benchmark_trace import score_summary, stable_text_hash, text_preview, write_trace_event

logger = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

_reranker: Any | None = None
_reranker_lock: asyncio.Lock | None = None


@dataclass(frozen=True)
class RerankSettings:
    enabled: bool
    model_name: str
    batch_size: int
    max_chars_per_doc: int
    normalize_scores: bool


def get_rerank_settings() -> RerankSettings:
    return RerankSettings(
        enabled=_env_bool("LIGHTRAG_RERANK_ENABLED", True),
        model_name=os.getenv("LIGHTRAG_RERANK_MODEL", DEFAULT_RERANK_MODEL).strip()
        or DEFAULT_RERANK_MODEL,
        batch_size=max(1, int(os.getenv("LIGHTRAG_RERANK_BATCH_SIZE", "16"))),
        max_chars_per_doc=max(200, int(os.getenv("LIGHTRAG_RERANK_MAX_CHARS", "4000"))),
        normalize_scores=_env_bool("LIGHTRAG_RERANK_NORMALIZE_SCORES", True),
    )


def build_rerank_model_func(settings: RerankSettings | None = None):
    settings = settings or get_rerank_settings()
    if not settings.enabled:
        logger.info("LightRAG rerank disabled by LIGHTRAG_RERANK_ENABLED=false")
        return None

    async def _rerank(query: str, documents: list[str], top_n: int | None = None) -> list[dict[str, float | int]]:
        if not query or not documents:
            return []

        model = await _get_reranker(settings)
        pairs = [
            (query, _truncate_for_rerank(document, settings.max_chars_per_doc))
            for document in documents
        ]

        raw_scores = await asyncio.to_thread(
            model.predict,
            pairs,
            batch_size=settings.batch_size,
            show_progress_bar=False,
        )
        scores = [float(score) for score in raw_scores]
        if settings.normalize_scores:
            scores = [_sigmoid(score) for score in scores]

        ranked = sorted(
            (
                {"index": index, "relevance_score": score}
                for index, score in enumerate(scores)
            ),
            key=lambda item: item["relevance_score"],
            reverse=True,
        )
        if top_n is not None:
            ranked = ranked[:top_n]

        write_trace_event(
            "rerank",
            {
                "model": settings.model_name,
                "query_hash": stable_text_hash(query),
                "query_preview": text_preview(query),
                "candidate_count": len(documents),
                "returned_count": len(ranked),
                "top_n": top_n,
                "score_summary": score_summary(scores),
                "top_results": [
                    {
                        "rank": rank + 1,
                        "index": int(item["index"]),
                        "score": float(item["relevance_score"]),
                        "doc_hash": stable_text_hash(documents[int(item["index"])]),
                        "doc_preview": text_preview(documents[int(item["index"])]),
                    }
                    for rank, item in enumerate(ranked[:5])
                ],
            },
        )

        logger.info(
            "LightRAG rerank applied: model=%s documents=%d returned=%d",
            settings.model_name,
            len(documents),
            len(ranked),
        )
        return ranked

    return _rerank


async def _get_reranker(settings: RerankSettings) -> Any:
    global _reranker, _reranker_lock
    if _reranker is not None:
        return _reranker
    if _reranker_lock is None:
        _reranker_lock = asyncio.Lock()

    async with _reranker_lock:
        if _reranker is not None:
            return _reranker

        def _load_model() -> Any:
            from sentence_transformers import CrossEncoder

            return CrossEncoder(settings.model_name)

        logger.info("Loading LightRAG rerank model: %s", settings.model_name)
        _reranker = await asyncio.to_thread(_load_model)
        return _reranker


def _truncate_for_rerank(text: str, max_chars: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars]


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
