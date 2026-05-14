# Slice 04 - LightRAG Rerank Production-Local

## 1. Muc tieu

Slice nay xu ly warning xuat hien trong phase apply LightRAG real:

```text
Rerank is enabled but no rerank model is configured.
```

Muc tieu khong phai la them mot model cho co, ma la lam dung vai tro cua rerank trong LightRAG:

- Giu first-stage retrieval co recall cao: KG entity, KG relation, vector chunk van lay nhieu ung vien.
- Dung reranker lam second-stage precision layer: sap xep lai cac chunk ung vien theo muc do lien quan voi query.
- Khong thay doi KG schema, khong thay doi manifest, khong re-index neu chi cau hinh rerank.
- Co rollback nhanh bang environment variable.
- Kiem chung bang smoke run that qua MAS audit.

## 2. LightRAG rerank hoat dong nhu the nao trong code hien tai

Source of truth la package LightRAG dang cai trong `.venv`, khong suy luan theo tai lieu chung.

### 2.1 QueryParam bat rerank mac dinh

Trong `.venv/Lib/site-packages/lightrag/base.py:160`, `QueryParam.enable_rerank` doc mac dinh tu env `RERANK_BY_DEFAULT`, default la `true`.

Dieu nay giai thich vi sao pipeline cu van log warning du khong truyen rerank rieng: LightRAG da bat rerank by default, nhung client cua minh chua dua `rerank_model_func`.

### 2.2 LightRAG constructor nhan rerank hook

Trong `.venv/Lib/site-packages/lightrag/lightrag.py:442-448`, LightRAG co hai config chinh:

- `rerank_model_func`: callable dung de rerank retrieved documents.
- `min_rerank_score`: threshold loc chunk sau khi rerank.

Viec can lam la truyen dung callable vao `LightRAG(...)`, khong can sua code package.

### 2.3 Rerank chi tac dong text chunks

Trong `.venv/Lib/site-packages/lightrag/utils.py:2570-2638`, LightRAG goi:

```python
rerank_results = await rerank_func(query=query, documents=document_texts, top_n=top_n)
```

Format moi ma LightRAG uu tien la:

```python
[
  {"index": 0, "relevance_score": 0.85},
  ...
]
```

LightRAG se map `index` ve chunk goc, gan `rerank_score`, roi tra ve danh sach da sap xep.

### 2.4 Thu tu xu ly chunk

Trong `.venv/Lib/site-packages/lightrag/utils.py:2654-2712`, pipeline chunk la:

1. Nhan danh sach `unique_chunks` tu retrieval.
2. Neu `enable_rerank` true va co query: goi `apply_rerank_if_enabled`.
3. Loc theo `min_rerank_score` neu threshold > 0.
4. Gioi han `chunk_top_k`.
5. Token truncation theo ngan sach context.

Vay rerank khong thay doi entity/relation retrieval. No chi reorder/filter text chunks truoc khi context duoc dua sang LLM.

## 3. Vi sao chon local cross-encoder reranker

### 3.1 Phuong an duoc chon

Da chon local reranker:

```text
cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
```

Ly do:

- Multilingual, phu hop truy van tieng Viet hon reranker chi tieng Anh.
- Chay local bang `sentence-transformers`, khong can them API key moi.
- Nhe hon cac reranker lon nhu BGE reranker M3, phu hop production-local tren laptop/CPU.
- Rerank la inference classification/ranking, khong can dung GPT-4o-mini. Dung LLM de rerank tung query se ton token, latency cao, va kho on dinh quota.

### 3.2 Phuong an khong chon

#### LLM rerank bang GPT-4o-mini

Khong chon vi:

- Moi retrieval query co 20-60 chunk ung vien. Dua tat ca vao LLM de rank lam tang chi phi.
- Ket qua kho deterministic hon cross-encoder.
- MAS da dung GPT-4o-mini cho router/critic/generator va LightRAG keyword extraction; them rerank LLM lam tang diem nghen quota.

#### API rerank ben ngoai nhu Cohere/Jina

