"""Recommend next benchmark calibration steps from A/B diagnosis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_calibration(summary: dict[str, Any], diagnosis: dict[str, Any]) -> dict[str, Any]:
    comparison = diagnosis.get("variant_comparison") or {}
    loss_points = {
        item.get("likely_loss_point")
        for item in diagnosis.get("diagnoses") or []
    }
    results = list(summary.get("results") or [])

    recommendation = {
        "decision": "hold_threshold",
        "recommended_env": {
            "LIGHTRAG_MIN_RERANK_SCORE": "0.0",
        },
        "rationale": [
            "Do not calibrate min_rerank_score until expected-law hit labels exist.",
        ],
        "next_benchmark_command_flags": [],
    }

    if comparison.get("rerank_effect") == "rerank_reduced_recall":
        recommendation["decision"] = "increase_candidate_pool_before_threshold"
        recommendation["recommended_env"].update({
            "LIGHTRAG_QUERY_TOP_K": "30",
            "LIGHTRAG_CHUNK_TOP_K": "40",
            "LIGHTRAG_CONTEXT_MAX_CHARS": "1500",
        })
        recommendation["rationale"].append(
            "Rerank reduced recall in A/B; first-stage candidate pool is the safer knob."
        )
        recommendation["next_benchmark_command_flags"].append("--include-sweep")
    elif "retrieval_context_quality" in loss_points:
        recommendation["decision"] = "widen_retrieval_candidates"
        recommendation["recommended_env"].update({
            "LIGHTRAG_QUERY_TOP_K": "30",
            "LIGHTRAG_CHUNK_TOP_K": "40",
            "LIGHTRAG_CONTEXT_MAX_CHARS": "1500",
        })
        recommendation["rationale"].append("Context validator indicates retrieval quality loss.")
        recommendation["next_benchmark_command_flags"].append("--include-sweep")
    elif "critic_admissibility_prune" in loss_points:
        recommendation["decision"] = "debug_critic_before_retrieval_tuning"
        recommendation["rationale"].append(
            "Critic pruning is visible while context quality is acceptable; tune pruning before retrieval thresholds."
        )
    elif comparison.get("rerank_effect") == "rerank_neutral_on_recall":
        recommendation["decision"] = "add_expected_law_hit_labels"
        recommendation["rationale"].append(
            "A/B recall is neutral; add retrieval-level labels before changing ranker settings."
        )

    recommendation["observed_variants"] = [
        {
            "variant": result.get("variant"),
            "env": result.get("env") or {},
            "metrics": result.get("metrics") or {},
            "trace_summary": result.get("trace_summary") or {},
        }
        for result in results
    ]
    return recommendation


def build_calibration_markdown(calibration: dict[str, Any]) -> str:
    lines = [
        "# Benchmark Calibration Recommendation",
        "",
        f"- Decision: {calibration.get('decision')}",
        "",
        "## Recommended Env",
    ]
    for key, value in (calibration.get("recommended_env") or {}).items():
        lines.append(f"- `{key}={value}`")

    lines += ["", "## Rationale"]
    for item in calibration.get("rationale") or []:
        lines.append(f"- {item}")

    flags = calibration.get("next_benchmark_command_flags") or []
    if flags:
        lines += ["", "## Next Benchmark Flags"]
        for flag in flags:
            lines.append(f"- `{flag}`")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recommend rerank benchmark calibration")
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--diagnosis", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    diagnosis = json.loads(args.diagnosis.read_text(encoding="utf-8"))
    calibration = build_calibration(summary, diagnosis)
    out_json = args.out_json or args.summary.with_name("calibration.json")
    out_md = args.out_md or args.summary.with_name("calibration.md")
    out_json.write_text(json.dumps(calibration, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(build_calibration_markdown(calibration), encoding="utf-8")
    print(f"Calibration JSON written: {out_json}")
    print(f"Calibration Markdown written: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
