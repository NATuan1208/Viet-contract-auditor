"""Results area: 3-tab view (findings / full report / logs)."""
from __future__ import annotations

import streamlit as st
from components.finding_cards import show_finding_cards


def render_report_section() -> None:
    findings       = st.session_state.findings
    report         = st.session_state.report
    logs           = st.session_state.logs
    violation_count = st.session_state.violation_count

    tab_v, tab_r, tab_l = st.tabs([
        f"Vi phạm ({violation_count})",
        "Báo cáo đầy đủ",
        "Log pipeline",
    ])

    with tab_v:
        if violation_count:
            st.markdown(
                f'<div style="font-size:12px;color:#6B7280;margin-bottom:10px">'
                f'Phát hiện <strong style="color:#E24B4A">{violation_count}</strong> vi phạm tiềm năng</div>',
                unsafe_allow_html=True,
            )
        show_finding_cards(findings)

    with tab_r:
        st.markdown(report)
        st.download_button(
            label="Tải báo cáo (.md)",
            data=report,
            file_name="audit_report.md",
            mime="text/markdown",
            use_container_width=True,
        )

    with tab_l:
        last_200 = logs[-200:]
        st.caption(f"Hiển thị {len(last_200)} / {len(logs)} dòng log cuối")
        st.code("\n".join(last_200), language=None)