LightRAG co `cohere_rerank`, `jina_rerank`, `ali_rerank` trong `.venv/Lib/site-packages/lightrag/rerank.py`, nhung khong chon default vi:

- Can API key rieng.
- Them dependency vao external service.
- Production-local cua do an nen uu tien self-contained stack.

#### BAAI/bge-reranker-v2-m3 mac dinh

Day la ung vien manh cho multilingual rerank, nhung khong chon default vi:

- Nang hon, cold start va CPU latency cao hon.
- Phu hop hon khi co GPU hoac batch offline.
- Co the override bang env `LIGHTRAG_RERANK_MODEL` neu can benchmark sau.

## 4. Code mapping

### 4.1 Adapter reranker

File: `src/core/rerank_client.py`

Thanh phan chinh:

- `RerankSettings`: gom enabled, model name, batch size, max chars, normalize score.
- `get_rerank_settings()`: doc env config.
- `build_rerank_model_func()`: tra ve coroutine dung signature cua LightRAG.
- `_get_reranker()`: lazy-load `sentence_transformers.CrossEncoder`.
- `_sigmoid()`: dua raw cross-encoder score ve khoang 0..1 de log/threshold de doc hon.

Adapter tra ve format:

```python
{"index": original_chunk_index, "relevance_score": normalized_score}
```

Dung `asyncio.to_thread(...)` de prediction CPU khong block event loop truc tiep.

### 4.2 Tich hop vao LightRAG client

File: `src/core/lightrag_client.py`

Tai `LightRAG(...)`, them:

```python
rerank_model_func=rerank_model_func,
min_rerank_score=float(os.getenv("LIGHTRAG_MIN_RERANK_SCORE", "0.0")),
```

Va log:

```text
LightRAG client ready (..., rerank=True, rerank_model=cross-encoder/...)
```

Threshold mac dinh de `0.0`. Ly do: diem cua cross-encoder co tinh model-specific. Trong slice nay muc tieu dau tien la reorder top chunks, khong loc manh. Loc theo threshold nen lam sau khi co evaluation set va distribution score.

### 4.3 Env config

File: `.env.example`

Them:

```text
LIGHTRAG_RERANK_ENABLED=true
LIGHTRAG_RERANK_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
LIGHTRAG_RERANK_BATCH_SIZE=16
LIGHTRAG_RERANK_MAX_CHARS=4000
LIGHTRAG_RERANK_NORMALIZE_SCORES=true
LIGHTRAG_MIN_RERANK_SCORE=0.0
```

`docker-compose.yml` cung forward cac bien nay cho worker LightRAG/KG.

## 5. Verification da chay

### 5.1 Unit va compile

```bash
uv run python -m unittest discover -s tests
```

Ket qua:

```text
Ran 24 tests
OK
```

Them tests cho:

- `LIGHTRAG_RERANK_ENABLED=false` thi khong build rerank func.
- Local reranker tra ve dung format index-score.
- Truncate va sigmoid score helper.

```bash
uv run python -m compileall src
```

Ket qua: compile OK.

```bash
docker-compose config --quiet
```

Ket qua: config OK.

### 5.2 Runtime smoke voi MAS

Command:

```bash
uv run python src/run_audit.py result-example/HDLD/HDLD_ThucHanh_01.docx --output reports/final_outputs/hdld_report_rerank_smoke_20260514.md
```

Evidence trong log:

```text
Successfully reranked: 20 chunks from 65 original chunks
Successfully reranked: 20 chunks from 66 original chunks
Successfully reranked: 20 chunks from 28 original chunks
...
```

Warning cu khong con xuat hien:

```text
Rerank is enabled but no rerank model is configured
```

Output report:

```text
reports/final_outputs/hdld_report_rerank_smoke_20260514.md
```

Ket qua run:

```text
domain=Lao động, chunks=6, findings=2, confidence=0.60
```

### 5.3 Health check

```bash
uv run python src/pipeline_health.py
```

Ket qua:

- Neo4j healthy
- Qdrant healthy
- PostgreSQL healthy
- MinIO healthy
- Tika healthy

