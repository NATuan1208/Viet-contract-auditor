# Slice 01: Production-local Lakehouse va Incremental KG Manifest

## Muc tieu cua slice

Slice nay bien pipeline tu mot luong xu ly demo thanh mot production-local pipeline co storage ro rang, co provenance, co kha nang chay lai ma khong tao duplicate logical document trong KG.

Muc tieu cu the:

- Giu duoc du lieu raw de audit lai nguon crawl.
- Tao duoc record normalized cho van ban phap luat.
- Tao duoc manifest cap nhat KG theo co che incremental.
- Chay production-local bang Docker Compose voi PostgreSQL, MinIO, Neo4j, Qdrant.
- Dua Iceberg vao nhu table format that tren MinIO, thay vi chi ghi JSON local.

Ket qua hien tai: Iceberg write da hoat dong. Lan validate gan nhat co:

- `legal.bronze_raw_artifacts`: 26 rows
- `legal.silver_legal_documents`: 14 rows
- `legal.gold_kg_update_manifest`: 2 rows

## Van de ban dau

He thong MAS/LightRAG ban dau da co Neo4j, Qdrant va PostgreSQL phuc vu retrieval/KG, nhung phan data pipeline cap nhat hang ngay chua co cac thuoc tinh can cho mot RAG phap luat co the defend:

- Khong co source registry chuan hoa de noi ro nguon nao la canonical, nguon nao chi dung discovery.
- Khong co bronze/silver/gold layer de truy vet tu raw response den record dua vao KG.
- Khong co versioning theo checksum nen kho phan biet "van ban moi", "van ban khong doi", va "van ban da bi sua".
- Cong bao RSS ban dau chi lay duoc issue/feed item, nhung chua phan biet ro issue voi legal document.
- Iceberg production write chua hoat dong, nen "lakehouse" moi chi la y tuong hoac local JSON debug.

Trong phong van, diem can defend la: RAG phap luat khong chi can tra loi dung, ma can biet du lieu den tu dau, tai sao duoc dua vao KG, va khi nguon doi thi update nhu the nao.

## Quyet dinh thiet ke

### 1. Chon official-first thay vi crawl tat ca nguon co the crawl

Chinh sach duoc encode trong `config/legal_sources.yml`:

- `policy.canonical_mode: official_first`
- `commercial_sources: discovery_only`
- `provenance_required: true`

Mapping code:

- Source registry va validation: `src/pipeline/registry.py`
- Config source: `config/legal_sources.yml`
- Rule discovery-only khong duoc co `detail`: `src/pipeline/registry.py` trong `validate_sources`

Ly do chon:

- Van ban phap luat la domain co rui ro cao: sai nguon, sai hieu luc, hoac crawl noi dung khong co license deu co the lam KG sai.
- Nguon nha nuoc la canonical cho KG chinh.
- Nguon thuong mai nhu Thu Vien Phap Luat/LuatVietnam co gia tri discovery/cross-check metadata, nhung khong nen dua full text vao lakehouse/KG khi chua co API/license ro.

Tai sao khong chon crawl full text tu nguon thuong mai:

- Ve ky thuat thi de hon vi UI cua cac trang nay thuong co danh sach van ban rat tot.
- Nhung ve defend/production thi yeu: khong ro license, co the violate ToS, va kho giai thich provenance canonical.
- Phu hop hon la dung chung nhu "discovery signal", sau do resolve sang nguon official.

### 2. Chon bronze/silver/gold lakehouse thay vi ghi truc tiep vao KG

Mapping code:

- Model chung: `src/pipeline/models.py`
  - `SourceItem`
  - `RawArtifact`
  - `LegalDocumentRecord`
  - `KGUpdateManifest`
- Local debug writer: `src/pipeline/lakehouse.py`
  - `write_bronze`
  - `write_silver`
  - `append_gold_manifest`
- Iceberg writer: `src/pipeline/iceberg_lakehouse.py`
  - `append_bronze`
  - `append_silver`
  - `append_gold_manifest`

Vai tro tung layer:

| Layer | Noi dung | Vai tro defend |
|---|---|---|
| Bronze | Raw HTML/PDF/DOC/RSS body, response headers, checksum, fetched_at | Bang chung goc. Co the audit lai parser va provenance |
| Silver | Text/metadata normalized, article split, doc_id, source_url | Dau vao sach cho retrieval/KG |
| Gold | KG update manifest, relation/entity seed trong cac slice sau | Dieu phoi incremental KG update |

Tai sao khong insert thang vao LightRAG:

