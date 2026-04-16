"""Critic Agent — validates audit findings for missed negations and confidence.

Inputs:  AuditState.legal_context, AuditState.audit_findings,
         AuditState.confidence, AuditState.retry_count
Outputs: AuditState.negations_found, AuditState.critic_feedback,
         AuditState.confidence (possibly adjusted), AuditState.retry_count (+1),
         AuditState.error_type

Layer 1 (no LLM): regex scan of legal_context for negation/exception patterns.
Layer 2 (LLM, Cerebras): called only when Layer 1 finds negations OR confidence < 0.7.
  Checks: missed exceptions, reference_law validity, adjusted confidence, refined_query.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, cast

from openai import APITimeoutError, AsyncOpenAI, RateLimitError

from core.prompts import CRITIC_SYSTEM_PROMPT
from core.state import AuditState, ErrorType

logger = logging.getLogger(__name__)

_MODEL = "qwen-3-235b-a22b-instruct-2507"
_cerebras = AsyncOpenAI(
    api_key=os.getenv("CEREBRAS_API_KEY"),
    base_url="https://api.cerebras.ai/v1",
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


# ---------------------------------------------------------------------------
# LangGraph node 
# ---------------------------------------------------------------------------


async def critic_node(state: AuditState) -> dict:
    """LangGraph node: validate findings with negation scan + optional LLM critic."""
    legal_context: str = state.get("legal_context", "")
    findings: list[dict] = state.get("audit_findings", [])
    confidence: float = _clamp01(float(state.get("confidence", 0.0)))
    retry_count: int = state.get("retry_count", 0)
    context_quality: float = _clamp01(
        float(state.get("context_quality", _estimate_context_quality(legal_context, findings, confidence)))
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
        "reference_law_valid": True,
        "adjusted_confidence": confidence,
        "context_quality": context_quality,
        "refined_query": None,
        "parse_ok": True,
        "reason": "Layer-1 pass, no additional review needed.",
    }

    layer2_needed = bool(negations_found) or confidence < 0.7 or context_quality < 0.6

    if not layer2_needed:
        logger.warning(
            "critic: confidence=%.2f, context_quality=%.2f, retry=%d — layer-2 skipped",
            confidence,
            context_quality,
            retry_count + 1,
        )
        return {
            "negations_found": negations_found,
            "critic_feedback": critic_feedback,
            "confidence": confidence,
            "context_quality": context_quality,
            "error_type": "ok",
            "retry_count": retry_count + 1,
        }

    # ------------------------------------------------------------------
    # Layer 2 — LLM critic (Cerebras)
    # ------------------------------------------------------------------
    logger.info(
        "critic: calling LLM critic (negations=%d, confidence=%.2f, context_quality=%.2f, retry=%d)",
        len(negations_found),
        confidence,
        context_quality,
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

    try:
        response = await _cerebras.chat.completions.create(
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
        parsed_context_quality = _clamp01(float(data.get("context_quality", context_quality)))
        error_type = _normalize_error_type(data.get("error_type"))
        if data.get("reference_law_valid") is False:
            error_type = "hallucination"

        critic_feedback.update({
            "layer2_called": True,
            "missed_exceptions": data.get("missed_exceptions", []),
            "reference_law_valid": bool(data.get("reference_law_valid", True)),
            "adjusted_confidence": adjusted,
            "context_quality": parsed_context_quality,
            "refined_query": data.get("refined_query"),
            "parse_ok": parse_ok,
            "reason": str(data.get("reason") or "Layer-2 decision applied."),
        })
        confidence = adjusted
        context_quality = parsed_context_quality

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

    if error_type == "ok" and confidence < 0.7:
        error_type = "low_confidence"
    if error_type == "ok" and negations_found:
        error_type = "reasoning"

    if error_type == "low_confidence" and critic_feedback.get("reference_law_valid") is False:
        error_type = "hallucination"

    logger.warning(
        "critic: confidence=%.2f, context_quality=%.2f, error_type=%s, retry=%d",
        confidence,
        context_quality,
        error_type,
        retry_count + 1,
    )
    return {
        "negations_found": negations_found,
        "critic_feedback": critic_feedback,
        "confidence": confidence,
        "context_quality": context_quality,
        "error_type": error_type,
        "retry_count": retry_count + 1,
    }
