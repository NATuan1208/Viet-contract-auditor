# Luồng hoạt động MAS LangGraph — Viet-Contract Auditor

> **Mục đích tài liệu:** Mô tả chi tiết cách 7 agent node cộng tác trong pipeline LangGraph để chuyển đổi một hợp đồng tiếng Việt thành báo cáo kiểm toán pháp lý. Tài liệu hướng đến người đọc đã quen với Python nhưng chưa quen với kiến trúc multi-agent.

---

## 1. Tổng quan kiến trúc

Hệ thống sử dụng **LangGraph StateGraph** — một đồ thị có hướng mà mỗi node là một agent xử lý một giai đoạn của pipeline. Tất cả agents chia sẻ một đối tượng trạng thái chung (`AuditState`) thay vì truyền tin nhắn trực tiếp cho nhau.

```
                    ┌──────────────────────────────────────────────┐
                    │              AuditState (shared)              │
                    │  contract_text, chunks, clause_risk_scores,   │
                    │  legal_context, audit_findings, confidence ... │
                    └──────────────────────────────────────────────┘
                                          │
           ┌──────────────────────────────▼──────────────────────────────┐
START ──► ROUTER ──► PREPROCESSOR ──► RETRIEVAL ──► CONTEXT_VALIDATOR    │
                │                          ▲               │              │
                │ (lỗi/rỗng)              │ (retry)        │ (to_audit)   │
                │                          └───────────────┤              │
                ▼                                          ▼              │
           GENERATOR ◄────────────────── CRITIC ◄──── AUDIT              │
                │          (finalize)       │                             │
                │                          │ (to_retrieval)              │
                │                          └─────────────────────────────┘
                ▼
               END
```

### Nguyên tắc thiết kế
- **State-based communication**: Không có agent nào gọi trực tiếp agent khác. Mỗi node nhận vào `AuditState`, xử lý, rồi trả về dict các field cần cập nhật.
- **Conditional routing**: Các cạnh có điều kiện (`conditional_edges`) quyết định node tiếp theo dựa trên giá trị trong state (ví dụ: `context_quality`, `error_type`, `retry_count`).
- **Graceful degradation**: Mỗi node xử lý lỗi bằng cách trả về giá trị mặc định an toàn thay vì crash toàn pipeline.

---

## 2. Sơ đồ luồng chi tiết

