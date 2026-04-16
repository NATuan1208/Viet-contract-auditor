"""Audit Agent — cross-checks contract clauses against legal context.

Inputs:  AuditState.chunks, AuditState.legal_context, AuditState.clause_risk_scores
Outputs: AuditState.audit_findings, AuditState.confidence, AuditState.skipped_clauses

Hybrid optimization before LLM calls:
  - low-risk clauses are skipped directly
  - medium/high clauses are filtered by relevance:
      * include if clause has risk keywords
      * or include if clause/context keyword overlap is high enough
Then calls Cerebras qwen-3-235b for analyzed clauses only (sequential, semaphore(1)).
Each clause receives its own legal_context section (matched by clause index, capped at 3000 chars).
Exponential backoff retry on 429 / timeout (2s -> 4s -> 8s, max 3 retries).
1.5s sleep between clause completions to respect Cerebras TPM.
confidence = fraction of findings with a non-empty reference_law.
>50% analyzed clause failures -> pipeline error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from openai import APITimeoutError, AsyncOpenAI, RateLimitError

from core.prompts import AUDIT_DEEP_SYSTEM_PROMPT, AUDIT_QUICK_SYSTEM_PROMPT
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


def _split_legal_context_by_clause_index(legal_context: str, n_clauses: int) -> list[str]:
    """Map markdown sections (### Dieu khoan N) back to clause indices."""
    matches = list(re.finditer(r"###\s*Điều khoản\s*(\d+)\s*\n", legal_context))
    mapped: dict[int, str] = {}
    for i, match in enumerate(matches):
        clause_index = int(match.group(1)) - 1
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(legal_context)
        mapped[clause_index] = legal_context[start:end].strip()[:_MAX_CONTEXT_PER_CLAUSE]

    return [mapped.get(i, "") for i in range(n_clauses)]


def _keyword_set(text: str) -> set[str]:
    """Extract lowercase keyword tokens for lightweight relevance checks."""
    if not text:
        return set()
    normalized = text.lower().replace("_", " ")
    tokens = re.findall(r"[0-9a-zA-ZÀ-ỹà-ỹđ]+", normalized)
    return {tok for tok in tokens if len(tok) >= 3 and tok not in _STOP_WORDS}


def _has_risk_keywords(clause: str) -> bool:
    lowered = clause.lower()
    return any(keyword in lowered for keyword in _RISK_KEYWORDS)


def _should_audit_clause(clause: str, clause_context: str) -> tuple[bool, str]:
    """Hybrid relevance policy for medium/high-risk clauses."""
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


async def _call_with_retry(chunk: str, clause_context: str, prompt_template: str) -> list[dict] | None:
    """Call Cerebras with exponential backoff on 429 / timeout; return None on non-retriable error."""
    for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
        try:
            response = await _cerebras.chat.completions.create(
                model=_MODEL,
                messages=[{
                    "role": "user",
                    "content": prompt_template.format(
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
                    attempt,
                    len(_RETRY_DELAYS),
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "audit_agent: clause failed after %d retries: %s",
                    len(_RETRY_DELAYS),
                    exc,
                )
                return None
        except Exception as exc:
            logger.warning("audit_agent: non-retriable error, skipping clause: %s", exc)
            return None
    return None


async def audit_node(state: AuditState) -> dict:
    """LangGraph node: detect violations in each contract clause."""
    chunks = state.get("chunks", [])
    clause_risk_scores = state.get("clause_risk_scores", [])
    skipped_clauses = list(state.get("skipped_clauses", []))

    if not chunks:
        logger.warning("audit_agent: no chunks to audit")
        return {"audit_findings": [], "confidence": 0.0, "skipped_clauses": skipped_clauses}

    if len(clause_risk_scores) != len(chunks):
        clause_risk_scores = ["medium"] * len(chunks)

    legal_context = state.get("legal_context", "")
    clause_contexts = _split_legal_context_by_clause_index(legal_context, len(chunks))

    sem = asyncio.Semaphore(1)
    all_findings: list[dict] = []
    failed = 0
    skipped_by_risk = 0
    skipped_by_relevance = 0

    route_reasons = {
        "risk_keyword": 0,
        "context_overlap": 0,
    }

    async def _audit_clause(chunk: str, ctx: str, prompt_template: str) -> list[dict] | None:
        async with sem:
            return await _call_with_retry(chunk, ctx, prompt_template)

    tasks: list[tuple[int, asyncio.Future]] = []
    for i, (chunk, ctx, risk) in enumerate(zip(chunks, clause_contexts, clause_risk_scores)):
        if risk == "low":
            skipped_by_risk += 1
            skipped_clauses.append({
                "clause_index": i,
                "reason": "Skipped - Low Risk",
                "clause_preview": chunk[:120],
            })
            continue

        should_audit, reason = _should_audit_clause(chunk, ctx)
        if not should_audit:
            skipped_by_relevance += 1
            skipped_clauses.append({
                "clause_index": i,
                "reason": f"Skipped - {reason}",
                "clause_preview": chunk[:120],
            })
            continue

        if reason in route_reasons:
            route_reasons[reason] += 1

        prompt_template = AUDIT_DEEP_SYSTEM_PROMPT if risk == "high" else AUDIT_QUICK_SYSTEM_PROMPT
        tasks.append((i, _audit_clause(chunk, ctx, prompt_template)))

    analyzed_count = len(tasks)
    if analyzed_count == 0:
        logger.info(
            "audit_agent: all clauses skipped (total=%d, skipped_low=%d, skipped_relevance=%d)",
            len(chunks),
            skipped_by_risk,
            skipped_by_relevance,
        )
        return {
            "audit_findings": [],
            "confidence": 1.0 if skipped_by_risk == len(chunks) else 0.0,
            "skipped_clauses": skipped_clauses,
        }

    # Process sequentially with 1.5s sleep between completions for analyzed clauses
    for pos, (_, coro) in enumerate(tasks):
        result = await coro
        if result is None:
            failed += 1
        else:
            all_findings.extend(result)
        if pos < len(tasks) - 1:
            await asyncio.sleep(1.5)

    if failed > analyzed_count // 2:
        return {
            "audit_findings": all_findings,
            "confidence": 0.0,
            "skipped_clauses": skipped_clauses,
            "error": f"audit_agent: {failed}/{analyzed_count} analyzed clauses failed",
        }

    scored = sum(1 for f in all_findings if f.get("reference_law"))
    confidence = scored / len(all_findings) if all_findings else 0.0
    confidence = max(0.0, min(1.0, confidence))

    logger.info(
        (
            "audit_agent: total=%d, analyzed=%d, skipped_low=%d, skipped_relevance=%d, "
            "routed_by_risk=%d, routed_by_overlap=%d, findings=%d, failed=%d, confidence=%.2f"
        ),
        len(chunks),
        analyzed_count,
        skipped_by_risk,
        skipped_by_relevance,
        route_reasons["risk_keyword"],
        route_reasons["context_overlap"],
        len(all_findings),
        failed,
        confidence,
    )

    return {
        "audit_findings": all_findings,
        "confidence": confidence,
        "skipped_clauses": skipped_clauses,
    }
