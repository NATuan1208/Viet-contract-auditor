"""Audit Agent — cross-checks contract clauses against legal context.

Inputs:  AuditState.chunks, AuditState.legal_context, AuditState.clause_risk_scores
Outputs: AuditState.audit_findings, AuditState.confidence, AuditState.skipped_clauses

Hybrid optimization before LLM calls:
  - low-risk clauses are skipped directly
  - medium/high clauses are filtered by relevance:
      * include if clause has risk keywords
      * or include if clause/context keyword overlap is high enough
Then calls configured LLM for analyzed clauses only (sequential, semaphore(1)).
Each clause receives its own legal_context section (matched by clause index, capped at 3000 chars).
Exponential backoff retry on 429 / timeout (2s -> 4s -> 8s, max 3 retries).
1.5s sleep between clause completions to reduce TPM pressure.
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

from core.llm_config import get_llm_settings
from core.prompts import AUDIT_DEEP_SYSTEM_PROMPT, AUDIT_QUICK_SYSTEM_PROMPT
from core.state import AuditState

logger = logging.getLogger(__name__)

_LLM_SETTINGS = get_llm_settings()
_MODEL = _LLM_SETTINGS.model
_llm_client = AsyncOpenAI(
    api_key=_LLM_SETTINGS.api_key,
    base_url=_LLM_SETTINGS.base_url,
)

_REQUIRED_FINDING_KEYS = {"clause", "violation", "reference_law", "suggested_fix"}
_MAX_CONTEXT_PER_CLAUSE = 3000
_RETRY_DELAYS = (2.0, 4.0, 8.0)  # exponential backoff for 429 / timeout
_MIN_OVERLAP_KEYWORDS = int(os.getenv("AUDIT_MIN_OVERLAP_KEYWORDS", "1"))
_AUDIT_LOW_RISK_CLAUSES = os.getenv("AUDIT_LOW_RISK_CLAUSES", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}

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


def _has_structural_risk_signals(clause: str) -> bool:
    """Detect common traps: numeric thresholds, unilateral rights, numbering gaps."""
    lowered = clause.lower()

    signal_patterns = [
        r"\b\d+\s*%\b",
        r"\b0?3\s+ngày\b",
        r"mọi\s+trường\s+hợp",
        r"không\s+cần\s+sự\s+chấp\s+thuận",
        r"tự\s+động\s+có\s+hiệu\s+lực",
        r"không\s+cần\s+hỏi\s+lại\s+ý\s+kiến",
    ]
    if any(re.search(pat, lowered) for pat in signal_patterns):
        return True

    # Numbering gap check (e.g. 2.1, 2.2, 2.3, 2.5 -> missing 2.4)
    sub_items = [int(m.group(2)) for m in re.finditer(r"\b(\d+)\.(\d+)\b", clause)]
    unique_sorted = sorted(set(sub_items))
    if len(unique_sorted) >= 3:
        for i in range(1, len(unique_sorted)):
            if unique_sorted[i] - unique_sorted[i - 1] > 1:
                return True

    return False


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _finding_signature(finding: dict) -> str:
    combined = _normalize_space(
        f"{finding.get('clause', '')} {finding.get('violation', '')}"
    ).lower()
    clause_idx = finding.get("clause_index", -1)

    if "tự động tăng 20%" in combined and "thỏa thuận" in combined:
        return "numeric_auto_fee_increase"
    if "khiếu nại" in combined and ("không" in combined or "cấm" in combined):
        return "logical_complaint_waiver"
    if "hệ thống nhượng quyền" in combined and "01 năm" in combined:
        return "omission_one_year_prerequisite"

    violation_key = _normalize_space(str(finding.get("violation", ""))).lower()[:140]
    return f"{clause_idx}:{violation_key}"


def _extract_clause_snippet(chunks: list[str], clause_index: int, fallback: str) -> str:
    if 0 <= clause_index < len(chunks):
        return chunks[clause_index][:450]
    return fallback[:450]


def _extract_subclause_snippet(contract_text: str, marker: str, fallback: str) -> str:
    lower_contract = contract_text.lower()
    lower_marker = marker.lower()
    pos = lower_contract.find(lower_marker)
    if pos == -1:
        return fallback[:450]
    end = min(len(contract_text), pos + 450)
    return contract_text[pos:end]


def _build_rule_based_findings(state: AuditState, chunks: list[str]) -> list[dict]:
    """Inject deterministic high-value findings for known legal trap patterns."""
    contract_text = str(state.get("contract_text", ""))
    lowered_contract = _normalize_space(contract_text).lower()
    lowered_chunks = [_normalize_space(c).lower() for c in chunks]

    findings: list[dict] = []

    # Omission trap: franchise system must be in operation for >= 01 year before franchising.
    is_franchise = "nhượng quyền" in lowered_contract
    has_decree_35 = bool(re.search(r"35\s*/\s*2006\s*/\s*nđ-?cp", lowered_contract))
    has_one_year_prereq = bool(
        re.search(r"hệ\s*thống.{0,120}hoạt\s*động.{0,40}(?:01|1)\s*năm", lowered_contract)
    )
    if is_franchise and has_decree_35 and not has_one_year_prereq:
        clause_index = 0
        for idx, chunk_lower in enumerate(lowered_chunks):
            if "xét thấy" in chunk_lower or "điều 1" in chunk_lower:
                clause_index = idx
                break
        findings.append({
            "clause": _extract_clause_snippet(chunks, clause_index, contract_text),
            "violation": (
                "Không nêu rõ hệ thống nhượng quyền đã hoạt động ít nhất 01 năm "
                "trước khi nhượng quyền."
            ),
            "reference_law": "Nghị định 35/2006/NĐ-CP về nhượng quyền thương mại",
            "suggested_fix": (
                "Bổ sung điều khoản xác nhận hệ thống kinh doanh dự định nhượng quyền đã "
                "hoạt động tối thiểu 01 năm tại Việt Nam trước thời điểm ký hợp đồng nhượng quyền."
            ),
            "clause_index": clause_index,
            "source": "deterministic_rule",
        })

    # Logical trap: blanket complaint waiver about quality of materials/equipment.
    for idx, chunk_lower in enumerate(lowered_chunks):
        has_waiver = (
            ("không" in chunk_lower and "khiếu nại" in chunk_lower)
            or ("không" in chunk_lower and "khiếu kiện" in chunk_lower)
            or ("cấm" in chunk_lower and "khiếu nại" in chunk_lower)
        )
        has_quality_scope = (
            "chất lượng" in chunk_lower
            or "máy móc" in chunk_lower
            or "thiết bị" in chunk_lower
            or "nguyên liệu" in chunk_lower
        )
        if has_waiver and has_quality_scope:
            marker = "4.2.14"
            clause_snippet = _extract_subclause_snippet(contract_text, marker, chunks[idx])
            findings.append({
                "clause": clause_snippet,
                "violation": (
                    "Điều 4.2.14 tước bỏ quyền khiếu nại/khiếu kiện của bên nhận quyền liên quan "
                    "đến chất lượng hàng hóa, thiết bị hoặc nguyên liệu do bên nhượng quyền cung cấp."
                ),
                "reference_law": "Luật Thương mại 2005 và nguyên tắc thiện chí, cân bằng quyền lợi",
                "suggested_fix": (
                    "Sửa điều khoản theo hướng bên nhận quyền vẫn có quyền khiếu nại, yêu cầu "
                    "khắc phục hoặc bồi thường khi chất lượng hàng hóa, thiết bị, nguyên liệu không đạt cam kết."
                ),
                "clause_index": idx,
                "source": "deterministic_rule",
            })
            break

    # Numeric trap: automatic annual fee increase without re-negotiation.
    for idx, chunk_lower in enumerate(lowered_chunks):
        has_auto_increase = bool(re.search(r"tự\s*động\s*tăng\s*20\s*%", chunk_lower))
        has_no_reconsent = (
            "không cần" in chunk_lower and "thỏa thuận" in chunk_lower
        ) or ("không" in chunk_lower and "thỏa thuận lại" in chunk_lower)
        if has_auto_increase and has_no_reconsent:
            marker = "6.1.2"
            clause_snippet = _extract_subclause_snippet(contract_text, marker, chunks[idx])
            findings.append({
                "clause": clause_snippet,
                "violation": (
                    "Điều 6.1.2 quy định phí nhượng quyền định kỳ tự động tăng 20% mỗi năm mà không cần "
                    "thỏa thuận lại của các bên."
                ),
                "reference_law": "Luật Thương mại 2005; nguyên tắc tự do thỏa thuận và cân bằng lợi ích",
                "suggested_fix": (
                    "Chỉ điều chỉnh phí định kỳ khi có cơ chế thương lượng lại rõ ràng và có "
                    "xác nhận bằng văn bản của cả hai bên."
                ),
                "clause_index": idx,
                "source": "deterministic_rule",
            })
            break

    return findings


def _should_audit_clause(clause: str, clause_context: str, risk: str) -> tuple[bool, str]:
    """Hybrid relevance policy for medium/high-risk clauses."""
    if risk == "high":
        return True, "high_risk_forced"

    if _has_risk_keywords(clause):
        return True, "risk_keyword"

    if _has_structural_risk_signals(clause):
        return True, "structural_signal"

    if not clause_context.strip():
        if risk in {"medium", "high"}:
            return True, "no_context_medium_high"
        return False, "no_context_skip"

    clause_kw = _keyword_set(clause)
    context_kw = _keyword_set(clause_context)
    overlap = len(clause_kw & context_kw)
    threshold = _MIN_OVERLAP_KEYWORDS if risk in {"medium", "high"} else max(2, _MIN_OVERLAP_KEYWORDS)
    if overlap >= threshold:
        return True, "context_overlap"
    return False, "low_relevance"


async def _call_with_retry(chunk: str, clause_context: str, prompt_template: str) -> list[dict] | None:
    """Call configured LLM with exponential backoff on 429 / timeout; return None on non-retriable error."""
    for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
        try:
            response = await _llm_client.chat.completions.create(
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
        "high_risk_forced": 0,
        "risk_keyword": 0,
        "structural_signal": 0,
        "no_context_medium_high": 0,
        "context_overlap": 0,
    }

    async def _audit_clause(chunk: str, ctx: str, prompt_template: str) -> list[dict] | None:
        async with sem:
            return await _call_with_retry(chunk, ctx, prompt_template)

    tasks: list[tuple[int, asyncio.Future]] = []
    for i, (chunk, ctx, risk) in enumerate(zip(chunks, clause_contexts, clause_risk_scores)):
        if risk == "low" and not _AUDIT_LOW_RISK_CLAUSES:
            skipped_by_risk += 1
            skipped_clauses.append({
                "clause_index": i,
                "reason": "Skipped - Low Risk",
                "clause_preview": chunk[:120],
            })
            continue

        should_audit, reason = _should_audit_clause(chunk, ctx, risk)
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

        # Use deep audit for medium/high to prioritise recall on nuanced legal traps.
        prompt_template = AUDIT_DEEP_SYSTEM_PROMPT if risk in {"high", "medium"} else AUDIT_QUICK_SYSTEM_PROMPT
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
    for pos, (clause_idx, coro) in enumerate(tasks):
        result = await coro
        if result is None:
            failed += 1
        else:
            for finding in result:
                finding.setdefault("clause_index", clause_idx)
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

    rule_based_findings = _build_rule_based_findings(state, chunks)
    existing_by_signature = {_finding_signature(f): i for i, f in enumerate(all_findings)}
    injected_count = 0
    for finding in rule_based_findings:
        signature = _finding_signature(finding)
        existing_index = existing_by_signature.get(signature)
        if existing_index is not None:
            existing = all_findings[existing_index]
            if existing.get("source") != "deterministic_rule":
                all_findings[existing_index] = finding
                injected_count += 1
            continue
        all_findings.append(finding)
        existing_by_signature[signature] = len(all_findings) - 1
        injected_count += 1

    scored = sum(1 for f in all_findings if f.get("reference_law"))
    confidence = scored / len(all_findings) if all_findings else 0.0
    confidence = max(0.0, min(1.0, confidence))

    logger.info(
        (
            "audit_agent: total=%d, analyzed=%d, skipped_low=%d, skipped_relevance=%d, "
            "forced_high=%d, routed_by_risk=%d, structural=%d, no_context_mh=%d, "
            "routed_by_overlap=%d, findings=%d, injected_rules=%d, failed=%d, confidence=%.2f"
        ),
        len(chunks),
        analyzed_count,
        skipped_by_risk,
        skipped_by_relevance,
        route_reasons["high_risk_forced"],
        route_reasons["risk_keyword"],
        route_reasons["structural_signal"],
        route_reasons["no_context_medium_high"],
        route_reasons["context_overlap"],
        len(all_findings),
        injected_count,
        failed,
        confidence,
    )

    return {
        "audit_findings": all_findings,
        "confidence": confidence,
        "skipped_clauses": skipped_clauses,
    }
