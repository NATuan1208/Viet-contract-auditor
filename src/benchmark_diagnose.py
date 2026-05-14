"""Diagnose where E2E recall is lost from benchmark outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def diagnose_result(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics") or {}
    state = result.get("state_summary") or {}
    trace = result.get("trace_summary") or {}
    env = result.get("env") or {}

    recall = _float(metrics.get("recall"))
    pred = _int(metrics.get("predicted_violations"))
    gt = _int(metrics.get("groundtruth_vulnerabilities"))
    pruned = _int(state.get("critic_findings_pruned"))
    context_score = _float(state.get("context_quality_score"))
    context_quality = str(state.get("context_quality") or "")
    rerank_enabled = str(env.get("LIGHTRAG_RERANK_ENABLED", "")).lower() == "true"
    rerank_events = _int(trace.get("rerank_events"))

    findings: list[str] = []
    likely_loss_point = "unknown"

    if result.get("returncode") not in {0, None}:
        likely_loss_point = "run_failed"
        findings.append("Benchmark variant failed before reliable diagnosis.")
    elif rerank_enabled and rerank_events == 0:
        likely_loss_point = "rerank_not_exercised"
        findings.append("Rerank is enabled in env but no rerank trace events were recorded.")
    elif context_quality and context_quality != "good":
        likely_loss_point = "retrieval_context_quality"
        findings.append(f"Context validator returned {context_quality}.")
    elif context_score is not None and context_score < 0.6:
        likely_loss_point = "retrieval_context_quality"
        findings.append(f"Context quality score is low ({context_score:.3f}).")
    elif pruned > 0 and pred < gt:
        likely_loss_point = "critic_admissibility_prune"
        findings.append(f"Critic pruned {pruned} findings while final prediction count is below GT.")
    elif pred == 0 and gt > 0:
        likely_loss_point = "audit_generation"
        findings.append("No final findings were produced despite non-empty ground truth.")
    elif recall is not None and recall < 0.5:
        likely_loss_point = "audit_or_retrieval_gap"
        findings.append("Recall is low, but available summary does not prove whether retrieval or audit caused it.")
    else:
        likely_loss_point = "no_major_loss_detected"
        findings.append("No major loss point is visible from current benchmark summary.")

    if recall is not None:
        findings.append(f"Recall={recall:.3f}; use variant comparison before tuning thresholds.")
    if state.get("critic_admissibility_reason"):
        findings.append(f"Critic reason: {state['critic_admissibility_reason']}")

    return {
        "variant": result.get("variant"),
        "likely_loss_point": likely_loss_point,
        "findings": findings,
        "metrics": metrics,
        "state_summary": state,
        "trace_summary": trace,
    }


def compare_variants(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {str(result.get("variant")): result for result in results}
    baseline = by_name.get("baseline_no_rerank")
    rerank = by_name.get("rerank_default")
    if not baseline or not rerank:
        return {
            "rerank_effect": "not_comparable",
            "reason": "Need baseline_no_rerank and rerank_default variants.",
        }

    base_recall = _float((baseline.get("metrics") or {}).get("recall"))
    rerank_recall = _float((rerank.get("metrics") or {}).get("recall"))
    if base_recall is None or rerank_recall is None:
        return {
            "rerank_effect": "not_comparable",
            "reason": "Missing recall metrics.",
        }

    delta = rerank_recall - base_recall
    if delta > 0.001:
        effect = "rerank_improved_recall"
    elif delta < -0.001:
        effect = "rerank_reduced_recall"
    else:
        effect = "rerank_neutral_on_recall"
    return {
        "rerank_effect": effect,
        "baseline_recall": base_recall,
        "rerank_recall": rerank_recall,
        "recall_delta": delta,
    }


def build_diagnosis(summary: dict[str, Any]) -> dict[str, Any]:
    results = list(summary.get("results") or [])
    diagnoses = [diagnose_result(result) for result in results]
    return {
        "run_dir": summary.get("run_dir"),
        "variant_comparison": compare_variants(results),
        "diagnoses": diagnoses,
        "next_actions": recommend_next_actions(diagnoses, compare_variants(results)),
    }


def recommend_next_actions(
    diagnoses: list[dict[str, Any]],
    comparison: dict[str, Any],
) -> list[str]:
    loss_points = {diag.get("likely_loss_point") for diag in diagnoses}
    actions: list[str] = []

    if comparison.get("rerank_effect") == "rerank_reduced_recall":
        actions.append("Increase first-stage candidate pool before changing min_rerank_score.")
    if "rerank_not_exercised" in loss_points:
        actions.append("Fix rerank wiring or trace config before interpreting A/B results.")
    if "retrieval_context_quality" in loss_points:
        actions.append("Improve retrieval query/corpus coverage before changing audit prompts.")
    if "critic_admissibility_prune" in loss_points:
        actions.append("Inspect critic admissibility logic; compare pre-critic findings before pruning.")
    if "audit_generation" in loss_points:
        actions.append("Inspect audit prompt/output for missed known traps after confirming context contains supporting law.")
    if not actions:
        actions.append("Run a wider top_k/chunk_top_k sweep and add expected-law hit labels.")
    return actions


def build_diagnosis_markdown(diagnosis: dict[str, Any]) -> str:
    comparison = diagnosis.get("variant_comparison") or {}
    lines = [
        "# Benchmark Diagnosis",
        "",
        "## Variant Comparison",
        f"- Rerank effect: {comparison.get('rerank_effect')}",
    ]
    if "recall_delta" in comparison:
        lines.append(f"- Recall delta: {_fmt(comparison.get('recall_delta'))}")

    lines += ["", "## Loss Points"]
    for item in diagnosis.get("diagnoses") or []:
        lines.append(f"### {item.get('variant')}")
        lines.append(f"- Likely loss point: {item.get('likely_loss_point')}")
        for finding in item.get("findings") or []:
            lines.append(f"- {finding}")

    lines += ["", "## Recommended Next Actions"]
    for action in diagnosis.get("next_actions") or []:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fmt(value: Any) -> str:
    number = _float(value)
    return "" if number is None else f"{number:.3f}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose benchmark loss points")
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    diagnosis = build_diagnosis(summary)
    out_json = args.out_json or args.summary.with_name("diagnosis.json")
    out_md = args.out_md or args.summary.with_name("diagnosis.md")
    out_json.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(build_diagnosis_markdown(diagnosis), encoding="utf-8")
    print(f"Diagnosis JSON written: {out_json}")
    print(f"Diagnosis Markdown written: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
