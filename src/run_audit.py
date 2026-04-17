"""CLI entry point for the Viet-Contract Auditor Phase 4 pipeline.

Usage:
    uv run python src/run_audit.py contract.txt
    uv run python src/run_audit.py contract.docx
    uv run python src/run_audit.py contract.txt --output report.md
    uv run python src/run_audit.py contract.txt --verbose

Supported input formats: .txt, .docx
.doc files are not supported — convert to .docx first.

Exit codes:
    0 — success
    1 — input file not found, unreadable, or unsupported format
    2 — pipeline error (state["error"] is set)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Windows fixes — must be set before any asyncio usage
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Reconfigure stdout/stderr to UTF-8 for Vietnamese characters
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

# Ensure src/ is in sys.path when this script is run directly
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Load .env — do NOT validate OPENAI_API_KEY (stubs skip it)
_ROOT = _SRC_DIR.parent
_ENV_PATH = _ROOT / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _stripped = _line.strip()
        if not _stripped or _stripped.startswith("#") or "=" not in _stripped:
            continue
        _key, _val = _stripped.split("=", 1)
        _key = _key.strip()
        _val = _val.strip().strip('"').strip("'")
        if _key and _key not in os.environ:
            os.environ[_key] = _val

from agents.orchestrator import run_audit  # noqa: E402

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        format=LOG_FORMAT,
        level=logging.DEBUG if verbose else logging.INFO,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_audit",
        description="Viet-Contract Auditor — Phase 4 LangGraph pipeline",
    )
    parser.add_argument("contract", type=Path, help="Path to contract file (.txt or .docx)")
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Write Markdown report to this file. Defaults to reports/final_outputs/<contract_name>_report.md",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


async def _main(contract_path: Path, output_path: Path | None, verbose: bool) -> int:
    _setup_logging(verbose)
    logger = logging.getLogger(__name__)

    if not contract_path.exists():
        logger.error("Contract file not found: %s", contract_path)
        return 1

    suffix = contract_path.suffix.lower()

    if suffix == ".doc":
        logger.error(
            "Legacy .doc format is not supported. Convert to .docx first "
            "(File → Save As → Word Document .docx in Microsoft Word)."
        )
        return 1

    try:
        if suffix == ".docx":
            import docx  # python-docx; install with: uv add python-docx
            doc = docx.Document(str(contract_path))
            contract_text = "\n".join(p.text for p in doc.paragraphs)
        else:
            contract_text = contract_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to read contract file: %s", exc)
        return 1

    logger.info("Loaded: %s (%d chars)", contract_path.name, len(contract_text))

    state = await run_audit(contract_text)

    report = state.get("final_report") or "_(no report generated)_"
    pipeline_error = state.get("error")

    if not output_path:
        output_path = Path("reports/final_outputs") / f"{contract_path.name}_report.md"

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        logger.info("Report written to: %s", output_path)
    except Exception as exc:
        logger.error("Failed to write report: %s", exc)
        return 2

    if pipeline_error:
        logger.error("Pipeline error: %s", pipeline_error)
        return 2

    return 0


def main() -> None:
    args = _parse_args()
    sys.exit(asyncio.run(_main(args.contract, args.output, args.verbose)))


if __name__ == "__main__":
    main()
