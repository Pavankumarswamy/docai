"""
multi_agents.py – LangGraph Multi-Agent Pipeline for DOCAI

Flow: Retriever → Planner → Editor → Validator → Refiner → END

  Retriever  – narrows the pre-filtered CSV context to the 20 most relevant rows
  Planner    – produces a structured JSON edit plan (intent + target per change)
  Editor     – generates exact edits guided strictly by the plan (Pass 1)
  Validator  – deterministic rule-based check (no LLM); rejects forbidden language,
               heading rewrites, no-ops, oversized replacements
  Refiner    – Pass 2: improves clarity, removes duplication, finalises edit list

Design constraints
------------------
- Linear flow only — no conditional edges, no loops
- All LLM outputs are strict JSON (enforced via _strip_markdown + json.loads)
- Validator is rule-based (fast, deterministic, no extra LLM call)
- If any node fails, the pipeline gracefully falls back to the previous state
"""

import json
import logging
from typing import TypedDict, List, Any

from langgraph.graph import StateGraph, END
from llm_client import _call_ollama, _strip_markdown
from planner import build_edit_plan, plan_to_context_string
from validator import validate_edits, sanitize_new_text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    section_name:        str
    original_section:    str
    all_relevant_rows:   str        # full relevance-filtered context
    focused_rows:        str        # retriever's tighter subset
    edit_plan:           List[dict] # structured plan from planner (NEW)
    pass1_edits:         List[dict]
    final_edits:         List[dict]
    explanation:         str
    errors:              List[Any]


# ---------------------------------------------------------------------------
# Shared prompt fragments
# ---------------------------------------------------------------------------

BUSINESS_LANGUAGE_RULES = (
    "CRITICAL LANGUAGE RULE:\n"
    "NEVER OUTPUT: 'bug fixed', 'issue resolved', 'defect corrected', 'error handled'.\n"
    "ALWAYS USE professional process language:\n"
    "  'The system now...'  |  'The process has been enhanced to...'  |  "
    "'Validation has been introduced to...'  |  'The workflow now supports...'\n"
)

EDIT_JSON_SCHEMA = (
    "{\n"
    '  "edits": [\n'
    '    {\n'
    '      "type": "paragraph" | "table",\n'
    '      "id": <block_index_integer>,\n'
    '      "original_text": "Exact fragment to replace (must exist verbatim in section)",\n'
    '      "new_text": "Updated professional fragment",\n'
    '      "row_index": <int or null>,\n'
    '      "col_index": <int or null>\n'
    '    }\n'
    '  ],\n'
    '  "explanation": "One-sentence business-language summary"\n'
    "}"
)

STRUCTURE_RULES = (
    "STRUCTURE RULES:\n"
    "- Paragraph → stays Paragraph. List → stays List. Table → stays Table.\n"
    "- NEVER modify section heading text.\n"
    "- Merge intelligently. DO NOT overwrite entire sections or duplicate content.\n"
    "- Be crisp, professional, and structured. No long paragraphs.\n"
)


# ---------------------------------------------------------------------------
# Node: Retriever
# ---------------------------------------------------------------------------

def retriever_node(state: AgentState) -> dict:
    """
    Refine the relevance-filtered rows to a focused, concise subset (max 20 rows).
    Acts as the smart context selector before the Planner and Editor see anything.
    """
    all_rows_str = state.get("all_relevant_rows", "")
    if not all_rows_str or all_rows_str.startswith("(No relevant"):
        return {"focused_rows": all_rows_str}

    sys_prompt = (
        "You are a Context Retriever for a Document Update System.\n"
        "Given a section name and all candidate context rows, select the MOST RELEVANT "
        "20 rows maximum.\n"
        "Return ONLY the selected rows verbatim in the same format, no extra text.\n"
        f"{BUSINESS_LANGUAGE_RULES}"
    )
    user_prompt = (
        f"### Section: {state.get('section_name', '')}\n\n"
        f"### All Candidate Rows:\n{all_rows_str[:6000]}\n\n"
        "Return the most relevant rows."
    )

    try:
        resp = _call_ollama([
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": user_prompt},
        ])
        focused = resp.strip() or all_rows_str
        logger.debug(f"[Retriever] '{state.get('section_name')}' → focused context ready.")
        return {"focused_rows": focused}
    except Exception as e:
        logger.warning(f"[Retriever] Failed ({e}), using all rows as fallback.")
        return {"focused_rows": all_rows_str}


# ---------------------------------------------------------------------------
# Node: Planner (NEW)
# ---------------------------------------------------------------------------

def planner_node(state: AgentState) -> dict:
    """
    Build a structured edit plan from the focused context rows.
    The Editor will only generate edits that are in this plan.
    This is the key determinism gate: no plan item = no edit.
    """
    focused = state.get("focused_rows", "")
    if not focused or focused.startswith("(No relevant"):
        return {"edit_plan": []}

    plan = build_edit_plan(
        section_name    = state.get("section_name", ""),
        section_content = state.get("original_section", ""),
        focused_rows    = focused,
    )
    logger.debug(f"[Planner] '{state.get('section_name')}' → {len(plan)} plan items.")
    return {"edit_plan": plan}


# ---------------------------------------------------------------------------
# Node: Editor
# ---------------------------------------------------------------------------

