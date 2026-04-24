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

# Viet Contract Auditor

Hệ thống kiểm toán hợp đồng tiếng Việt theo pháp luật hiện hành, dùng LightRAG + LangGraph multi-agent để phát hiện vi phạm, trích dẫn căn cứ luật và đề xuất sửa điều khoản.

## 1. Trạng thái hiện tại

- Phase 1-2: ETL + semantic chunking dữ liệu luật (done)
- Phase 3: migrate offline LightRAG artifacts sang Neo4j + Qdrant + PostgreSQL (done)
- Phase 4: LangGraph pipeline Router -> Retrieval -> Audit -> Generator (done)
- Phase 4B: bổ sung Preprocessor + Critic loop tự hiệu chỉnh, anti-nitpicking guardrails (done)
- Phase 5: Streamlit UI — dual-mode (production + HF Spaces demo) (done)
- Phase 6: evaluator nâng cao LLM-as-judge (pending)

## 2. Kiến trúc hệ thống

### 2.1 Storage production (Phase 3)

- PostgreSQL (pgvector, port 55432): KV store + doc status
- Neo4j 5.26 (ports 7474/7687): knowledge graph entities/relations
- Qdrant 1.13.2 (port 6333): vector search cho entities/relations/chunks

Migration dữ liệu offline: `src/init_storage.py` (không cần gọi LLM API).

### 2.2 Audit pipeline runtime (Phase 4B)

```text
START
  -> Router Agent (classify domain)
  -> Preprocessor Agent (tokenize + legal alias + xref extraction)
  -> Retrieval Agent (LightRAG hybrid query from Neo4j/Qdrant/PostgreSQL)
  -> Audit Agent (detect violations as JSON findings)
  -> Critic Agent (negation scan + LLM critic + confidence routing)
  -> [finalize] Generator Agent (final Markdown report)
     or [retry] Retrieval Agent
END
```

Hiện tại Generator chạy theo hướng LLM-first, có fallback template khi LLM không sẵn sàng.

## 3. Cấu trúc repo chính

```text
src/
  main.py                     # ETL orchestrator (Phase 1-2)
  data_ingestion.py           # Load law corpus (HF + local txt)
  semantic_chunker.py         # Regex chunker theo Điều/Khoản
  init_storage.py             # Offline migration -> Neo4j/Qdrant/PostgreSQL
  run_audit.py                # Entry point chạy audit pipeline
  e2e_eval.py                 # End-to-end evaluation vs groundtruth
  agents/
    router_agent.py
    preprocessor_agent.py
    retrieval_agent.py
    audit_agent.py
    critic_agent.py
    generator_agent.py
    orchestrator.py
  core/
    state.py
    prompts.py
    lightrag_client.py
    vn_preprocessor.py
    legal_patterns.py

data/
  raw/
  processed/

lightrag_index/               # Prebuilt artifacts để migrate storage
result-example/
  HDLD/
    HDLD_ThucHanh_01.docx
    groundtruth_hdld_01_test copy.json
  HDNQTM01/
    HDNQTH_ThucHanh_01.docx
    groundtruth_hdnqtm_01.json

reports/
  final_outputs/              # final report markdown theo case
  metrics/                    # state/eval metrics theo case
```

## 4. Cách chạy nhanh

Lưu ý: dùng `uv run` cho toàn bộ lệnh Python.

### 4.1 ETL luật (Phase 1-2)

```bash
uv run python src/main.py
```

### 4.2 Khởi động storage services

```bash
docker compose up -d
```

### 4.3 Migrate artifacts vào storage production

```bash
uv run python src/init_storage.py
```

### 4.4 Kiểm tra health của storage

```bash
uv run python src/check_storage.py
```

### 4.5 Chạy audit 1 hợp đồng

```bash
uv run python src/run_audit.py result-example/HDLD/HDLD_ThucHanh_01.docx --output reports/final_outputs/hdld_report.md
```

### 4.6 Chạy E2E eval cho 1 groundtruth

```bash
uv run python src/e2e_eval.py --groundtruth result-example/HDLD/groundtruth_hdld_01_test copy.json
```

### 4.7 Chạy batch E2E cho toàn bộ groundtruth trong result-example

PowerShell:

```powershell
$files = Get-ChildItem -Path "result-example" -Recurse -Filter "groundtruth*.json"
foreach ($f in $files) {
  uv run python src/e2e_eval.py --groundtruth "$($f.FullName)"
}
```

## 5. Biến môi trường

Thiết lập trong file `.env` (không commit secrets):

- Storage: `POSTGRES_*`, `NEO4J_*`, `QDRANT_*`
- Workspace: `WORKSPACE`, `PG_WORKSPACE`, `NEO4J_WORKSPACE`, `QDRANT_WORKSPACE`
- LLM: `CEREBRAS_API_KEY` (hoặc API key tương thích endpoint OpenAI-style)

## 6. Output sau khi chạy

- Báo cáo cuối: `reports/final_outputs/*_report.md`
- Metrics heuristic: `reports/metrics/*_eval.md`
- Raw state pipeline: `reports/metrics/*_state.json`

## 7. Quy tắc dữ liệu quan trọng

- Data segregation: corpus luật để index KG và bộ groundtruth/eval phải tách biệt.
- Chunking luật: regex-based theo `Điều \d+\.`; không dùng character split.
- Agent runtime phải truy cập storage production, không đọc trực tiếp JSON artifacts trong `lightrag_index`.

## 8. Chạy Streamlit UI (Phase 5)

### Local — production profile (full Docker stack)

```bash
docker compose up -d
uv run streamlit run src/ui/streamlit_app.py
# Mở http://localhost:8501
```

### Local — demo profile (không cần Docker)

```bash
STORAGE_PROFILE=demo uv run streamlit run src/ui/streamlit_app.py
```

### Hugging Face Spaces (Docker)

```bash
docker build -t viet-auditor-demo .
docker run -p 7860:7860 -e OPENAI_API_KEY=$OPENAI_API_KEY viet-auditor-demo
# Mở http://localhost:7860
```

## 9. Roadmap gần nhất

1. Bổ sung evaluator LLM-as-judge ở Phase 6.
2. Mở rộng bộ groundtruth đa domain để giảm overfitting theo từng case.
