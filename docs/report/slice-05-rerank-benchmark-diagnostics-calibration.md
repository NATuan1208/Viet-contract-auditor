# Slice 05 - Rerank Benchmark, Diagnostics, and Calibration

## 1. Muc tieu

Slice 04 da cau hinh LightRAG rerank dung runtime, nhung E2E recall tren mau HDLD van thap:

- Precision: `1.000`
- Recall: `0.200`
- F1: `0.333`
- Predicted violations: `1`
- Groundtruth vulnerabilities: `5`

Ket luan ky thuat quan trong: recall thap khong nen duoc xem ngay la loi cau hinh rerank. Rerank chi sap xep lai cac candidate ma first-stage retrieval da lay duoc. Neu candidate pool khong co dung dieu luat, hoac audit/critic cat bo finding sau retrieval, viec tang threshold hay doi model rerank se lam nhieu hon la sua dung van de.

Muc tieu cua slice 05 la tao mot benchmark loop co the debug tung tang:

1. Do trace query/rerank truoc khi tune.
2. Chay A/B rerank bat/tat tren cung groundtruth.
3. Chan doan diem mat recall: retrieval, rerank, audit generation, hay critic admissibility.
4. Dua ra calibration de tiep tuc benchmark ma khong overfit threshold.

## 2. Research va quyet dinh

### 2.1 LightRAG rerank flow

Qua source package dang cai trong `.venv`, LightRAG flow lien quan:

- `QueryParam.top_k`: lay entity/relationship top-k theo mode.
- `QueryParam.chunk_top_k`: so chunk giu lai sau retrieval/rerank.
- `QueryParam.enable_rerank`: bat/tat rerank theo query.
- `apply_rerank_if_enabled(...)`: goi `rerank_model_func(query, documents, top_n)`.
- `process_chunks_unified(...)`: rerank, filter theo `min_rerank_score`, cat `chunk_top_k`, roi cat token.

Dieu nay dan den quy tac:

- `min_rerank_score` la threshold nguy hiem neu chua calibrate score distribution.
- `top_k/chunk_top_k` la knob an toan hon de debug recall truoc.
- Muon biet rerank co lam mat candidate khong thi can trace candidate count va score distribution.

### 2.2 Best practice cho benchmark RAG/rerank

Approach duoc chon:

- Benchmark cung mot groundtruth, cung corpus, chi thay env config.
- Tach metric retrieval/rerank/audit thay vi chi nhin E2E F1.
- Giu `LIGHTRAG_MIN_RERANK_SCORE=0.0` trong cac sweep dau tien.
- Chi calibrate threshold khi co expected-law hit labels hoac retrieval-level labels.

Khong chon:

- Khong tang threshold len `0.5` hoac `0.7` ngay: score cua cross-encoder la model-specific, chua co distribution va chua co label relevance.
- Khong doi model rerank ngay: runtime da dung, van de hien tai co dau hieu nam o retrieval/audit/critic.
- Khong dua benchmark vao unit test: se dot OpenAI/HF, cham va flaky.

## 3. Slice 5A - Instrumentation

### 3.1 Thay doi code

Them module:

- `src/core/benchmark_trace.py`

Chuc nang:

- `LIGHTRAG_BENCHMARK_TRACE_ENABLED=false` mac dinh de tranh ghi query hop dong rieng tu.
- `LIGHTRAG_BENCHMARK_TRACE_PATH=reports/benchmarks/retrieval_trace.jsonl`
- `LIGHTRAG_TRACE_PREVIEW_CHARS=180`, co the set `0` de chi luu hash.
- Moi event co `ts`, `event_type`, hash va preview rut gon.

Noi vao reranker:

- `src/core/rerank_client.py`
- Ghi event `rerank` gom:
  - `model`
  - `query_hash`, `query_preview`
  - `candidate_count`, `returned_count`, `top_n`
  - `score_summary`: count/min/p50/p90/max/mean
  - top 5 result: rank/index/score/doc_hash/doc_preview

Noi vao query:

- `src/core/lightrag_client.py`
- `query_hybrid(...)` doc env knobs:
  - `LIGHTRAG_QUERY_TOP_K`
  - `LIGHTRAG_CHUNK_TOP_K`
  - `LIGHTRAG_RERANK_ENABLED`
