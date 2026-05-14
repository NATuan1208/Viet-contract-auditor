# Slice 02: Crawler Correctness, Discovery-only Signals va Crawl Run Observability

## Muc tieu cua slice

Slice 2 xu ly van de con lai sau slice 1: storage/Iceberg da chay, version/KG manifest logic dung, nhung crawler chua lay duoc "van ban that" de tao manifest KG co y nghia.

Muc tieu cu the:

- Lay duoc danh sach van ban that tu nguon official.
- Dung Thu Vien Phap Luat nhu discovery signal, khong crawl full text vao KG.
- Them `crawl_run_id` di xuyen qua bronze/silver/gold/manifest de debug theo tung batch.
- Cai thien monitor backlog de phan biet raw manifest va unique manifest.
- Giu human-in-loop truoc khi apply vao LightRAG.

Ket qua hien tai:

- `vanban_chinhphu` dry-run lay duoc 3 sample van ban that.
- Local write smoke: `discovered=3`, `written=3`, `kg_updates=3`.
- Rerun cung data: `kg_updates=0`, chung minh idempotency.
- TVPL write smoke: `discovered=5`, `written=0`, `discovery_only_skipped=5`.
- Iceberg smoke: `written=2`, `iceberg_silver_appended=2`, `kg_updates=2`.
- KG dry-run unique backlog: 3 items.

## Van de can giai quyet

Slice 1 da phat hien Cong bao RSS co hai loai:

- Feed van ban moi co the tro den detail van ban.
- Feed so cong bao moi dang thuong la issue/container.

Khi crawler coi issue nhu document, KG se bi nhieu. Khi crawler skip issue, gold manifest bang 0. Nghia la storage da dung nhung input document chua du.

User yeu cau "uu tien lay danh sach van ban that hoac crawl web bang cong cu chuyen sau tu Thu Vien Phap Luat va cac trang uy tin khac". Yeu cau nay co hai lop:

- Discovery: can biet hom nay/co gan day co van ban nao moi.
- Canonical ingestion: can lay noi dung/metadata tu nguon official de dua vao KG chinh.

## Quyet dinh thiet ke

### 1. Dung TVPL de discovery-only, khong fetch full text

Mapping code:

- Connector: `src/pipeline/connectors.py`, `ThuvienPhapLuatDiscoveryConnector`
- Parser: `src/pipeline/connectors.py`, `parse_thuvienphapluat_new_documents`
- Config: `config/legal_sources.yml`, source `thuvienphapluat_discovery`
- CLI skip: `src/crawl_legal_sources.py`, field `discovery_only_skipped`

Ly do chon:

- TVPL co listing van ban moi rat huu ich de phat hien van ban gan day.
- Nhung TVPL la source thuong mai, license full text chua ro.
- Policy da chot tu dau la official-first, commercial discovery-only.

Tai sao khong crawl full text tu TVPL:

- Co the nhanh cho demo, nhung yeu khi defend ve license va canonical provenance.
- KG chinh se kho giai thich "tai sao noi dung nay duoc coi la ban goc".
- Neu sau nay co API/license, connector co the nang cap rieng ma khong pha policy hien tai.

Cach implement:

- `discover()` GET `https://thuvienphapluat.vn/van-ban-moi/van-ban-moi`.
- Parser lay cac link `/van-ban/...aspx`.
- Extract `title`, `canonical_number`, `commercial_source_url`.
- `fetch()` cua connector raise error neu bi goi, de dam bao khong vo tinh lay full text.
- Crawler neu `source.discovery_only` va `--write-lakehouse` thi skip write, tang `discovery_only_skipped`.

### 2. Dung `vanban.chinhphu.vn` lam official fulltext/listing connector

Mapping code:

- Connector: `src/pipeline/connectors.py`, `VanBanChinhPhuConnector`
- Listing parser: `src/pipeline/connectors.py`, `parse_vanban_chinhphu_listing`
- Detail parser: `src/pipeline/connectors.py`, `parse_vanban_chinhphu_detail_record`
- Registry source: `config/legal_sources.yml`, source `vanban_chinhphu`

Ly do chon:

- `vanban.chinhphu.vn` la nguon official.
- Listing `https://vanban.chinhphu.vn/he-thong-van-ban?classid=0&maxresults=50&mode=1` tra ve danh sach van ban that, co `docid`.
- Detail page dang `https://vanban.chinhphu.vn/?pageid=27160&docid=...&classid=0` co metadata va attachment.

Tai sao khong tiep tuc chi sua Cong bao:

- Cong bao la canonical publication feed, nhung issue parsing can them logic tach van ban ben trong so cong bao.
- `vanban.chinhphu.vn` cho vertical slice nhanh hon: listing -> detail -> LegalDocumentRecord -> KG manifest.
- Cach nay giai quyet muc tieu "crawler correctness" ma khong lam parser Cong bao qua phuc tap trong mot slice.

