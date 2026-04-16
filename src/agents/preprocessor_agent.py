"""Preprocessor Agent — word-tokenises clauses and extracts cross-references.

Inputs:  AuditState.chunks
Outputs: AuditState.segmented_chunks, AuditState.cross_refs,
         AuditState.clause_risk_scores, AuditState.clause_risk_reasons

No LLM calls — pure regex + underthesea (see core/vn_preprocessor.py).
Cross-references are detected on the *original* clause text to preserve
character offsets, then the normalised+tokenised text is used for retrieval.
"""

from __future__ import annotations

import logging

from core.state import AuditState, RiskLevel
from core.vn_preprocessor import detect_cross_refs, normalize_aliases, segment_clause

logger = logging.getLogger(__name__)

_LOW_RISK_KEYWORDS = {
    "điều khoản chung",
    "định nghĩa",
    "giải thích từ ngữ",
    "thông tin các bên",
    "hiệu lực hợp đồng",
    "phụ lục",
    "thông báo",
    "đại diện",
}

_HIGH_RISK_KEYWORDS = {
    "phạt",
    "bồi thường",
    "chấm dứt",
    "đơn phương",
    "trách nhiệm",
    "vi phạm",
    "thanh toán",
    "công nợ",
    "thiệt hại",
    "lãi suất",
    "trọng tài",
}


def _score_clause_risk(clause: str) -> tuple[RiskLevel, str]:
    text = clause.lower()

    for kw in _HIGH_RISK_KEYWORDS:
        if kw in text:
            return "high", f"Contains high-risk keyword: '{kw}'"

    for kw in _LOW_RISK_KEYWORDS:
        if kw in text:
            return "low", f"Administrative/general clause keyword: '{kw}'"

    return "medium", "Default legal review required"


async def preprocessor_node(state: AuditState) -> dict:
    """LangGraph node: segment clauses and extract cross-references."""
    chunks = state.get("chunks", [])

    if not chunks:
        logger.warning("preprocessor: no chunks to process")
        return {
            "segmented_chunks": [],
            "cross_refs": [],
            "clause_risk_scores": [],
            "clause_risk_reasons": [],
            "skipped_clauses": [],
        }

    segmented_chunks: list[str] = []
    all_cross_refs: list[dict] = []
    clause_risk_scores: list[RiskLevel] = []
    clause_risk_reasons: list[str] = []

    for idx, clause in enumerate(chunks):
        # Detect refs on original text to keep character offsets meaningful
        refs = detect_cross_refs(clause)
        for ref in refs:
            ref_with_index = dict(ref)
            ref_with_index["clause_index"] = idx
            all_cross_refs.append(ref_with_index)

        risk_score, risk_reason = _score_clause_risk(clause)
        clause_risk_scores.append(risk_score)
        clause_risk_reasons.append(risk_reason)

        # Normalise abbreviations first, then word-tokenise
        normalised = normalize_aliases(clause)
        segmented = segment_clause(normalised)
        segmented_chunks.append(segmented)

    logger.warning(
        "preprocessor: segmented %d clauses, found %d cross-refs (risk: low=%d, medium=%d, high=%d)",
        len(segmented_chunks),
        len(all_cross_refs),
        sum(1 for r in clause_risk_scores if r == "low"),
        sum(1 for r in clause_risk_scores if r == "medium"),
        sum(1 for r in clause_risk_scores if r == "high"),
    )
    return {
        "segmented_chunks": segmented_chunks,
        "cross_refs": all_cross_refs,
        "clause_risk_scores": clause_risk_scores,
        "clause_risk_reasons": clause_risk_reasons,
        "skipped_clauses": [],
    }