- Ghi event `query` gom:
  - mode, query hash/preview
  - top_k, chunk_top_k, enable_rerank
  - result chars/hash/preview

Noi vao retrieval cap:

- `src/agents/retrieval_agent.py`
- Them `LIGHTRAG_CONTEXT_MAX_CHARS`, default `1000`.

### 3.2 Tests

Them unit tests trong `tests/test_legal_pipeline.py`:

- Trace opt-in va JSONL.
- Score summary percentile.
- Reranker trace distribution.
- Query env knobs + trace event.

### 3.3 Critic 5A

Dung:

- Trace mac dinh tat, khong lam lo contract text neu khong bat.
- Trace ghi hash + preview ngan, phu hop benchmark/debug.
- Query knobs cho phep A/B ma khong sua code.

Con thieu co chu dich:

- 5A chua ket luan recall vi chua chay A/B.
- Trace chua co expected-law hit labels, nen chua tinh duoc retrieval Recall@K that su.

## 4. Slice 5B - A/B Benchmark Runner

### 4.1 Thay doi code

Them CLI:

- `src/benchmark_rerank_ab.py`

Chay:

```powershell
uv run python src\benchmark_rerank_ab.py --groundtruth "result-example\HDLD\groundtruth_hdld_01_test copy.json"
```

Dry-run:

```powershell
uv run python src\benchmark_rerank_ab.py --groundtruth "result-example\HDLD\groundtruth_hdld_01_test copy.json" --run-id smoke-dry-run --dry-run
```

Mac dinh chay hai variant:

1. `baseline_no_rerank`
   - `LIGHTRAG_RERANK_ENABLED=false`
   - `LIGHTRAG_QUERY_TOP_K=10`
   - `LIGHTRAG_CHUNK_TOP_K=20`
   - `LIGHTRAG_CONTEXT_MAX_CHARS=1000`
   - `LIGHTRAG_MIN_RERANK_SCORE=0.0`

2. `rerank_default`
   - `LIGHTRAG_RERANK_ENABLED=true`
   - cung top_k/chunk_top_k/context cap
   - `LIGHTRAG_MIN_RERANK_SCORE=0.0`

Moi variant ghi vao:

- `reports/benchmarks/<run_id>/<variant>/report.md`
- `reports/benchmarks/<run_id>/<variant>/eval.md`
- `reports/benchmarks/<run_id>/<variant>/state.json`
- `reports/benchmarks/<run_id>/<variant>/retrieval_trace.jsonl`
- `reports/benchmarks/<run_id>/<variant>/stdout.log`

Runner xuat:

- `reports/benchmarks/<run_id>/summary.json`
- `reports/benchmarks/<run_id>/summary.md`

### 4.2 Tests

Them tests:

- Build command/env tach variant.
- Parse eval/state/trace summary.
- Build summary markdown.

### 4.3 Critic 5B

Dung:

- A/B dung cung groundtruth, cung artifact layout, moi variant co trace rieng.
- Dry-run co the review command truoc khi dot API.
- Summary gom metric E2E, critic prune, trace event count.

Con thieu:

- Runner khong tu chay trong test, nen smoke hien tai chi kiem tra cau truc.
- Muon co so lieu that can chay CLI real voi OpenAI/HF.

## 5. Slice 5C - Loss-Point Diagnostics

### 5.1 Thay doi code

Them CLI:

- `src/benchmark_diagnose.py`

Chay:

```powershell
uv run python src\benchmark_diagnose.py --summary reports\benchmarks\<run_id>\summary.json
```

Output:

- `diagnosis.json`
- `diagnosis.md`

Logic chan doan theo thu tu bang chung:

1. Variant fail -> `run_failed`.
2. Rerank enabled nhung khong co rerank event -> `rerank_not_exercised`.
3. Context validator poor/missing/score thap -> `retrieval_context_quality`.
4. Critic prune nhieu trong khi pred < GT -> `critic_admissibility_prune`.
5. Pred = 0 trong khi GT > 0 -> `audit_generation`.
6. Recall thap nhung chua du bang chung -> `audit_or_retrieval_gap`.