```
START
  │
  ▼
┌────────────────────────────────────────────────┐
│ 1. ROUTER — Phân loại & tách điều khoản        │
│  ● Đọc: contract_text                           │
│  ● Ghi: contract_domain, chunks                 │
│  ─────────────────────────────────────────────  │
│  Bước 1: Keyword classifier (không cần LLM)     │
│  Bước 2: Nếu không rõ → gọi gpt-4o-mini        │
│  Bước 3: Tách clause bằng regex "Điều X."       │
│          Nếu < 2 clause → fallback LLM split    │
└──────────────┬─────────────────────────────────┘
               │
       ┌───────┴───────┐
   [ok]                [error/rỗng]
       │                    │
       ▼                    ▼ (bỏ qua toàn bộ)
┌─────────────────┐   ┌───────────┐
│ 2. PREPROCESSOR │   │ GENERATOR │──► END
│ (không gọi LLM) │   └───────────┘
│  ● Đọc: chunks  │
│  ● Ghi:         │
│    segmented_   │
│    chunks,      │
│    cross_refs,  │
│    clause_risk_ │
│    scores       │
└────────┬────────┘
         │
  ┌──────┴──────┐
[all_low]    [needs_retrieval]
  │                │
  ▼                ▼
GENERATOR   ┌─────────────────────────────────────────┐
            │ 3. RETRIEVAL — Tìm ngữ cảnh pháp lý     │
            │  ● Đọc: segmented_chunks, contract_     │
            │         domain, cross_refs              │
            │  ● Ghi: legal_context,                  │
            │         retrieved_clause_indices         │
            │  ─────────────────────────────────────  │
            │  Với mỗi clause (medium/high risk):      │
            │    query = "[Tên luật]: [clause text]"  │
            │    → LightRAG hybrid(Neo4j + Qdrant)    │
            │  Thêm xref queries cho "Điều X" refs    │
            │  Semaphore(1), backoff 2/4/8s, dedup    │
            └─────────────────┬───────────────────────┘
                              │
                              ▼
            ┌─────────────────────────────────────────┐
            │ 4. CONTEXT_VALIDATOR — Kiểm tra chất    │
            │    lượng ngữ cảnh (không gọi LLM)       │
            │  ● Đọc: legal_context, chunks,          │
            │         clause_risk_scores, cross_refs  │
            │  ● Ghi: context_quality,                │
            │         context_quality_score,          │
            │         context_validation_errors       │
            │  ─────────────────────────────────────  │
            │  Score = 0.3×coverage                   │
            │        + 0.5×avg_relevance  (dominant)  │
            │        + 0.2×ref_coverage               │
            │  Nếu avg_relevance < 0.15 → "poor"      │
            └─────────────────┬───────────────────────┘
                              │
              ┌───────────────┴───────────────┐
          [to_audit]                    [to_retrieval]
          (quality=good,               (quality≠good AND
          or retry cap=2)              retry_count < 2)
              │                               │
              │                    ┌──────────┘
              │                    └──► quay lại RETRIEVAL
              ▼
            ┌─────────────────────────────────────────┐
            │ 5. AUDIT — Phát hiện vi phạm             │
            │  ● Đọc: chunks, legal_context,          │
            │         clause_risk_scores              │
            │  ● Ghi: audit_findings, confidence,     │
            │         skipped_clauses                 │
            │  ─────────────────────────────────────  │
            │  Bước 1 (không LLM):                    │
            │    Deterministic rules inject trước     │
            │    (overtime 100%, khiếu nại trắng,...) │
            │  Bước 2 (LLM - gpt-4o-mini):            │
            │    Với clause medium/high risk:          │
            │      → _should_audit_clause() filter    │
            │      → gọi AUDIT_DEEP_SYSTEM_PROMPT     │
            │      → parse JSON array findings        │
            │  confidence = tỷ lệ findings có ref_law │
            └─────────────────┬───────────────────────┘
                              │
                              ▼
            ┌─────────────────────────────────────────┐
            │ 6. CRITIC — Tự kiểm tra kết quả kiểm   │
            │    toán                                  │
            │  ● Đọc: audit_findings, legal_context,  │
            │         confidence, retry_count         │
            │  ● Ghi: negations_found,                │
            │         critic_feedback,                │
            │         audit_findings (có thể cắt),   │
            │         confidence, error_type          │
            │  ─────────────────────────────────────  │
            │  Layer 1 (không LLM - regex):           │
            │    Quét legal_context tìm phủ định:     │
            │    "không được", "cấm", "ngoại trừ"... │
            │  Layer 2 (LLM - gpt-4o-mini):           │
            │    Kích hoạt khi: negations > 0 OR      │
            │    confidence < 0.7 OR context ≠ good   │
            │    → đánh giá findings, cắt hallucination│
            │    → trả về error_type + confidence     │
            └─────────────────┬───────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
    [finalize]           [to_retrieval]        [finalize]
    (error=ok/reasoning  (error=hallucination   (retry≥2)
    + context good)       hoặc low_confidence)
         │                    │                    │
         │            ┌───────┘                    │
         │            └──► quay lại RETRIEVAL      │
         └────────────────────┬────────────────────┘
                              │
                              ▼
            ┌─────────────────────────────────────────┐
            │ 7. GENERATOR — Tạo báo cáo cuối         │
            │  ● Đọc: audit_findings, contract_domain,│
            │         confidence, critic_feedback     │
            │  ● Ghi: final_report                    │
            │  ─────────────────────────────────────  │
            │  Bước 1: gpt-4o-mini tạo báo cáo MD    │
            │  Bước 2: Nếu LLM fail → template tĩnh  │
            └─────────────────┬───────────────────────┘
                              │
                              ▼
                             END
```

---

## 3. AuditState — Hợp đồng dữ liệu giữa các agents

`AuditState` là TypedDict được định nghĩa trong `src/core/state.py`. Mỗi agent chỉ đọc các field nó cần và chỉ ghi các field nó tạo ra.

