# Slice 03: Official Attachment Extraction va OCR Fallback

## Muc tieu cua slice

Slice nay xu ly hai ton dong sau slice 2:

- `vanban_chinhphu` da parse HTML detail va lay attachment URL, nhung chua extract noi dung PDF/DOC.
- TVPL van giu dung policy discovery-only, nen can mot cach hop le de co full text ma khong can license/API thuong mai.

Huong giai quyet: khong lay full text tu TVPL. TVPL chi la discovery signal. Full text duoc lay tu file dinh kem official cua `vanban.chinhphu.vn`, sau do extract text local bang parser/OCR.

Ket qua hien tai:

- PDF/DOCX attachment official duoc download va ghi Bronze.
- DOCX extract bang `python-docx`.
- PDF text layer extract bang `pypdf`.
- PDF scan/no text layer fallback sang Apache Tika full image + Tesseract OCR.
- Silver record duoc merge text attachment va checksum composite, de KG manifest thay doi khi attachment content thay doi.
- Smoke OCR official da extract duoc `36614` ky tu tu `5994_btc.pdf`.

## Tai sao khong crawl full text TVPL

TVPL co gia tri thuc te cao de phat hien van ban moi, nhung khong phu hop lam canonical full-text source khi chua co license/API.

Neu crawl full text TVPL:

- Kho defend ve license va ToS.
- KG chinh khong con official-first.
- Khi co tranh chap noi dung, provenance khong du manh bang nguon nha nuoc.

Best practice duoc chon:

1. TVPL/LuatVietnam discovery-only.
2. Resolve theo so ky hieu/ngay ban hanh sang official source.
3. Download file official attachment.
4. Extract text local.
5. Dua text official vao Silver/KG voi provenance den URL official.

## Thay doi chinh trong code

### Module attachment extraction

File moi:

- `src/pipeline/attachment_extraction.py`

Thanh phan chinh:

- `AttachmentExtractionResult`: ket qua extract moi attachment.
- `attachment_item`: tao `SourceItem` cho attachment, gan `parent_doc_id`.
- `fetch_attachment`: download file official, co whitelist host.
- `extract_attachment_text`: route extraction theo dinh dang.
- `merge_attachment_extractions`: merge text attachment vao `LegalDocumentRecord`.

### Crawler opt-in

File:

- `src/crawl_legal_sources.py`

Flags moi:

```powershell
--extract-attachments
--attachment-limit 1
--min-attachment-chars 200
--tika-url http://127.0.0.1:9998
```

Ly do opt-in:

- Download/OCR attachment cham hon crawl HTML.
- OCR co chi phi CPU cao.
- Human-in-loop can chay smoke nho truoc khi batch lon.

### Docker Compose OCR service

File:

- `docker-compose.yml`

Service moi:

- `tika`
- image: `apache/tika:latest-full`
- port: `9998`
- profiles: `pipeline`, `ocr`

Ly do chon Tika full:

- Tika la tool pho bien cho document extraction nhieu format.
- Full image co them dependencies cho Tesseract/GDAL.
- Chay nhu service rieng, khong lam Python app phinh to vi OCR dependencies.

### Monitor

File:

- `src/pipeline_health.py`

Them port check:

- `tika: 127.0.0.1:9998`

## Extraction strategy

### PDF

1. Thu `pypdf` de lay text layer.
2. Neu text < `min_attachment_chars`, danh dau `needs_ocr`.
3. Neu co `--tika-url`, fallback sang Tika OCR:
   - endpoint: `/tika`
   - header: `X-Tika-OCRLanguage: vie+eng`
   - header: `X-Tika-PDFOcrStrategy: ocr_only`

Ly do:

- Van ban PDF official co hai loai: PDF digital co text layer, va PDF scan/ky so khong co text layer.
- Dung pypdf truoc nhanh va deterministic.
- Chi OCR khi can, de tranh ton CPU.

### DOCX

Dung `python-docx`.

Ly do:

- Dependency da co san trong repo.
- Phu hop voi `.docx`.
- Co the extract paragraph va table text.

### DOC/RTF legacy

Neu co Tika URL:

- Dung Tika legacy Office extraction.

Neu khong co Tika URL:

- Danh dau `unsupported`.

Ly do:

- `.doc` binary legacy khong nen hand-roll parser.
- LibreOffice/Tika la huong production-local hop ly hon.

