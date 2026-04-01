"""
planner.py – Deterministic Edit Planner for DOCAI

Sits between the Retriever and Editor in the LangGraph pipeline.

Purpose
-------
Before the Editor generates any text, the Planner produces a STRUCTURED
JSON plan that specifies *what* to change, *where*, and *why*.
This eliminates hallucinated rewrites: the Editor can only act on items
that appear in the approved plan.

Plan item schema
----------------
{
  "action":      "modify" | "insert" | "skip",
  "target_type": "paragraph" | "list_item" | "table_cell",
  "target_hint": "<5-10 word excerpt from the section to locate the block>",
  "intent":      "<concise business-language description of the change>",
  "priority":    1 | 2 | 3          # 1=high, 2=medium, 3=low
}

Rules enforced on the plan (not on the edit)
--------------------------------------------
- max 10 plan items per section
- action="skip" is valid and means: matched row is noted but no edit needed
- target_hint must quote real text from the section — verified at parse time
- intent must NOT contain: bug, issue, defect, error, fix, resolve
"""

import json
import logging
from typing import List

from llm_client import _call_ollama, _strip_markdown

logger = logging.getLogger(__name__)


# ── Forbidden intent words (same list as Validator) ──────────────────────────
_FORBIDDEN = {"bug", "issue", "defect", "error", "fix", "resolve", "patch"}

PLAN_SCHEMA = """\
[
  {
    "action":      "modify" | "insert" | "skip",
    "target_type": "paragraph" | "list_item" | "table_cell",
    "target_hint": "exact 5-10 word excerpt from section content",
    "intent":      "professional business-language description of the change",
    "priority":    1
  }
]"""

_SYSTEM_PROMPT = (
    "You are an Edit Planner for a Document Transformation System.\n"
    "Your job is to produce a STRUCTURED PLAN of what to update in a document section.\n\n"
    "RULES:\n"
    "1. Output ONLY a valid JSON array — no markdown, no prose.\n"
    "2. Maximum 10 plan items.\n"
    "3. 'target_hint' MUST quote a real phrase (5-10 words) from the section content.\n"
    "4. 'intent' must use professional language:\n"
    "   NEVER: bug, issue, defect, error, fix, resolve, patch\n"
    "   ALWAYS: 'The system now...', 'Validation has been introduced...', "
    "'The process has been enhanced to...'\n"
    "5. If a context row is not relevant to this section, set action='skip'.\n"
    "6. Return an empty array [] if no meaningful updates are needed.\n\n"
    f"Output schema:\n{PLAN_SCHEMA}"
)


# ── Public API ────────────────────────────────────────────────────────────────

def build_edit_plan(
    section_name: str,
    section_content: str,
    focused_rows: str,
) -> List[dict]:
    """
    Ask the LLM to produce a structured edit plan for one section.

    Args:
        section_name:    Heading of the section.
        section_content: Full text of the section (capped at 4000 chars).
        focused_rows:    Pre-filtered CSV context (capped at 2500 chars).

    Returns:
        List of plan-item dicts.  Empty list = no changes needed.
    """
    user_prompt = (
        f"### Section: {section_name}\n\n"
        f"### Section Content:\n{section_content[:4000]}\n\n"
        f"### Context Data:\n{focused_rows[:2500]}\n\n"
        "Produce the edit plan JSON array."
    )

    try:
        raw = _call_ollama([
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ])
        cleaned = _strip_markdown(raw)
        plan = json.loads(cleaned)

        if not isinstance(plan, list):
            logger.warning("[Planner] LLM returned non-list plan — wrapping.")
            plan = [plan] if isinstance(plan, dict) else []

        validated = _validate_plan_items(plan, section_content)
        logger.debug(f"[Planner] Section '{section_name}': {len(validated)} plan items.")
        return validated

    except Exception as e:
        logger.warning(f"[Planner] Failed for '{section_name}': {e}. Returning empty plan.")
        return []


# ── Internal validation ───────────────────────────────────────────────────────

def _validate_plan_items(plan: list, section_content: str) -> List[dict]:
    """
    Sanitise plan items:
    - Remove items whose intent contains forbidden words.
    - Discard items where target_hint cannot be loosely found in section_content.
    - Cap at 10 items.
    - Normalise action values.
    """
    valid_actions = {"modify", "insert", "skip"}
    valid_types   = {"paragraph", "list_item", "table_cell"}
    section_lower = section_content.lower()
    result = []

    for item in plan[:10]:
        if not isinstance(item, dict):
            continue

        action      = str(item.get("action", "modify")).lower().strip()
        target_type = str(item.get("target_type", "paragraph")).lower().strip()
        target_hint = str(item.get("target_hint", "")).strip()
        intent      = str(item.get("intent", "")).strip()
        priority    = item.get("priority", 2)

        # Normalise
        if action not in valid_actions:
            action = "modify"
        if target_type not in valid_types:
            target_type = "paragraph"
        try:
            priority = int(priority)
        except (ValueError, TypeError):
            priority = 2

        # Reject forbidden language in intent
        intent_lower = intent.lower()
        if any(word in intent_lower for word in _FORBIDDEN):
            logger.debug(f"[Planner] Rejected plan item — forbidden word in intent: {intent[:60]}")
            continue

        # Skip items without a hint (can't locate them)
        if not target_hint:
            continue

        # Verify hint is loosely present (first 4 words match)
        hint_words = target_hint.lower().split()
        probe = " ".join(hint_words[:4])
        if probe and probe not in section_lower and action != "insert":
            logger.debug(f"[Planner] target_hint not found in section, converting to insert: {probe}")
            action = "insert"

        result.append({
            "action":      action,
            "target_type": target_type,
            "target_hint": target_hint,
            "intent":      intent,
            "priority":    priority,
        })

    return result


def plan_to_context_string(plan: List[dict]) -> str:
    """
    Render the edit plan as a compact string for the Editor prompt.
    """
    if not plan:
        return "(No edit plan — no changes needed.)"
    lines = []
    for i, item in enumerate(plan, 1):
        lines.append(
            f"[{i}] action={item['action']} | type={item['target_type']} | "
            f"priority={item['priority']}\n"
            f"     target: \"{item['target_hint']}\"\n"
            f"     intent: {item['intent']}"
        )
    return "\n".join(lines)