Trade-off:

- `vanban.chinhphu.vn` khong bao phu tat ca VBQPPL nhu `vbpl.vn`.
- Hien tai parser HTML detail chua extract full PDF/DOC text, moi lay metadata va page text.
- Van can them `vbpl.vn` connector de full canonical national database.

### 3. Them `crawl_run_id` vao toan bo pipeline

Mapping code:

- Model:
  - `RawArtifact.crawl_run_id`
  - `LegalDocumentRecord.crawl_run_id`
  - `KGUpdateManifest.crawl_run_id`
- Local bronze path:
  - `data/lakehouse/bronze/<source>/<date>/<crawl_run_id>/<checksum>.bin`
  - Implemented in `src/pipeline/lakehouse.py`
- Iceberg schema/rows:
  - `src/pipeline/iceberg_lakehouse.py`
  - `bronze_schema`, `silver_schema`, `gold_schema`
  - `bronze_row`, `silver_row`, `gold_row`
- CLI:
  - `src/crawl_legal_sources.py --crawl-run-id`

Ly do chon:

- Khi chay daily batch, can biet raw artifact nao thuoc lan crawl nao.
- Khi monitor thay backlog bat thuong, can trace ve batch cu the.
- Khi retry/fail partial, `crawl_run_id` giup nhin ra du lieu sinh tu lan nao.

Tai sao khong chi dung `fetched_at`:

- `fetched_at` la timestamp tung item, khong dai dien cho batch.
- Mot batch co nhieu item, moi item co fetched_at khac nhau.
- Human-in-loop can noi "batch ngay X" hon la "timestamp tung request".

Tai sao khong tao crawl_runs table rieng ngay:

- Slice nay uu tien nho va debug duoc.
- Dua `crawl_run_id` vao rows truoc la du cho trace.
- Slice sau co the them table `crawl_runs` de quan ly status/start/end/error counts.

## Chi tiet parser va correctness

### TVPL parser

Mapping code:

- `parse_thuvienphapluat_new_documents`
- `_extract_document_number`
- `SHORT_DOCUMENT_NUMBER_RE`

Bug da gap:

- Live dry-run TVPL tra ve title dang `Quyet dinh 840/QD-TTg nam 2026`.
- Regex ban dau chi bat dang `148/2026/ND-CP`, nen `canonical_number` bi rong.

Fix:

- Them `SHORT_DOCUMENT_NUMBER_RE` de bat dang `840/QD-TTg`, `708/QD-BXD`, `2418/QD-BQP`.
- Them test case tuong ung trong `tests/test_legal_pipeline.py`.

Ket qua live sau fix:

- `840/QĐ-TTg`
- `708/QĐ-BXD`
- `2418/QĐ-BQP`

Gioi han:

- TVPL co dau tieng Viet va nhieu format title; regex chi la heuristic.
- Discovery signal nen chap nhan metadata co the thieu `issue_date`.
- Khong dung TVPL metadata lam canonical write.

### VanBan ChinhPhu listing parser

Mapping code:

- `parse_vanban_chinhphu_listing`
- `_query_value`
- `_best_title_for_listing`
- `_extract_date`

Logic:

- Scan anchor co `docid=` va `pageid=27160`.
- Resolve relative URL bang `urljoin`.
- Lay `docid` lam `external_id`.
- Extract title tu anchor/container.
- Extract `canonical_number` neu co trong text.
- Extract `issue_date` tu date trong listing.
- Dedupe theo URL, uu tien title dai hon.

Ket qua dry-run sample:

```json
{
  "title": "Sửa đổi, bổ sung một số điều của Nghị định số 72/2015/NĐ-CP ...",
  "url": "https://vanban.chinhphu.vn/?pageid=27160&docid=218059&classid=0",
  "external_id": "218059",
  "canonical_number": "148/2026/NĐ-CP",
  "issue_date": "2026-05-12"
}
```

Van de thuc te:

- Co item listing la cong van/van ban hanh chinh khong co so dang `148/2026/NĐ-CP`, parser fallback `canonical_number = docid`.
- Day la pragmatic de van co stable id, nhung slice sau nen phan loai `LegalDocument` vs `AdministrativeDispatch` tot hon.

### VanBan ChinhPhu detail parser

Mapping code:

- `parse_vanban_chinhphu_detail_record`
- `_labeled_fields`
- `_attachment_links`

Logic:

- Remove script/style.
- Lay text lines.
- Extract labeled fields:
  - `So ky hieu`
  - `Ngay ban hanh`
  - `Ngay co hieu luc`
  - `Loai van ban`
  - `Co quan ban hanh`
  - `Trich yeu`
