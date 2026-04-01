"""
scorer.py – 5-Stage Block Locator + Confidence Scoring for DOCAI

Stages (each only runs if the previous failed to meet its threshold):

  Stage 1  Exact          target_hint is a substring of block.text
           confidence = 1.0

  Stage 2  Fuzzy          difflib SequenceMatcher ratio >= FUZZY_THRESHOLD (0.80)
           confidence = ratio

  Stage 3  Keyword-cos    TF cosine similarity >= KW_COS_THRESHOLD (0.72)
           confidence = cosine  |  pure-Python, no deps, deterministic

  Stage 4  Semantic        sentence-transformers cosine >= SEMANTIC_THRESHOLD (0.75)
           confidence = cosine  |  OPTIONAL — skipped if library not installed
           Only triggers when fuzzy < 0.80 AND keyword_cos < 0.72
           Deterministic layers are NEVER bypassed by this stage.

  Stage 5  Safe insert     No match found → INSERT, never DROP
           confidence = 0.3

Confidence thresholds (enforced by edit_engine, not here):
  >= 0.85  → apply silently
  >= 0.50  → apply + log WARNING
  <  0.50  → skip + log (NEVER silent)

Traceability on every result:
  match_type   : "exact"|"fuzzy"|"keyword_cos"|"semantic"|"insert_fallback"
  confidence   : float 0.0–1.0
"""

import re
import logging
from difflib import SequenceMatcher
from typing import Optional, Tuple

from docx.text.paragraph import Paragraph
from docx.table import Table

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
FUZZY_THRESHOLD    = 0.80
KW_COS_THRESHOLD   = 0.72
SEMANTIC_THRESHOLD = 0.75
CONF_APPLY         = 0.85
CONF_WARN          = 0.50

# ── Optional sentence-transformers (Stage 4) ──────────────────────────────────
_SEMANTIC_AVAILABLE = False
_st_model           = None

def _try_load_semantic():
    global _SEMANTIC_AVAILABLE, _st_model
    try:
        from sentence_transformers import SentenceTransformer
        _st_model = SentenceTransformer("all-MiniLM-L6-v2")
        _SEMANTIC_AVAILABLE = True
        logger.info("[Scorer] sentence-transformers loaded — Stage 4 semantic active.")
    except Exception:
        _SEMANTIC_AVAILABLE = False
        logger.debug("[Scorer] sentence-transformers not available — Stage 4 skipped.")

_try_load_semantic()


# ── TF cosine (Stage 3) ───────────────────────────────────────────────────────
_STOP = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "is","are","was","were","be","been","this","that","it","its","by","from",
    "as","into","not","has","have","will","shall","may","can","do","does",
    "which","who","what","how","when","where","their","they","we","our",
}

def _tf_vector(text: str) -> dict:
    freq: dict = {}
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) >= 3 and w not in _STOP:
            freq[w] = freq.get(w, 0) + 1
    return freq

