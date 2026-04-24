"""Viet-Contract Auditor — Phase 5 Streamlit UI (dual-mode)."""
from __future__ import annotations
import io, os, queue, re, socket, subprocess, sys, tempfile, threading, time
from dataclasses import dataclass
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path: sys.path.insert(0, str(_SRC))

import streamlit as st
from dotenv import load_dotenv
from pypdf import PdfReader
from components.progress_panel import STAGES, build_stage_html, infer_stage
from components.sidebar import render_sidebar
from components.metrics_cards import render_metrics
from components.report_tabs import render_report_section
from components.upload_panel import render_upload
from core.storage_profile import demo_index_ready, get_profile
from theme import CUSTOM_CSS

load_dotenv()
st.set_page_config(page_title="Viet-Contract Auditor", page_icon="📄", layout="wide")
TIMEOUT_SECONDS = 300


@dataclass
class PipelineResult:
    report_md: str
    returncode: int
    logs: list[str]

class PipelineTimeoutError(RuntimeError): pass


def init_state() -> None:
    for k, v in {"report": None, "findings": [], "domain": "N/A", "confidence": None,
                 "violation_count": 0, "logs": [], "returncode": None}.items():
        if k not in st.session_state: st.session_state[k] = v


def _port_open(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(1.5)
    try: s.connect(("127.0.0.1", port)); return True
    except OSError: return False
    finally: s.close()

def storage_offline() -> list[str]:
    return [n for n, p in [("Neo4j", 7687), ("Qdrant", 6333), ("PostgreSQL", 55432)] if not _port_open(p)]

def has_llm_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("CEREBRAS_API_KEY"))


def extract_pdf_text(file_bytes: bytes) -> str:
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(file_bytes)).pages).strip()


def prepare_contract_file(file_bytes: bytes, filename: str) -> tuple[str, list[str], str | None]:
    suffix = Path(filename).suffix.lower()
    note = None
    if suffix == ".pdf":
        note = "PDF được trích xuất thành .txt trước khi đưa vào pipeline."
        text = extract_pdf_text(file_bytes)
        if not text: raise ValueError("Không trích xuất được nội dung từ PDF.")
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(text.encode("utf-8")); return tmp.name, [tmp.name], note
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes); return tmp.name, [tmp.name], note


def run_subprocess(contract_path: str, report_path: str) -> PipelineResult:
    cmd = ["uv", "run", "python", "src/run_audit.py", contract_path, "--output", report_path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    q: queue.Queue = queue.Queue()

    def _read() -> None:
        assert proc.stdout
        for ln in proc.stdout: q.put(("log", ln.rstrip("\n")))
        proc.wait(); q.put(("done", proc.returncode))

    threading.Thread(target=_read, daemon=True).start()
    logs: list[str] = []
    stage_idx, done_code, t0 = 0, None, time.time()
    status = st.status("Đang chạy pipeline kiểm định...", expanded=True)
    progress_bar, placeholder = st.progress(0.0), st.empty()

    while done_code is None:
        elapsed = time.time() - t0
        if elapsed > TIMEOUT_SECONDS:
            proc.kill(); status.update(label="Pipeline bị timeout", state="error")
            raise PipelineTimeoutError("timeout")
        try:
            kind, payload = q.get(timeout=0.2)
            if kind == "log":
                logs.append(str(payload)); stage_idx = infer_stage(str(payload), stage_idx)
            else:
                done_code = int(payload)
        except queue.Empty: pass
        pct = min(int(stage_idx / len(STAGES) * 100), 99)
        progress_bar.progress(pct / 100)
        placeholder.markdown(build_stage_html(stage_idx, time.time() - t0), unsafe_allow_html=True)

    ok = done_code == 0
    progress_bar.progress(1.0 if ok else min(stage_idx / len(STAGES), 0.99))
    status.update(label="Pipeline hoàn tất ✓" if ok else "Pipeline gặp lỗi",
                  state="complete" if ok else "error")
    report_md = Path(report_path).read_text(encoding="utf-8", errors="replace") if os.path.exists(report_path) else ""
    return PipelineResult(report_md=report_md, returncode=done_code, logs=logs)


def run_pipeline(file_bytes: bytes, filename: str) -> PipelineResult:
    contract_path, cleanup, note = prepare_contract_file(file_bytes, filename)
    with tempfile.NamedTemporaryFile(suffix=".report.md", delete=False) as rpt:
        report_path = rpt.name
    cleanup.append(report_path)
    if note: st.info(note)
    try:
        return run_subprocess(contract_path, report_path)
    finally:
        for p in cleanup:
            try: os.unlink(p)
            except OSError: pass


_FIND_RE = re.compile(r"###\s*Vi phạm\s*\d+[^\n]*\n(.*?)(?=\n###\s*Vi phạm\s*\d+|\Z)", re.DOTALL)

def parse_findings(report_md: str) -> list[dict]:
    def _g(p: str, b: str) -> str:
        m = re.search(p, b); return m.group(1).strip() if m else ""
    return [{"clause":        _g(r"\*\*Điều khoản:\*\*\s*(.+)", b),
             "violation":     _g(r"\*\*Vi phạm:\*\*\s*(.+)", b),
             "reference_law": _g(r"\*\*Căn cứ pháp lý:\*\*\s*(.+)", b),
             "suggested_fix": _g(r"\*\*Khuyến nghị sửa đổi:\*\*\s*(.+)", b)}
            for b in _FIND_RE.findall(report_md)]

def parse_meta(report_md: str) -> tuple[str, float | None]:
    d = re.search(r"Lĩnh vực:\s*\*\*(.+?)\*\*", report_md)
    c = re.search(r"Điểm tin cậy:\s*([0-9]+(?:\.[0-9]+)?)", report_md)
    return (d.group(1).strip() if d else "N/A"), (float(c.group(1)) if c else None)


def main() -> None:
    init_state()
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    render_sidebar()
    uploaded, start_clicked = render_upload()

    if start_clicked:
        if not uploaded:
            st.error("Vui lòng tải lên file hợp đồng trước khi kiểm định."); st.stop()
        if not has_llm_key():
            st.error("Chưa cấu hình API key. Thêm OPENAI_API_KEY vào file .env"); st.stop()
        if get_profile() == "production":
            offline = storage_offline()
            if offline:
                st.error(f"Storage chưa sẵn sàng: {', '.join(offline)}. Chạy: docker compose up -d")
                st.stop()
        elif not demo_index_ready():
            st.error("lightrag_index/ chưa đầy đủ. Demo mode cần pre-built index artifacts."); st.stop()
        try:
            result = run_pipeline(uploaded.getvalue(), uploaded.name)
        except PipelineTimeoutError:
            st.warning("Pipeline timeout. Thử với hợp đồng ngắn hơn."); st.stop()
        except ValueError as exc:
            st.error(str(exc)); st.stop()
        except Exception as exc:
            st.error("Lỗi không mong đợi."); st.exception(exc); st.stop()
        ss = st.session_state
        ss.report, ss.logs, ss.returncode = result.report_md, result.logs, result.returncode
        ss.findings = parse_findings(result.report_md)
        ss.domain, ss.confidence = parse_meta(result.report_md)
        ss.violation_count = len(ss.findings)
        if result.returncode != 0 and not result.report_md.strip():
            st.error("Pipeline lỗi — xem tab Log pipeline bên dưới.")

    if st.session_state.report:
        st.markdown("---")
        render_metrics(st.session_state.domain, st.session_state.confidence, st.session_state.violation_count)
        st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
        render_report_section()


if __name__ == "__main__":
    main()
