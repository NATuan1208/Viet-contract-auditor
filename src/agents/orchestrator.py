"""LangGraph StateGraph wiring for the audit pipeline.

Graph:
    START → router → [ok: preprocessor | error: generator]
    → preprocessor → retrieval → audit → critic
    → [finalize: generator | retry: retrieval] → generator → END

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


def route_after_critic(state: AuditState) -> str:
    """Conditional edge: go to generator when confident or max iterations reached.

    confidence >= 0.7 OR iteration >= 2  →  "finalize"  (→ generator)
    otherwise                             →  "retry"     (→ retrieval)
    """
    confidence = state.get("confidence_score", 0.0)
    iteration = state.get("iteration", 0)
    if confidence >= 0.7 or iteration >= 2:
        return "finalize"
    return "retry"


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
    {"finalize": "generator", "retry": "retrieval"},
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
      state["confidence_score"] for overall confidence (0.0 in stub mode)
      state["negations_found"]  for negation/exception patterns detected
      state["iteration"]        for number of critic iterations performed
    """
    initial: AuditState = {
        "contract_text": contract_text,
        "contract_domain": "",
        "chunks": [],
        "segmented_chunks": [],
        "cross_refs": [],
        "negations_found": [],
        "critic_feedback": {},
        "iteration": 0,
        "legal_context": "",
        "audit_findings": [],
        "final_report": "",
        "confidence_score": 0.0,
        "error": None,
    }

    logger.warning("run_audit: starting pipeline (%d chars)", len(contract_text))
    result: AuditState = await app.ainvoke(initial)
    logger.warning(
        "run_audit: done — domain=%s, chunks=%d, findings=%d, confidence=%.2f, iteration=%d",
        result.get("contract_domain"),
        len(result.get("chunks", [])),
        len(result.get("audit_findings", [])),
        result.get("confidence_score", 0.0),
        result.get("iteration", 0),
    )
    return result
