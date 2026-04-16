"""Context Validator Agent — evaluates retrieval quality before audit.

Inputs:  AuditState.legal_context, AuditState.chunks,
         AuditState.cross_refs, AuditState.context_retry_count
Outputs: AuditState.context_quality, AuditState.context_quality_label,
         AuditState.context_validator_feedback, AuditState.context_retry_count

This node provides an early quality gate after retrieval so weak context can
be retried before spending LLM cost in the audit node.
"""

from __future__ import annotations

import logging
import re

from core.state import AuditState

logger = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^###\s+Điều khoản\s+\d+", re.MULTILINE)
_XREF_DIEU_RE = re.compile(r"\bđiều\s+\d+\b", re.IGNORECASE)

_MIN_CONTEXT_LEN = 300
_GOOD_THRESHOLD = 0.6


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _length_score(context_len: int) -> float:
    if context_len >= 3000:
        return 1.0
    if context_len >= 1500:
        return 0.8
    if context_len >= 700:
        return 0.6
    if context_len >= 300:
        return 0.4
    return 0.1


def _normalize_xref_value(value: str) -> str:
    m = _XREF_DIEU_RE.search(value)
    return m.group(0).lower().strip() if m else value.lower().strip()


async def context_validator_node(state: AuditState) -> dict:
    """LangGraph node: score legal_context quality and emit a good/bad label."""
    legal_context = (state.get("legal_context") or "").strip()
    chunks = state.get("chunks", [])
    cross_refs = state.get("cross_refs", [])
    retry_count = int(state.get("context_retry_count", 0))

    chunk_count = len(chunks)
    context_length = len(legal_context)
    section_count = len(_SECTION_RE.findall(legal_context))

    reasons: list[str] = []

    if chunk_count == 0:
        reasons.append("no_chunks")
    if context_length < _MIN_CONTEXT_LEN:
        reasons.append("context_too_short")
    if section_count == 0:
        reasons.append("missing_clause_sections")

    if chunk_count > 0:
        section_ratio = _clamp01(section_count / chunk_count)
        mapped_ratio = _clamp01(min(section_count, chunk_count) / chunk_count)
    else:
        section_ratio = 0.0
        mapped_ratio = 0.0

    xref_values = [
        _normalize_xref_value(str(ref.get("value", "")))
        for ref in cross_refs
        if ref.get("type") == "dieu" and str(ref.get("value", "")).strip()
    ]

    if xref_values:
        context_lc = legal_context.lower()
        matched = sum(1 for x in xref_values if x and x in context_lc)
        xref_coverage = _clamp01(matched / len(xref_values))
        if xref_coverage < 0.5:
            reasons.append("low_xref_coverage")
    else:
        xref_coverage = 1.0

    score = _clamp01(
        (_length_score(context_length) * 0.30)
        + (section_ratio * 0.30)
        + (mapped_ratio * 0.20)
        + (xref_coverage * 0.20)
    )

    quality_label = "good" if score >= _GOOD_THRESHOLD else "bad"
    if quality_label == "bad" and "low_score" not in reasons:
        reasons.append("low_score")

    updated_retry_count = retry_count + 1 if quality_label == "bad" else retry_count

    feedback = {
        "section_count": section_count,
        "chunk_count": chunk_count,
        "section_ratio": round(section_ratio, 4),
        "mapped_ratio": round(mapped_ratio, 4),
        "xref_count": len(xref_values),
        "xref_coverage": round(xref_coverage, 4),
        "context_length": context_length,
        "reasons": reasons,
    }

    logger.info(
        "context_validator: quality=%s score=%.2f sections=%d chunks=%d xref_cov=%.2f retry=%d",
        quality_label,
        score,
        section_count,
        chunk_count,
        xref_coverage,
        updated_retry_count,
    )

    return {
        "context_quality": score,
        "context_quality_label": quality_label,
        "context_validator_feedback": feedback,
        "context_retry_count": updated_retry_count,
    }
