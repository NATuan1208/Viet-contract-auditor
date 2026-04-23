"""Critic Agent — validates audit findings for missed negations and confidence.

Inputs:  AuditState.legal_context, AuditState.audit_findings,
         AuditState.confidence, AuditState.retry_count
Outputs: AuditState.negations_found, AuditState.critic_feedback,
         AuditState.confidence (possibly adjusted), AuditState.retry_count (+1),
         AuditState.error_type

Layer 1 (no LLM): regex scan of legal_context for negation/exception patterns.
Layer 2 (LLM): called only when Layer 1 finds negations OR confidence < 0.7.
  Checks: missed exceptions, reference_law validity, adjusted confidence, refined_query.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, cast

from openai import APITimeoutError, AsyncOpenAI, RateLimitError

from core.llm_config import get_llm_settings
from core.prompts import CRITIC_SYSTEM_PROMPT
from core.state import AuditState, ContextQualityStatus, ErrorType

logger = logging.getLogger(__name__)

_LLM_SETTINGS = get_llm_settings()
_MODEL = _LLM_SETTINGS.model
_llm_client = AsyncOpenAI(
    api_key=_LLM_SETTINGS.api_key,
    base_url=_LLM_SETTINGS.base_url,
)

# ---------------------------------------------------------------------------
# Layer-1 negation patterns
# ---------------------------------------------------------------------------

_NEGATION_PATTERNS: list[str] = [
    "không được",
    "cấm",
    "trừ trường hợp",
    "ngoại trừ",
    "miễn là",
    "chỉ khi",
    "không có quyền",
    "không áp dụng",
]

_NEGATION_RE = re.compile(
    "|".join(re.escape(p) for p in _NEGATION_PATTERNS),
    re.IGNORECASE,
)


def _extract_json_obj(raw: str) -> dict:
    """Extract the first JSON object from raw model output."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass
    return {}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalize_error_type(value: Any) -> ErrorType:
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"hallucination", "reasoning", "low_confidence", "ok"}:
            return cast(ErrorType, cleaned)
    return "low_confidence"


def _normalize_context_quality_status(value: Any) -> ContextQualityStatus:
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"good", "poor", "missing_refs"}:
            return cast(ContextQualityStatus, cleaned)
    return "poor"


def _normalize_rejected_indices(value: Any, total_findings: int) -> list[int]:
    """Normalize 0-based finding indices emitted by critic model."""
    if not isinstance(value, list):
        return []

    normalized: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int) and 0 <= item < total_findings and item not in seen:
            seen.add(item)
            normalized.append(item)
    return sorted(normalized)


def _status_to_score(status: ContextQualityStatus) -> float:
    if status == "good":
        return 0.8
    if status == "missing_refs":
        return 0.35
    return 0.25


def _score_to_status(score: float, reference_law_valid: bool) -> ContextQualityStatus:
    if not reference_law_valid and score < 0.6:
        return "missing_refs"
    return "good" if score >= 0.6 else "poor"


def _estimate_context_quality(legal_context: str, findings: list[dict], confidence: float) -> float:
    # Baseline heuristic for Phase 1 routing safety before full context validator.
    context_len = len(legal_context.strip())
    if context_len >= 3000:
        density_score = 1.0
    elif context_len >= 1500:
        density_score = 0.8
    elif context_len >= 700:
        density_score = 0.6
    elif context_len >= 300:
        density_score = 0.4
    else:
        density_score = 0.2

    ref_ratio = 0.0
    if findings:
        referenced = sum(1 for f in findings if f.get("reference_law"))
        ref_ratio = referenced / len(findings)

    return _clamp01((density_score * 0.6) + (ref_ratio * 0.2) + (confidence * 0.2))


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _tokenize_keywords(text: str) -> set[str]:
    tokens = re.findall(r"[0-9a-zA-ZÀ-ỹà-ỹđ]+", text.lower())
    stop = {
        "và",
        "hoặc",
        "là",
        "của",
        "các",
        "những",
        "được",
        "không",
        "cho",
        "trong",
        "theo",
        "với",
        "khi",
        "này",
        "đó",
        "đến",
        "tại",
        "như",
        "có",
        "thì",
        "điều",
        "khoản",
    }
    return {t for t in tokens if len(t) >= 3 and t not in stop}


def _keyword_overlap_ratio(a: str, b: str) -> float:
    ta = _tokenize_keywords(a)
    tb = _tokenize_keywords(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta))