So sanh variant:

- `rerank_improved_recall`
- `rerank_reduced_recall`
- `rerank_neutral_on_recall`
- `not_comparable`

### 5.2 Tests

Them tests:

- Flag `critic_admissibility_prune` khi context good nhung critic prune nhieu.
- So sanh recall baseline vs rerank.
- Build markdown diagnosis.

### 5.3 Critic 5C

Dung:

- Khong overclaim neu trace chua co expected-law labels.
- Chuyen E2E recall thap thanh loss-point hypothesis co bang chung.
- Neu state hien tai giong run truoc, kha nang lon bottleneck la critic/admissibility, khong phai rerank config.

Con thieu:

- Chua co retrieval-level gold labels de tinh Recall@K/MRR/nDCG that su.
- Chua tach pre-critic findings thanh artifact rieng; hien state chi giu final findings + critic feedback.

## 6. Slice 5D - Calibration va Sweep

### 6.1 Thay doi code

Mo rong `src/benchmark_rerank_ab.py` voi flag:

```powershell
uv run python src\benchmark_rerank_ab.py --groundtruth "result-example\HDLD\groundtruth_hdld_01_test copy.json" --include-sweep
```

Them sweep variants:

1. `rerank_wide_30_40`
   - `LIGHTRAG_QUERY_TOP_K=30`
   - `LIGHTRAG_CHUNK_TOP_K=40`
   - `LIGHTRAG_CONTEXT_MAX_CHARS=1500`
   - `LIGHTRAG_MIN_RERANK_SCORE=0.0`

2. `rerank_wide_50_60`
   - `LIGHTRAG_QUERY_TOP_K=50`
   - `LIGHTRAG_CHUNK_TOP_K=60`
   - `LIGHTRAG_CONTEXT_MAX_CHARS=2000`
   - `LIGHTRAG_MIN_RERANK_SCORE=0.0`

Them CLI:

- `src/benchmark_calibrate.py`

Chay:

```powershell
uv run python src\benchmark_calibrate.py --summary reports\benchmarks\<run_id>\summary.json --diagnosis reports\benchmarks\<run_id>\diagnosis.json
```

Output:

- `calibration.json`
- `calibration.md`

Decision rules:

- Neu rerank giam recall -> tang candidate pool truoc threshold.
- Neu retrieval context poor -> widen retrieval candidates.
- Neu critic prune la bottleneck -> debug critic truoc khi tune retrieval.
- Neu rerank neutral -> them expected-law hit labels truoc khi tune.
- Luon giu `LIGHTRAG_MIN_RERANK_SCORE=0.0` cho den khi co relevance labels.

### 6.2 Tests

Them tests:

- Sweep variants ton tai va khong doi threshold.
- Calibration recommend candidate pool khi rerank giam recall.
- Calibration uu tien critic debug khi bottleneck la critic.
- Trace summary aggregate p50/max score.

### 6.3 Critic 5D

Dung:

- Calibration conservative, tranh "tune mo" threshold.
- Sweep tang candidate pool, dung voi nguyen ly rerank chi reorder candidate.
- Neu bottleneck la critic, calibration khong ep retrieval phai chiu loi.

Con thieu:

- Van can real A/B run de co so lieu.
- Van can expected-law hit labels de tinh retrieval-level Recall@K.
- Chua co pre-critic findings artifact, nen critic diagnosis dua tren `findings_pruned` va final prediction count.

## 7. Commands da verify

Unit tests:

```powershell
uv run python -m unittest discover -s tests
```

Ket qua:

- `Ran 36 tests`
- `OK`

Compile:

```powershell
uv run python -m compileall src
```

Ket qua:

- compile pass

Docker compose config:

```powershell
docker-compose config --quiet
```

Ket qua:

- pass, no output

Pipeline health:

```powershell
uv run python src\pipeline_health.py
```

Ket qua luc verify 5A:

- Neo4j healthy
- Qdrant healthy
- Postgres healthy
- MinIO healthy
- Tika healthy
- Lakehouse exists
- Backlog raw/unique van co do manifest cu, khong lien quan truc tiep den benchmark slice

