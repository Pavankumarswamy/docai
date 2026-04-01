"""
validator.py – Edit Validator for DOCAI

Validates LLM-generated edits BEFORE they are applied to the document.
Extracted from multi_agents.py::reviewer_node and hardened.

Validation rules (deterministic — no LLM involved):
  1. Business language  — reject edits containing forbidden words
  2. Heading protection — reject edits that target a section heading
  3. Non-empty check    — reject edits with empty new_text
  4. No-op check        — reject edits where new_text == original_text
  5. Length sanity      — reject new_text longer than 10× original_text
  6. Duplication guard  — reject new_text already present verbatim in the section

Returns: (valid_edits: list[dict], report: list[dict])
  - valid_edits: edits that passed all checks
  - report:      list of {"edit": ..., "reason": ...} for every rejection
"""

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ── Rules ─────────────────────────────────────────────────────────────────────

_FORBIDDEN_WORDS = re.compile(
    r'\b(bug|bugs|bugfix|issue|issues|defect|defects|error|errors|'
    r'fix(ed|ing)?|resolve[ds]?|resolving|patch(ed|ing)?|workaround)\b',
    re.IGNORECASE,
)

# Patterns that indicate the edit is targeting a heading
_HEADING_PATTERNS = re.compile(
    r'^\s*(\d+[\.\d]*\s+)?'                            # optional numbering
    r'(introduction|overview|scope|description|'
    r'architecture|requirements?|conclusion|summary|'
    r'appendix|references?|background|purpose)\s*$',
    re.IGNORECASE,
)


# ── Public API ─────────────────────────────────────────────────────────────────

def validate_edits(
    edits: List[dict],
    section_name: str,
    section_content: str = "",
) -> Tuple[List[dict], List[dict]]:
    """
    Validate a list of LLM-generated edit dicts.

    Args:
        edits:           List of edit dicts from the Editor/Refiner.
        section_name:    Name of the section being edited (heading protection).
        section_content: Full text of the section (duplication guard).

    Returns:
        (valid_edits, rejections)
        - valid_edits: safe to apply
        - rejections:  [{"edit": ..., "reason": "..."}] for audit log
    """
    valid    = []
    rejected = []
    section_lower = section_content.lower()

    for edit in edits:
        reason = _check_edit(edit, section_name, section_lower)
        if reason:
            logger.debug(f"[Validator] Rejected edit — {reason}: {str(edit)[:120]}")
            rejected.append({"edit": edit, "reason": reason})
        else:
            valid.append(edit)

    if rejected:
        logger.info(
            f"[Validator] Section '{section_name}': "
            f"{len(valid)} valid, {len(rejected)} rejected."
        )
    return valid, rejected


def sanitize_new_text(text: str) -> str:
    """
    Replace forbidden words in generated text with professional alternatives.
    Used as a soft-fix pass before full rejection.
    """
    replacements = {
        r'\bbug(s)?\b':            "process gap",
        r'\bissue(s)?\b':          "concern",
        r'\bdefect(s)?\b':         "discrepancy",
        r'\berror(s)?\b':          "exception",
        r'\bfixed\b':              "updated",
        r'\bresolve[ds]?\b':       "addressed",
        r'\bworkaround(s)?\b':     "interim approach",
    }
    result = text
    for pattern, replacement in replacements.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


# ── Internal checks ───────────────────────────────────────────────────────────

def _check_edit(edit: dict, section_name: str, section_lower: str) -> str | None:
    """
    Return a rejection reason string, or None if the edit is valid.
    """
    new_text      = str(edit.get("new_text",      "")).strip()
    original_text = str(edit.get("original_text", "")).strip()

    # 1. Non-empty check
    if not new_text:
        return "empty new_text"

    # 2. No-op check
    if new_text == original_text:
        return "new_text identical to original_text"

    # 3. Heading protection — reject if new_text looks like a bare heading rewrite
    if _HEADING_PATTERNS.match(new_text):
        return f"new_text looks like a heading rewrite: {new_text[:60]}"

    # 4. Heading protection — reject if original_text IS the section heading
    if original_text and original_text.strip().lower() == section_name.strip().lower():
        return "edit targets section heading directly"

    # 5. Forbidden language check
    match = _FORBIDDEN_WORDS.search(new_text)
    if match:
        return f"forbidden word '{match.group()}' in new_text"

    # 6. Length sanity (LLM sometimes replaces 5 words with 500)
    orig_len = len(original_text) if original_text else 1
    if len(new_text) > max(orig_len * 10, 2000):
        return f"new_text is {len(new_text)} chars vs original {orig_len} chars — too long"

    # 7. Duplication guard (skip for insert actions which have no original_text)
    if original_text and new_text.lower() in section_lower:
        return "new_text already present verbatim in section"

    return None