def _extract_law_markers(reference_law: str) -> list[str]:
    lower = reference_law.lower()
    markers: list[str] = []
    if "bộ luật dân sự" in lower:
        markers.append("bộ luật dân sự")
    if "luật thương mại" in lower:
        markers.append("luật thương mại")
    if "luật doanh nghiệp" in lower:
        markers.append("luật doanh nghiệp")
    if "bộ luật lao động" in lower:
        markers.append("bộ luật lao động")
    if "luật trọng tài" in lower:
        markers.append("luật trọng tài")
    if "nghị định" in lower:
        markers.append("nghị định")
    return markers


def _extract_law_codes(reference_law: str) -> list[str]:
    # Examples: 35/2006/NĐ-CP, 91/2015/QH13
    return re.findall(r"\d{2,3}\s*/\s*\d{4}\s*/\s*[a-zA-ZđĐ0-9\-]+", reference_law)


def _extract_article_refs(reference_law: str) -> list[str]:
    return [
        _normalize_space(x)
        for x in re.findall(r"điều\s*\d+(?:\.\d+)?", reference_law.lower())
    ]


def _is_reference_supported(reference_law: str, legal_context: str, contract_text: str) -> bool:
    reference_law = reference_law.strip()
    if not reference_law:
        return False

    combined = f"{legal_context}\n{contract_text}".lower()
    compact_combined = re.sub(r"\s+", "", combined)

    markers = _extract_law_markers(reference_law)
    if markers and not any(marker in combined for marker in markers):
        return False

    for code in _extract_law_codes(reference_law):
        compact_code = re.sub(r"\s+", "", code.lower())
        if compact_code not in compact_combined:
            return False

    articles = _extract_article_refs(reference_law)
    if articles and not any(article in _normalize_space(legal_context) for article in articles):
        return False

    return True


def _find_admissibility_rejections(
    findings: list[dict],
    legal_context: str,
    contract_text: str,
    context_validation_errors: list[str],
) -> tuple[list[int], str]:
    rejected: list[int] = []
    reasons: list[str] = []

    for idx, finding in enumerate(findings):
        if finding.get("source") == "deterministic_rule":
            continue

        reference_law = str(finding.get("reference_law", "")).strip()
        if not _is_reference_supported(reference_law, legal_context, contract_text):
            rejected.append(idx)
            reasons.append(f"#{idx}:reference_not_supported")
            continue

        evidence_text = f"{finding.get('clause', '')} {finding.get('violation', '')}"
        overlap = _keyword_overlap_ratio(evidence_text, legal_context)
        if overlap < 0.03:
            rejected.append(idx)
            reasons.append(f"#{idx}:weak_legal_overlap")

    dedup = sorted(set(rejected))
    reason_text = ", ".join(reasons[:8]) if reasons else ""
    return dedup, reason_text


def _apply_confidence_caps(
    confidence: float,
    error_type: ErrorType,
    context_quality_status: ContextQualityStatus,
    context_quality_score: float,
    context_validation_errors: list[str],
) -> float:
    cap = 1.0
    if error_type == "reasoning":
        cap = min(cap, 0.6)
    if context_quality_status != "good":
        cap = min(cap, 0.7)
    if context_quality_score < 0.6:
        cap = min(cap, 0.65)
    if any("low relevance score" in str(err).lower() for err in context_validation_errors):
        cap = min(cap, 0.55)
    return _clamp01(min(confidence, cap))


# ---------------------------------------------------------------------------
# LangGraph node 
# ---------------------------------------------------------------------------