### 5.4 E2E evaluation

Command:

```bash
uv run python src/e2e_eval.py --groundtruth "result-example/HDLD/groundtruth_hdld_01_test copy.json"
```

Ket qua:

```text
Report written: reports/final_outputs/hdld_01_test copy_report.md
State written: reports/metrics/hdld_01_test copy_state.json
Evaluation written: reports/metrics/hdld_01_test copy_eval.md
Heuristic metrics => precision=1.000, recall=0.200, f1=0.333, pred=1, gt=5
```

Nhan xet: rerank da chay trong E2E path, nhung heuristic recall hien chi 0.200. Day khong phai loi runtime cua rerank, ma la tin hieu can tiep tuc calibrate retrieval/evaluation: rerank dang tang precision cua context, nhung prediction layer van chua bat du 5 expected findings trong ground truth.

## 6. Final critics

### 6.1 Cold start con cham

Lan dau load reranker can tai/cache model tu HuggingFace. Log co warning HF unauthenticated va Windows symlink fallback. Trong production-local, day la chap nhan duoc cho dev, nhung khi demo/phong van nen prewarm cache truoc.

Khuyen nghi:

- Chay mot smoke query truoc buoi demo.
- Them `HF_TOKEN` neu download bi rate-limit.
- Co the set `HF_HUB_DISABLE_SYMLINKS_WARNING=1` de giam noise log tren Windows, nhung khong bat buoc.

### 6.2 CPU latency tang

Rerank cross-encoder cham hon pure vector ordering. Day la tradeoff dung: tang precision doi lay latency. Vi pipeline MAS query 6 clause + xref, moi query rerank 20-60 chunks, latency se thay ro tren CPU.

Khuyen nghi:

- Giu `chunk_top_k=20`.
- Giu `LIGHTRAG_RERANK_BATCH_SIZE=16`.
- Chi tang top_k/chunk_top_k sau khi co benchmark.

### 6.2.1 Metric acceptance chua dat muc mong muon

E2E heuristic sau khi bat rerank dat precision 1.000 nhung recall 0.200. Nghia la finding sinh ra it hon ground truth. Day la tradeoff can theo doi: rerank co the dua context gon/chinh xac hon, nhung neu downstream agent chi sinh 1 finding thi recall van thap.

Khuyen nghi:

- So sanh cung ground truth voi `LIGHTRAG_RERANK_ENABLED=false`.
- Log rerank score distribution cua 20 chunks moi query.
- Neu recall giam, tang first-stage candidate pool (`top_k`) truoc, sau do de rerank cat ve `chunk_top_k=20`.

### 6.3 Threshold chua nen bat manh

`LIGHTRAG_MIN_RERANK_SCORE=0.0` la co chu dich. Neu dat 0.5/0.7 ngay, co nguy co mat citation quan trong do score distribution cua model chua duoc calibrate tren legal Vietnamese corpus.

Khuyen nghi:

- Thu thap score distribution tu e2e set.
- Moi sau do dat threshold theo F1/citation recall.

### 6.4 Rerank khong sua duoc loi corpus

Rerank chi sap xep chunks da retrieve. Neu KG/corpus khong co van ban lien quan, hoac OCR noise qua nang, reranker khong the tao ra context dung.

Khuyen nghi:

- Tiep tuc quality gate OCR.
- Uu tien crawl/canonical source dung domain lao dong, dan su, thuong mai.

### 6.5 Qdrant client/server version warning van con

Smoke van co warning:

```text
qdrant-client 1.17.1 incompatible with server 1.13.2
```

Chua block rerank, nhung nen align version sau de giam risk.

## 7. Ket luan

Slice nay da chuyen LightRAG tu trang thai "rerank enabled but no model" sang rerank that bang local multilingual cross-encoder. Day la huong hop ly cho production-local: khong them API key, khong dung GPT-4o-mini cho tac vu ranking, co env rollback, co tests, va runtime smoke xac nhan rerank duoc goi trong retrieval path.