| Field | Kiểu | Agent tạo ra | Agent đọc |
|---|---|---|---|
| `contract_text` | `str` | (input ban đầu) | Router, Audit, Critic |
| `contract_domain` | `str` | Router | Retrieval, Generator |
| `chunks` | `list[str]` | Router | Tất cả |
| `clause_risk_scores` | `list[RiskLevel]` | Preprocessor | Retrieval, Validator, Audit |
| `clause_risk_reasons` | `list[str]` | Preprocessor | — |
| `segmented_chunks` | `list[str]` | Preprocessor | Retrieval |
| `cross_refs` | `list[dict]` | Preprocessor | Retrieval, Validator |
| `retrieved_clause_indices` | `list[int]` | Retrieval | Validator |
| `legal_context` | `str` | Retrieval | Validator, Audit, Critic |
| `context_quality` | `ContextQualityStatus` | Validator | Orchestrator (routing), Critic |
| `context_quality_score` | `float` | Validator | Critic |
| `context_validation_errors` | `list[str]` | Validator | Critic |
| `context_retry_count` | `int` | Validator | Orchestrator (routing) |
| `audit_findings` | `list[dict]` | Audit | Critic, Generator |
| `skipped_clauses` | `list[dict]` | Audit/Preprocessor | Generator |
| `confidence` | `float` | Audit → Critic | Orchestrator, Generator |
| `negations_found` | `list[str]` | Critic | Generator |
| `critic_feedback` | `CriticFeedback` | Critic | Orchestrator, Generator |
| `error_type` | `ErrorType` | Critic | Orchestrator (routing) |
| `retry_count` | `int` | Critic | Orchestrator (routing) |
| `final_report` | `str` | Generator | (output cuối) |
| `error` | `str \| None` | Bất kỳ | Orchestrator |

---

## 4. Mô tả chi tiết từng agent

### 4.1 Router Agent (`router_agent.py`)

**Mục tiêu:** Xác định loại hợp đồng và tách thành các điều khoản.

**Luồng xử lý:**
```
contract_text
    │
    ├─[keyword match]─► classify_domain_by_keywords()
    │                       Đếm keyword hits theo từng domain
    │                       Yêu cầu: top ≥ 2 hits VÀ ≥ 2× runner-up
    │                       ├─[rõ ràng]──► contract_domain = "Lao động"/"Thương mại"/...
    │                       └─[mơ hồ]───► gọi gpt-4o-mini phân loại
    │
    └─[split clauses]─► split_contract_into_clauses()
                            Regex: r"(?=^Điều\s+\d+[\.\:\-\s])"
                            Nếu < 2 clause → gọi gpt-4o-mini split
```

**Routing sau Router:**
- Nếu `chunks` rỗng hoặc `error` → đi thẳng đến **Generator** (báo cáo lỗi)
- Ngược lại → đi đến **Preprocessor**

---

### 4.2 Preprocessor Agent (`preprocessor_agent.py`)

**Mục tiêu:** Làm giàu thông tin về từng điều khoản mà không tốn token LLM.

**Luồng xử lý:**
```
chunks
    │
    ├─[tokenize]──► underthesea word_tokenize() cho từng clause
    │               → segmented_chunks (từ được ghép gạch dưới: "lao_động")
    │
    ├─[risk score]─► Cho từng clause:
    │               • Keyword "đơn phương", "bồi thường" → HIGH
    │               • Keyword "thông báo", "đại diện" → LOW
    │               • Default → MEDIUM
    │
    └─[xref detect]─► Regex tìm tham chiếu pháp lý:
                      • "Điều X" → type=dieu
                      • "điểm X" → type=diem
                      • "Luật X năm YYYY" → type=luat
                      → cross_refs với clause_index
```

**Routing sau Preprocessor:**
- Tất cả clauses đều `low` risk → đi thẳng đến **Generator** (tiết kiệm token)
- Có ít nhất 1 clause `medium/high` → đi đến **Retrieval**

---

### 4.3 Retrieval Agent (`retrieval_agent.py`)

**Mục tiêu:** Tìm điều luật liên quan từ knowledge base (Neo4j + Qdrant + PostgreSQL).