def editor_node(state: AgentState) -> dict:
    """
    Pass 1: Generate structured edits guided by the Planner's approved plan.
    Without a plan, no edits are generated.
    """
    edit_plan = state.get("edit_plan", [])
    if not edit_plan:
        logger.debug(f"[Editor] No plan for '{state.get('section_name')}' — skipping.")
        return {"pass1_edits": [], "explanation": "No plan items — section retained."}

    plan_str = plan_to_context_string(edit_plan)

    sys_prompt = (
        "You are an Expert Document Editor AI.\n"
        "You MUST only generate edits for items listed in the EDIT PLAN below.\n"
        "Do NOT invent new edits outside the plan.\n"
        f"{BUSINESS_LANGUAGE_RULES}\n"
        f"{STRUCTURE_RULES}\n"
        "Rules:\n"
        "1. Response MUST be valid JSON — no markdown fences.\n"
        "2. 'original_text' must be an exact verbatim fragment from the section.\n"
        "3. Generate one edit per plan item (action='skip' → omit from edits list).\n"
        "4. Return an empty edits list if no plan items need changes.\n\n"
        f"Output schema:\n{EDIT_JSON_SCHEMA}"
    )
    user_prompt = (
        f"### Section Name: {state.get('section_name', '')}\n\n"
        f"### Section Content:\n{state.get('original_section', '')[:4000]}\n\n"
        f"### Edit Plan (approved items only):\n{plan_str}\n\n"
        f"### Supporting Context:\n{state.get('focused_rows', '')[:2000]}\n\n"
        "Generate JSON edits strictly following the plan."
    )

    try:
        logger.debug(f"[Editor] Processing section: {state.get('section_name')}")
        resp = _call_ollama([
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": user_prompt},
        ])
        data = json.loads(_strip_markdown(resp))
        edits = data.get("edits", [])
        return {
            "pass1_edits": edits,
            "explanation": data.get("explanation", ""),
        }
    except Exception as e:
        logger.error(f"[Editor] Error: {e}")
        return {"errors": [f"Editor failed: {e}"], "pass1_edits": []}


# ---------------------------------------------------------------------------
# Node: Validator (deterministic — NO LLM call)
# ---------------------------------------------------------------------------

def validator_node(state: AgentState) -> dict:
    """
    Deterministic rule-based validation of Pass-1 edits.
    No LLM call — fast, reliable, consistent.

    Applies two passes:
      1. Hard reject (empty text, heading rewrite, forbidden words, no-op)
      2. Soft fix    (sanitize forbidden words before hard-rejecting)
    """
    edits = state.get("pass1_edits", [])
    if not edits:
        return {}

    section_name    = state.get("section_name", "")
    section_content = state.get("original_section", "")

    # Soft-fix pass: try sanitizing forbidden words first
    softfixed = []
    for edit in edits:
        new_text = str(edit.get("new_text", ""))
        edit = {**edit, "new_text": sanitize_new_text(new_text)}
        softfixed.append(edit)

    # Hard validation pass
    valid_edits, rejections = validate_edits(softfixed, section_name, section_content)

    if rejections:
        logger.info(
            f"[Validator] '{section_name}': "
            f"{len(valid_edits)} passed, {len(rejections)} rejected — "
            + "; ".join(r["reason"] for r in rejections[:3])
        )

    return {"pass1_edits": valid_edits}


# ---------------------------------------------------------------------------
# Node: Refiner
# ---------------------------------------------------------------------------

def refiner_node(state: AgentState) -> dict:
    """
    Pass 2: Refine validated edits for clarity, conciseness, and correctness.
    Falls back to Pass-1 edits if the LLM fails.
    """
    pass1 = state.get("pass1_edits", [])
    if not pass1:
        return {"final_edits": [], "explanation": "No meaningful updates — section retained."}

    sys_prompt = (
        "You are the Final Documentation Coordinator.\n"
        "Review the original section and proposed edits.\n"
        "Refine edits to:\n"
        "  - Improve clarity and conciseness\n"
        "  - Remove redundancy or duplication\n"
        "  - Ensure all changes sound like a domain expert, not AI output\n"
        f"{BUSINESS_LANGUAGE_RULES}\n"
        f"{STRUCTURE_RULES}\n"
        "Output ONLY the final JSON edits. No markdown."
    )
    user_prompt = (
        f"### Original Section:\n{state.get('original_section', '')[:3000]}\n\n"
        f"### Context Data:\n{state.get('focused_rows', '')[:1500]}\n\n"
        f"### Pass-1 Edits:\n{json.dumps({'edits': pass1}, indent=2)[:3000]}\n\n"
        "Provide final JSON."
    )

    try:
        logger.debug(f"[Refiner] Finalizing section: {state.get('section_name')}")
        resp = _call_ollama([
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": user_prompt},
        ])
        data = json.loads(_strip_markdown(resp))
        return {
            "final_edits": data.get("edits", []),
            "explanation": data.get("explanation", ""),
        }
    except Exception as e:
        logger.error(f"[Refiner] Error ({e}). Falling back to Pass-1 edits.")
        return {
            "final_edits": pass1,
            "errors": state.get("errors", []) + [f"Refiner failed: {e}"],
        }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

graph = StateGraph(AgentState)
graph.add_node("retriever",  retriever_node)
graph.add_node("planner",    planner_node)
graph.add_node("editor",     editor_node)
graph.add_node("validator",  validator_node)
graph.add_node("refiner",    refiner_node)

graph.add_edge("retriever",  "planner")
graph.add_edge("planner",    "editor")
graph.add_edge("editor",     "validator")
graph.add_edge("validator",  "refiner")
graph.add_edge("refiner",    END)

graph.set_entry_point("retriever")

multi_agent_pipeline = graph.compile()
