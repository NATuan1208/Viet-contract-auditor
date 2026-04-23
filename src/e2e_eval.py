"""Run full audit pipeline and compare generated report against groundtruth JSON.

Usage:
    uv run python src/e2e_eval.py \
        --contract HDLD_ThucHanh_01.docx \
        --groundtruth result-example/groundtruth_hdld_01.json \
        --report-out HDLD_ThucHanh_01_report_e2e.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

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

from agents.orchestrator import run_audit


@dataclass
class MatchResult:
    gt_index: int
    matched_prediction_index: int | None
    score: float
    location_score: float = 0.0
    semantic_score: float = 0.0
    type_score: float = 0.0


def _read_contract(contract_path: Path) -> str:
    suffix = contract_path.suffix.lower()
    if suffix == ".docx":
        import docx

        doc = docx.Document(str(contract_path))
        return "\n".join(p.text for p in doc.paragraphs)
    if suffix == ".txt":
        return contract_path.read_text(encoding="utf-8")
    raise ValueError("Unsupported contract format. Use .txt or .docx")


def _extract_violation_sections(report_md: str) -> list[str]:
    pattern = re.compile(r"^###\s+Vi phạm\s+\d+.*$", re.MULTILINE)
    matches = list(pattern.finditer(report_md))
    sections: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report_md)
        sections.append(report_md[start:end].strip())
    return sections


def _article_from_chunk(chunk_text: str) -> str | None:
    """Extract 'Điều N' header from a chunk string, or None if absent (e.g. preamble)."""
    m = re.search(r"(?:Điều|ĐIỀU)\s+(\d+)", chunk_text)
    if m:
        return f"Điều {m.group(1)}"
    return None


def _build_prediction_texts(state: dict, report_md: str) -> list[str]:
    """Prefer raw audit findings over report sections for less paraphrase noise."""
    findings = state.get("audit_findings", [])
    if isinstance(findings, list) and findings:
        chunks: list[str] = state.get("chunks", [])
        preds: list[str] = []
        for f in findings:
            clause = str(f.get("clause", ""))
            violation = str(f.get("violation", ""))
            ref = str(f.get("reference_law", ""))
            fix = str(f.get("suggested_fix", ""))
            clause_idx = f.get("clause_index")
            loc_hint = ""
            if isinstance(clause_idx, int):
                # Read actual article header from the chunk rather than computing
                # clause_index+1, which is wrong when a preamble chunk is at index 0.
                chunk_text = chunks[clause_idx] if clause_idx < len(chunks) else ""
                article = _article_from_chunk(chunk_text)
                loc_hint = article if article else f"Điều {clause_idx + 1}"
            preds.append(" ".join(x for x in [loc_hint, clause, violation, ref, fix] if x).strip())
        if preds:
            return preds
    return _extract_violation_sections(report_md)


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[0-9a-zA-ZÀ-ỹà-ỹđ]+", text.lower())
    stop = {
        "và",
        "hoặc",
        "là",
        "của",
        "các",
        "những",
        "được",
        "không",
        "cho",
        "trong",
        "theo",
        "với",
        "khi",
        "này",
        "đó",
        "đến",
        "tại",
        "như",
        "có",
        "thì",
        "điều",
        "khoản",
    }
    return {t for t in tokens if len(t) >= 3 and t not in stop}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _extract_location_tokens(text: str) -> tuple[str | None, str | None]:
    """Extract top-level and detailed location tokens like 'điều 3' / 'điều 3.1'."""
    lowered = text.lower()
    detailed = None
    top = None

    m_detailed = re.search(r"điều\s*(\d+(?:\.\d+)?)", lowered)
    if m_detailed:
        detailed = f"điều {m_detailed.group(1)}"
        top = f"điều {m_detailed.group(1).split('.')[0]}"
        return top, detailed

    m_top = re.search(r"điều\s*(\d+)", lowered)
    if m_top:
        top = f"điều {m_top.group(1)}"
    return top, detailed


def _location_score(gt_location: str, pred_text: str) -> float:
    gt_lower = gt_location.lower()
    pred_lower = pred_text.lower()

    if gt_lower in pred_lower:
        return 1.0

    gt_top, gt_detail = _extract_location_tokens(gt_lower)
    pred_top, pred_detail = _extract_location_tokens(pred_lower)

    if gt_detail and pred_detail and gt_detail == pred_detail:
        return 0.95
    if gt_top and pred_top and gt_top == pred_top:
        return 0.55
    return 0.0


def _type_signal_score(vuln_type: str, pred_text: str) -> float:
    lowered = pred_text.lower()
    if vuln_type == "numeric":
        keys = ["%", "ngày", "giờ", "thời hạn", "lương", "mức", "03"]
    elif vuln_type == "logical":
        keys = ["đơn phương", "không cần", "tự động", "mâu thuẫn", "không chấp thuận"]
    elif vuln_type == "omission":
        keys = ["thiếu", "bỏ sót", "lược bỏ", "không có", "khuyết", "nhảy số"]
    else:
        keys = []

    if not keys:
        return 0.0
    hits = sum(1 for k in keys if k in lowered)
    return min(1.0, hits / 2.0)


def _combined_match_score(vuln: dict, pred_text: str) -> tuple[float, float, float, float]:
    gt_text = " ".join(
        [
            str(vuln.get("description", "")),
            str(vuln.get("reasoning", "")),
            str(vuln.get("legal_risk", "")),
        ]
    )
    semantic_score = _jaccard(_tokenize(gt_text), _tokenize(pred_text))
    location_score = _location_score(str(vuln.get("location", "")), pred_text)
    type_score = _type_signal_score(str(vuln.get("type", "")).lower(), pred_text)

    final = (0.50 * location_score) + (0.35 * semantic_score) + (0.15 * type_score)
    return final, location_score, semantic_score, type_score


def _score_groundtruth_matches(
    gt_vulnerabilities: list[dict],
    pred_sections: list[str],
    threshold: float = 0.30,
) -> tuple[list[MatchResult], float, float, float]:
    used_pred: set[int] = set()
    results: list[MatchResult] = []

    for gi, vuln in enumerate(gt_vulnerabilities):
        best_idx: int | None = None
        best_score = 0.0
        best_loc = 0.0
        best_sem = 0.0
        best_type = 0.0
        for pi, pred_text in enumerate(pred_sections):
            if pi in used_pred:
                continue
            score, loc_s, sem_s, type_s = _combined_match_score(vuln, pred_text)
            if score > best_score:
                best_score = score
                best_idx = pi
                best_loc = loc_s
                best_sem = sem_s
                best_type = type_s

        if best_idx is not None and best_score >= threshold:
            used_pred.add(best_idx)
            results.append(MatchResult(gi, best_idx, best_score, best_loc, best_sem, best_type))
        else:
            results.append(MatchResult(gi, None, best_score, best_loc, best_sem, best_type))

    matched = sum(1 for r in results if r.matched_prediction_index is not None)
    recall = matched / len(gt_vulnerabilities) if gt_vulnerabilities else 0.0
    precision = matched / len(pred_sections) if pred_sections else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return results, precision, recall, f1


def _build_eval_markdown(
    report_path: Path,
    gt_path: Path,
    pred_count: int,
    gt_count: int,
    precision: float,
    recall: float,
    f1: float,
    matches: list[MatchResult],
    gt_vulns: list[dict],
) -> str:
    lines = [
        "# E2E Evaluation Report",
        "",
        "## Inputs",
        f"- Report: {report_path}",
        f"- Groundtruth: {gt_path}",
        "",
        "## Metrics",
        f"- Predicted violations: {pred_count}",
        f"- Groundtruth vulnerabilities: {gt_count}",
        f"- Precision (heuristic): {precision:.3f}",
        f"- Recall (heuristic): {recall:.3f}",
        f"- F1 (heuristic): {f1:.3f}",
        "",
        "## Groundtruth Coverage",
    ]

    for m in matches:
        vuln = gt_vulns[m.gt_index]
        loc = vuln.get("location", "(unknown)")
        typ = vuln.get("type", "unknown")
        desc = vuln.get("description", "")
        if m.matched_prediction_index is None:
            lines.append(
                f"- GT#{m.gt_index + 1} [{typ}] [{loc}] -> MISS "
                f"(score={m.score:.3f}, loc={m.location_score:.3f}, sem={m.semantic_score:.3f}, type={m.type_score:.3f}) "
                f"| {desc}"
            )
        else:
            lines.append(
                f"- GT#{m.gt_index + 1} [{typ}] [{loc}] -> MATCH PRED#{m.matched_prediction_index + 1} "
                f"(score={m.score:.3f}, loc={m.location_score:.3f}, sem={m.semantic_score:.3f}, type={m.type_score:.3f}) "
                f"| {desc}"
            )

    lines += [
        "",
        "## Notes",
        "- Đây là so sánh heuristic theo token overlap, không phải legal judgment tuyệt đối.",
        "- Nếu cần đánh giá sát nghĩa hơn, có thể thêm LLM-as-judge ở Phase 6.",
    ]
    return "\n".join(lines)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Run E2E audit and compare with groundtruth")
    parser.add_argument(
        "--contract",
        type=Path,
        default=None,
        help="Optional contract file (.docx/.txt). If omitted, use perturbed_contract_text from groundtruth.",
    )
    parser.add_argument("--groundtruth", type=Path, required=True)
    parser.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Where to write final Markdown report (default: reports/final_outputs/<name>_report.md)",
    )
    parser.add_argument(
        "--eval-out",
        type=Path,
        default=None,
        help="Where to write evaluation markdown (default: reports/metrics/<name>_eval.md)",
    )
    parser.add_argument(
        "--state-out",
        type=Path,
        default=None,
        help="Where to write raw final AuditState JSON (default: reports/metrics/<name>_state.json)",
    )
    args = parser.parse_args()

    gt_data = json.loads(args.groundtruth.read_text(encoding="utf-8"))
    
    # Determine base name for outputs
    base_name = args.contract.stem if args.contract else args.groundtruth.stem.replace("groundtruth_", "")
    
    report_out = args.report_out or Path(f"reports/final_outputs/{base_name}_report.md")
    eval_out = args.eval_out or Path(f"reports/metrics/{base_name}_eval.md")
    state_out = args.state_out or Path(f"reports/metrics/{base_name}_state.json")
    
    # Ensure directories exist
    report_out.parent.mkdir(parents=True, exist_ok=True)
    eval_out.parent.mkdir(parents=True, exist_ok=True)
    state_out.parent.mkdir(parents=True, exist_ok=True)

    if args.contract is not None:
        contract_text = _read_contract(args.contract)
    else:
        contract_text = str(gt_data.get("perturbed_contract_text", "")).strip()
        if not contract_text:
            raise ValueError(
                "Groundtruth does not include perturbed_contract_text. Provide --contract explicitly."
            )

    state = await run_audit(contract_text)

    report = state.get("final_report") or "_(no report generated)_"
    report_out.write_text(report, encoding="utf-8")

    state_out.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    gt_vulns = gt_data.get("vulnerabilities", [])

    pred_sections = _build_prediction_texts(state, report)
    matches, precision, recall, f1 = _score_groundtruth_matches(gt_vulns, pred_sections)

    eval_md = _build_eval_markdown(
        report_path=report_out,
        gt_path=args.groundtruth,
        pred_count=len(pred_sections),
        gt_count=len(gt_vulns),
        precision=precision,
        recall=recall,
        f1=f1,
        matches=matches,
        gt_vulns=gt_vulns,
    )
    eval_out.write_text(eval_md, encoding="utf-8")

    print(f"Report written: {report_out}")
    print(f"State written: {state_out}")
    print(f"Evaluation written: {eval_out}")
    print(
        "Heuristic metrics => "
        f"precision={precision:.3f}, recall={recall:.3f}, f1={f1:.3f}, "
        f"pred={len(pred_sections)}, gt={len(gt_vulns)}"
    )

    if state.get("error"):
        print(f"Pipeline error: {state['error']}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