## Provenance va checksum

Attachment duoc tao thanh `RawArtifact` rieng:

- `source_item.metadata.artifact_kind = attachment`
- `parent_doc_id`
- `parent_source_url`
- `attachment_index`

Bronze path tiep tuc co `crawl_run_id`:

```text
data/lakehouse/bronze/<source>/<date>/<crawl_run_id>/<checksum>.bin
```

Silver record duoc merge:

- `record.text` them section `NOI DUNG FILE DINH KEM CHINH THUC`.
- `metadata.attachment_extraction` luu method/status/char_count/checksum.
- `record.checksum` duoc doi thanh checksum composite cua HTML checksum + attachment checksum.

Tai sao doi checksum:

- Neu HTML detail khong doi nhung file PDF official doi, KG van phai biet de tao version moi.
- Neu chi dung checksum HTML, update attachment se bi missed.

## Bug va cach fix

### 1. PDF official khong co text layer

Smoke ban dau voi `5994_btc.pdf`:

- `pypdf_text_layer`
- `char_count = 0`
- `status = needs_ocr`

Day khong phai bug parser. Day la thuc te PDF scan/signed.

Fix:

- Them Tika OCR fallback.
- Chay `docker-compose --profile ocr up -d tika`.
- Smoke voi `--tika-url http://127.0.0.1:9998`.

Ket qua:

- `attachments_attempted = 1`
- `attachments_extracted = 1`
- `attachments_needing_review = 0`
- `method = tika_ocr`
- `char_count = 36614`

### 2. Attachment URL la external input

Rui ro:

- HTML official co the co link ngoai domain.
- Neu downloader fetch mu, co SSRF/data leakage risk.

Fix:

- `fetch_attachment(..., allowed_hosts=...)` validate hostname.
- Crawler pass `set(source.domain_whitelist)`.
- `config/legal_sources.yml` them `datafiles.chinhphu.vn` vao whitelist cua `vanban_chinhphu`.

### 3. Metadata detail bi sidebar overwrite

Bug live:

- Parser lay `Cơ quan ban hành` dung trong detail la `Bộ Tài chính`.
- Nhung sau do sidebar co filter `Cơ quan ban hành -> Quốc hội`.
- `_labeled_fields` overwrite field bang gia tri cuoi, lam `issuer = Quốc hội`.

Fix:

- Doi `_labeled_fields` sang `setdefault`, giu gia tri dau tien cua detail block.
- Them test regression trong `tests/test_legal_pipeline.py`.

Ket qua smoke sau fix:

- `issuer = Bộ Tài chính`.

## Verification da chay

```powershell
uv run python -m unittest discover -s tests
uv run python -m compileall src
docker-compose config --quiet
docker-compose --profile ocr up -d tika
docker-compose ps tika
uv run python src\crawl_legal_sources.py --source-id vanban_chinhphu --since 2026-05-01 --limit 1 --write-lakehouse --extract-attachments --attachment-limit 1 --min-attachment-chars 200 --tika-url http://127.0.0.1:9998 --crawl-run-id slice3-tika-ocr-smoke2-20260514 --state-path data\pipeline_state\slice3_tika_ocr_smoke2_versions.json
uv run python src\pipeline_health.py
```

Observed:

- Unit tests: 18 OK.
- Compile: OK.
- Tika service: healthy.
- Tika version: Apache Tika 3.3.0.
- OCR smoke: extracted 1 attachment, 0 needs_review.
- Health now includes `tika` port 9998.

## Gioi han hien tai

- OCR output tu Tesseract/Tika van co loi dau tieng Viet va layout/table noise.
- HTML detail text van con navigation/footer noise; attachment text moi la nguon chinh nen can chunking/filter sau.
- Tika OCR cham hon text-layer extraction, nen can batch limit va timeout.
- Chua co applied-state rieng cho KG worker; van dung manifest dry-run/human-in-loop.

## Next slice nen lam

1. Clean Silver text:
   - Loai nav/footer HTML.
   - Uu tien attachment text khi extraction thanh cong.
2. Them OCR quality metrics:
   - Vietnamese character ratio.
   - line noise ratio.
   - minimum legal keyword count.
3. Them KG apply small batch:
   - Chon manifest tu OCR-extracted official docs.
   - Apply vao LightRAG voi dry-run -> apply.
4. Them manifest applied-state:
   - Tranh apply lai manifest da apply.
