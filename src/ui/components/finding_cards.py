"""Violation finding cards with keyword-based severity classification."""
from __future__ import annotations

import streamlit as st

_HIGH_KW = frozenset({"nghiêm trọng", "vi phạm pháp luật", "trái", "vô hiệu"})
_MED_KW  = frozenset({"thiếu", "chưa", "không đầy đủ"})


def _severity(finding: dict) -> tuple[str, str]:
    text = " ".join([
        finding.get("violation", ""),
        finding.get("clause", ""),
    ]).lower()
    if any(kw in text for kw in _HIGH_KW):
        return "Nghiêm trọng", "badge-high"
    if any(kw in text for kw in _MED_KW):
        return "Trung bình", "badge-med"
    return "Nhẹ", "badge-low"


def render_finding_card(finding: dict, idx: int) -> None:
    sev_label, sev_class = _severity(finding)
    clause = finding.get("clause") or "—"
    viol   = finding.get("violation") or "—"
    law    = finding.get("reference_law") or "—"
    fix    = finding.get("suggested_fix", "")

    fix_html = (
        f'<div class="field-label" style="margin-top:4px">Đề xuất sửa đổi</div>'
        f'<div class="fix-box">{fix}</div>'
        if fix else ""
    )

    st.markdown(
        f'<div class="finding-card">'
        f'  <div class="finding-header">'
        f'    <span style="font-size:11px;font-weight:600;color:#6B7280">Vi phạm {idx+1:02d}</span>'
        f'    <span class="{sev_class}">{sev_label}</span>'
        f'  </div>'
        f'  <div class="finding-body">'
        f'    <div class="field-label">Điều khoản hợp đồng</div>'
        f'    <div class="field-value">{clause}</div>'
        f'    <div class="field-label">Lý do vi phạm</div>'
        f'    <div class="field-value">{viol}</div>'
        f'    <div class="field-label">Căn cứ pháp lý</div>'
        f'    <div style="margin-bottom:12px"><span class="law-chip">{law}</span></div>'
        f'    {fix_html}'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def show_finding_cards(findings: list[dict]) -> None:
    if not findings:
        st.info("Không phát hiện vi phạm có cấu trúc. Xem báo cáo đầy đủ ở tab kế bên.")
        return
    for i, finding in enumerate(findings):
        render_finding_card(finding, i)
