---
title: Viet Contract Auditor
emoji: 📄
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Vietnamese contract audit with LightRAG + LangGraph
---

# Viet-Contract Auditor

Hệ thống kiểm toán hợp đồng tiếng Việt tự động, sử dụng **LightRAG** (graph-based RAG) và **LangGraph** (multi-agent pipeline 7 node) để phát hiện vi phạm pháp lý, trích dẫn căn cứ luật và đề xuất sửa đổi điều khoản. Đầu vào là file hợp đồng PDF/DOCX/TXT, đầu ra là báo cáo kiểm định Markdown đầy đủ.

---

## Kiến trúc hệ thống

![Kiến trúc multi-agent pipeline](Arch-diagramjpg.jpg)

Pipeline gồm 7 node chạy tuần tự trên `AuditState` (Python TypedDict):

| Node | Vai trò | LLM |
|---|---|---|
| **Router** | Phân loại domain hợp đồng, tách điều khoản | GPT-4o-mini (fallback keyword) |
| **Preprocessor** | Tokenize, chuẩn hoá alias pháp lý, phát hiện cross-reference | underthesea (không LLM) |
| **Retrieval** | Truy vấn hybrid Neo4j + Qdrant + PostgreSQL qua LightRAG | Không LLM |
| **Context Validator** | Kiểm tra chất lượng context theo coverage/relevance/xref | Heuristic (không LLM) |
| **Audit** | Phát hiện vi phạm điều khoản, sinh JSON findings | GPT-4o-mini |
| **Critic** | Scan negation regex + xác minh LLM, anti-hallucination, chặn nitpicking | GPT-4o-mini (on-demand) |
| **Generator** | Tổng hợp báo cáo Markdown cuối + fallback template | GPT-4o-mini |

Routing logic bao gồm retry loop (context validator → retrieval, tối đa 2 lần) và critic loop (critic → retrieval, tối đa 2 lần).

---

## Trạng thái các Phase

| Phase | Mô tả | Trạng thái |
|---|---|---|
| 1–2 | ETL: ingest luật → semantic chunks (regex Điều-level) | ✅ Done |
| 3 | LightRAG indexing + migrate → Neo4j + Qdrant + PostgreSQL | ✅ Done |
| 4 | LangGraph 7-node pipeline: Router → Generator | ✅ Done |
| 4B | Preprocessor + Context Validator + Critic, self-correction loop | ✅ Done |
| 5 | Streamlit UI — dual-mode (production + HF Spaces demo) | ✅ Done |
| 6 | Evaluator LLM-as-judge (precision/recall/F1 per domain) | ⬜ Pending |

**Kết quả eval Phase 4B (fine-tuned):**

| Hợp đồng | Domain | F1 |
|---|---|---|
| HDLD_01 | Lao động | 0.750 |
| HDNQTM_01 | Thương mại | 0.857 |
| HDBDS_01 (blind) | Dân sự | 0.800 |

---

## Cấu trúc repo

```
src/
  run_audit.py              # Entry point CLI — xem mục "Chạy audit"
  main.py                   # ETL orchestrator (Phase 1–2)
  init_storage.py           # Migrate LightRAG artifacts → Neo4j/Qdrant/PostgreSQL
  agents/
    router_agent.py
    preprocessor_agent.py
    retrieval_agent.py
    context_validator_agent.py
    audit_agent.py
    critic_agent.py
    generator_agent.py
    orchestrator.py         # StateGraph wiring (LangGraph)
  core/
    state.py                # AuditState TypedDict
    llm_config.py           # LLM provider: OpenAI > Cerebras
    storage_profile.py      # Dual-mode: production vs demo
    vn_preprocessor.py      # underthesea + LEGAL_ALIAS_MAP
    lightrag_client.py      # LightRAG hybrid query client
  ui/
    streamlit_app.py        # Main UI (185 lines)
    theme.py                # CUSTOM_CSS
    components/             # sidebar, upload, progress, metrics, findings, tabs

lightrag_index/             # Pre-built LightRAG artifacts (graphml + vdb JSON)
result-example/             # Sample contracts + groundtruth JSON
reports/final_outputs/      # Generated audit reports
docker-compose.yml          # Neo4j 5.26 + Qdrant v1.13.2 + PostgreSQL 17 (pgvector)
Dockerfile                  # HF Spaces single-container deployment
.env.example                # Template biến môi trường
```

