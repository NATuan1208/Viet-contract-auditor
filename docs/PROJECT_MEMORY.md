# Project Memory (Archived)

Last updated: 2026-05-13

> Archived reference only. Native Codex memory is now the primary project memory source.
> Do not rely on this file as the default session handoff unless explicitly requested by the maintainer.

## Working Agreements
- Communicate with the maintainer in Vietnamese by default.
- For non-trivial tasks, follow a senior workflow: clarify/specify, plan, implement in small slices, verify, review, then close out.
- Use `uv` for Python commands and dependency management. Do not switch to `pip`-driven workflows.

## Product Snapshot
- Project: `viet-contract-auditor`
- Goal: audit Vietnamese contracts and produce Markdown reports with violations, legal references, and suggested fixes.
- Inputs: `.txt`, `.docx`, and PDF via the Streamlit upload flow.
- Outputs: Markdown reports under `reports/final_outputs/`.

## Architecture
- Retrieval stack: LightRAG artifacts plus production storage on Neo4j, Qdrant, and PostgreSQL.
- Runtime pipeline: LangGraph orchestrator with router, preprocessor, retrieval, context validator, audit, critic, and generator agents.
- UI: Streamlit app in `src/ui/streamlit_app.py` with reusable components in `src/ui/components/`.
- Profiles: `production` uses Docker-backed storage; `demo` reads prebuilt artifacts prepared for lightweight runs and Spaces deployment.

## Operational Rules
- PostgreSQL runs on port `5433`, not `55432`.
- Agent runtime should read through the storage layer, not directly from `lightrag_index/` JSON files.
- Keep evaluation and retrieval corpora segregated.
- Preserve the repo's current commit style: Conventional Commits with scopes such as `fix(ui): ...`.

## Key Commands
- `uv sync`
- `docker compose up -d`
- `uv run python src/init_storage.py`
- `uv run python src/run_audit.py <contract> --output <report.md>`
- `uv run streamlit run src/ui/streamlit_app.py`
- `uv run python src/check_storage.py`
- `uv run python src/e2e_eval.py --groundtruth <file>`

## Current Priorities
- Phase 5 UI is present and should remain dual-mode (`production` and `demo`).
- Phase 6 evaluation work is the next major roadmap area.

## Archive Notes
- This file previously served as the primary cross-session handoff.
- Native Codex memory now replaces that role for normal day-to-day workflow.
- Keep this file only as a historical snapshot unless the maintainer explicitly revives file-based memory.
