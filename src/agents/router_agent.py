"""Router Agent — classifies contract domain and splits into clauses.

Inputs:  AuditState.contract_text
Outputs: AuditState.contract_domain, AuditState.chunks

Classification logic:
    1. Keyword-based (pure regex, no API call)
    2. LLM fallback when keywords are ambiguous

Clause splitting logic:
  1. Pure-regex split at Điều boundaries
  2. If < 3 clauses found: LLM fallback using CLAUSE_SPLIT_SYSTEM_PROMPT
"""

from __future__ import annotations

import json
import logging

from core.llm_config import create_async_llm_client, get_llm_settings
from core.legal_patterns import classify_domain_by_keywords, split_contract_into_clauses
from core.prompts import CLAUSE_SPLIT_SYSTEM_PROMPT, ROUTER_SYSTEM_PROMPT
from core.state import AuditState

logger = logging.getLogger(__name__)

_LLM_SETTINGS = get_llm_settings()
_MODEL = _LLM_SETTINGS.model
_llm_client = create_async_llm_client()


async def router_node(state: AuditState) -> dict:
    """LangGraph node: classify domain and split contract into clauses."""
    contract_text = state.get("contract_text", "")

    if not contract_text.strip():
        logger.error("router_agent: contract_text is empty")
        return {
            "error": "router_agent: contract_text is empty",
            "contract_domain": "",
            "chunks": [],
        }

    # --- Domain classification ---
    domain = classify_domain_by_keywords(contract_text)

    if domain is None:
        logger.info("router_agent: keyword classification inconclusive, calling LLM")
        try:
            response = await _llm_client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": contract_text[:8000]},
                ],
            )
            result = json.loads(response.choices[0].message.content)
            domain = result.get("domain", "Thương mại")
            logger.info(
                "router_agent: %s/%s domain=%s reason=%s",
                _LLM_SETTINGS.provider,
                _MODEL,
                domain,
                result.get("reason", ""),
            )
        except Exception as exc:
            logger.error("router_agent: LLM classification failed: %s", exc)
            domain = "Thương mại"

    logger.info("router_agent: domain=%s", domain)

    # --- Clause splitting ---
    chunks = split_contract_into_clauses(contract_text)

    if len(chunks) < 3:
        logger.info(
            "router_agent: only %d clause(s) from regex, calling LLM clause splitter", len(chunks)
        )
        try:
            response = await _llm_client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": CLAUSE_SPLIT_SYSTEM_PROMPT},
                    {"role": "user", "content": contract_text[:8000]},
                ],
            )
            result = json.loads(response.choices[0].message.content)
            llm_chunks = result.get("clauses", [])
            if llm_chunks:
                chunks = llm_chunks
                logger.info("router_agent: LLM produced %d clause(s)", len(chunks))
        except Exception as exc:
            logger.warning("router_agent: LLM clause split failed: %s", exc)

    logger.info("router_agent: %d clause(s)", len(chunks))
    return {"contract_domain": domain, "chunks": chunks}


def route_after_router(state: AuditState) -> str:
    """Conditional edge: route to 'retrieval' on success, 'generator' on error."""
    if state.get("error"):
        return "error"
    if not state.get("chunks"):
        return "error"
    return "ok"
