"""Generator Agent — formats audit findings into a Vietnamese Markdown report.

Inputs:  AuditState.audit_findings, AuditState.contract_domain, AuditState.confidence
Outputs: AuditState.final_report

Two paths:
    - confidence >= 0.3: Cerebras qwen-3-235b call with GENERATOR_SYSTEM_PROMPT
    - confidence < 0.3: pure template formatter (no LLM call)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from openai import AsyncOpenAI

from core.prompts import GENERATOR_SYSTEM_PROMPT
from core.state import AuditState

logger = logging.getLogger(__name__)

_MODEL = "qwen-3-235b-a22b-instruct-2507"
_cerebras = AsyncOpenAI(
    api_key=os.getenv("CEREBRAS_API_KEY"),
    base_url="https://api.cerebras.ai/v1",
)


def _template_report(state: AuditState) -> str:
    """Build a Markdown audit report purely from template (no LLM call)."""
    findings = state.get("audit_findings", [])
    domain = state.get("contract_domain", "Chưa xác định")
    confidence = state.get("confidence", 0.0)
    error_type = state.get("error_type", "low_confidence")
    context_quality = state.get("context_quality", 0.0)
    retry_count = state.get("retry_count", 0)
    chunks = state.get("chunks", [])
    negations = state.get("negations_found", [])
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
        lines.append(
            "Không phát hiện vi phạm nào. "
            "_(Lưu ý: đang chạy ở chế độ STUB — cần OPENAI_API_KEY để phân tích thực sự)_"
        )
    else:
        stub_note = " _(STUB — cần OPENAI_API_KEY)_" if confidence == 0.0 else ""
        lines.append(
            f"Đã kiểm tra **{len(chunks)}** điều khoản, "
            f"phát hiện **{len(findings)}** mục cần xem xét{stub_note}."
        )

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
        "_(Xem chi tiết từng vi phạm ở trên. "
        "Cần OPENAI_API_KEY để có khuyến nghị tổng hợp từ AI.)_",
        "",
        "---",
        f"*Lĩnh vực: **{domain}** | Điểm tin cậy: {confidence:.2f} | "
        f"Chất lượng ngữ cảnh: {context_quality:.2f} | Loại lỗi: {error_type} | "
        f"Số lần critic: {retry_count} | Điều khoản: {len(chunks)} | "
        f"Vi phạm: {len(findings)} | {now}*",
    ]

    return "\n".join(lines)


async def generator_node(state: AuditState) -> dict:
    """LangGraph node: format audit findings into a Vietnamese Markdown report."""
    confidence = state.get("confidence", 0.0)
    findings = state.get("audit_findings", [])
    domain = state.get("contract_domain", "")
    error = state.get("error")

    negations = state.get("negations_found", [])
    negations_str = (
        "\n".join(f"- `{n}`" for n in negations)
        if negations
        else "Không phát hiện điều khoản phủ định hay ngoại lệ pháp lý."
    )

    if confidence >= 0.3 and findings and not error:
        try:
            response = await _cerebras.chat.completions.create(
                model=_MODEL,
                messages=[{
                    "role": "user",
                    "content": GENERATOR_SYSTEM_PROMPT.format(
                        domain=domain,
                        negations=negations_str,
                        findings_json=json.dumps(findings, ensure_ascii=False, indent=2),
                    ),
                }],
            )
            report = response.choices[0].message.content
            logger.info("generator_agent: LLM report generated (%d chars)", len(report))
            return {"final_report": report}
        except Exception as exc:
            logger.error("generator_agent: LLM call failed: %s", exc)
            # fall through to template

    logger.warning(
        "generator_agent: using template formatter (confidence=%.2f)", confidence
    )
    report = _template_report(state)
    logger.info("generator_agent: report generated (%d chars)", len(report))
    return {"final_report": report}
