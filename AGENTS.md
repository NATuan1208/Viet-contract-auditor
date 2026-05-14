# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `src/`. Keep pipeline agents in `src/agents/`, shared runtime logic in `src/core/`, and Streamlit UI code in `src/ui/` with reusable widgets under `src/ui/components/`. ETL and operational entry points include `main.py`, `init_storage.py`, `run_audit.py`, `check_storage.py`, and `e2e_eval.py`.

Data and generated artifacts are separated by purpose: raw legal texts in `data/raw/`, processed chunks in `data/processed/`, prebuilt demo indexes in `lightrag_index/`, sample contracts and ground truth in `result-example/`, and generated reports in `reports/`. Design and flow documentation belongs in `docs/`.

## Build, Test, and Development Commands
- `uv sync`: install project dependencies from `pyproject.toml` and `uv.lock`.
- `docker compose up -d`: start Neo4j, Qdrant, PostgreSQL, and MinIO for the production storage profile.
- `docker compose --profile pipeline up -d`: start the production storage stack plus the daily pipeline scheduler.
- `uv run python src/init_storage.py`: load `lightrag_index/` artifacts into the storage stack.
- `uv run python src/run_audit.py result-example/HDLD/HDLD_ThucHanh_01.docx --output reports/final_outputs/hdld_report.md`: run a CLI audit.
- `uv run streamlit run src/ui/streamlit_app.py`: launch the local UI on `http://localhost:8501`.
- `uv run python src/check_storage.py`: verify database connectivity and migrated records.
- `uv run python src/crawl_legal_sources.py --since 2026-05-01 --dry-run`: discover legal-source updates without writing lakehouse or KG data.
- `uv run python src/lakehouse_validate.py`: validate source registry, local lakehouse folders, and catalog readiness.
- `uv run python src/iceberg_validate.py --init-tables --counts`: create and validate production-local Iceberg tables on PostgreSQL catalog + MinIO warehouse.
- `uv run python src/crawl_legal_sources.py --source-id congbao --since 2026-05-01 --write-lakehouse --iceberg`: write crawler outputs to both local debug lakehouse and Iceberg.
- `uv run python src/kg_incremental_update.py --dry-run`: validate pending LightRAG KG update manifests without mutating storage.
- `KG_UPDATE_APPLY=true uv run python src/kg_update_scheduler.py --once`: apply pending KG manifests once using the shared LightRAG/OpenAI configuration.
- `uv run python src/pipeline_health.py`: check storage ports, lakehouse state, crawler state, and KG update backlog.
- `uv run python src/e2e_eval.py --groundtruth "result-example/HDLD/groundtruth_hdld_01_test copy.json"`: run end-to-end evaluation.

## Coding Style & Naming Conventions
Target Python 3.11. Use 4-space indentation, type hints on new public functions, and `from __future__ import annotations` where the module already uses it. Follow existing naming: `snake_case` for modules, files, functions, and variables; `PascalCase` for classes and dataclasses; uppercase for constants.

Keep functions narrow and place UI-only logic in `src/ui/` rather than agent modules. No formatter or linter is currently wired in `pyproject.toml`, so match the surrounding file style closely.

## Testing Guidelines
There is no dedicated `tests/` package yet. Validate changes with the smallest relevant workflow: `check_storage.py` for infra work, `run_audit.py` for pipeline work, `e2e_eval.py` for scoring logic, and Streamlit smoke testing for UI changes. Add fixtures under `result-example/` and keep ground-truth filenames descriptive, for example `groundtruth_hdld_01.json`.

## Commit & Pull Request Guidelines
Recent history uses Conventional Commits with scopes, for example `fix(ui): ...`, `fix(eval): ...`, and `feat(phase5): ...`. Keep that format: `<type>(<scope>): <summary>`.

PRs should state the affected pipeline area, required environment profile (`production` or `demo`), commands run for verification, and any report or UI evidence. Include screenshots for Streamlit changes and link related issues when relevant.

## Security & Configuration Tips
Start from `.env.example` and never commit real API keys. Treat `lightrag_index/` and `reports/` as generated assets; only update them when the change depends on refreshed indexes or checked-in sample outputs.

## Data Pipeline Rules
Use `config/legal_sources.yml` as the source registry. The default policy is official-first: Tier 0 and Tier 1 government/judicial sources are allowed for canonical KG data, while Tier 2 commercial sources are discovery-only unless a license/API explicitly permits full-text storage.

Do not commit raw crawl output, lakehouse data files, crawler state, or large downloaded legal artifacts. Keep generated pipeline outputs under ignored paths such as `data/lakehouse/` and `data/pipeline_state/`.

Every crawled record must carry provenance: `source_id`, canonical URL, `fetched_at`, checksum, source license note, and the normalized `doc_id`. KG updates must be idempotent: unchanged checksums create no update, changed checksums create a new version and a replace manifest.

For pipeline changes, run the smallest relevant smoke commands: `uv run python src/lakehouse_validate.py`, `uv run python src/kg_incremental_update.py --dry-run`, `uv run python src/pipeline_health.py`, and targeted unit tests when parser/versioning code changes.

## Agent Notes
Communicate with the maintainer in Vietnamese unless they explicitly ask otherwise. At the start of each new session, rely on native Codex memory first before making assumptions about architecture, workflow, or priorities. Treat `docs/PROJECT_MEMORY.md` as archived reference material only; do not use it as the default session memory or keep it updated as part of normal workflow unless the maintainer explicitly asks for file-based memory again. When workflow or project conventions change, prefer updating the native memory rather than maintaining an in-repo handoff file.
