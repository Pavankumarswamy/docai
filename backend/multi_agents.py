"""
multi_agents.py – LangGraph Multi-Agent Pipeline for DOCAI

Flow: Retriever → Editor → Reviewer → Refiner → END
- Linear, no loops, low latency
- Retriever:  selects up to 20 focused rows from pre-filtered set
- Editor:     Pass 1 – generate structured edits (JSON)
- Reviewer:   Pass 1 QA – enforce language rules, check structure
- Refiner:    Pass 2 – improve clarity, remove redundancy, finalize edits
"""

import json
import logging
from typing import TypedDict, List, Any

from langgraph.graph import StateGraph, END
from llm_client import _call_nvidia, _strip_markdown

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    section_name:        str
    original_section:    str
    all_relevant_rows:   str   # Full relevance-filtered context (passed by pipeline)
    focused_rows:        str   # Retriever's tighter subset (max 20)
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
    "ALWAYS USE professional process language such as:\n"
    "  'The system now...'  |  'The process has been enhanced to...'  |  "
    "'Validation has been introduced to...'  |  'Added logic to...'  |  "
    "'The workflow now supports...'\n"
)

EDIT_JSON_SCHEMA = (
    "{\n"
    '  "edits": [\n'
    '    {\n'
    '      "type": "paragraph" | "table",\n'
    '      "id": <block_index_number>,\n'
    '      "original_text": "Exact fragment to replace",\n'
    '      "new_text": "Updated professional fragment",\n'
    '      "row_index": <int or null>,\n'
    '      "col_index": <int or null>\n'
    '    }\n'
    '  ],\n'
    '  "explanation": "Concise business-language reasoning"\n'
    "}"
)

STRUCTURE_RULES = (
    "STRUCTURE RULES:\n"
    "- Paragraph → stays Paragraph. List → stays List. Table → stays Table.\n"
    "- NEVER modify section heading text.\n"
    "- Merge intelligently. DO NOT overwrite entire sections or duplicate content.\n"
    "- Avoid long paragraphs. Be crisp, professional, and structured.\n"
)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def retriever_node(state: AgentState) -> dict:
    """
    Refine the relevance-filtered rows to a focused, concise subset (max 20 rows).
    Acts as the smart context selector before the editor sees anything.
    """
    all_rows_str = state.get("all_relevant_rows", "")
    if not all_rows_str or all_rows_str.startswith("(No relevant"):
        return {"focused_rows": all_rows_str}

    sys_prompt = (
        f"You are a Context Retriever for a Document Update System.\n"
        f"Given a section name and all candidate context rows, select the MOST RELEVANT 20 rows maximum.\n"
        f"Return ONLY the selected rows verbatim in the same format, no extra text.\n"
        f"If fewer than 20 rows exist, return all of them.\n"
        f"{BUSINESS_LANGUAGE_RULES}"
    )
    user_prompt = (
        f"### Section: {state.get('section_name', '')}\n\n"
        f"### All Candidate Rows:\n{all_rows_str[:6000]}\n\n"
        f"Return the most relevant rows."
    )

    try:
        resp = _call_nvidia([
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": user_prompt},
        ])
        focused = resp.strip() or all_rows_str
        logger.debug(f"[Retriever] Section '{state.get('section_name')}' → focused context set.")
        return {"focused_rows": focused}
    except Exception as e:
        logger.warning(f"[Retriever] Failed ({e}), using all rows as fallback.")
        return {"focused_rows": all_rows_str}


def editor_node(state: AgentState) -> dict:
    """Pass 1: Generate initial structured edits from section content + focused context."""
    sys_prompt = (
        "You are an Expert Document Editor AI.\n"
        "Analyze the section content and context data. Determine EXACTLY what text to update.\n"
        f"{BUSINESS_LANGUAGE_RULES}\n"
        f"{STRUCTURE_RULES}\n"
        "Rules:\n"
        "1. Response MUST be valid JSON. No markdown fences.\n"
        "2. 'original_text' must be an exact fragment from the section.\n"
        "3. Only generate edits where updates genuinely improve the document.\n"
        "4. Return an empty edits list if no meaningful update is needed.\n\n"
        f"Output schema:\n{EDIT_JSON_SCHEMA}"
    )
    user_prompt = (
        f"### Section Name: {state.get('section_name', '')}\n\n"
        f"### Section Content:\n{state.get('original_section', '')[:4000]}\n\n"
        f"### Context Data (apply only what's relevant):\n{state.get('focused_rows', '')[:3000]}\n\n"
        "Provide JSON edits."
    )

    try:
        logger.debug(f"[Editor] Processing section: {state.get('section_name')}")
        resp = _call_nvidia([
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


def reviewer_node(state: AgentState) -> dict:
    """Review Pass-1 edits for language compliance and structural integrity."""
    if not state.get("pass1_edits"):
        return {}

    sys_prompt = (
        "You are a Quality Assurance Reviewer for a Document Update System.\n"
        "Your tasks:\n"
        "1. Remove any edit that rewrites section headings.\n"
        "2. Correct any language that violates the business language rule.\n"
        "3. Ensure no structural format rules are broken (list stays list, table stays table).\n"
        "4. Return the SAME JSON structure, only modifying where needed.\n"
        f"{BUSINESS_LANGUAGE_RULES}\n"
        "Output: same JSON schema. No markdown."
    )
    payload = {
        "edits":       state["pass1_edits"],
        "explanation": state.get("explanation", ""),
    }
    user_prompt = (
        f"### Section: {state.get('section_name')}\n"
        f"### Proposed Edits:\n{json.dumps(payload, indent=2)[:4000]}\n\n"
        "Return reviewed JSON."
    )

    try:
        logger.debug(f"[Reviewer] Reviewing section: {state.get('section_name')}")
        resp = _call_nvidia([
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": user_prompt},
        ])
        data = json.loads(_strip_markdown(resp))
        return {
            "pass1_edits": data.get("edits", state["pass1_edits"]),
            "explanation": data.get("explanation", state.get("explanation", "")),
        }
    except Exception as e:
        logger.error(f"[Reviewer] Error: {e}")
        return {}


def refiner_node(state: AgentState) -> dict:
    """Pass 2: Refine edits for clarity, conciseness, and correctness."""
    if not state.get("pass1_edits"):
        return {"final_edits": [], "explanation": "No meaningful updates — section retained."}

    sys_prompt = (
        "You are the Final Documentation Coordinator.\n"
        "Review the original section, context data, and proposed edits.\n"
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
        f"### Context Data:\n{state.get('focused_rows', '')[:2000]}\n\n"
        f"### Pass-1 Edits:\n{json.dumps({'edits': state.get('pass1_edits', [])}, indent=2)[:3000]}\n\n"
        "Provide final JSON."
    )

    try:
        logger.debug(f"[Refiner] Finalizing section: {state.get('section_name')}")
        resp = _call_nvidia([
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
            "final_edits": state.get("pass1_edits", []),
            "errors": state.get("errors", []) + [f"Refiner failed: {e}"],
        }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

graph = StateGraph(AgentState)
graph.add_node("retriever", retriever_node)
graph.add_node("editor",    editor_node)
graph.add_node("reviewer",  reviewer_node)
graph.add_node("refiner",   refiner_node)

graph.add_edge("retriever", "editor")
graph.add_edge("editor",    "reviewer")
graph.add_edge("reviewer",  "refiner")
graph.add_edge("refiner",   END)

graph.set_entry_point("retriever")

multi_agent_pipeline = graph.compile()