**Luồng xử lý:**
```
Cho mỗi clause index có risk = medium hoặc high:
    query = "[Tên luật domain]: [nội dung clause đã tokenize]"
    → LightRAG hybrid_query(top_k=10)
       ┌── Qdrant: dense vector search (embedding 384-dim)
       └── Neo4j: KG traversal (entity → relation → entity)
    → Cắt kết quả tối đa 1000 chars
    → Dedup bằng MD5 của 100 chars đầu

Thêm xref expansion queries:
    Với mỗi "Điều X" trong cross_refs:
    query = "Điều X [domain]"
    → Đánh nhãn: "### Tham chiếu pháp lý: Điều X"

Kết quả ghép thành legal_context (markdown):
    ### Điều khoản 1
    [entity descriptions từ KG]
    ### Điều khoản 2
    ...
    ### Tham chiếu pháp lý: Điều 1 Lao động
    ...
```

**Bảo vệ rate limit:**
- `asyncio.Semaphore(1)`: chỉ 1 query chạy tại một thời điểm
- Backoff: 2s → 4s → 8s sau mỗi lần lỗi
- 1s sleep giữa các queries

---

### 4.4 Context Validator Agent (`context_validator_agent.py`)

**Mục tiêu:** Gatekeeper không tốn LLM — quyết định ngữ cảnh pháp lý có đủ chất lượng để audit không.

**Công thức tính điểm:**
```
coverage_ratio  = số clause có context / tổng clause medium+high
avg_relevance   = trung bình token_overlap(clause, clause_context)
ref_coverage    = cross-refs được cover / tổng cross-refs dạng "Điều"

context_quality_score = 0.3 × coverage_ratio
                      + 0.5 × avg_relevance    ← nhân tố quan trọng nhất
                      + 0.2 × ref_coverage
```

**Quyết định:**
```
avg_relevance < 0.15  → "poor" (bất kể coverage cao)
ref_coverage < 0.4 AND có xref → "missing_refs"
coverage < 0.5 OR relevance < 0.15 → "poor"
Ngược lại → "good"
```

**Routing:**
- `context_quality == "good"` OR đã retry đủ 2 lần → đến **Audit**
- `context_quality != "good"` AND retry < 2 → quay lại **Retrieval**

---

### 4.5 Audit Agent (`audit_agent.py`)

**Mục tiêu:** Phát hiện vi phạm pháp lý trong từng điều khoản.

**Luồng xử lý — 2 tầng:**

**Tầng 1: Deterministic rules (không LLM)**
```
_build_rule_based_findings(state, chunks):
    ● Overtime 100% blanket → Điều 98 BLLĐ 2019
      Pattern: "tiền lương làm thêm giờ" + "100% ... trong mọi trường hợp"
    ● Tăng phí tự động → Luật TM 2005
      Pattern: "tự động tăng 20%" + "không cần thỏa thuận"
    ● Cấm khiếu nại → Luật TM 2005
      Pattern: "không được khiếu nại/khiếu kiện" + "chất lượng/thiết bị"
    ● Thiếu điều kiện 1 năm nhượng quyền → NĐ 35/2006
      Pattern: is_franchise AND NOT has_one_year_prereq
```

**Tầng 2: LLM audit (gpt-4o-mini)**
```
Với mỗi clause medium/high risk:
    _should_audit_clause() filter:
        • risk == "high" → luôn audit
        • Có risk keywords ("phạt", "bồi thường"...) → audit
        • Có structural signals (%, "không cần chấp thuận"...) → audit
        • Token overlap(clause, context) ≥ threshold → audit
        • Ngược lại → skip (tiết kiệm token)

    Nếu audit:
        prompt = AUDIT_DEEP_SYSTEM_PROMPT.format(clause, legal_context)
        → gpt-4o-mini → JSON array findings
        [{clause, violation, reference_law, suggested_fix}, ...]
```

**Merge & dedup:**
```
Findings từ LLM + Deterministic rules → dedup bằng _finding_signature()
Deterministic rule thắng nếu cùng signature
confidence = số findings có reference_law / tổng findings
```

---

### 4.6 Critic Agent (`critic_agent.py`)

**Mục tiêu:** Tự kiểm tra kết quả audit — loại bỏ hallucination, phát hiện ngoại lệ bị bỏ sót.

**Luồng xử lý — 2 layer:**

**Layer 1: Regex negation scan (không LLM)**
```
Quét legal_context tìm:
    "không được", "cấm", "trừ trường hợp",
    "ngoại trừ", "miễn là", "chỉ khi",
    "không có quyền", "không áp dụng"
→ negations_found (danh sách phủ định được tìm thấy)
```