- Normalize date ve ISO `YYYY-MM-DD`.
- Extract attachment links co duoi `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.zip`.
- Tao `LegalDocumentRecord`.
- Tao `doc_id = stable_doc_id(source_id, canonical_number, issue_date, issuer)`.

Tai sao record text gom ca metadata va page text:

- Hien tai chua extract PDF/DOC full content.
- Dua metadata quan trong vao `text` giup retrieval co the tim theo so ky hieu, ngay ban hanh, co quan, trich yeu.
- Day la interim solution, khong thay the full text extraction.

## KG manifest va human-in-loop

Mapping code:

- Crawler tao manifest: `src/crawl_legal_sources.py`
- Version store: `src/pipeline/versioning.py`
- KG dry-run/apply: `src/pipeline/kg_update.py`

Ket qua local write smoke:

```json
{
  "source_id": "vanban_chinhphu",
  "crawl_run_id": "slice2-smoke-20260514",
  "discovered": 3,
  "written": 3,
  "kg_updates": 3,
  "kg_skipped": 0
}
```

Rerun cung `crawl_run_id` va cung data:

```json
{
  "discovered": 3,
  "written": 3,
  "kg_updates": 0
}
```

Y nghia:

- Fetch/write bronze van dien ra, vi bronze la raw fetch event.
- Version store khong tao manifest duplicate neu `doc_id + checksum` khong doi.
- Day la behavior dung cho daily batch.

Bug da gap:

- Khi chay smoke Iceberg voi `--state-path` rieng, local gold JSONL co duplicate exact manifest cho cung `doc_id/checksum/version`.
- Neu apply thang, co rui ro insert lap cung document.

Fix:

- `load_manifest` trong `src/pipeline/kg_update.py` dedupe theo tuple:
  - `doc_id`
  - `checksum`
  - `action`
  - `current_version`
- Van giu cac version khac nhau de xu ly replace/update.

Tai sao dedupe o loader thay vi xoa file manifest:

- Manifest JSONL la audit trail, khong nen silently rewrite/xoa raw operation log trong slice nay.
- Loader la noi gan apply, nen co the bao ve LightRAG khoi duplicate exact update.
- Health check van bao ca raw va unique backlog de minh bach.

## Monitor va observability

Mapping code:

- `src/pipeline_health.py`
- `src/crawl_legal_sources.py` summary fields

Thay doi:

- Crawler summary co:
  - `crawl_run_id`
  - `item_samples`
  - `discovery_only_skipped`
  - `iceberg_silver_appended`
  - `iceberg_silver_skipped`
- Health check co:
  - `kg_update_backlog_raw`
  - `kg_update_backlog_unique`

Van de da gap:

- Windows PowerShell stdout CP1252 crash khi JSON co tieng Viet:
  - Error: `UnicodeEncodeError: 'charmap' codec can't encode character`

Fix:

- Them `_configure_stdout()` trong `src/crawl_legal_sources.py`.
- Them `_configure_stdout()` trong `src/pipeline_health.py`.
- Dung `sys.stdout.reconfigure(encoding="utf-8")` neu runtime support.

Tai sao can fix:

- Pipeline log/monitor co tieng Viet la chuyen binh thuong trong domain phap luat Viet Nam.
- Neu CLI crash luc dry-run chi vi output encoding thi monitoring khong dang tin.

## Test strategy

Mapping tests:

- TVPL parser discovery-only: `tests/test_legal_pipeline.py`, `test_thuvienphapluat_parser_is_discovery_only_metadata`
- VanBan listing parser: `test_vanban_chinhphu_listing_discovers_real_document_items`
- VanBan detail parser: `test_vanban_chinhphu_detail_parse_creates_legal_document_record`
- Manifest dedupe: `test_manifest_loader_deduplicates_exact_update_events`
- Local lakehouse `crawl_run_id`: `test_local_lakehouse_writes_layers`
- Iceberg row `crawl_run_id`: `test_iceberg_rows_are_flat_and_debuggable`

Commands da chay:

```powershell
uv run python -m unittest discover -s tests
uv run python -m compileall src
uv run python src\crawl_legal_sources.py --source-id thuvienphapluat_discovery --since 2026-05-01 --limit 3 --dry-run
uv run python src\crawl_legal_sources.py --source-id vanban_chinhphu --since 2026-05-01 --limit 3 --dry-run
uv run python src\crawl_legal_sources.py --source-id vanban_chinhphu --since 2026-05-01 --limit 3 --write-lakehouse --crawl-run-id slice2-smoke-20260514
uv run python src\crawl_legal_sources.py --source-id thuvienphapluat_discovery --since 2026-05-01 --limit 5 --write-lakehouse --crawl-run-id slice2-tvpl-discovery-20260514
uv run python src\crawl_legal_sources.py --source-id vanban_chinhphu --since 2026-05-01 --limit 2 --write-lakehouse --iceberg --crawl-run-id slice2-iceberg-smoke-20260514 --state-path data\pipeline_state\slice2_iceberg_smoke_versions.json
uv run python src\kg_incremental_update.py --dry-run
uv run python src\lakehouse_validate.py --check-services --check-iceberg
uv run python src\pipeline_health.py
```