def _cosine_dict(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    keys  = set(a) | set(b)
    dot   = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    mag_a = sum(v * v for v in a.values()) ** 0.5
    mag_b = sum(v * v for v in b.values()) ** 0.5
    return (dot / (mag_a * mag_b)) if mag_a and mag_b else 0.0

# Expose for use by dedup / safe_insert_guard
def keyword_cosine(text_a: str, text_b: str) -> float:
    """Public TF cosine similarity between two strings."""
    return _cosine_dict(_tf_vector(text_a), _tf_vector(text_b))


# ── Semantic cosine (Stage 4) — only when sentence-transformers available ─────
def _semantic_cosine(text_a: str, text_b: str) -> float:
    if not _SEMANTIC_AVAILABLE or _st_model is None:
        return 0.0
    try:
        vecs = _st_model.encode([text_a, text_b], convert_to_numpy=True, show_progress_bar=False)
        a, b = vecs[0], vecs[1]
        mag_a = float((a * a).sum() ** 0.5)
        mag_b = float((b * b).sum() ** 0.5)
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return float((a * b).sum() / (mag_a * mag_b))
    except Exception as exc:
        logger.debug(f"[Scorer] semantic_cosine failed: {exc}")
        return 0.0


# ── Block text extractor ──────────────────────────────────────────────────────
def _block_text(block) -> str:
    if isinstance(block, Paragraph):
        return block.text.strip()
    if isinstance(block, Table):
        return " ".join(
            cell.text.strip()
            for row in block.rows
            for cell in row.cells
        )
    return ""


# ── 5-Stage Locator ───────────────────────────────────────────────────────────
def locate_block(
    blocks: list,
    target_hint: str,
    plan_id: int = 0,
    source_row_id: str = "",
) -> Tuple[Optional[object], int, str, float]:
    """
    Find the best-matching block using 5 stages.
    blocks must already be SECTION-SCOPED (enforced by edit_engine).

    Returns:
        (block, block_idx, match_type, confidence)
        block is None only on Stage 5 (insert fallback).
    """
    if not target_hint or not blocks:
        return None, -1, "insert_fallback", 0.3

    hint_lower = target_hint.lower()
    hint_tf    = _tf_vector(target_hint)

    best_fuzzy_ratio = 0.0;  best_fuzzy_idx = -1
    best_cos_score   = 0.0;  best_cos_idx   = -1

    for idx, block in enumerate(blocks):
        text = _block_text(block)
        if not text:
            continue
        text_lower = text.lower()

        # Stage 1 ─────────────────────────────────────────────────────────────
        if hint_lower in text_lower:
            logger.debug(f"[Scorer] plan={plan_id} stage=exact conf=1.0 hint='{target_hint[:40]}'")
            return block, idx, "exact", 1.0

        # Stage 2 candidates ──────────────────────────────────────────────────
        ratio = SequenceMatcher(None, hint_lower, text_lower).ratio()
        if ratio > best_fuzzy_ratio:
            best_fuzzy_ratio = ratio; best_fuzzy_idx = idx

        # Stage 3 candidates ──────────────────────────────────────────────────
        cos = _cosine_dict(hint_tf, _tf_vector(text))
        if cos > best_cos_score:
            best_cos_score = cos; best_cos_idx = idx

    # Stage 2 result ──────────────────────────────────────────────────────────
    if best_fuzzy_ratio >= FUZZY_THRESHOLD and best_fuzzy_idx >= 0:
        logger.debug(f"[Scorer] plan={plan_id} stage=fuzzy conf={best_fuzzy_ratio:.2f} hint='{target_hint[:40]}'")
        return blocks[best_fuzzy_idx], best_fuzzy_idx, "fuzzy", best_fuzzy_ratio

    # Stage 3 result ──────────────────────────────────────────────────────────
    if best_cos_score >= KW_COS_THRESHOLD and best_cos_idx >= 0:
        logger.debug(f"[Scorer] plan={plan_id} stage=keyword_cos conf={best_cos_score:.2f} hint='{target_hint[:40]}'")
        return blocks[best_cos_idx], best_cos_idx, "keyword_cos", best_cos_score

    # Stage 4 — semantic (only when deterministic stages all failed) ──────────
    if _SEMANTIC_AVAILABLE:
        best_sem_score = 0.0; best_sem_idx = -1
        for idx, block in enumerate(blocks):
            text = _block_text(block)
            if not text:
                continue
            sim = _semantic_cosine(target_hint, text)
            if sim > best_sem_score:
                best_sem_score = sim; best_sem_idx = idx
        if best_sem_score >= SEMANTIC_THRESHOLD and best_sem_idx >= 0:
            logger.debug(f"[Scorer] plan={plan_id} stage=semantic conf={best_sem_score:.2f} hint='{target_hint[:40]}'")
            return blocks[best_sem_idx], best_sem_idx, "semantic", best_sem_score

    # Stage 5 — safe insert fallback ──────────────────────────────────────────
    logger.info(
        f"[Scorer] plan={plan_id} stage=insert_fallback "
        f"fuzzy={best_fuzzy_ratio:.2f} cos={best_cos_score:.2f} "
        f"hint='{target_hint[:40]}'"
    )
    return None, -1, "insert_fallback", 0.3


def confidence_decision(confidence: float, section: str, hint: str) -> str:
    """Return 'apply' | 'warn_apply' | 'skip'. 'skip' is always logged."""
    if confidence >= CONF_APPLY:
        return "apply"
    if confidence >= CONF_WARN:
        logger.warning(f"[Scorer] Low conf {confidence:.2f} hint='{hint[:40]}' section='{section}' — applying.")
        return "warn_apply"
    logger.warning(f"[Scorer] Conf {confidence:.2f} below threshold hint='{hint[:40]}' section='{section}' — skipping.")
    return "skip"