**Layer 2: LLM critic (gpt-4o-mini) — chỉ khi cần**
```
Kích hoạt khi:
    ● negations_found không rỗng
    ● confidence < 0.7
    ● context_quality != "good"
    ● context_quality_score < 0.6
    ● admissibility_rejected không rỗng

LLM được hỏi:
    → Findings nào bị hallucinate (law code không có thật)?
    → Có ngoại lệ pháp lý nào bị bỏ sót?
    → Điều chỉnh confidence
    → Phân loại error_type: ok/reasoning/hallucination/low_confidence
```

**Admissibility check (không LLM):**
```
Với mỗi LLM finding (không phải deterministic rule):
    _is_reference_supported(reference_law, legal_context, contract_text):
        ● Tên luật ("bộ luật lao động") có trong context/contract? ✓
        ● Mã luật (45/2019/QH14) có trong combined text? ✓
        ● Số điều có trong legal_context? ✓
        Nếu tất cả pass → reference valid
        Ngược lại → rejected_finding

Safeguard: Nếu context kém (validator báo "Low relevance score")
    VÀ tất cả LLM findings đều bị reject → giữ lại TẤT CẢ
    (thay vì xóa hết, hạ confidence thay thế)
```

**Routing sau Critic:**
```
retry_count ≥ 2           → "finalize" (đến Generator)
error_type = "hallucination" hoặc "low_confidence"
    → "to_retrieval" (retry với context mới)
error_type = "reasoning"
    AND validator báo "Low relevance score"
    → "low_confidence" → "to_retrieval"
error_type = "reasoning"  → "finalize" (anti-nitpicking guardrail)
error_type = "ok"         → "finalize"
```

---

### 4.7 Generator Agent (`generator_agent.py`)

**Mục tiêu:** Tổng hợp tất cả findings thành báo cáo Markdown tiếng Việt.

**Luồng xử lý:**
```
Bước 1: gpt-4o-mini tạo báo cáo
    Input: audit_findings, contract_domain, confidence,
           critic_feedback, negations_found
    Prompt: GENERATOR_SYSTEM_PROMPT
    Output: Markdown báo cáo với sections:
        ## Tóm tắt
        ## Chi tiết vi phạm (mỗi finding = 1 sub-section)
        ## Điều khoản phủ định & ngoại lệ
        ## Khuyến nghị chung

Bước 2: Nếu LLM fail → template tĩnh
    Duyệt qua audit_findings, tạo markdown không cần LLM
    Đảm bảo pipeline không bao giờ trả về rỗng
```

---

## 5. Các vòng lặp retry và giới hạn

### Vòng lặp 1: Context quality loop (Retrieval ↔ Validator)
```
MAX_CONTEXT_RETRY = 2
counter: context_retry_count (trong AuditState)

Validator → "poor" → Retrieval → Validator (retry 1)
                   → "poor" → Retrieval → Validator (retry 2)
                            → "poor" → forced to_audit (cap reached)
```

### Vòng lặp 2: Audit quality loop (Retrieval ↔ Critic)
```
MAX_RETRY = 2
counter: retry_count (trong AuditState)

Critic → "low_confidence" → Retrieval → Validator → Audit → Critic (retry 1)
                          → "low_confidence" → ... → Critic (retry 2)
       → retry_count ≥ 2 → finalize (cap reached)
```

**Lưu ý:** Hai counters độc lập. Vòng lặp critic-triggered không ảnh hưởng `context_retry_count`.

---

## 6. Luồng dữ liệu điển hình — Ví dụ HDLD

