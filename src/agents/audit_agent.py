"""Audit Agent — cross-checks contract clauses against legal context.

Inputs:  AuditState.chunks, AuditState.legal_context
Outputs: AuditState.audit_findings, AuditState.confidence

Applies a hybrid relevance filter before LLM calls:
    - Audit if clause has risk keywords
    - Or audit if clause/context keyword overlap is high enough
    - If clause context is empty, only risky clauses are audited
Then calls Cerebras qwen-3-235b for analyzed clauses only (sequential, semaphore(1)).
Each clause receives its own legal_context section (matched by index, capped at 3000 chars).
Exponential backoff retry on 429 / timeout (2s → 4s → 8s, max 3 retries).
1.5s sleep between clause completions to respect Cerebras TPM.
confidence = fraction of findings with a non-empty reference_law.
>50% clause failures → pipeline error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from openai import AsyncOpenAI, RateLimitError, APITimeoutError

from core.prompts import AUDIT_SYSTEM_PROMPT
from core.state import AuditState

logger = logging.getLogger(__name__)

_MODEL = "qwen-3-235b-a22b-instruct-2507"
_cerebras = AsyncOpenAI(
    api_key=os.getenv("CEREBRAS_API_KEY"),
    base_url="https://api.cerebras.ai/v1",
)

_REQUIRED_FINDING_KEYS = {"clause", "violation", "reference_law", "suggested_fix"}
_MAX_CONTEXT_PER_CLAUSE = 3000
_RETRY_DELAYS = (2.0, 4.0, 8.0)  # exponential backoff for 429 / timeout
_MIN_OVERLAP_KEYWORDS = int(os.getenv("AUDIT_MIN_OVERLAP_KEYWORDS", "2"))

_RISK_KEYWORDS = [
    "phạt",
    "vi phạm",
    "bồi thường",
    "chấm dứt",
    "đơn phương",
    "trách nhiệm",
    "nghĩa vụ",
    "thiệt hại",
    "bảo mật",
    "đặt cọc",
    "trọng tài",
    "giải quyết tranh chấp",
]

_STOP_WORDS = {
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


def _extract_json(raw: str) -> list:
    """Extract the first JSON array from raw model output (handles trailing text)."""
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else data.get("findings", [])
    except json.JSONDecodeError:
        pass
    start = raw.find("[")
    if start == -1:
        return []
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    return []
    return []


def _split_legal_context_by_section(legal_context: str, n_clauses: int) -> list[str]:
    """Split legal_context into per-clause sections.

    legal_context is formatted as:
        ### Điều khoản 1\n...\n### Điều khoản 2\n...
    Returns a list of length n_clauses; each entry is the matching section
    (or "" if not found), capped at _MAX_CONTEXT_PER_CLAUSE chars.
    """
    import re
    sections = re.split(r"(?=### Điều khoản \d)", legal_context)
    sections = [s.strip() for s in sections if s.strip()]
    result: list[str] = []
    for i in range(n_clauses):
        if i < len(sections):
            result.append(sections[i][:_MAX_CONTEXT_PER_CLAUSE])
        else:
            result.append("")
    return result


def _keyword_set(text: str) -> set[str]:
    """Extract lowercase keyword tokens for lightweight relevance checks."""
    if not text:
        return set()
    normalized = text.lower().replace("_", " ")
    tokens = re.findall(r"[0-9a-zA-ZÀ-ỹà-ỹđ]+", normalized)
    return {
        tok
        for tok in tokens
        if len(tok) >= 3 and tok not in _STOP_WORDS
    }


def _has_risk_keywords(clause: str) -> bool:
    lowered = clause.lower()
    return any(keyword in lowered for keyword in _RISK_KEYWORDS)


def _should_audit_clause(clause: str, clause_context: str) -> tuple[bool, str]:
    """Hybrid filter policy.

    Rule:
      - Audit if clause has risk keywords.
      - Else audit if keyword overlap with context >= threshold.
      - No-context fallback: only risky clauses are audited.
    """
    if _has_risk_keywords(clause):
        return True, "risk_keyword"

    if not clause_context.strip():
        return False, "no_context_skip"

    clause_kw = _keyword_set(clause)
    context_kw = _keyword_set(clause_context)
    overlap = len(clause_kw & context_kw)
    if overlap >= _MIN_OVERLAP_KEYWORDS:
        return True, "context_overlap"
    return False, "low_relevance"


async def _call_with_retry(chunk: str, clause_context: str) -> list[dict] | None:
    """Call Cerebras with exponential backoff on 429 / timeout; return None on non-retriable error."""
    for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
        try:
            response = await _cerebras.chat.completions.create(
                model=_MODEL,
                messages=[{
                    "role": "user",
                    "content": AUDIT_SYSTEM_PROMPT.format(
                        clause=chunk,
                        legal_context=clause_context,
                    ),
                }],
            )
            raw = response.choices[0].message.content
            findings = _extract_json(raw)
            return [f for f in findings if _REQUIRED_FINDING_KEYS.issubset(f)]
        except (RateLimitError, APITimeoutError) as exc:
            if attempt < len(_RETRY_DELAYS):
                logger.warning(
                    "audit_agent: retriable error (attempt %d/%d), backing off %.1fs: %s",
                    attempt, len(_RETRY_DELAYS), delay, exc,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "audit_agent: clause failed after %d retries: %s", len(_RETRY_DELAYS), exc
                )
                return None
        except Exception as exc:
            logger.warning("audit_agent: non-retriable error, skipping clause: %s", exc)
            return None
    return None  # unreachable but satisfies type checker


async def audit_node(state: AuditState) -> dict:
    """LangGraph node: detect violations in each contract clause."""
    chunks = state.get("chunks", [])

    if not chunks:
        logger.warning("audit_agent: no chunks to audit")
        return {"audit_findings": [], "confidence": 0.0}

    legal_context = state.get("legal_context", "")
    clause_contexts = _split_legal_context_by_section(legal_context, len(chunks))

    sem = asyncio.Semaphore(1)
    all_findings: list[dict] = []
    failed = 0
    skipped = 0
    analyzed_pairs: list[tuple[str, str]] = []
    skip_reasons = {
        "no_context_skip": 0,
        "low_relevance": 0,
    }
    route_reasons = {
        "risk_keyword": 0,
        "context_overlap": 0,
    }

    async def _audit_clause(chunk: str, ctx: str) -> list[dict] | None:
        async with sem:
            return await _call_with_retry(chunk, ctx)

    # Filter clauses before creating LLM tasks to reduce unnecessary calls.
    for idx, (clause, ctx) in enumerate(zip(chunks, clause_contexts), start=1):
        should_audit, reason = _should_audit_clause(clause, ctx)
        if should_audit:
            analyzed_pairs.append((clause, ctx))
            route_reasons[reason] += 1
            continue

        skipped += 1
        if reason in skip_reasons:
            skip_reasons[reason] += 1
        logger.info("audit_agent: skipped clause %d (%s)", idx, reason)

    analyzed_count = len(analyzed_pairs)
    if analyzed_count == 0:
        logger.warning(
            "audit_agent: all clauses skipped (total=%d, skipped=%d, no_context=%d, low_relevance=%d)",
            len(chunks),
            skipped,
            skip_reasons["no_context_skip"],
            skip_reasons["low_relevance"],
        )
        return {"audit_findings": [], "confidence": 0.0}

    tasks = [_audit_clause(c, ctx) for c, ctx in analyzed_pairs]

    # Process sequentially with 1.5s sleep between completions
    for i, coro in enumerate(tasks):
        result = await coro
        if result is None:
            failed += 1
        else:
            all_findings.extend(result)
        if i < len(tasks) - 1:
            await asyncio.sleep(1.5)

    if failed > analyzed_count // 2:
        return {
            "audit_findings": all_findings,
            "confidence": 0.0,
            "error": f"audit_agent: {failed}/{analyzed_count} analyzed clauses failed",
        }

    scored = sum(1 for f in all_findings if f.get("reference_law"))
    confidence = scored / len(all_findings) if all_findings else 0.0
    confidence = max(0.0, min(1.0, confidence))

    logger.info(
        (
            "audit_agent: total=%d, analyzed=%d, skipped=%d, "
            "routed_by_risk=%d, routed_by_overlap=%d, "
            "skip_no_context=%d, skip_low_relevance=%d, findings=%d, failed=%d, confidence=%.2f"
        ),
        len(chunks),
        analyzed_count,
        skipped,
        route_reasons["risk_keyword"],
        route_reasons["context_overlap"],
        skip_reasons["no_context_skip"],
        skip_reasons["low_relevance"],
        len(all_findings),
        failed,
        confidence,
    )
    return {"audit_findings": all_findings, "confidence": confidence}
