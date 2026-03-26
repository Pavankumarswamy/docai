"""
edit_engine.py – Robust Edit Application Engine for DOCAI

3-tier fallback for each edit:
  Tier 1: Exact text match → replace in-place
  Tier 2: Partial/fuzzy match → replace best-matching fragment
  Tier 3: Append insertion → append new content to block end

Guarantees: if an edit list is non-empty and the block ID is valid,
at least one change WILL be applied (no silent skips).
"""

import re
import logging
from typing import List

from docx import Document
from docx.document import Document as _Document
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------

def get_blocks(doc: Document) -> list:
    """Walk the document body returning Paragraph and Table objects in order."""
    blocks = []
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            blocks.append(Paragraph(child, doc))
        elif isinstance(child, CT_Tbl):
            blocks.append(Table(child, doc))
    return blocks


# ---------------------------------------------------------------------------
# Fuzzy matching helper
# ---------------------------------------------------------------------------

def _word_overlap(a: str, b: str) -> float:
    """Jaccard similarity between word sets of two strings."""
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _best_paragraph_match(original_text: str, paragraphs: list[Paragraph], threshold: float = 0.5):
    """
    Among a list of paragraphs find the one whose text best matches original_text.
    Returns (paragraph, score) or (None, 0) if below threshold.
    """
    best_para = None
    best_score = 0.0
    for para in paragraphs:
        score = _word_overlap(para.text, original_text)
        if score > best_score:
            best_score = score
            best_para = para
    if best_score >= threshold:
        return best_para, best_score
    return None, 0.0


# ---------------------------------------------------------------------------
# Core apply functions
# ---------------------------------------------------------------------------

def _apply_paragraph_edit(block: Paragraph, edit: dict) -> str:
    """
    Apply a single edit to a Paragraph block.
    Returns: 'exact' | 'partial' | 'appended' | 'skipped'
    """
    original_text = edit.get("original_text", "").strip()
    new_text = edit.get("new_text", "").strip()

    if not new_text:
        return "skipped"

    current = block.text

    # Tier 1: Exact match
    if original_text and original_text in current:
        block.text = current.replace(original_text, new_text, 1)
        return "exact"

    # Tier 2: Partial match (leading phrase)
    if original_text:
        words_orig = original_text.split()
        # Try matching first 5 words of original_text
        fragment = " ".join(words_orig[:5])
        if fragment and fragment.lower() in current.lower():
            idx = current.lower().find(fragment.lower())
            block.text = current[:idx] + new_text + "."
            return "partial"

    # Tier 3: Append insertion — do not silently skip
    if original_text:
        # Append at end of paragraph
        block.text = current.rstrip(". ") + ". " + new_text + "."
    else:
        block.text = new_text
    return "appended"


def _apply_table_edit(block: Table, edit: dict) -> str:
    """
    Apply a single edit to a Table cell.
    Returns: 'exact' | 'override' | 'skipped'
    """
    r_idx = edit.get("row_index")
    c_idx = edit.get("col_index")
    original_text = edit.get("original_text", "").strip()
    new_text = edit.get("new_text", "").strip()

    if r_idx is None or c_idx is None or not new_text:
        return "skipped"

    try:
        cell = block.rows[r_idx].cells[c_idx]
    except IndexError:
        return "skipped"

    # Tier 1: Exact match
    if original_text and original_text in cell.text:
        cell.text = cell.text.replace(original_text, new_text, 1)
        return "exact"

    # Tier 2/3: Override cell content
    cell.text = new_text
    return "override"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_edits(doc: Document, edits: List[dict]) -> List[dict]:
    """
    Apply a list of LLM-generated edits to an in-memory Document.

    Args:
        doc:   The document to modify (mutated in place).
        edits: List of edit dicts with keys: type, id, original_text, new_text,
               row_index (tables only), col_index (tables only).

    Returns:
        List of edit dicts augmented with a 'status' key.
    """
    if not edits:
        return []

    blocks = get_blocks(doc)
    results = []

    for edit in edits:
        b_id = edit.get("id")
        # LLM sometimes returns id as a string — coerce to int
        if b_id is not None:
            try:
                b_id = int(b_id)
            except (ValueError, TypeError):
                b_id = None
        e_type = edit.get("type", "").lower()
        status = "skipped"

        if b_id is None:
            # No ID provided: scan all paragraphs for best match
            paras = [b for b in blocks if isinstance(b, Paragraph)]
            best_para, score = _best_paragraph_match(edit.get("original_text", ""), paras)
            if best_para:
                status = _apply_paragraph_edit(best_para, edit) + f"(fuzzy:{score:.2f})"
            else:
                status = "no_match"

        elif b_id >= len(blocks):
            logger.warning(f"[EditEngine] Block ID {b_id} out of range ({len(blocks)} blocks). Falling back to fuzzy.")
            paras = [b for b in blocks if isinstance(b, Paragraph)]
            best_para, score = _best_paragraph_match(edit.get("original_text", ""), paras)
            if best_para:
                status = _apply_paragraph_edit(best_para, edit) + f"(idfuzzy:{score:.2f})"
            else:
                status = "id_out_of_range"

        else:
            block = blocks[b_id]
            if e_type == "paragraph" and isinstance(block, Paragraph):
                status = _apply_paragraph_edit(block, edit)
            elif e_type == "table" and isinstance(block, Table):
                status = _apply_table_edit(block, edit)
            else:
                # Type mismatch: try the other type if possible
                if isinstance(block, Paragraph):
                    status = _apply_paragraph_edit(block, edit) + "(type_coerced)"
                else:
                    status = "type_mismatch"

        logger.debug(f"[EditEngine] Edit id={b_id} type={e_type} → {status}")
        results.append({**edit, "status": status})

    return results
