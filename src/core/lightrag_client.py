"""LightRAG production client.

LLM:        configurable OpenAI-compatible model (default gpt-4o-mini)
Embeddings: sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 (dim=384, local)
Storage:    Neo4j + Qdrant + PostgreSQL (credentials from .env)

Singleton: get_rag_client() initialises once and caches behind asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import logging
import os

import numpy as np
from openai import RateLimitError

from core.llm_config import get_llm_settings

logger = logging.getLogger(__name__)

_LLM_SETTINGS = get_llm_settings()
_MODEL = _LLM_SETTINGS.model
_EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_EMBED_DIM = 384

_rag_instance = None
_rag_lock: asyncio.Lock | None = None
_llm_call_count: int = 0  # incremented on every _lightrag_llm invocation


def _get_lock() -> asyncio.Lock:
    """Lazy-create the lock inside a running event loop (Python 3.10+ safe)."""
    global _rag_lock
    if _rag_lock is None:
        _rag_lock = asyncio.Lock()
    return _rag_lock


async def _lightrag_llm(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list | None = None,
    enable_cot: bool = False,
    keyword_extraction: bool = False,
    **kwargs,
) -> str:
    """LightRAG-compatible llm_model_func backed by configured OpenAI-compatible API.

    Inherits tenacity retry logic (RateLimitError, APIConnectionError, APITimeoutError).
    Logs every invocation as INFO so rate-limit debugging is possible without --verbose.
    """
    global _llm_call_count
    _llm_call_count += 1
    n = _llm_call_count
    logger.warning("lightrag_llm: call #%d for %.60s...", n, prompt)

    from lightrag.llm.openai import openai_complete_if_cache

    try:
        return await openai_complete_if_cache(
            _MODEL,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            enable_cot=enable_cot,
            keyword_extraction=keyword_extraction,
            base_url=_LLM_SETTINGS.base_url,
            api_key=_LLM_SETTINGS.api_key,
            **kwargs,
        )
    except RateLimitError:
        logger.warning(
            "lightrag_llm: rate limited on call #%d, openai_complete_if_cache will retry", n
        )
        raise


async def get_rag_client():
    """Return the shared LightRAG client (singleton, initialises on first call).

    Initialisation steps:
      1. Load sentence-transformers model (MiniLM-L12-v2, 384 dim)
      2. Run embedding smoke test (assert shape == (1, 384))
      3. Build LightRAG with production storage backends
      4. Call rag.initialize_storages()
    """
    global _rag_instance
    lock = _get_lock()
    async with lock:
        if _rag_instance is not None:
            return _rag_instance

        logger.info(
            "Initialising LightRAG client (%s + %s/%s + Neo4j/Qdrant/PG)...",
            _EMBED_MODEL_NAME,
            _LLM_SETTINGS.provider,
            _MODEL,
        )

        # --- local embedding model ---
        from sentence_transformers import SentenceTransformer
        _st_model = SentenceTransformer(_EMBED_MODEL_NAME)

        async def _embed(texts: list[str], **_kw) -> np.ndarray:
            vecs = _st_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            return np.array(vecs, dtype=np.float32)

        import importlib
        EmbeddingFunc = importlib.import_module("lightrag.utils").EmbeddingFunc

        embed_func = EmbeddingFunc(
            embedding_dim=_EMBED_DIM,
            func=_embed,
            max_token_size=8192,
            model_name=_EMBED_MODEL_NAME,
        )

        # smoke test
        test = await embed_func(["kiểm tra kết nối"])
        assert test.shape == (1, _EMBED_DIM), f"Embedding shape mismatch: {test.shape}"
        logger.info("Embedding smoke test passed: shape=%s", test.shape)

        # --- LightRAG with production backends ---
        LightRAG = importlib.import_module("lightrag").LightRAG

        rag = LightRAG(
            working_dir=os.getenv("LIGHTRAG_WORKING_DIR", "./lightrag_index"),
            workspace=os.getenv("WORKSPACE", "viet_contract_prod"),
            kv_storage="PGKVStorage",
            doc_status_storage="PGDocStatusStorage",
            graph_storage="Neo4JStorage",
            vector_storage="QdrantVectorDBStorage",
            llm_model_func=_lightrag_llm,
            embedding_func=embed_func,
        )
        await rag.initialize_storages()

        _rag_instance = rag
        logger.info("LightRAG client ready (Neo4j + Qdrant + PostgreSQL)")
        return _rag_instance


async def query_hybrid(rag, clause_text: str, top_k: int = 10) -> str:
    """Query LightRAG in hybrid mode (Neo4j graph + Qdrant vector + PG KV).

    Falls back to STUB message when rag is None (storage not connected).
    """
    if rag is None:
        logger.warning("STUB: query_hybrid() — returning placeholder context")
        preview = clause_text[:80].replace("\n", " ")
        return (
            f"[STUB] Chưa có dữ liệu pháp lý thực.\n"
            f"Cần kết nối storage để truy vấn LightRAG.\n"
            f"Điều khoản: {preview}..."
        )

    from lightrag.base import QueryParam

    return await rag.aquery(
        clause_text,
        param=QueryParam(mode="hybrid", top_k=top_k, only_need_context=True),
    )
