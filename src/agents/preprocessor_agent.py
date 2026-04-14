"""Preprocessor Agent — word-tokenises clauses and extracts cross-references.

Inputs:  AuditState.chunks
Outputs: AuditState.segmented_chunks, AuditState.cross_refs

No LLM calls — pure regex + underthesea (see core/vn_preprocessor.py).
Cross-references are detected on the *original* clause text to preserve
character offsets, then the normalised+tokenised text is used for retrieval.
"""

from __future__ import annotations

import logging

from core.state import AuditState
from core.vn_preprocessor import detect_cross_refs, normalize_aliases, segment_clause

logger = logging.getLogger(__name__)


async def preprocessor_node(state: AuditState) -> dict:
    """LangGraph node: segment clauses and extract cross-references."""
    chunks = state.get("chunks", [])

    if not chunks:
        logger.warning("preprocessor: no chunks to process")
        return {"segmented_chunks": [], "cross_refs": []}

    segmented_chunks: list[str] = []
    all_cross_refs: list[dict] = []

    for clause in chunks:
        # Detect refs on original text to keep character offsets meaningful
        refs = detect_cross_refs(clause)
        all_cross_refs.extend(refs)

        # Normalise abbreviations first, then word-tokenise
        normalised = normalize_aliases(clause)
        segmented = segment_clause(normalised)
        segmented_chunks.append(segmented)

    logger.warning(
        "preprocessor: segmented %d clauses, found %d cross-refs",
        len(segmented_chunks),
        len(all_cross_refs),
    )
    return {"segmented_chunks": segmented_chunks, "cross_refs": all_cross_refs}
