"""File upload widget with styled file chip and start button."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

_EXT_COLORS: dict[str, str] = {
    "PDF":  "#E24B4A",
    "DOCX": "#3B82F6",
    "TXT":  "#6B7280",
}


def render_upload() -> tuple[object | None, bool]:
    """Render upload zone. Returns (uploaded_file_or_None, start_button_clicked)."""
    st.markdown(
        '<div style="font-size:18px;font-weight:700;color:#1A1A1A;margin-bottom:4px">'
        'Kiểm định hợp đồng</div>'
        '<div style="font-size:13px;color:#6B7280;margin-bottom:12px">'
        'Demo pipeline đa tác tử cho kiểm toán hợp đồng pháp lý tiếng Việt</div>',
        unsafe_allow_html=True,
    )
    st.caption("Hỗ trợ: PDF · DOCX · TXT · Tối đa 50MB")

    uploaded = st.file_uploader(
        label="",
        type=["pdf", "docx", "txt"],
        label_visibility="collapsed",
    )

    if uploaded:
        ext = Path(uploaded.name).suffix.upper().lstrip(".")
        color = _EXT_COLORS.get(ext, "#6B7280")
        size_kb = uploaded.size / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px;padding:10px 14px;'
            f'background:#F9FAFB;border:1px solid rgba(0,0,0,.07);border-radius:10px;'
            f'margin-bottom:10px">'
            f'<div style="background:{color};color:#fff;font-size:10px;font-weight:800;'
            f'width:38px;height:38px;border-radius:8px;display:flex;align-items:center;'
            f'justify-content:center;flex-shrink:0;letter-spacing:.3px">{ext}</div>'
            f'<div style="min-width:0">'
            f'<div style="font-size:13px;font-weight:600;color:#1A1A1A;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis">{uploaded.name}</div>'
            f'<div style="font-size:11px;color:#6B7280;margin-top:1px">{size_str}</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    clicked = st.button(
        "Bắt đầu kiểm định",
        type="primary",
        use_container_width=True,
    )
    return uploaded, clicked
