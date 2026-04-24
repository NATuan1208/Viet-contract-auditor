"""Sidebar: brand block + storage status + footer."""
from __future__ import annotations

import streamlit as st
from core.storage_profile import get_profile, storage_status_labels


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            '<div style="margin-bottom:20px">'
            '<div style="background:#1E6B4A;color:white;font-size:10px;font-weight:700;'
            'padding:2px 8px;border-radius:4px;width:fit-content;margin-bottom:8px;'
            'letter-spacing:.5px">v1.0 DEMO</div>'
            '<div style="font-size:17px;font-weight:700;line-height:1.3;color:#1A1A1A">'
            'Viet-Contract Auditor</div>'
            '<div style="font-size:11px;color:#6B7280;margin-top:3px">'
            'LightRAG &nbsp;·&nbsp; LangGraph &nbsp;·&nbsp; GPT-4o-mini</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        st.divider()
        st.markdown(
            '<div style="font-size:11px;font-weight:700;text-transform:uppercase;'
            'letter-spacing:.6px;color:#6B7280;margin-bottom:8px">Trạng thái hệ thống</div>',
            unsafe_allow_html=True,
        )

        for name, online in storage_status_labels():
            dot_cls  = "dot-on" if online else "dot-off"
            status   = "online" if online else "offline"
            st.markdown(
                f'<div style="display:flex;align-items:center;justify-content:space-between;'
                f'font-size:12px;padding:4px 0">'
                f'<span style="color:#374151">{name}</span>'
                f'<span style="display:flex;align-items:center;gap:5px;color:#6B7280">'
                f'<span class="status-dot {dot_cls}"></span>{status}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        profile = get_profile()
        profile_badge_color = "#D97706" if profile == "demo" else "#3B82F6"
        st.markdown(
            f'<div style="margin-top:10px;display:flex;align-items:center;gap:6px">'
            f'<span style="font-size:10px;color:#9CA3AF">Chế độ</span>'
            f'<span style="font-size:10px;font-weight:700;background:{profile_badge_color}22;'
            f'color:{profile_badge_color};padding:1px 7px;border-radius:10px">'
            f'{profile.upper()}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.divider()
        st.caption("CS431 · UIT HK2 2025–2026")