Dry-run benchmark:

```powershell
uv run python src\benchmark_rerank_ab.py --groundtruth "result-example\HDLD\groundtruth_hdld_01_test copy.json" --run-id smoke-dry-run --dry-run
```

Dry-run sweep:

```powershell
uv run python src\benchmark_rerank_ab.py --groundtruth "result-example\HDLD\groundtruth_hdld_01_test copy.json" --run-id smoke-dry-run-sweep --dry-run --include-sweep
```

Diagnosis/calibration smoke:

```powershell
uv run python src\benchmark_diagnose.py --summary reports\benchmarks\smoke-dry-run-sweep\summary.json
uv run python src\benchmark_calibrate.py --summary reports\benchmarks\smoke-dry-run-sweep\summary.json --diagnosis reports\benchmarks\smoke-dry-run-sweep\diagnosis.json
```

## 8. Cach chay benchmark that

Chay A/B toi thieu:

```powershell
uv run python src\benchmark_rerank_ab.py --groundtruth "result-example\HDLD\groundtruth_hdld_01_test copy.json" --run-id hdld-rerank-ab-20260514
uv run python src\benchmark_diagnose.py --summary reports\benchmarks\hdld-rerank-ab-20260514\summary.json
uv run python src\benchmark_calibrate.py --summary reports\benchmarks\hdld-rerank-ab-20260514\summary.json --diagnosis reports\benchmarks\hdld-rerank-ab-20260514\diagnosis.json
```

Neu A/B cho thay rerank giam recall hoac context quality thap, chay sweep:

```powershell
uv run python src\benchmark_rerank_ab.py --groundtruth "result-example\HDLD\groundtruth_hdld_01_test copy.json" --run-id hdld-rerank-sweep-20260514 --include-sweep
uv run python src\benchmark_diagnose.py --summary reports\benchmarks\hdld-rerank-sweep-20260514\summary.json
uv run python src\benchmark_calibrate.py --summary reports\benchmarks\hdld-rerank-sweep-20260514\summary.json --diagnosis reports\benchmarks\hdld-rerank-sweep-20260514\diagnosis.json
```

Luu y: benchmark that se goi OpenAI va co the load model HuggingFace, nen thoi gian/cost khong nho.

## 9. Mapping code hien tai

- Trace helpers: `src/core/benchmark_trace.py`
- Rerank trace: `src/core/rerank_client.py`
- Query env knobs + query trace: `src/core/lightrag_client.py`
- Retrieval context cap: `src/agents/retrieval_agent.py`
- A/B runner: `src/benchmark_rerank_ab.py`
- Loss diagnosis: `src/benchmark_diagnose.py`
- Calibration recommendation: `src/benchmark_calibrate.py`
- Env docs: `.env.example`
- Compose env passthrough: `docker-compose.yml`
- Tests: `tests/test_legal_pipeline.py`

## 10. Final critics

### Dieu da giai quyet

- Co benchmark harness de so sanh rerank bat/tat tren cung groundtruth.
- Co trace score distribution va top result de tranh tune cam tinh.
- Co diagnosis de khong nham lan rerank issue voi critic prune issue.
- Co calibration rule conservative, giu threshold = 0.0 den khi co labels.

### Rui ro con lai

- Chua co real A/B numbers sau slice nay vi chua chay benchmark ton OpenAI/HF.
- Groundtruth hien co it, neu chi toi uu tren HDLD thi de overfit.
- Current diagnosis chua tinh duoc retrieval Recall@K that su vi chua co expected-law label theo query.
- Critic co dau hieu prune qua manh, can mot slice rieng luu pre-critic findings va ly do prune chi tiet.

### Huong tiep theo

1. Chay real A/B toi thieu `baseline_no_rerank` vs `rerank_default`.
2. Doc `diagnosis.md`.
3. Neu bottleneck la critic, lam slice "pre-critic finding audit".
4. Neu bottleneck la retrieval, chay `--include-sweep`.
5. Chi calibrate `LIGHTRAG_MIN_RERANK_SCORE` sau khi co expected-law hit labels va score distribution theo relevant/non-relevant chunks.
