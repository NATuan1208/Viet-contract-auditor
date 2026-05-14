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

from core.benchmark_trace import stable_text_hash, text_preview, write_trace_event
from core.llm_config import get_llm_settings
from core.rerank_client import build_rerank_model_func, get_rerank_settings

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
        _patch_lightrag_legal_prompts()
        rerank_settings = get_rerank_settings()
        rerank_model_func = build_rerank_model_func(rerank_settings)

        rag = LightRAG(
            working_dir=os.getenv("LIGHTRAG_WORKING_DIR", "./lightrag_index"),
            workspace=os.getenv("WORKSPACE", "viet_contract_prod"),
            kv_storage="PGKVStorage",
            doc_status_storage="PGDocStatusStorage",
            graph_storage="Neo4JStorage",
            vector_storage="QdrantVectorDBStorage",
            tiktoken_model_name=_MODEL,
            llm_model_name=_MODEL,
            llm_model_func=_lightrag_llm,
            embedding_func=embed_func,
            rerank_model_func=rerank_model_func,
            min_rerank_score=float(os.getenv("LIGHTRAG_MIN_RERANK_SCORE", "0.0")),
            addon_params={
                "language": "Vietnamese",
                "entity_types": [
                    "LegalDocument",
                    "Article",
                    "Clause",
                    "Issuer",
                    "Agency",
                    "LegalDomain",
                    "EffectiveStatus",
                    "Procedure",
                    "Obligation",
                    "Deadline",
                    "Location",
                    "Person",
                    "Other",
                ],
            },
        )
        await rag.initialize_storages()

        _rag_instance = rag
        logger.info(
            "LightRAG client ready (Neo4j + Qdrant + PostgreSQL, rerank=%s, rerank_model=%s)",
            rerank_settings.enabled,
            rerank_settings.model_name if rerank_settings.enabled else "disabled",
        )
        return _rag_instance


def _patch_lightrag_legal_prompts() -> None:
    """Replace generic few-shot examples that can leak into legal KG extraction."""
    import importlib

    prompt_module = importlib.import_module("lightrag.prompt")
    prompts = getattr(prompt_module, "PROMPTS")
    prompts["entity_extraction_examples"] = [
        """<Entity_types>
["LegalDocument","Article","Clause","Issuer","Agency","LegalDomain","EffectiveStatus","Procedure","Obligation","Deadline","Location","Person","Other"]

<Input Text>
```
Nghị định số 148/2026/NĐ-CP do Chính phủ ban hành ngày 12/05/2026 quy định về phân cấp, phân quyền trong lĩnh vực quản lý nhà nước. Điều 1 quy định phạm vi điều chỉnh của Nghị định.
```

<Output>
entity{tuple_delimiter}Nghị định số 148/2026/NĐ-CP{tuple_delimiter}LegalDocument{tuple_delimiter}Nghị định số 148/2026/NĐ-CP là văn bản do Chính phủ ban hành ngày 12/05/2026.
entity{tuple_delimiter}Chính phủ{tuple_delimiter}Issuer{tuple_delimiter}Chính phủ là cơ quan ban hành Nghị định số 148/2026/NĐ-CP.
entity{tuple_delimiter}Điều 1{tuple_delimiter}Article{tuple_delimiter}Điều 1 quy định phạm vi điều chỉnh của Nghị định số 148/2026/NĐ-CP.
relation{tuple_delimiter}Chính phủ{tuple_delimiter}Nghị định số 148/2026/NĐ-CP{tuple_delimiter}ban hành văn bản{tuple_delimiter}Chính phủ là cơ quan ban hành Nghị định số 148/2026/NĐ-CP.
relation{tuple_delimiter}Nghị định số 148/2026/NĐ-CP{tuple_delimiter}Điều 1{tuple_delimiter}có điều khoản{tuple_delimiter}Nghị định số 148/2026/NĐ-CP có Điều 1 quy định phạm vi điều chỉnh.
{completion_delimiter}

""",
    ]


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

    effective_top_k = _env_int("LIGHTRAG_QUERY_TOP_K", top_k, minimum=1)
    chunk_top_k = _env_optional_int("LIGHTRAG_CHUNK_TOP_K", minimum=1)
    enable_rerank = _env_bool("LIGHTRAG_RERANK_ENABLED", True)

    result = await rag.aquery(
        clause_text,
        param=QueryParam(
            mode="hybrid",
            top_k=effective_top_k,
            chunk_top_k=chunk_top_k,
            enable_rerank=enable_rerank,
            only_need_context=True,
        ),
    )
    write_trace_event(
        "query",
        {
            "mode": "hybrid",
            "query_hash": stable_text_hash(clause_text),
            "query_preview": text_preview(clause_text),
            "top_k": effective_top_k,
            "chunk_top_k": chunk_top_k,
            "enable_rerank": enable_rerank,
            "result_chars": len(result or ""),
            "result_hash": stable_text_hash(result or ""),
            "result_preview": text_preview(result or ""),
        },
    )
    return result


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %d", name, raw, default)
        return default


def _env_optional_int(name: str, minimum: int) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid integer for %s=%r; ignoring", name, raw)
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
