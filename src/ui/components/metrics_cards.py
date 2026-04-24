"""Metric row: domain · confidence · violation count."""
from __future__ import annotations

import streamlit as st


def render_metrics(domain: str, confidence: float | None, violation_count: int) -> None:
    conf_pct = int(confidence * 100) if confidence is not None else 0
    conf_str = f"{confidence:.2f}" if confidence is not None else "N/A"
    if conf_pct >= 70:
        conf_color = "#1E6B4A"
    elif conf_pct >= 50:
        conf_color = "#D97706"
    else:
        conf_color = "#E24B4A"
    v_color = "#E24B4A" if violation_count > 0 else "#1E6B4A"

    conf_bar = (
        f'<div style="height:5px;background:#E5E7EB;border-radius:3px;margin-top:8px">'
        f'<div style="height:5px;width:{conf_pct}%;background:{conf_color};'
        f'border-radius:3px;transition:width .4s ease"></div></div>'
    )

    boxes = [
        (
            "Lĩnh vực hợp đồng",
            f'<div class="metric-val-lg" style="font-size:16px;color:#1A1A1A;margin-top:6px">'
            f'{domain}</div>',
        ),
        (
            "Điểm tin cậy",
            f'<div class="metric-val-lg" style="color:{conf_color}">{conf_str}</div>'
            f'{conf_bar}',
        ),
        (
            "Vi phạm phát hiện",
            f'<div class="metric-val-lg" style="color:{v_color}">{violation_count}</div>',
        ),
    ]

    for col, (label, value_html) in zip(st.columns(3), boxes):
        with col:
            st.markdown(
                f'<div class="metric-box">'
                f'<div class="metric-label-sm">{label}</div>'
                f'{value_html}'
                f'</div>',
                unsafe_allow_html=True,
            )
