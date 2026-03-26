"""
relevance_engine.py – Keyword-based Relevance Scoring for DOCAI

Matches CSV rows to document sections using fast keyword/token scoring.
No external embeddings — deterministic, low latency.

Scoring factors:
  - Title keyword overlap with section name + content
  - Tags match
  - Description keyword overlap
  - Acceptance Criteria overlap
  - Work Item Type boost
"""

import re
import logging
from typing import List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "is", "are", "was", "were", "be", "been",
    "this", "that", "it", "its", "by", "from", "as", "into", "not",
    "has", "have", "will", "shall", "may", "can", "do", "does"
}

def _tokenize(text: str) -> set:
    """Lowercase words ≥ 3 chars, excluding stop words."""
    if not text:
        return set()
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOP_WORDS}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_WEIGHTS = {
    "title":               3.0,
    "tags":                2.5,
    "description":         1.5,
    "acceptance_criteria": 1.0,
}

_WORK_ITEM_BOOST = {
    "user story":    1.2,
    "feature":       1.1,
    "task":          1.0,
    "bug":           0.6,   # Lower—bugs map to process enhancements
    "issue":         0.6,
    "epic":          0.9,
}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def score_row(section_name: str, section_content: str, row: dict) -> float:
    """
    Compute a relevance float score [0, ∞) for a CSV row against a section.
    Higher = more relevant.
    """
    section_tokens = _tokenize(section_name) | _tokenize(section_content[:2000])

    score = 0.0

    # Title
    title_tokens = _tokenize(str(row.get("Title", "")))
    score += _WEIGHTS["title"] * _jaccard(section_tokens, title_tokens)

    # Tags
    tags_tokens = _tokenize(str(row.get("Tags", "")))
    score += _WEIGHTS["tags"] * _jaccard(section_tokens, tags_tokens)

    # Description
    desc_tokens = _tokenize(str(row.get("Description", ""))[:1000])
    score += _WEIGHTS["description"] * _jaccard(section_tokens, desc_tokens)

    # Acceptance Criteria
    ac_tokens = _tokenize(str(row.get("Acceptance Criteria", ""))[:500])
    score += _WEIGHTS["acceptance_criteria"] * _jaccard(section_tokens, ac_tokens)

    # Work Item Type multiplier
    wit = str(row.get("Work Item Type", "")).strip().lower()
    multiplier = next((v for k, v in _WORK_ITEM_BOOST.items() if k in wit), 1.0)
    score *= multiplier

    return score


def get_relevant_rows(
    section_name: str,
    section_content: str,
    all_rows: List[dict],
    top_k: int = 30,
    min_score: float = 0.01,
) -> List[dict]:
    """
    Return the top-k most relevant CSV rows for a section.

    Args:
        section_name:    Heading of the current section.
        section_content: Full text content of the section.
        all_rows:        Complete list of CSV rows (pre-cached).
        top_k:           Maximum rows to return (default 30).
        min_score:       Minimum score threshold; rows below this are excluded.

    Returns:
        List of dicts filtered and sorted by relevance score (descending).
    """
    if not all_rows:
        return []

    scored = []
    for row in all_rows:
        s = score_row(section_name, section_content, row)
        if s >= min_score:
            scored.append((s, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = [row for _, row in scored[:top_k]]

    logger.debug(
        f"[RelevanceEngine] Section '{section_name}': "
        f"{len(result)}/{len(all_rows)} rows selected (top_k={top_k})"
    )
    return result


def rows_to_context_string(rows: List[dict]) -> str:
    """
    Converts a filtered list of CSV rows to a compact context block for the LLM.
    Format preserves all relevant fields.
    """
    if not rows:
        return "(No relevant context data found for this section.)"

    parts = []
    for i, row in enumerate(rows, start=1):
        fields = []
        for key in ["ID", "Work Item Type", "Title", "Tags", "Description", "Acceptance Criteria", "State"]:
            val = str(row.get(key, "")).strip()
            if val:
                fields.append(f"  {key}: {val[:300]}")  # cap field length
        if fields:
            parts.append(f"[Row {i}]\n" + "\n".join(fields))

    return "\n\n".join(parts)
