"""LangGraph StateGraph wiring for the audit pipeline.

Graph:
    START → router → [ok: preprocessor | error: generator]
    → preprocessor → retrieval → audit → critic
    → [to_retrieval|to_audit|finalize] → generator → END

This is the only file in src/ that imports from langgraph.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from agents.audit_agent import audit_node
from agents.critic_agent import critic_node
from agents.generator_agent import generator_node
from agents.preprocessor_agent import preprocessor_node
from agents.retrieval_agent import retrieval_node
from agents.router_agent import route_after_router, router_node
from core.state import AuditState

logger = logging.getLogger(__name__)
MAX_RETRY = 2


def route_after_critic(state: AuditState) -> str:
    """Conditional edge: route based on critic error_type with retry guard."""
    retry_count = state.get("retry_count", 0)
    error_type = state.get("error_type", "low_confidence")
    confidence = state.get("confidence", 0.0)
    context_quality = state.get("context_quality", 0.0)

    if retry_count >= MAX_RETRY:
        logger.info(
            "route_after_critic: finalize due to retry cap (retry=%d, error_type=%s)",
            retry_count,
            error_type,
        )
        return "finalize"

    if error_type in {"hallucination", "low_confidence"}:
        logger.info(
            "route_after_critic: retrieval (error_type=%s, confidence=%.2f, context_quality=%.2f, retry=%d)",
            error_type,
            confidence,
            context_quality,
            retry_count,
        )
        return "to_retrieval"

    if error_type == "reasoning":
        logger.info(
            "route_after_critic: audit (confidence=%.2f, context_quality=%.2f, retry=%d)",
            confidence,
            context_quality,
            retry_count,
        )
        return "to_audit"

    logger.info(
        "route_after_critic: finalize (error_type=%s, confidence=%.2f, context_quality=%.2f, retry=%d)",
        error_type,
        confidence,
        context_quality,
        retry_count,
    )
    return "finalize"


# ---------------------------------------------------------------------------
# Build and compile the StateGraph
# ---------------------------------------------------------------------------

_builder = StateGraph(AuditState)

_builder.add_node("router", router_node)
_builder.add_node("preprocessor", preprocessor_node)
_builder.add_node("retrieval", retrieval_node)
_builder.add_node("audit", audit_node)
_builder.add_node("critic", critic_node)
_builder.add_node("generator", generator_node)

_builder.set_entry_point("router")
_builder.add_conditional_edges(
    "router",
    route_after_router,
    {"ok": "preprocessor", "error": "generator"},
)
_builder.add_edge("preprocessor", "retrieval")
_builder.add_edge("retrieval", "audit")
_builder.add_edge("audit", "critic")
_builder.add_conditional_edges(
    "critic",
    route_after_critic,
    {
        "finalize": "generator",
        "to_retrieval": "retrieval",
        "to_audit": "audit",
    },
)
_builder.add_edge("generator", END)

app = _builder.compile()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_audit(contract_text: str) -> AuditState:
    """Run the full 6-agent audit pipeline on a contract text string.

    Returns the final AuditState. Check:
      state["final_report"]     for the Markdown output
      state["error"]            for pipeline failures
            state["confidence"]       for overall confidence (0.0 in stub mode)
      state["negations_found"]  for negation/exception patterns detected
            state["retry_count"]      for number of critic retries performed
    """
    initial: AuditState = {
        "contract_text": contract_text,
        "contract_domain": "",
        "chunks": [],
        "segmented_chunks": [],
        "cross_refs": [],
        "negations_found": [],
        "critic_feedback": {},
        "retry_count": 0,
        "error_type": "low_confidence",
        "legal_context": "",
        "audit_findings": [],
        "final_report": "",
        "confidence": 0.0,
        "context_quality": 0.0,
        "error": None,
    }

    logger.warning("run_audit: starting pipeline (%d chars)", len(contract_text))
    result: AuditState = await app.ainvoke(initial)
    logger.warning(
        "run_audit: done — domain=%s, chunks=%d, findings=%d, confidence=%.2f, retry=%d, error_type=%s",
        result.get("contract_domain"),
        len(result.get("chunks", [])),
        len(result.get("audit_findings", [])),
        result.get("confidence", 0.0),
        result.get("retry_count", 0),
        result.get("error_type", "low_confidence"),
    )
    return result
