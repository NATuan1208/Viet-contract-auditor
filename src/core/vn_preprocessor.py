"""Vietnamese text preprocessor for contract clauses.

No LLM calls — pure regex + underthesea word tokenisation.

Functions:
    segment_clause(text)    -> str          word-tokenised clause (underscores join multi-word)
    detect_cross_refs(text) -> list[dict]   legal cross-reference extraction
    normalize_aliases(text) -> str          expand common legal abbreviations
"""

from __future__ import annotations

import re

try:
    from underthesea import word_tokenize as _word_tokenize

    _HAS_UNDERTHESEA = True
except ImportError:  # pragma: no cover
    _HAS_UNDERTHESEA = False

# ---------------------------------------------------------------------------
# Legal abbreviation map — 25 entries
# ---------------------------------------------------------------------------

LEGAL_ALIAS_MAP: dict[str, str] = {
    # Labour law
    "BLLĐ": "Bộ luật Lao động",
    "NLĐ": "người lao động",
    "NSDLĐ": "người sử dụng lao động",
    "HĐLĐ": "hợp đồng lao động",
    "BHXH": "Bảo hiểm xã hội",
    "BHYT": "Bảo hiểm y tế",
    "BHTN": "Bảo hiểm thất nghiệp",
    # Civil / general contract
    "BLDS": "Bộ luật Dân sự",
    "HĐ": "hợp đồng",
    "HĐDS": "hợp đồng dân sự",
    # Commercial
    "LTM": "Luật Thương mại",
    "TTTM": "Trọng tài Thương mại",
    # Enterprise
    "DN": "doanh nghiệp",
    "DNTN": "doanh nghiệp tư nhân",
    "TNHH": "trách nhiệm hữu hạn",
    "CTCP": "công ty cổ phần",
    "HĐQT": "Hội đồng quản trị",
    "ĐHĐCĐ": "Đại hội đồng cổ đông",
    "HĐTV": "Hội đồng thành viên",
    "GĐ": "Giám đốc",
    "TGĐ": "Tổng Giám đốc",
    # Judicial
    "TAND": "Tòa án nhân dân",
    "VKSND": "Viện kiểm sát nhân dân",
    "VKS": "Viện kiểm sát",
    # Arbitration
    "HĐTT": "Hội đồng Trọng tài",
}

# Pre-compiled alias pattern — longest keys first to avoid partial matches
_ALIAS_KEYS_SORTED = sorted(LEGAL_ALIAS_MAP.keys(), key=len, reverse=True)
_ALIAS_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _ALIAS_KEYS_SORTED) + r")\b"
)

# ---------------------------------------------------------------------------
# Cross-reference pattern
# ---------------------------------------------------------------------------

# Matches:
#   Điều 123 / điều 45
#   Khoản 1 / khoản 2
#   Điểm a / điểm b
#   Bộ luật Dân sự 2015 / Luật Lao động 2019
LEGAL_XREF_PATTERN = re.compile(
    r"""
    (?:
        (?P<dieu>  (?:Điều|điều)      \s+ \d+                      )
        |
        (?P<khoan> (?:[Kk]hoản)       \s+ \d+                      )
        |
        (?P<diem>  (?:[Đđ]iểm)        \s+ [a-zA-ZăâđêôơưÀ-ỹ]+     )
        |
        (?P<luat>  (?:Bộ\s+luật|Luật) \s+ [\w\s]+ \d{4}            )
    )
    """,
    re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_aliases(text: str) -> str:
    """Expand common Vietnamese legal abbreviations in *text*.

    Example: "NLĐ ký HĐLĐ với NSDLĐ" ->
             "người lao động ký hợp đồng lao động với người sử dụng lao động"
    """
    return _ALIAS_RE.sub(lambda m: LEGAL_ALIAS_MAP[m.group(0)], text)


def detect_cross_refs(text: str) -> list[dict]:
    """Extract legal cross-references from a clause.

    Returns a list of dicts with keys: type, value, start, end.
    ``type`` is one of "dieu", "khoan", "diem", "luat".
    """
    refs: list[dict] = []
    for m in LEGAL_XREF_PATTERN.finditer(text):
        for group_name in ("dieu", "khoan", "diem", "luat"):
            val = m.group(group_name)
            if val:
                refs.append(
                    {
                        "type": group_name,
                        "value": val.strip(),
                        "start": m.start(),
                        "end": m.end(),
                    }
                )
                break
    return refs


def segment_clause(text: str) -> str:
    """Tokenise a Vietnamese clause with underthesea word_tokenize.

    Returns the tokenised string (multi-word tokens joined by underscores).
    Falls back silently to the original text when underthesea is unavailable.

    Example: "người lao động" -> "người_lao_động"
    """
    if not _HAS_UNDERTHESEA:
        return text
    try:
        return _word_tokenize(text, format="text")
    except Exception:  # pragma: no cover
        return text


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    samples = [
        "theo Điều 29 Bộ luật Lao động 2019",
        "quy định tại khoản 1 Điều 35 BLLĐ",
        "NLĐ ký HĐLĐ với NSDLĐ",
    ]
    for s in samples:
        print("Original :", s)
        print("Normalized:", normalize_aliases(s))
        print("Segmented :", segment_clause(normalize_aliases(s)))
        print("Xrefs     :", detect_cross_refs(s))
        print()