```
Input: "HĐLĐ Số: ... Điều 3.1(ix) tiền lương làm thêm 100%..."

1. ROUTER:
   → keyword match: "người lao động" ×5, "hợp đồng lao động" ×3 → Lao động
   → regex split: 6 clauses (Điều 1 đến Điều 5 + preamble)

2. PREPROCESSOR:
   → underthesea tokenize: "tiền_lương", "làm_thêm_giờ"...
   → risk: clause[3]="high" (has "đơn phương"), clause[4]="high" (has "bồi thường")
   → xrefs: 6 "Điều X" references

3. RETRIEVAL:
   → query = "Bộ luật Lao động: Điều 3: Quyền lợi và nghĩa vụ..."
   → LightRAG → KG entities: "Hợp đồng lao động", "Người lao động"...
   → legal_context = 6 sections × ~1000 chars

4. CONTEXT_VALIDATOR:
   → avg_relevance = 0.07 (token overlap thấp)
   → score = 0.3×1.0 + 0.5×0.07 + 0.2×1.0 = 0.535
   → "poor" → retry (context_retry_count = 1)
   → (retry x2) → forced to_audit

5. AUDIT:
   → Deterministic: "100% ... trong mọi trường hợp" + "tiền lương làm thêm"
     → findings[0] = overtime violation (Điều 98 BLLĐ 2019)
   → LLM audit clause[4], clause[5]:
     → findings[1] = silence consent mechanism
     → findings[2] = missing Điều 2.4

6. CRITIC:
   → Layer 1: negations_found = ["không được", "trừ khi"]
   → admissibility: findings[1],[2] rejected (law code not in KG context)
   → safeguard: all LLM findings rejected + low_relevance → KEEP ALL
   → Layer 2 LLM: confidence adjusted = 0.55, error_type = "low_confidence"
   → retry_count = 1 → route to_retrieval

7. (retry cycle x1) → RETRIEVAL → VALIDATOR → AUDIT → CRITIC
   → retry_count = 2 → finalize

8. GENERATOR:
   → gpt-4o-mini tạo báo cáo với 3 findings
   → Markdown: Tóm tắt + Chi tiết vi phạm × 3 + Khuyến nghị

Output: final_report (Markdown tiếng Việt, confidence=0.55)
```

---

## 7. Cấu trúc thư mục liên quan

```
src/
├── agents/
│   ├── orchestrator.py        # StateGraph wiring + routing functions
│   ├── router_agent.py        # Node 1: phân loại + tách clause
│   ├── preprocessor_agent.py  # Node 2: tokenize + risk score + xref
│   ├── retrieval_agent.py     # Node 3: LightRAG hybrid query
│   ├── context_validator_agent.py  # Node 4: heuristic quality gate
│   ├── audit_agent.py         # Node 5: LLM audit + deterministic rules
│   ├── critic_agent.py        # Node 6: negation scan + LLM critic
│   └── generator_agent.py     # Node 7: Markdown report generation
├── core/
│   ├── state.py               # AuditState TypedDict definition
│   ├── legal_patterns.py      # Domain keywords + clause splitting regex
│   ├── prompts.py             # AUDIT_DEEP/QUICK, CRITIC, GENERATOR prompts
│   ├── llm_config.py          # OpenAI/Cerebras provider selection
│   └── lightrag_client.py     # LightRAG client wrapper
└── run_audit.py               # CLI entry point
```

---

## 8. Câu hỏi thường gặp

**Tại sao không gọi LLM song song?**
`asyncio.Semaphore(1)` buộc các LLM call chạy tuần tự để tránh rate limit. LightRAG hybrid query cũng dùng LLM nội bộ cho keyword extraction.

**Khi nào deterministic rules thắng LLM?**
Khi `_finding_signature()` trả về cùng signature. Deterministic rule luôn override finding LLM tương ứng vì độ chính xác cao hơn.

**Tại sao confidence thường thấp (0.55)?**
Confidence bị cap bởi nhiều điều kiện: `context_quality_score < 0.6 → cap 0.65`, `"Low relevance score" → cap 0.55`. Đây là hành vi chủ đích để thể hiện mức độ chắc chắn thực của hệ thống khi context từ KB không đủ mạnh.

**Luật Nhà ở 2023 không có trong KB thì sao?**
Pipeline vẫn hoạt động nhờ LLM có training knowledge về luật này. Deterministic rules cho housing domain có thể được thêm vào `_build_rule_based_findings()` trong tương lai mà không cần reindex KB.

**Làm sao thêm domain mới?**
1. Thêm keywords vào `DOMAIN_KEYWORDS` trong `legal_patterns.py`
2. Thêm domain entry vào `_DOMAIN_LAW_CONTEXT` trong `retrieval_agent.py`
3. (Tùy chọn) Thêm deterministic rules vào `_build_rule_based_findings()`
4. Index corpus mới vào Neo4j + Qdrant qua `init_storage.py`
