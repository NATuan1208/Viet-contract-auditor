"""Context Validator Agent — gatekeeper between retrieval and audit.

Inputs:  AuditState.legal_context, AuditState.chunks, AuditState.clause_risk_scores,
         AuditState.cross_refs, AuditState.retrieved_clause_indices,
         AuditState.context_retry_count
Outputs: AuditState.context_quality, AuditState.context_quality_score,
         AuditState.context_validation_errors, AuditState.context_retry_count

Heuristic-only validator (no LLM call) to save token costs in development.
"""

from __future__ import annotations

import logging
import re

from core.state import AuditState

logger = logging.getLogger(__name__)


def _parse_clause_sections(legal_context: str) -> dict[int, str]:
    """Parse markdown sections like '### Điều khoản N' into {clause_index: text}."""
    matches = list(re.finditer(r"###\s*Điều khoản\s*(\d+)\s*\n", legal_context))
    if not matches:
        return {}

    sections: dict[int, str] = {}
    for i, match in enumerate(matches):
        clause_no = int(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(legal_context)
        body = legal_context[start:end].strip()
        sections[clause_no - 1] = body
    return sections


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[A-Za-zÀ-ỹ0-9_]+", text.lower())
    return {w for w in words if len(w) >= 3}


def _overlap_score(a: str, b: str) -> float:
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(1, len(ta))


async def context_validator_node(state: AuditState) -> dict:
    """LangGraph node: validate retrieval quality before expensive audit calls."""
    chunks = state.get("chunks", [])
    risk_scores = state.get("clause_risk_scores", [])
    cross_refs = state.get("cross_refs", [])
    legal_context = state.get("legal_context", "")
    retrieved_clause_indices = state.get("retrieved_clause_indices", [])
    retry_count = state.get("context_retry_count", 0)

    if len(risk_scores) != len(chunks):
        risk_scores = ["medium"] * len(chunks)

    target_indices = [
        i for i, risk in enumerate(risk_scores)
        if risk in {"medium", "high"}
    ]

    if not target_indices:
        return {
            "context_quality": "good",
            "context_quality_score": 1.0,
            "context_validation_errors": [],
            "context_retry_count": 0,
        }

    clause_sections = _parse_clause_sections(legal_context)
    retrieved_set = set(retrieved_clause_indices)
    covered_set = set(target_indices) & set(clause_sections.keys())

    coverage_ratio = len(covered_set) / len(target_indices) if target_indices else 0.0

    relevance_scores: list[float] = []
    for idx in covered_set:
        relevance_scores.append(_overlap_score(chunks[idx], clause_sections.get(idx, "")))
    avg_relevance = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0.0

    expected_refs = [
        ref for ref in cross_refs
        if ref.get("type") == "dieu" and ref.get("clause_index") in target_indices
    ]
    if not expected_refs:
        ref_coverage = 1.0
    else:
        clauses_with_refs = {int(ref["clause_index"]) for ref in expected_refs if "clause_index" in ref}
        ref_covered = len(clauses_with_refs & retrieved_set)
        ref_coverage = ref_covered / max(1, len(clauses_with_refs))

    context_quality_score = (
        (coverage_ratio * 0.5)
        + (avg_relevance * 0.3)
        + (ref_coverage * 0.2)
    )
    context_quality_score = max(0.0, min(1.0, context_quality_score))

    errors: list[str] = []
    if coverage_ratio < 0.5:
        errors.append(
            f"Low coverage for medium/high clauses ({len(covered_set)}/{len(target_indices)})"
        )
    if avg_relevance < 0.15:
        errors.append(f"Low relevance score ({avg_relevance:.2f})")
    if ref_coverage < 0.4 and expected_refs:
        errors.append(f"Missing legal references coverage ({ref_coverage:.2f})")

    if ref_coverage < 0.4 and expected_refs:
        context_quality = "missing_refs"
    elif coverage_ratio < 0.5 or avg_relevance < 0.15:
        context_quality = "poor"
    else:
        context_quality = "good"

    next_retry = retry_count + 1 if context_quality != "good" else 0

    logger.warning(
        "context_validator: quality=%s score=%.2f coverage=%.2f relevance=%.2f ref_coverage=%.2f retry=%d",
        context_quality,
        context_quality_score,
        coverage_ratio,
        avg_relevance,
        ref_coverage,
        next_retry,
    )

    return {
        "context_quality": context_quality,
        "context_quality_score": context_quality_score,
        "context_validation_errors": errors,
        "context_retry_count": next_retry,
    }
