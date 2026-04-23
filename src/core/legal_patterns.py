"""Regex patterns and domain keyword mapping for Vietnamese contract analysis.

No external dependencies — pure Python.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Domain classification keywords
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "Lao động": [
        "người lao động",
        "hợp đồng lao động",
        "tiền lương",
        "thời giờ làm việc",
        "nghỉ phép",
        "bảo hiểm xã hội",
        "sa thải",
        "thôi việc",
        "nội quy lao động",
        "thỏa ước lao động",
        "tai nạn lao động",
        "bệnh nghề nghiệp",
        "người sử dụng lao động",
        "chấm dứt hợp đồng lao động",
    ],
    "Doanh nghiệp": [
        "góp vốn",
        "cổ phần",
        "cổ đông",
        "hội đồng quản trị",
        "đại hội đồng cổ đông",
        "vốn điều lệ",
        "liên doanh",
        "sáp nhập",
        "mua lại",
        "thành viên công ty",
        "hội đồng thành viên",
        "giám đốc điều hành",
        "cổ tức",
        "phần vốn góp",
    ],
    "Thương mại": [
        "mua bán hàng hóa",
        "cung ứng dịch vụ",
        "đại lý thương mại",
        "nhượng quyền thương mại",
        "trung gian thương mại",
        "phân phối",
        "xuất khẩu",
        "nhập khẩu",
        "giao nhận hàng",
        "thanh toán thương mại",
        "tín dụng thư",
        "hàng hóa",
        "thương nhân",
        "hoạt động thương mại",
    ],
    "Dân sự": [
        "hợp đồng dân sự",
        "mua bán tài sản",
        "cho thuê tài sản",
        "vay mượn",
        "thừa kế",
        "tặng cho",
        "ủy quyền",
        "bảo lãnh",
        "thế chấp",
        "cầm cố",
        "quyền sử dụng đất",
        "bất động sản",
        "hợp đồng dân dụng",
        "mua bán nhà ở",
        "căn hộ",
        "chung cư",
        "sở hữu chung",
        "nhà ở thương mại",
    ],
}

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches article headers in contracts: "Điều 1.", "Điều 1:", "Điều 1 -", "ĐIỀU 1."
_SPLIT_RE = re.compile(
    r"(?=^Điều\s+\d+[\.\:\-\s])",
    re.MULTILINE | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def classify_domain_by_keywords(text: str) -> str | None:
    """Return the dominant domain if keyword signals are clear, else None.

    Returns None to signal that LLM-based classification is needed.
    Requires top domain to have >= 2 hits AND >= 2x the runner-up score.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}

    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[domain] = score

    if not scores:
        return None

    top_domain, top_score = max(scores.items(), key=lambda x: x[1])
    sorted_scores = sorted(scores.values(), reverse=True)

    # Require a clear dominant signal
    if top_score < 2:
        return None
    if len(sorted_scores) >= 2 and top_score < 2 * sorted_scores[1]:
        return None

    return top_domain


def split_contract_into_clauses(text: str) -> list[str]:
    """Split contract text into clause-level chunks using regex.

    Splits at 'Điều X.' boundaries. Falls back to double-newline splitting
    if no article markers are found. Never returns an empty list.
    """
    parts = _SPLIT_RE.split(text)
    clauses = [p.strip() for p in parts if p.strip()]

    if len(clauses) < 2:
        # Fallback: split on double newlines (paragraph-level)
        clauses = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    if not clauses:
        clauses = [text.strip()]

    return clauses