Observed verification:

- Unit test: 14 tests OK.
- Compile: OK.
- TVPL dry-run: discovered 3, no write.
- TVPL write mode: discovered 5, `discovery_only_skipped=5`.
- `vanban_chinhphu` dry-run: discovered 3, sample metadata visible.
- `vanban_chinhphu` write: generated 3 KG manifests.
- Re-run same official write: `kg_updates=0`.
- Iceberg write: `iceberg_silver_appended=2`, `kg_updates=2`.
- Health:
  - Neo4j healthy
  - Qdrant healthy
  - Postgres healthy
  - MinIO healthy
  - raw backlog 5
  - unique backlog 3

## Interview defense: tai sao cach nay dung

Neu duoc hoi "tai sao lai dung TVPL neu policy official-first?", cau tra loi:

TVPL duoc dung nhu discovery signal, khong phai canonical source. No giup phat hien nhanh van ban moi va metadata so ky hieu. Full text va KG chinh van lay tu official connector. Cach nay tan dung gia tri thuc te cua nguon uy tin trong cong dong, nhung khong pha provenance/license boundary.

Neu duoc hoi "tai sao chon `vanban.chinhphu.vn` thay vi `vbpl.vn` ngay?", cau tra loi:

Day la vertical slice nho de chung minh crawler correctness end-to-end: official listing -> detail -> LegalDocumentRecord -> versioning -> manifest -> Iceberg. `vbpl.vn` van la source canonical quan trong hon cho coverage, nhung co search/detail structure can parser rieng. Slice nay uu tien mot duong chay that, debug duoc, verify duoc.

Neu duoc hoi "tai sao chua apply LightRAG?", cau tra loi:

Pipeline da sinh manifest va dry-run unique backlog. Apply LightRAG se goi GPT-4o mini va mutate KG/vector store. Theo human-in-loop, dung o manifest review truoc la dung: con nguoi xem source, sample, backlog, roi moi approve apply.

Neu duoc hoi "crawl_run_id giai quyet gi?", cau tra loi:

Nó bien batch thanh mot don vi quan sat duoc. Khong co `crawl_run_id`, khi co loi chi thay nhieu raw files va manifests roi roi rac. Co `crawl_run_id`, ta truy nguoc duoc: batch nao discover item nao, raw artifact nao, silver record nao, manifest nao, va Iceberg row nao.

## Nhung diem con yeu can noi thang

1. Detail parser chua extract noi dung PDF/DOC attachment.

   Hien tai record text co metadata + page text. De retrieval phap luat that su manh, can download attachment va OCR/text extraction.

2. `vanban_chinhphu` listing co the gom ca cong van/van ban hanh chinh khong phai VBQPPL.

   Can them document classification hoac filter theo `Loai van ban`.

3. `doc_id` voi item khong co canonical number dang fallback docid.

   On cho stability, nhung semantic doc_id se kem hon. Can enrich tu detail parse truoc khi commit version state.

4. Manifest raw JSONL co duplicate do smoke voi state path rieng.

   Loader da dedupe unique apply, monitor da tach raw/unique. Ve lau dai nen co applied-state table hoac manifest table co unique constraint.

5. Iceberg schema evolution moi them column don gian.

   `crawl_run_id` duoc add bang `_ensure_column`. Ve production lon hon can migration/versioned schema doc.

## Trang thai san sang truoc khi apply LightRAG

San sang de human review:

- Backlog unique hien co 3 official documents tu `vanban_chinhphu`.
- Moi manifest co `source_url`, `silver_record_path`, `checksum`, `crawl_run_id`.
- `kg_incremental_update.py --dry-run` doc duoc manifest va khong crash.

Chua nen apply hang loat:

- Nen inspect silver records truoc de xem text co du chat luong khong.
- Nen chay apply voi limit nho hoac manifest rieng.
- Nen log result sau apply vao applied-state de tranh apply lai sau restart.

Next slice de production hon:

1. Tao `crawl_runs` manifest/table rieng co status `started/completed/failed`.
2. Them extraction attachment PDF/DOC cho `vanban_chinhphu`.
3. Them `vbpl.vn` connector de tang coverage canonical.
4. Them applied-manifest state cho KG worker.
5. Chay LightRAG apply voi human-approved small batch.
