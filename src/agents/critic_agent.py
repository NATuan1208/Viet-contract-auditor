"""Critic Agent — validates audit findings for missed negations and confidence.

Inputs:  AuditState.legal_context, AuditState.audit_findings,
         AuditState.confidence_score, AuditState.iteration
Outputs: AuditState.negations_found, AuditState.critic_feedback,
         AuditState.confidence_score (possibly adjusted), AuditState.iteration (+1)

Layer 1 (no LLM): regex scan of legal_context for negation/exception patterns.
Layer 2 (LLM, Cerebras): called only when Layer 1 finds negations OR confidence < 0.7.
  Checks: missed exceptions, reference_law validity, adjusted confidence, refined_query.
"""

from __future__ import annotations

import json
import logging
import os
import re

from openai import APITimeoutError, AsyncOpenAI, RateLimitError

from core.prompts import CRITIC_SYSTEM_PROMPT
from core.state import AuditState

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


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


async def critic_node(state: AuditState) -> dict:
    """LangGraph node: validate findings with negation scan + optional LLM critic."""
    legal_context: str = state.get("legal_context", "")
    findings: list[dict] = state.get("audit_findings", [])
    confidence: float = state.get("confidence_score", 0.0)
    iteration: int = state.get("iteration", 0)

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
        "refined_query": None,
    }

    layer2_needed = bool(negations_found) or confidence < 0.7

    if not layer2_needed:
        logger.warning(
            "critic: confidence=%.2f, iteration=%d — layer-2 skipped (no negations, confidence>=0.7)",
            confidence,
            iteration + 1,
        )
        return {
            "negations_found": negations_found,
            "critic_feedback": critic_feedback,
            "confidence_score": confidence,
            "iteration": iteration + 1,
        }

    # ------------------------------------------------------------------
    # Layer 2 — LLM critic (Cerebras)
    # ------------------------------------------------------------------
    logger.info(
        "critic: calling LLM critic (negations=%d, confidence=%.2f, iteration=%d)",
        len(negations_found),
        confidence,
        iteration + 1,
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

        adjusted = float(data.get("confidence", confidence))
        critic_feedback.update({
            "layer2_called": True,
            "missed_exceptions": data.get("missed_exceptions", []),
            "reference_law_valid": bool(data.get("reference_law_valid", True)),
            "adjusted_confidence": adjusted,
            "refined_query": data.get("refined_query"),
        })
        confidence = adjusted

    except (RateLimitError, APITimeoutError) as exc:
        logger.warning("critic: LLM rate-limited, using layer-1 result only: %s", exc)
    except Exception as exc:
        logger.warning("critic: LLM call failed, using layer-1 result only: %s", exc)

    logger.warning(
        "critic: confidence=%.2f, iteration=%d",
        confidence,
        iteration + 1,
    )
    return {
        "negations_found": negations_found,
        "critic_feedback": critic_feedback,
        "confidence_score": confidence,
        "iteration": iteration + 1,
    }
