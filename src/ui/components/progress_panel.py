"""Pipeline progress display — stage tracker and HTML builder."""
from __future__ import annotations

import streamlit as st

STAGES = [
    "Phân loại hợp đồng",
    "Tiền xử lý văn bản",
    "Truy xuất điều luật",
    "Xác thực ngữ cảnh",
    "Kiểm định điều khoản",
    "Phản biện và xác minh",
    "Tạo báo cáo",
]


def infer_stage(line: str, current: int) -> int:
    """Advance stage index from a single log line (never go backwards)."""
    low = line.lower()
    if any(s in low for s in ["router", "loaded:", "run_audit: starting"]):
        return max(current, 0)
    if "preprocessor" in low:
        return max(current, 1)
    if "context_validator" in low:
        return max(current, 3)
    if "retrieval" in low:
        return max(current, 2)
    if "audit_agent" in low or "audit:" in low:
        return max(current, 4)
    if "critic" in low:
        return max(current, 5)
    if "generator" in low or "report written" in low:
        return max(current, 6)
    return current


def build_stage_html(stage_idx: int, elapsed: float) -> str:
    """Return HTML for the live-updating stage list (used by run_subprocess loop)."""
    rows: list[str] = []
    for i, label in enumerate(STAGES):
        if i < stage_idx:
            dot, style = "dot-on", "color:#1E6B4A;font-size:13px"
        elif i == stage_idx:
            dot, style = "dot-amber", "color:#78350F;font-size:13px;font-weight:600"
        else:
            dot, style = "dot-muted", "color:#9CA3AF;font-size:13px"
        rows.append(
            f'<div style="display:flex;align-items:center;gap:10px;padding:5px 0">'
            f'<span class="status-dot {dot}"></span>'
            f'<span style="{style}">{label}</span>'
            f'</div>'
        )
    pct = min(int(stage_idx / len(STAGES) * 100), 99)
    rows.append(
        f'<div style="margin-top:8px;font-size:11px;color:#9CA3AF">'
        f'Thời gian: {elapsed:.0f}s &nbsp;·&nbsp; {pct}% hoàn thành'
        f'</div>'
    )
    return "".join(rows)


def render_progress(logs: list[str], stage_idx: int, elapsed: float) -> None:
    """Static render of pipeline progress (used for display outside the live loop)."""
    st.markdown(
        f'<div style="padding:4px 0">{build_stage_html(stage_idx, elapsed)}</div>',
        unsafe_allow_html=True,
    )
    st.progress(min(stage_idx / len(STAGES), 1.0))