---

## Yêu cầu

- Python 3.11
- [uv](https://docs.astral.sh/uv/) (package manager — không dùng pip)
- Docker (cho production storage stack)
- `OPENAI_API_KEY` (GPT-4o-mini) hoặc `CEREBRAS_API_KEY` (fallback)

---

## Thiết lập biến môi trường

Tạo file `.env` tại root project (tham khảo `.env.example`):

```bash
OPENAI_API_KEY=sk-...          # Bắt buộc (hoặc dùng CEREBRAS_API_KEY)
STORAGE_PROFILE=production     # production | demo
```

---

## Cách chạy

### 1. Cài đặt dependencies

```bash
uv sync
```

### 2. Khởi động storage (production profile)

```bash
docker compose up -d
# Chờ services healthy (~30s), kiểm tra:
docker compose ps
```

### 3. Migrate LightRAG artifacts vào storage

Chỉ cần chạy 1 lần sau khi `docker compose up -d`:

```bash
uv run python src/init_storage.py
```

### 4. Kiểm định 1 hợp đồng (CLI)

```bash
uv run python src/run_audit.py result-example/HDLD/HDLD_ThucHanh_01.docx \
  --output reports/final_outputs/hdld_report.md
```

Đầu ra: file Markdown với các vi phạm, căn cứ pháp lý và khuyến nghị sửa đổi.

### 5. Chạy Streamlit UI

**Production profile** (cần Docker stack đang chạy):

```bash
uv run streamlit run src/ui/streamlit_app.py
# Truy cập: http://localhost:8501
```

**Demo profile** (không cần Docker — dùng LightRAG JSON artifacts):

```bash
STORAGE_PROFILE=demo uv run streamlit run src/ui/streamlit_app.py
```

### 6. Chạy evaluation (Phase 4B)

```bash
uv run python src/e2e_eval.py \
  --groundtruth "result-example/HDLD/groundtruth_hdld_01_test copy.json"
```

---

## Triển khai lên Hugging Face Spaces

```bash
# Build và test local trước:
docker build -t viet-auditor-demo .
docker run -p 7860:7860 -e OPENAI_API_KEY=$OPENAI_API_KEY viet-auditor-demo
# Truy cập: http://localhost:7860

# Push lên HF Spaces:
# 1. Tạo Space mới với SDK=Docker trên huggingface.co
# 2. git push remote hf main
# 3. Thêm OPENAI_API_KEY vào Space secrets
```

> **Lưu ý:** `lightrag_index/` phải được commit vào repo HF Spaces để demo profile hoạt động.
> Tổng dung lượng index ~50MB — nằm trong giới hạn HF Spaces free tier.

---

## Corpus luật (dữ liệu index)

| Nguồn | Luật | Chunks |
|---|---|---|
| HuggingFace `NghiemAbe/Legal-Corpus-Zalo` | BLDS 2015 (91/2015/QH13) | 118 |
| HuggingFace `NghiemAbe/Legal-Corpus-Zalo` | Luật DN 2020 (59/2020/QH14) | 115 |
| HuggingFace `NghiemAbe/Legal-Corpus-Zalo` | Luật TTTM 2010 (54/2010/QH12) | 23 |
| Local `.txt` | Luật Thương mại 2005 | — |
| Local `.txt` | Bộ luật Lao động 2019 | — |

**Tổng:** 256 chunks, ~240K tokens. Được index vào Neo4j (knowledge graph) + Qdrant (vector) + PostgreSQL (KV store).

---

## Quy tắc dữ liệu quan trọng

- **Data segregation:** Corpus luật chỉ dùng để index KG. Bộ groundtruth/eval phải tách biệt hoàn toàn.
- **Chunking:** Regex-based theo `Điều \d+\.` — không dùng character-split.
- **Agent runtime:** Phải đọc từ production storage (Neo4j/Qdrant/PostgreSQL) — không đọc trực tiếp JSON trong `lightrag_index/`.

---

*CS431 · University of Information Technology (UIT) · HK2 2025–2026*