- Khi crawl loi hoac parse sai, se rat kho rollback vi KG/vector store da bi mutate.
- Khong co audit trail de tra loi "chunk nay den tu raw file nao".
- Khong co co che dry-run/human-in-loop truoc khi apply.

Phuong an hien tai hop ly vi cho phep:

- Crawl va parse truoc.
- Review manifest sau.
- Apply KG sau khi nguoi van hanh duyet.

### 3. Chon MinIO + Iceberg + PostgreSQL SQL catalog cho production-local

Mapping code:

- Docker MinIO: `docker-compose.yml`, service `minio`
- PyIceberg writer: `src/pipeline/iceberg_lakehouse.py`
- Iceberg validation CLI: `src/iceberg_validate.py`
- Lakehouse validation CLI: `src/lakehouse_validate.py`

Ly do chon:

- MinIO mo phong object storage production ma van chay local.
- Iceberg la table format phu hop lakehouse: co schema, metadata, append table, evolve schema.
- PostgreSQL SQL catalog dung lai infra san co, giam chi phi van hanh production-local.

Tai sao khong chi dung file JSON:

- JSON local rat tot cho debug, nhung yeu khi scale va query.
- Khong co table schema/evolution.
- Kho tich hop voi analytical tooling sau nay.

Tai sao khong dung cloud managed lakehouse:

- Do la do an/local production, can chay duoc tren may dev bang Docker Compose.
- Cloud lam tang chi phi va tang phu thuoc moi truong.

## Logic versioning va incremental KG

Mapping code:

- Stable version state: `src/pipeline/versioning.py`
- Manifest model: `src/pipeline/models.py`
- KG loader/apply: `src/pipeline/kg_update.py`
- CLI crawler tao manifest: `src/crawl_legal_sources.py`

Co che:

1. Parser tao `LegalDocumentRecord` co:
   - `doc_id`
   - `checksum`
   - `source_url`
   - `fetched_at`
2. `DocumentVersionStore.plan_update(record)` so sanh record moi voi state cu.
3. Neu `doc_id` chua ton tai:
   - Tao manifest `action = insert`
   - `current_version = 1`
4. Neu `doc_id` ton tai va checksum khong doi:
   - Return `None`
   - Khong tao manifest moi
5. Neu `doc_id` ton tai nhung checksum doi:
   - Tao manifest `action = replace`
   - Ghi `previous_version`
   - Ghi `supersedes_checksum`

Ly do dung checksum:

- Don gian, deterministic, phu hop slice nho.
- Neu raw/detail content khong doi, pipeline idempotent.
- Neu noi dung doi, he thong co tin hieu ro de replace KG.

Trade-off:

- Checksum raw HTML co the doi vi layout/quang cao/tracking, khong nhat thiet noi dung phap ly doi.
- Slice hien tai chap nhan vi dang uu tien correctness/audit. Slice sau nen checksum normalized legal text hoac canonical attachment text de giam false positive.

## Iceberg write da hoat dong nhu the nao

Mapping code:

- `IcebergLakehouse.from_registry_config`: doc cau hinh catalog/MinIO tu `legal_sources.yml`
- `_local_catalog_uri_if_needed`: fallback tu `postgres:5432` sang `127.0.0.1:5433` khi chay CLI tu host
- `_local_endpoint_if_needed`: fallback tu `minio:9000` sang `127.0.0.1:9000`
- `ensure_tables`: tao namespace/table neu chua co
- `append_bronze`, `append_silver`, `append_gold_manifest`: ghi rows vao Iceberg

Van de da gap:

### Van de 1: service hostname trong Docker khac hostname khi chay CLI tu host

Trong Compose, service goi nhau bang `postgres:5432` va `minio:9000`. Nhung CLI chay tren host Windows phai dung `127.0.0.1:5433` va `127.0.0.1:9000`.

Fix:

- Them fallback TCP trong `src/pipeline/iceberg_lakehouse.py`.
- Neu hostname Docker khong connect duoc tu host, thay bang local port mapping.

Tai sao hop ly:

- Cung mot config van chay duoc trong container va tren host.
- Khong can tao hai file config rieng cho dev/compose.

### Van de 2: Bronze bi tang row khi rerun

Ket qua thuc te:

- Chay lai cung batch lam `bronze_raw_artifacts` tang.
- `silver_legal_documents` khong tang duplicate neu `doc_id + checksum` da co.

Day la thiet ke co chu dich:

- Bronze la raw fetch event log, moi fetch deu co gia tri audit.
- Silver la logical normalized document, can idempotent theo document/checksum.

Rui ro:

