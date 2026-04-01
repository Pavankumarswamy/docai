"""
edit_engine.py – Robust Edit Application Engine for DOCAI

Block location uses scorer.locate_block() — 4-stage matching:
  exact → fuzzy (≥0.80) → keyword-cosine (≥0.72) → safe insert fallback

Confidence gate (per edit):
  ≥ 0.85  → apply silently
  ≥ 0.50  → apply + log WARNING
  <  0.50 → skip  + log WARNING  (NEVER silent)

Every applied/skipped edit carries full traceability metadata:
  match_type, confidence, plan_id, source_row_id, status
"""

import logging
from typing import List

from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl

from scorer import locate_block, confidence_decision

logger = logging.getLogger(__name__)


# ── Block walker ─────────────────────────────────────────────────────────────

def get_blocks(doc: Document) -> list:
    """Return all Paragraph and Table objects in document order."""
    blocks = []
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            blocks.append(Paragraph(child, doc))
        elif isinstance(child, CT_Tbl):
            blocks.append(Table(child, doc))
    return blocks


# ── Paragraph text writer (preserves run structure where possible) ────────────

def _set_paragraph_text(para: Paragraph, new_text: str) -> None:
    """
    Write new_text into a paragraph.
    Clears all runs and writes into the first run to preserve formatting.
    """
    for run in para.runs:
        run.text = ""
    if para.runs:
        para.runs[0].text = new_text
    else:
        para.add_run(new_text)


# ── Paragraph edit ────────────────────────────────────────────────────────────

def _apply_paragraph_edit(block: Paragraph, edit: dict, match_type: str) -> str:
    """
    Apply one edit to a located paragraph.

    For exact/fuzzy: replace the matched fragment in-place.
    For keyword_cos / insert_fallback: append to paragraph end.

    Returns: status string.
    """
    original_text = edit.get("original_text", "").strip()
    new_text      = edit.get("new_text",      "").strip()

    if not new_text:
        return "skipped_empty"

    current = block.text

    if match_type in ("exact", "fuzzy") and original_text and original_text in current:
        _set_paragraph_text(block, current.replace(original_text, new_text, 1))
        return "replaced"

    if match_type == "insert_fallback" or not original_text:
        _set_paragraph_text(block, new_text)
        return "inserted"

    # Keyword-cos match: original_text may not be verbatim — append instead
    cleaned = current.rstrip(". ")
    _set_paragraph_text(block, f"{cleaned}. {new_text}.")
    return "appended"


# ── Table edit ────────────────────────────────────────────────────────────────

def _apply_table_edit(block: Table, edit: dict) -> str:
    r_idx = edit.get("row_index")
    c_idx = edit.get("col_index")
    new_text = edit.get("new_text", "").strip()

    if r_idx is None or c_idx is None or not new_text:
        return "skipped_empty"
    try:
        cell = block.rows[r_idx].cells[c_idx]
    except IndexError:
        return "skipped_oob"

    original_text = edit.get("original_text", "").strip()
    if original_text and original_text in cell.text:
        cell.text = cell.text.replace(original_text, new_text, 1)
        return "replaced"

    cell.text = new_text
    return "overridden"


# ── Insert new paragraph after last block in document ────────────────────────

def _insert_paragraph(doc: Document, new_text: str) -> None:
    """Append a new paragraph at the end of the document body."""
    doc.add_paragraph(new_text)


# ── Public API ────────────────────────────────────────────────────────────────

def apply_edits(doc: Document, edits: List[dict]) -> List[dict]:
    """
    Apply LLM-generated edits to an in-memory Document using the 4-stage locator.

    Args:
        doc:   In-memory Document (mutated in place).
        edits: List of edit dicts from the pipeline. Expected keys:
               type, original_text, new_text, id (optional),
               row_index / col_index (tables), plan_id (optional),
               source_row_id (optional), target_hint (optional).

    Returns:
        List of edit dicts, each augmented with:
          status, match_type, confidence, plan_id, source_row_id
    """
    if not edits:
        return []

    blocks  = get_blocks(doc)
    results = []

    for edit in edits:
        # Coerce id to int (LLM sometimes returns string)
        raw_id = edit.get("id")
        if raw_id is not None:
            try:
                raw_id = int(raw_id)
            except (ValueError, TypeError):
                raw_id = None

        e_type        = edit.get("type", "paragraph").lower()
        target_hint   = edit.get("target_hint") or edit.get("original_text", "")
        plan_id       = int(edit.get("plan_id", 0))
        source_row_id = str(edit.get("source_row_id", ""))

        # ── If edit carries an explicit block id, use it as a hint first ──
        if raw_id is not None and 0 <= raw_id < len(blocks):
            named_block = blocks[raw_id]
            named_text  = named_block.text if isinstance(named_block, Paragraph) else ""
            # Accept it only if the hint loosely appears in that block
            if target_hint and target_hint[:20].lower() in named_text.lower():
                block, b_idx, match_type, confidence = named_block, raw_id, "exact", 1.0
            else:
                block, b_idx, match_type, confidence = locate_block(
                    blocks, target_hint, plan_id, source_row_id
                )
        else:
            block, b_idx, match_type, confidence = locate_block(
                blocks, target_hint, plan_id, source_row_id
            )

        # ── Confidence gate ───────────────────────────────────────────────
        decision = confidence_decision(confidence, edit.get("section_name", ""), target_hint)

        if decision == "skip":
            results.append({
                **edit,
                "status":        "skipped_low_confidence",
                "match_type":    match_type,
                "confidence":    round(confidence, 3),
                "plan_id":       plan_id,
                "source_row_id": source_row_id,
            })
            continue

        # ── Apply ─────────────────────────────────────────────────────────
        if match_type == "insert_fallback" or block is None:
            # Stage 4 safe insert — append to document
            new_text = edit.get("new_text", "").strip()
            if new_text:
                _insert_paragraph(doc, new_text)
                status = "inserted_fallback"
            else:
                status = "skipped_empty"
        elif e_type == "table" and isinstance(block, Table):
            status = _apply_table_edit(block, edit)
        elif isinstance(block, Paragraph):
            status = _apply_paragraph_edit(block, edit, match_type)
        else:
            # Type mismatch — coerce to paragraph edit if possible
            if isinstance(block, Paragraph):
                status = _apply_paragraph_edit(block, edit, match_type) + "_coerced"
            else:
                status = "skipped_type_mismatch"

        logger.info(
            f"[Applier] plan={plan_id} section='{edit.get('section_name','')}' "
            f"match={match_type} conf={confidence:.2f} status={status}"
        )
        results.append({
            **edit,
            "status":        status,
            "match_type":    match_type,
            "confidence":    round(confidence, 3),
            "plan_id":       plan_id,
            "source_row_id": source_row_id,
        })

    return results
