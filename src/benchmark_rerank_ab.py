"""Run same-groundtruth A/B benchmark variants for LightRAG rerank.

The runner executes `e2e_eval.py` repeatedly with isolated output paths and
per-variant environment knobs. It is intentionally thin: benchmark runs are
expensive, so tests validate command/env construction without calling OpenAI.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@dataclass(frozen=True)
class BenchmarkVariant:
    name: str
    env: dict[str, str]


DEFAULT_VARIANTS = [
    BenchmarkVariant(
        name="baseline_no_rerank",
        env={
            "LIGHTRAG_RERANK_ENABLED": "false",
            "LIGHTRAG_MIN_RERANK_SCORE": "0.0",
            "LIGHTRAG_QUERY_TOP_K": "10",
            "LIGHTRAG_CHUNK_TOP_K": "20",
            "LIGHTRAG_CONTEXT_MAX_CHARS": "1000",
        },
    ),
    BenchmarkVariant(
        name="rerank_default",
        env={
            "LIGHTRAG_RERANK_ENABLED": "true",
            "LIGHTRAG_MIN_RERANK_SCORE": "0.0",
            "LIGHTRAG_QUERY_TOP_K": "10",
            "LIGHTRAG_CHUNK_TOP_K": "20",
            "LIGHTRAG_CONTEXT_MAX_CHARS": "1000",
        },
    ),
]

SWEEP_VARIANTS = [
    BenchmarkVariant(
        name="rerank_wide_30_40",
        env={
            "LIGHTRAG_RERANK_ENABLED": "true",
            "LIGHTRAG_MIN_RERANK_SCORE": "0.0",
            "LIGHTRAG_QUERY_TOP_K": "30",
            "LIGHTRAG_CHUNK_TOP_K": "40",
            "LIGHTRAG_CONTEXT_MAX_CHARS": "1500",
        },
    ),
    BenchmarkVariant(
        name="rerank_wide_50_60",
        env={
            "LIGHTRAG_RERANK_ENABLED": "true",
            "LIGHTRAG_MIN_RERANK_SCORE": "0.0",
            "LIGHTRAG_QUERY_TOP_K": "50",
            "LIGHTRAG_CHUNK_TOP_K": "60",
            "LIGHTRAG_CONTEXT_MAX_CHARS": "2000",
        },
    ),
]


def build_run_id(prefix: str = "rerank-ab") -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def build_variant_command(
    variant: BenchmarkVariant,
    groundtruth: Path,
    run_dir: Path,
    contract: Path | None = None,
) -> tuple[list[str], dict[str, str], dict[str, Path]]:
    variant_dir = run_dir / variant.name
    paths = {
        "report": variant_dir / "report.md",
        "eval": variant_dir / "eval.md",
        "state": variant_dir / "state.json",
        "trace": variant_dir / "retrieval_trace.jsonl",
        "stdout": variant_dir / "stdout.log",
    }

    cmd = [
        sys.executable,
        str(SRC / "e2e_eval.py"),
        "--groundtruth",
        str(groundtruth),
        "--report-out",
        str(paths["report"]),
        "--eval-out",
        str(paths["eval"]),
        "--state-out",
        str(paths["state"]),
    ]
    if contract is not None:
        cmd.extend(["--contract", str(contract)])

    env = {
        **os.environ,
        **variant.env,
        "LIGHTRAG_BENCHMARK_TRACE_ENABLED": "true",
        "LIGHTRAG_BENCHMARK_TRACE_PATH": str(paths["trace"]),
    }
    return cmd, env, paths


def run_variant(
    variant: BenchmarkVariant,
    groundtruth: Path,
    run_dir: Path,
    contract: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    cmd, env, paths = build_variant_command(variant, groundtruth, run_dir, contract)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "variant": variant.name,
        "env": variant.env,
        "paths": {key: str(path) for key, path in paths.items()},
        "command": cmd,
        "dry_run": dry_run,
    }
    if dry_run:
        result["returncode"] = None
        return result

    completed = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    paths["stdout"].write_text(completed.stdout or "", encoding="utf-8")

    result["returncode"] = completed.returncode
    result["metrics"] = parse_eval_metrics(paths["eval"]) if paths["eval"].exists() else {}
    result["state_summary"] = parse_state_summary(paths["state"]) if paths["state"].exists() else {}
    result["trace_summary"] = parse_trace_summary(paths["trace"]) if paths["trace"].exists() else {}
    return result


def parse_eval_metrics(eval_path: Path) -> dict[str, float | int]:
    text = eval_path.read_text(encoding="utf-8")
    metrics: dict[str, float | int] = {}
    patterns = {
        "predicted_violations": r"Predicted violations:\s*(\d+)",
        "groundtruth_vulnerabilities": r"Groundtruth vulnerabilities:\s*(\d+)",
        "precision": r"Precision \(heuristic\):\s*([0-9.]+)",
        "recall": r"Recall \(heuristic\):\s*([0-9.]+)",
        "f1": r"F1 \(heuristic\):\s*([0-9.]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if not match:
            continue
        value = match.group(1)
        metrics[key] = int(value) if value.isdigit() else float(value)
    return metrics


def parse_state_summary(state_path: Path) -> dict[str, Any]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    feedback = state.get("critic_feedback") or {}
    return {
        "context_quality": state.get("context_quality"),
        "context_quality_score": state.get("context_quality_score"),
        "context_retry_count": state.get("context_retry_count"),
        "error_type": state.get("error_type"),
        "confidence": state.get("confidence"),
        "audit_findings": len(state.get("audit_findings") or []),
        "retrieved_clause_indices": state.get("retrieved_clause_indices") or [],
        "critic_findings_pruned": feedback.get("findings_pruned", 0),
        "critic_admissibility_reason": feedback.get("admissibility_reason", ""),
    }


def parse_trace_summary(trace_path: Path) -> dict[str, Any]:
    if not trace_path.exists():
        return {"events": 0}
    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rerank_events = [event for event in events if event.get("event_type") == "rerank"]
    query_events = [event for event in events if event.get("event_type") == "query"]
    candidate_counts = [int(event.get("candidate_count", 0) or 0) for event in rerank_events]
    score_p50s = [
        float((event.get("score_summary") or {}).get("p50"))
        for event in rerank_events
        if (event.get("score_summary") or {}).get("p50") is not None
    ]
    score_maxes = [
        float((event.get("score_summary") or {}).get("max"))
        for event in rerank_events
        if (event.get("score_summary") or {}).get("max") is not None
    ]
    return {
        "events": len(events),
        "query_events": len(query_events),
        "rerank_events": len(rerank_events),
        "rerank_candidate_min": min(candidate_counts) if candidate_counts else None,
        "rerank_candidate_max": max(candidate_counts) if candidate_counts else None,
        "rerank_score_p50_avg": (sum(score_p50s) / len(score_p50s)) if score_p50s else None,
        "rerank_score_max_avg": (sum(score_maxes) / len(score_maxes)) if score_maxes else None,
    }


def write_summary(run_dir: Path, results: list[dict[str, Any]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_json = {
        "run_dir": str(run_dir),
        "results": results,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "summary.md").write_text(build_summary_markdown(results), encoding="utf-8")


def build_summary_markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Rerank A/B Benchmark Summary",
        "",
        "| Variant | Return | Precision | Recall | F1 | Pred | GT | Pruned | Rerank events |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        metrics = result.get("metrics") or {}
        state = result.get("state_summary") or {}
        trace = result.get("trace_summary") or {}
        lines.append(
            "| {variant} | {returncode} | {precision} | {recall} | {f1} | {pred} | {gt} | {pruned} | {rerank_events} |".format(
                variant=result.get("variant"),
                returncode=result.get("returncode"),
                precision=_fmt(metrics.get("precision")),
                recall=_fmt(metrics.get("recall")),
                f1=_fmt(metrics.get("f1")),
                pred=metrics.get("predicted_violations", ""),
                gt=metrics.get("groundtruth_vulnerabilities", ""),
                pruned=state.get("critic_findings_pruned", ""),
                rerank_events=trace.get("rerank_events", ""),
            )
        )
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return ""
    return str(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LightRAG rerank A/B benchmark")
    parser.add_argument("--groundtruth", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/benchmarks"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-sweep",
        action="store_true",
        help="Also run wider candidate-pool variants; keeps min_rerank_score=0.0.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = args.run_id or build_run_id()
    run_dir = args.out_dir / run_id

    variants = list(DEFAULT_VARIANTS)
    if args.include_sweep:
        variants.extend(SWEEP_VARIANTS)

    results = [
        run_variant(
            variant=variant,
            groundtruth=args.groundtruth,
            contract=args.contract,
            run_dir=run_dir,
            dry_run=args.dry_run,
        )
        for variant in variants
    ]
    write_summary(run_dir, results)

    print(f"Benchmark summary written: {run_dir / 'summary.md'}")
    print(f"Benchmark JSON written: {run_dir / 'summary.json'}")

    failures = [result for result in results if result.get("returncode") not in {0, None}]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