- Ve lau dai bronze can `crawl_run_id`, partition, va retention policy.
- Slice 2 da them `crawl_run_id` de giai quyet phan observability nay.

### Van de 3: Cong bao RSS khong phai luc nao cung la legal document

Ket qua slice 1:

- Cong bao RSS lay duoc 12 item.
- Nhung tat ca la `OfficialGazetteIssue`, khong dua vao KG.
- `kg_updates = 0`, `kg_skipped = 12`.

Fix trong logic:

- Parser set `document_type = OfficialGazetteIssue` cho feed `cong_bao_moi_dang`.
- Crawler chi tao KG manifest neu `record.document_type == "LegalDocument"`.

Tai sao hop ly:

- Mot so cong bao la container issue, khong phai van ban phap ly don le.
- Dua thang issue vao KG se lam retrieval bi nhieu va metadata sai.
- Can slice rieng de parse inner documents hoac crawl official fulltext source khac.

## Cau truc Docker Compose production-local

Mapping code:

- Existing services:
  - Neo4j
  - Qdrant
  - PostgreSQL
- Them:
  - `minio`
  - `pipeline-worker`
  - `kg-update-worker`

Vai tro:

| Service | Vai tro |
|---|---|
| PostgreSQL | LightRAG storage + Iceberg SQL catalog |
| MinIO | Object store cho Iceberg warehouse |
| Neo4j | Graph/KG storage |
| Qdrant | Vector storage |
| pipeline-worker | Scheduler/crawler batch |
| kg-update-worker | Apply KG manifest, dry-run by default |

Gia tri cua profile `pipeline`:

- UI/audit khong tu crawl.
- Pipeline co the bat/tat rieng.
- Giam rui ro khi demo: app doc du lieu da co, crawler la workflow van hanh rieng.

## Cach verify slice 1

Commands da chay:

```powershell
uv run python -m unittest discover -s tests
uv run python -m compileall src
uv run python src\iceberg_validate.py --init-tables --counts
uv run python src\crawl_legal_sources.py --source-id congbao --since 2026-05-01 --write-lakehouse --iceberg
uv run python src\lakehouse_validate.py --check-services --check-iceberg
uv run python src\pipeline_health.py
uv run python src\kg_incremental_update.py --dry-run
```

Observed behavior:

- Unit tests pass.
- Docker services healthy.
- MinIO reachable.
- Iceberg table counts non-zero after write.
- Silver dedupes on rerun.
- Gold manifest for Cong bao remained 0 because issue feed was correctly not treated as legal document.

## Interview defense: tai sao slice nay hop ly

Neu duoc hoi "vi sao lam phuc tap nhu lakehouse/Iceberg khi chi can RAG?", cau tra loi nen la:

RAG phap luat can reproducibility va provenance. Neu chi insert thang vao vector store, ta khong co audit trail khi cau tra loi sai. Bronze/Silver/Gold chia ro giai doan: raw source, normalized legal record, va KG update intent. Iceberg/MinIO giup cai nay khong chi la folder debug ma la table layer co schema cho production-local.

Neu duoc hoi "co over-engineered khong?", cau tra loi nen la:

Co kha nang over-engineer neu muc tieu chi la demo mot lan. Nhung voi muc tieu daily legal RAG va defend provenance, thiet ke nay hop ly. Phan duoc kiem soat de khong over-engineer la: local JSON writer van giu cho debug, Iceberg chi co 3 flat tables, metadata complex de JSON string trong slice dau, chua lam relation extraction phuc tap.

Neu duoc hoi "diem yeu hien tai la gi?", cau tra loi nen la:

- Bronze append moi fetch nen can retention/crawl_run_id.
- Silver dedupe dang scan table, chua scale.
- Cong bao issue chua parse inner documents.
- Checksum raw HTML co the false positive neu layout doi.

## Trang thai sau slice 1

Da dat:

- Source registry official-first.
- Lakehouse 3 layer local debug.
- Iceberg production-local write tren MinIO/PostgreSQL.
- Incremental manifest insert/replace theo checksum.
- Docker Compose co MinIO va worker profiles.

Chua dat:

- Chua co crawler official fulltext tot de tao KG manifest thuc te tu van ban moi.
- Chua apply KG thuc vao LightRAG.
- Chua extract attachment PDF/DOC thanh legal text chuan.

Ly do chuyen sang slice 2:

Slice 1 chung minh storage va manifest logic chay. Nhung crawler correctness con thieu: can lay danh sach van ban that, khong phai chi issue feed. Do do slice 2 tap trung vao connector dung nguon va observability bang `crawl_run_id`.
