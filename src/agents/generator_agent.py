"""Generator Agent — produce final Vietnamese legal audit report.

Inputs:  AuditState.audit_findings, AuditState.contract_domain, AuditState.confidence
Outputs: AuditState.final_report

Primary path:
    - LLM-first generation using GENERATOR_SYSTEM_PROMPT when model config is available.
Fallback path:
    - deterministic template formatter when LLM is unavailable or call fails.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from openai import AsyncOpenAI

from core.llm_config import get_llm_settings
from core.prompts import GENERATOR_SYSTEM_PROMPT
from core.state import AuditState

logger = logging.getLogger(__name__)


def _init_llm_client() -> tuple[str | None, AsyncOpenAI | None]:
    try:
        llm_settings = get_llm_settings()
    except Exception as exc:
        logger.warning("generator_agent: LLM disabled, fallback to template only: %s", exc)
        return None, None

    client = AsyncOpenAI(
        api_key=llm_settings.api_key,
        base_url=llm_settings.base_url,
    )
    return llm_settings.model, client


_MODEL, _llm_client = _init_llm_client()


def _template_report(state: AuditState) -> str:
    """Build a Markdown audit report purely from template (no LLM call)."""
    findings = state.get("audit_findings", [])
    domain = state.get("contract_domain", "Chưa xác định")
    confidence = state.get("confidence", 0.0)
    error_type = state.get("error_type", "low_confidence")
    context_quality = state.get("context_quality", "poor")
    context_quality_score = state.get("context_quality_score", 0.0)
    retry_count = state.get("retry_count", 0)
    chunks = state.get("chunks", [])
    negations = state.get("negations_found", [])
    skipped_clauses = state.get("skipped_clauses", [])
    error = state.get("error")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        "# Báo cáo Kiểm toán Hợp đồng",
        "",
        "## Tóm tắt",
        "",
    ]

    if error:
        lines.append(f"**Lỗi pipeline:** {error}")
    elif not findings:
        lines.append("Không phát hiện vi phạm nào trong lần chạy hiện tại.")
    else:
        lines.append(
            f"Đã kiểm tra **{len(chunks)}** điều khoản, "
            f"phát hiện **{len(findings)}** mục cần xem xét."
        )
        if skipped_clauses:
            lines.append(f"Đã bỏ qua **{len(skipped_clauses)}** điều khoản rủi ro thấp.")

    lines += [
        "",
        "## Chi tiết vi phạm",
        "",
    ]

    if not findings:
        lines.append("_(Không có vi phạm nào được phát hiện)_")
    else:
        for i, f in enumerate(findings, 1):
            clause_preview = f.get("clause", "")[:120]
            ref = f.get("reference_law") or "_(chưa xác định)_"
            lines += [
                f"### Vi phạm {i}",
                f"- **Điều khoản:** {clause_preview}",
                f"- **Vi phạm:** {f.get('violation', '')}",
                f"- **Căn cứ pháp lý:** {ref}",
                f"- **Khuyến nghị sửa đổi:** {f.get('suggested_fix', '')}",
                "",
            ]

    lines += [
        "",
        "## Điều khoản Bỏ Qua (Rủi ro thấp)",
        "",
    ]
    if skipped_clauses:
        for item in skipped_clauses:
            idx = int(item.get("clause_index", -1)) + 1
            reason = item.get("reason", "Skipped - Low Risk")
            preview = item.get("clause_preview", "")
            lines.append(f"- Điều khoản {idx}: {reason} — {preview}")
    else:
        lines.append("Không có điều khoản nào bị bỏ qua.")

    # --- Negations section (always present) ---
    lines += [
        "## Điều khoản phủ định & ngoại lệ",
        "",
    ]
    if negations:
        lines.append(
            "Critic Agent phát hiện các biểu thức phủ định/ngoại lệ trong ngữ cảnh pháp lý "
            "— cần kiểm tra xem hợp đồng có bao gồm đầy đủ các ngoại lệ bắt buộc không:"
        )
        lines.append("")
        for neg in negations:
            lines.append(f"- `{neg}`")
    else:
        lines.append("Không phát hiện điều khoản phủ định hay ngoại lệ pháp lý.")

    lines += [
        "",
        "## Khuyến nghị chung",
        "",
        "Ưu tiên xử lý các vi phạm ảnh hưởng trực tiếp tới quyền lợi và nghĩa vụ cốt lõi.",
        "",
        "---",
        f"*Lĩnh vực: **{domain}** | Điểm tin cậy: {confidence:.2f} | "
        f"Chất lượng ngữ cảnh: {context_quality} ({context_quality_score:.2f}) | Loại lỗi: {error_type} | "
        f"Số lần critic: {retry_count} | Điều khoản: {len(chunks)} | "
        f"Vi phạm: {len(findings)} | {now}*",
    ]

    return "\n".join(lines)


def _build_generator_payload(state: AuditState) -> str:
    findings = state.get("audit_findings", [])
    payload = {
        "findings": findings,
        "confidence": state.get("confidence", 0.0),
        "context_quality": state.get("context_quality", "poor"),
        "context_quality_score": state.get("context_quality_score", 0.0),
        "error_type": state.get("error_type", "low_confidence"),
        "skipped_clauses": state.get("skipped_clauses", []),
        "critic_feedback": state.get("critic_feedback", {}),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def generator_node(state: AuditState) -> dict:
    """LangGraph node: format audit findings into a Vietnamese Markdown report."""
    confidence = state.get("confidence", 0.0)
    domain = state.get("contract_domain", "")
    error = state.get("error")

    negations = state.get("negations_found", [])
    negations_str = (
        "\n".join(f"- `{n}`" for n in negations)
        if negations
        else "Không phát hiện điều khoản phủ định hay ngoại lệ pháp lý."
    )

    llm_ready = bool(_MODEL and _llm_client)

    if llm_ready and not error:
        try:
            response = await _llm_client.chat.completions.create(
                model=_MODEL,
                messages=[{
                    "role": "user",
                    "content": GENERATOR_SYSTEM_PROMPT.format(
                        domain=domain,
                        negations=negations_str,
                        findings_json=_build_generator_payload(state),
                    ),
                }],
                temperature=0.2,
            )
            report = (response.choices[0].message.content or "").strip()
            if report:
                logger.info("generator_agent: LLM report generated (%d chars)", len(report))
                return {"final_report": report}
            logger.warning("generator_agent: LLM returned empty report, fallback to template")
        except Exception as exc:
            logger.error("generator_agent: LLM call failed: %s", exc)
    elif not llm_ready:
        logger.warning("generator_agent: LLM not configured, fallback to template")

    logger.warning(
        "generator_agent: using template formatter (confidence=%.2f)", confidence
    )
    report = _template_report(state)
    logger.info("generator_agent: report generated (%d chars)", len(report))
    return {"final_report": report}
