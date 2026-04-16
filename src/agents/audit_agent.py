"""Audit Agent — cross-checks contract clauses against legal context.

Inputs:  AuditState.chunks, AuditState.legal_context
Outputs: AuditState.audit_findings, AuditState.confidence

Calls Cerebras qwen-3-235b for each clause (sequential, semaphore(1)).
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

    async def _audit_clause(chunk: str, ctx: str) -> list[dict] | None:
        async with sem:
            return await _call_with_retry(chunk, ctx)

    tasks = [_audit_clause(c, ctx) for c, ctx in zip(chunks, clause_contexts)]

    # Process sequentially with 1.5s sleep between completions
    for i, coro in enumerate(tasks):
        result = await coro
        if result is None:
            failed += 1
        else:
            all_findings.extend(result)
        if i < len(tasks) - 1:
            await asyncio.sleep(1.5)

    if failed > len(chunks) // 2:
        return {
            "audit_findings": all_findings,
            "confidence": 0.0,
            "error": f"audit_agent: {failed}/{len(chunks)} clauses failed",
        }

    scored = sum(1 for f in all_findings if f.get("reference_law"))
    confidence = scored / len(all_findings) if all_findings else 0.0
    confidence = max(0.0, min(1.0, confidence))

    logger.info(
        "audit_agent: %d finding(s), %d failed clause(s), confidence=%.2f",
        len(all_findings),
        failed,
        confidence,
    )
    return {"audit_findings": all_findings, "confidence": confidence}