async def critic_node(state: AuditState) -> dict:
    """LangGraph node: validate findings with negation scan + optional LLM critic."""
    legal_context: str = state.get("legal_context", "")
    contract_text: str = str(state.get("contract_text", ""))
    findings: list[dict] = state.get("audit_findings", [])
    confidence: float = _clamp01(float(state.get("confidence", 0.0)))
    retry_count: int = state.get("retry_count", 0)
    context_quality_status: ContextQualityStatus = _normalize_context_quality_status(
        state.get("context_quality", "poor")
    )
    context_quality_score: float = _clamp01(
        float(state.get("context_quality_score", _status_to_score(context_quality_status)))
    )
    if context_quality_score == 0.0 and legal_context:
        context_quality_score = _estimate_context_quality(legal_context, findings, confidence)
        context_quality_status = _score_to_status(context_quality_score, True)
    context_validation_errors = state.get("context_validation_errors", [])
    if not isinstance(context_validation_errors, list):
        context_validation_errors = []

    admissibility_rejected, admissibility_reason = _find_admissibility_rejections(
        findings,
        legal_context,
        contract_text,
        context_validation_errors,
    )

    # ------------------------------------------------------------------
    # Layer 1 — regex negation scan (no LLM)
    # ------------------------------------------------------------------
    matches = _NEGATION_RE.findall(legal_context)
    # Deduplicate while preserving first-occurrence order
    seen_neg: set[str] = set()
    negations_found: list[str] = []
    for m in matches:
        key = m.lower()
        if key not in seen_neg:
            seen_neg.add(key)
            negations_found.append(key)

    critic_feedback: dict = {
        "layer1_negations": negations_found,
        "layer2_called": False,
        "missed_exceptions": [],
        "rejected_finding_indices": [],
        "findings_pruned": 0,
        "reference_law_valid": True,
        "adjusted_confidence": confidence,
        "context_quality_score": context_quality_score,
        "context_quality_status": context_quality_status,
        "refined_query": None,
        "parse_ok": True,
        "reason": "Layer-1 pass, no additional review needed.",
    }

    layer2_needed = (
        bool(negations_found)
        or confidence < 0.7
        or context_quality_status != "good"
        or context_quality_score < 0.6
        or bool(admissibility_rejected)
    )

    if not layer2_needed:
        logger.warning(
            "critic: confidence=%.2f, context_quality=%s, quality_score=%.2f, retry=%d — layer-2 skipped",
            confidence,
            context_quality_status,
            context_quality_score,
            retry_count + 1,
        )
        return {
            "negations_found": negations_found,
            "critic_feedback": critic_feedback,
            "audit_findings": findings,
            "confidence": confidence,
            "context_quality": context_quality_status,
            "context_quality_score": context_quality_score,
            "error_type": "ok",
            "retry_count": retry_count + 1,
        }

    # ------------------------------------------------------------------
    # Layer 2 — LLM critic
    # ------------------------------------------------------------------
    logger.info(
        "critic: calling LLM critic (negations=%d, confidence=%.2f, context_quality=%s, quality_score=%.2f, retry=%d)",
        len(negations_found),
        confidence,
        context_quality_status,
        context_quality_score,
        retry_count + 1,
    )

    negations_str = ", ".join(negations_found) if negations_found else "Không có"

    # Safe truncation: compact-serialize findings one by one, stop before 2000 chars.
    # Avoids cutting mid-JSON that makes _extract_json_obj return {} silently.
    safe_findings: list[dict] = []
    _total = 0
    for f in findings:
        s = json.dumps(f, ensure_ascii=False)
        if _total + len(s) > 2000:
            break
        safe_findings.append(f)
        _total += len(s)
    findings_str = json.dumps(safe_findings, ensure_ascii=False)
    error_type: ErrorType = "low_confidence"
    rejected_indices: list[int] = []

    try:
        response = await _llm_client.chat.completions.create(
            model=_MODEL,
            messages=[{
                "role": "user",
                "content": CRITIC_SYSTEM_PROMPT.format(
                    negations=negations_str,
                    confidence=confidence,
                    findings_json=findings_str,
                    legal_context=legal_context[:4000],
                ),
            }],
        )
        raw = response.choices[0].message.content
        data = _extract_json_obj(raw)

        parse_ok = bool(data)
        adjusted = _clamp01(float(data.get("confidence", confidence)))
        parsed_context_score = _clamp01(float(data.get("context_quality", context_quality_score)))
        reference_law_valid = bool(data.get("reference_law_valid", True))
        error_type = _normalize_error_type(data.get("error_type"))
        rejected_indices = _normalize_rejected_indices(
            data.get("rejected_finding_indices", []),
            len(findings),
        )
        if not reference_law_valid:
            error_type = "hallucination"
        parsed_context_status = _score_to_status(parsed_context_score, reference_law_valid)

        if error_type == "reasoning" and findings and not rejected_indices:
            # Enforce anti-nitpicking guardrail: if critic says reasoning but omits indices,
            # treat all current findings as non-issues to avoid false-positive leakage.
            rejected_indices = list(range(len(findings)))

        critic_feedback.update({
            "layer2_called": True,
            "missed_exceptions": data.get("missed_exceptions", []),
            "rejected_finding_indices": rejected_indices,
            "reference_law_valid": reference_law_valid,
            "adjusted_confidence": adjusted,
            "context_quality_score": parsed_context_score,
            "context_quality_status": parsed_context_status,
            "refined_query": data.get("refined_query"),
            "parse_ok": parse_ok,
            "reason": str(data.get("reason") or "Layer-2 decision applied."),
        })
        confidence = adjusted
        context_quality_score = parsed_context_score
        context_quality_status = parsed_context_status

    except (RateLimitError, APITimeoutError) as exc:
        logger.warning("critic: LLM rate-limited, using layer-1 result only: %s", exc)
        critic_feedback.update({
            "layer2_called": False,
            "parse_ok": False,
            "reason": "LLM critic unavailable (rate limit/timeout).",
        })
    except Exception as exc:
        logger.warning("critic: LLM call failed, using layer-1 result only: %s", exc)
        critic_feedback.update({
            "layer2_called": False,
            "parse_ok": False,
            "reason": "LLM critic call failed.",
        })

    if error_type == "ok" and negations_found and critic_feedback.get("missed_exceptions"):
        error_type = "reasoning"

    if admissibility_rejected:
        rejected_indices = sorted(set(rejected_indices) | set(admissibility_rejected))
        critic_feedback["admissibility_rejected_indices"] = admissibility_rejected
        critic_feedback["admissibility_reason"] = admissibility_reason
        if error_type in {"ok", "reasoning"}:
            # If the validator already flagged low retrieval relevance, the rejections
            # are likely an artefact of bad context rather than hallucinated references.
            # Route back to retrieval instead of finalising prematurely.
            # Handles both: LLM critic returns "ok" (common) or "reasoning" directly.
            _low_relevance = any(
                "low relevance score" in str(err).lower()
                for err in context_validation_errors
            )
            error_type = "low_confidence" if _low_relevance else "reasoning"

    if rejected_indices:
        reject_set = set(rejected_indices)
        low_relevance = any(
            "low relevance score" in str(err).lower() for err in context_validation_errors
        )
        # Deterministic-rule findings are high-confidence and exempt from admissibility,
        # so exclude them from the safeguard threshold to avoid defeating the safeguard.
        non_det_count = sum(1 for f in findings if f.get("source") != "deterministic_rule")
        # Keep findings when retrieval relevance is poor; prefer lowering confidence
        # over erasing all potential legal issues.
        if (
            low_relevance
            and non_det_count > 0
            and len(reject_set) >= non_det_count
            and error_type != "hallucination"
        ):
            critic_feedback["prune_safeguard"] = "kept_all_due_low_relevance"
            reject_set = set()

        if not reject_set:
            rejected_indices = []

    if rejected_indices:
        reject_set = set(rejected_indices)
        original_len = len(findings)
        findings = [f for idx, f in enumerate(findings) if idx not in reject_set]
        pruned = original_len - len(findings)
        critic_feedback["findings_pruned"] = pruned
        critic_feedback["rejected_finding_indices"] = sorted(reject_set)

        if findings:
            referenced = sum(1 for f in findings if f.get("reference_law"))
            confidence = _clamp01(referenced / len(findings))
        else:
            confidence = _clamp01(min(confidence, 0.55))
        critic_feedback["adjusted_confidence"] = confidence

    confidence = _apply_confidence_caps(
        confidence,
        error_type,
        context_quality_status,
        context_quality_score,
        context_validation_errors,
    )
    critic_feedback["adjusted_confidence"] = confidence

    if error_type == "ok" and confidence < 0.7:
        error_type = "low_confidence"

    if error_type == "low_confidence" and critic_feedback.get("reference_law_valid") is False:
        error_type = "hallucination"

    logger.warning(
        "critic: confidence=%.2f, context_quality=%s, quality_score=%.2f, error_type=%s, retry=%d",
        confidence,
        context_quality_status,
        context_quality_score,
        error_type,
        retry_count + 1,
    )
    return {
        "negations_found": negations_found,
        "critic_feedback": critic_feedback,
        "audit_findings": findings,
        "confidence": confidence,
        "context_quality": context_quality_status,
        "context_quality_score": context_quality_score,
        "error_type": error_type,
        "retry_count": retry_count + 1,
    }
