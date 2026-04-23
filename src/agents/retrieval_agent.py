"""Retrieval Agent — queries LightRAG to build legal context.

Inputs:  AuditState.segmented_chunks (preferred) | AuditState.chunks,
         AuditState.contract_domain, AuditState.cross_refs,
         AuditState.clause_risk_scores
Outputs: AuditState.legal_context, AuditState.retrieved_clause_indices

Queries LightRAG hybrid (Neo4j graph + Qdrant vector + PG KV) for each clause.
Additionally fires one query per unique "Điều X" cross-reference detected by
the preprocessor (xref expansion), labelled as "### Tham chiếu pháp lý:" sections.
Semaphore(1) serialises queries — LightRAG hybrid internally calls shared LLM,
so concurrent queries can still trigger rate limits.
Exponential backoff on all exceptions (2s → 4s → 8s, max 3 retries).
1s inter-query sleep mirrors audit_agent pacing.
Deduplicates passages by MD5 of first 100 chars.
Caps each result to 1000 chars to avoid downstream token overflow.
Reads from production storage only — never from lightrag_index/ JSON files.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os

from core.lightrag_client import get_rag_client, query_hybrid
from core.state import AuditState

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (2.0, 4.0, 8.0)  # exponential backoff for any query failure

# Prepend the governing law name to clause queries so the KG/vector search
# biases toward the correct legal domain when the corpus contains multiple laws.
_DOMAIN_LAW_CONTEXT: dict[str, str] = {
    "Lao động": "Bộ luật Lao động",
    "Thương mại": "Luật Thương mại",
    "Dân sự": "Bộ luật Dân sự",
    "Doanh nghiệp": "Luật Doanh nghiệp",
}
_RETRIEVE_LOW_RISK_CLAUSES = os.getenv("RETRIEVE_LOW_RISK_CLAUSES", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}


async def retrieval_node(state: AuditState) -> dict:
    """LangGraph node: retrieve relevant law passages for each contract clause."""
    # Prefer word-tokenised chunks from preprocessor; fall back to raw chunks
    chunks: list[str] = state.get("segmented_chunks") or state.get("chunks", [])
    cross_refs: list[dict] = state.get("cross_refs", [])
    clause_risk_scores = state.get("clause_risk_scores", [])
    domain: str = state.get("contract_domain", "")

    if not chunks:
        logger.warning("retrieval_agent: no chunks to retrieve for")
        return {"legal_context": "", "retrieved_clause_indices": []}

    if len(clause_risk_scores) != len(chunks):
        clause_risk_scores = ["medium"] * len(chunks)

    if _RETRIEVE_LOW_RISK_CLAUSES:
        active_clause_indices = list(range(len(chunks)))
    else:
        active_clause_indices = [
            i for i, risk in enumerate(clause_risk_scores) if risk in {"medium", "high"}
        ]

    if not active_clause_indices:
        logger.warning("retrieval_agent: all clauses marked low-risk, skipping retrieval")
        return {"legal_context": "", "retrieved_clause_indices": []}

    rag = await get_rag_client()
    sem = asyncio.Semaphore(1)

    async def _query_with_retry(text: str) -> str:
        for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
            try:
                result = await query_hybrid(rag, text, top_k=10)
                return (result or "")[:1000]
            except Exception as exc:
                if attempt < len(_RETRY_DELAYS):
                    logger.warning(
                        "retrieval_agent: query failed (attempt %d/%d), backing off %.1fs: %s",
                        attempt, len(_RETRY_DELAYS), delay, exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning(
                        "retrieval_agent: query failed after %d retries: %s",
                        len(_RETRY_DELAYS), exc,
                    )
                    return ""
        return ""  # unreachable — satisfies type checker

    async def _query(text: str) -> str:
        async with sem:
            return await _query_with_retry(text)

    # --- Clause queries (one per medium/high segmented chunk) ---
    _law_ctx = _DOMAIN_LAW_CONTEXT.get(domain, domain)
    clause_queries: list[tuple[int, str]] = [
        (i, f"{_law_ctx}: {chunks[i]}" if _law_ctx else chunks[i])
        for i in active_clause_indices
    ]

    # --- Xref-expansion queries (one per unique "Điều X" reference) ---
    xref_queries: list[tuple[str, int | None]] = []
    seen_xrefs: set[str] = set()
    for ref in cross_refs:
        clause_index = ref.get("clause_index")
        if isinstance(clause_index, int) and clause_index not in active_clause_indices:
            continue
        if ref.get("type") == "dieu":
            q = f"{ref['value']} {domain}".strip()
            if q not in seen_xrefs:
                seen_xrefs.add(q)
                xref_queries.append((q, clause_index if isinstance(clause_index, int) else None))

    query_specs: list[dict] = (
        [{"kind": "clause", "clause_index": i, "text": text} for i, text in clause_queries]
        + [{"kind": "xref", "clause_index": idx, "text": text} for text, idx in xref_queries]
    )

    # Process sequentially with 1s inter-query sleep (mirrors audit_agent pacing)
    tasks = [_query(spec["text"]) for spec in query_specs]
    raw_results: list[str] = []
    for i, coro in enumerate(tasks):
        result = await coro
        raw_results.append(result)
        if i < len(tasks) - 1:
            await asyncio.sleep(1.0)

    seen_md5: set[str] = set()
    sections: list[str] = []
    retrieved_clause_indices: set[int] = set()
    for i, result in enumerate(raw_results):
        if not result:
            continue
        spec = query_specs[i]
        key = hashlib.md5(result.encode()).hexdigest()
        if key in seen_md5:
            continue
        seen_md5.add(key)

        if spec["kind"] == "clause":
            clause_index = int(spec["clause_index"])
            retrieved_clause_indices.add(clause_index)
            sections.append(f"### Điều khoản {clause_index + 1}\n{result}\n")
        else:
            xref_q = spec["text"]
            sections.append(f"### Tham chiếu pháp lý: {xref_q}\n{result}\n")

    legal_context = "\n".join(sections)
    logger.warning(
        "retrieval: %d vector chunks retrieved (%d active clauses + %d xref queries, %d unique, %d chars)",
        sum(1 for r in raw_results if r),
        len(clause_queries),
        len(xref_queries),
        len(sections),
        len(legal_context),
    )
    return {
        "legal_context": legal_context,
        "retrieved_clause_indices": sorted(retrieved_clause_indices),
    }
