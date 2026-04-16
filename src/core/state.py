"""Shared AuditState TypedDict for the LangGraph audit pipeline."""

from __future__ import annotations

from typing import Literal, TypedDict


ErrorType = Literal["hallucination", "reasoning", "low_confidence", "ok"]


class CriticFeedback(TypedDict, total=False):
    """Structured diagnostics produced by the critic agent."""

    layer1_negations: list[str]
    layer2_called: bool
    missed_exceptions: list[str]
    reference_law_valid: bool
    adjusted_confidence: float
    refined_query: str | None
    parse_ok: bool
    reason: str
    context_quality: float


class AuditState(TypedDict):
    """State passed between all agents in the audit pipeline."""

    contract_text: str           # raw input contract
    contract_domain: str         # "Dân sự" | "Thương mại" | "Lao động" | "Doanh nghiệp"
    chunks: list[str]            # contract split into clauses (raw, from router)
    segmented_chunks: list[str]  # word-tokenised clauses (from preprocessor)
    cross_refs: list[dict]       # legal cross-references per clause (from preprocessor)
    negations_found: list[str]   # negation strings found by critic layer-1
    critic_feedback: CriticFeedback
    retry_count: int             # critic retry counter (0-based)
    error_type: ErrorType        # route decision from critic
    legal_context: str           # Markdown assembled from LightRAG queries
    audit_findings: list[dict]   # [{clause, violation, reference_law, suggested_fix}]
    final_report: str            # formatted Vietnamese Markdown report
    confidence: float            # bounded score in [0.0, 1.0]
    context_quality: float       # bounded score in [0.0, 1.0]
    error: str | None            # per-agent error propagation
