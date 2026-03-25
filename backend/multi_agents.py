import json
import logging
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END
from llm_client import _call_nvidia, _strip_markdown

logger = logging.getLogger(__name__)

class AgentState(TypedDict):
    section_name: str
    original_section: str
    csv_rows: str
    pass1_edits: List[dict]
    final_edits: List[dict]
    explanation: str
    errors: list

BUSINESS_LANGUAGE_RULES = (
    "CRITICAL BUSINESS LANGUAGE RULE:\n"
    "You MUST NEVER output phrases like 'bug fixed', 'issue resolved', 'defect corrected', or 'error handled' in your explanation.\n"
    "Instead, you MUST use professional business language such as: 'The system now...', 'The process has been enhanced to...', 'Validation has been introduced...', 'Added logic to...'.\n"
)

def editor_node(state: AgentState):
    """Pass 1: Generate initial edits based on Section and CSV rows."""
    sys_prompt = (
        "You are an expert Document Editor AI. Analyze the section content and the provided context data (CSV rows), and determine EXACTLY what text to replace.\n"
        "### RULES:\n"
        "1. Response MUST be a valid JSON object. No markdown blocks.\n"
        "2. PRESERVE STRUCTURE: Do NOT change section numbers/headers. Keep formatting exactly the same.\n"
        "3. PRECISION: Provide a specific 'original_text' fragment from the paragraph/cell. If 'new_text' is different, a change is applied. If inserting text, include adjacent original text to replace with 'original text + new text'.\n"
        f"{BUSINESS_LANGUAGE_RULES}\n"
        "{\n"
        '  "edits": [\n'
        '    {\n'
        '      "type": "paragraph" | "table",\n'
        '      "id": <index_number_from_context>,\n'
        '      "original_text": "The exact text fragment to replace.",\n'
        '      "new_text": "The updated text fragment.",\n'
        '      "row_index": <int_or_null>,\n'
        '      "col_index": <int_or_null>\n'
        '    }\n'
        "  ],\n"
        '  "explanation": "Brief reasoning using business language ONLY."\n'
        "}"
    )
    user_prompt = f"### Section Content:\n{state.get('original_section', '')}\n\n### Context Data (Apply relevant rows):\n{state.get('csv_rows', '')}\n\nProvide JSON edits."
    
    try:
        resp = _call_nvidia([{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}])
        data = json.loads(_strip_markdown(resp))
        edits = data.get("edits", [])
        return {"pass1_edits": edits, "explanation": data.get("explanation", "")}
    except Exception as e:
        logger.error(f"Editor node error: {e}")
        return {"errors": [f"Editor failed: {e}"], "pass1_edits": []}

def reviewer_node(state: AgentState):
    """Review pass 1 output for bug language and structure constraints."""
    if not state.get("pass1_edits"):
        return {}
        
    sys_prompt = (
        "You are a Quality Assurance Reviewer. Your job is to verify edits.\n"
        f"{BUSINESS_LANGUAGE_RULES}\n"
        "Ensure no structure is broken. Return the identical JSON structure as received, modifying only the 'explanation' to remove unacceptable words, or filtering out edits that rewrite entire headers.\n"
    )
    payload = {"edits": state['pass1_edits'], "explanation": state.get('explanation', '')}
    user_prompt = f"### Proposed Edits (JSON):\n{json.dumps(payload, indent=2)}\n\nReturn fixed JSON."
    
    try:
        resp = _call_nvidia([{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}])
        data = json.loads(_strip_markdown(resp))
        edits = data.get("edits", state['pass1_edits'])
        return {"pass1_edits": edits, "explanation": data.get("explanation", state.get('explanation', ''))}
    except Exception as e:
        logger.error(f"Reviewer node error: {e}")
        return {}

def refiner_node(state: AgentState):
    """Pass 2: Refine the content, remove redundancy, ensure correctness."""
    if not state.get("pass1_edits"):
        return {"final_edits": [], "explanation": "No meaningful updates -> skip"}
        
    sys_prompt = (
        "You are the Final Documentation Coordinator. Review the original section, Context Data, and the Proposed Edits.\n"
        "Your task: Refine the edits to improve clarity, remove redundancy, and finalize the payload.\n"
        f"{BUSINESS_LANGUAGE_RULES}\n"
        "Output the FINAL JSON edits list.\n"
        "{\n"
        '  "edits": [...] \n'
        '  "explanation": "..." \n'
        "}"
    )
    user_prompt = (
        f"### Original Section Content:\n{state.get('original_section', '')}\n\n"
        f"### Context Data:\n{state.get('csv_rows', '')}\n\n"
        f"### Proposed Edits (Pass 1):\n{json.dumps({'edits': state.get('pass1_edits', [])}, indent=2)}\n\n"
        "Provide final JSON."
    )
    
    try:
        resp = _call_nvidia([{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}])
        data = json.loads(_strip_markdown(resp))
        return {"final_edits": data.get("edits", []), "explanation": data.get("explanation", "")}
    except Exception as e:
        logger.error(f"Refiner error: {e}")
        # Failsafe: fallback to pass 1
        return {"final_edits": state.get("pass1_edits", []), "errors": state.get("errors", []) + [f"Refiner failed, fallback to pass 1. {e}"]}


graph = StateGraph(AgentState)
graph.add_node("editor", editor_node)
graph.add_node("reviewer", reviewer_node)
graph.add_node("refiner", refiner_node)

graph.add_edge("editor", "reviewer")
graph.add_edge("reviewer", "refiner")
graph.add_edge("refiner", END)

graph.set_entry_point("editor")

multi_agent_pipeline = graph.compile()
