"""
scorer.py – 4-Stage Block Locator + Confidence Scoring for DOCAI

Locates the best-matching paragraph/block for a given target_hint using
four progressively weaker (but always safe) strategies:

  Stage 1  Exact        target_hint is a substring of block.text
           confidence = 1.0

  Stage 2  Fuzzy        difflib SequenceMatcher ratio >= FUZZY_THRESHOLD (0.80)
           confidence = ratio

  Stage 3  Keyword-cos  TF cosine similarity >= KW_COS_THRESHOLD (0.72)
           confidence = cosine score
           (pure-Python, no external packages, deterministic)

  Stage 4  Safe insert  no match found; action converted to "insert"
           confidence = 0.3

Confidence thresholds (applied by edit_engine, not here):
  >= 0.85  → apply normally
  >= 0.50  → apply + log WARNING
  <  0.50  → skip + log (NEVER silently dropped)

Traceability fields added to every edit result:
  match_type   : "exact" | "fuzzy" | "keyword_cos" | "insert_fallback"
  confidence   : float 0.0–1.0
  plan_id      : int (index of plan item, if provided)
  source_row_id: str (CSV row ID, if provided)
"""

import re
import logging
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from docx.text.paragraph import Paragraph
from docx.table import Table

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
FUZZY_THRESHOLD   = 0.80
KW_COS_THRESHOLD  = 0.72
CONF_APPLY        = 0.85   # apply silently
CONF_WARN         = 0.50   # apply + warn
CONF_SKIP         = 0.50   # skip (log it)

# ── Stop-word set for TF cosine ───────────────────────────────────────────────
_STOP = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "is","are","was","were","be","been","this","that","it","its","by","from",
    "as","into","not","has","have","will","shall","may","can","do","does","its",
    "which","who","what","how","when","where","their","they","we","our",
}


# ── TF cosine (keyword semantic proxy) ───────────────────────────────────────

def _tf_vector(text: str) -> dict:
    words = re.findall(r"[a-z]+", text.lower())
    freq: dict = {}
    for w in words:
        if len(w) >= 3 and w not in _STOP:
            freq[w] = freq.get(w, 0) + 1
    return freq


def _cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    keys  = set(a) | set(b)
    dot   = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    mag_a = sum(v * v for v in a.values()) ** 0.5
    mag_b = sum(v * v for v in b.values()) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Per-block text extractor ──────────────────────────────────────────────────

def _block_text(block) -> str:
    if isinstance(block, Paragraph):
        return block.text.strip()
    if isinstance(block, Table):
        cells = []
        for row in block.rows:
            for cell in row.cells:
                cells.append(cell.text.strip())
        return " ".join(cells)
    return ""


# ── 4-Stage Locator ──────────────────────────────────────────────────────────

def locate_block(
    blocks: list,
    target_hint: str,
    plan_id: int = 0,
    source_row_id: str = "",
) -> Tuple[Optional[object], int, str, float]:
    """
    Find the best-matching block for target_hint using 4 stages.

    Args:
        blocks:        List of Paragraph / Table objects (from get_blocks).
        target_hint:   The phrase to locate (from the edit plan).
        plan_id:       Plan item index for traceability.
        source_row_id: CSV row ID for traceability.

    Returns:
        (block, block_idx, match_type, confidence)
        block is None only when Stage 4 (insert fallback) triggers.
    """
    if not target_hint or not blocks:
        return None, -1, "insert_fallback", 0.3

    hint_lower = target_hint.lower()
    hint_tf    = _tf_vector(target_hint)

    best_fuzzy_ratio  = 0.0
    best_fuzzy_idx    = -1
    best_cos_score    = 0.0
    best_cos_idx      = -1

    for idx, block in enumerate(blocks):
        text = _block_text(block)
        if not text:
            continue
        text_lower = text.lower()

        # ── Stage 1: Exact ────────────────────────────────────────────────
        if hint_lower in text_lower:
            logger.debug(
                f"[Scorer] plan={plan_id} stage=exact conf=1.0 "
                f"hint='{target_hint[:40]}'"
            )
            return block, idx, "exact", 1.0

        # ── Stage 2: Fuzzy (collect best) ────────────────────────────────
        ratio = SequenceMatcher(None, hint_lower, text_lower).ratio()
        if ratio > best_fuzzy_ratio:
            best_fuzzy_ratio = ratio
            best_fuzzy_idx   = idx

        # ── Stage 3: Keyword cosine (collect best) ────────────────────────
        cos = _cosine(hint_tf, _tf_vector(text))
        if cos > best_cos_score:
            best_cos_score = cos
            best_cos_idx   = idx

    # ── Stage 2 result ────────────────────────────────────────────────────
    if best_fuzzy_ratio >= FUZZY_THRESHOLD and best_fuzzy_idx >= 0:
        logger.debug(
            f"[Scorer] plan={plan_id} stage=fuzzy conf={best_fuzzy_ratio:.2f} "
            f"hint='{target_hint[:40]}'"
        )
        return blocks[best_fuzzy_idx], best_fuzzy_idx, "fuzzy", best_fuzzy_ratio

    # ── Stage 3 result ────────────────────────────────────────────────────
    if best_cos_score >= KW_COS_THRESHOLD and best_cos_idx >= 0:
        logger.debug(
            f"[Scorer] plan={plan_id} stage=keyword_cos conf={best_cos_score:.2f} "
            f"hint='{target_hint[:40]}'"
        )
        return blocks[best_cos_idx], best_cos_idx, "keyword_cos", best_cos_score

    # ── Stage 4: Safe insert fallback ─────────────────────────────────────
    logger.info(
        f"[Scorer] plan={plan_id} stage=insert_fallback "
        f"fuzzy_best={best_fuzzy_ratio:.2f} cos_best={best_cos_score:.2f} "
        f"hint='{target_hint[:40]}'"
    )
    return None, -1, "insert_fallback", 0.3


def confidence_decision(confidence: float, section: str, hint: str) -> str:
    """
    Return 'apply' | 'warn_apply' | 'skip' based on confidence.
    'skip' still logs — no silent drops.
    """
    if confidence >= CONF_APPLY:
        return "apply"
    if confidence >= CONF_WARN:
        logger.warning(
            f"[Scorer] Low confidence {confidence:.2f} for hint='{hint[:40]}' "
            f"in section='{section}' — applying with caution."
        )
        return "warn_apply"
    logger.warning(
        f"[Scorer] Confidence {confidence:.2f} below threshold for "
        f"hint='{hint[:40]}' in section='{section}' — skipping edit."
    )
    return "skip"
