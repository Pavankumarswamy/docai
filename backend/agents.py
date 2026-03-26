"""
agents.py – Production Pipeline Orchestrator for DOCAI

Orchestrates all 9 modules:

  1. input_layer        → read_docx, read_csv (cached)
  2. document_processor → extract_sections_from_docx
  3. relevance_engine   → get_relevant_rows, rows_to_context_string
  4. multi_agents       → Retriever → Editor → Reviewer → Refiner
  5. edit_engine        → apply_edits (with 3-tier fallback)
  6. change_tracker     → apply_tracked_changes (inline strikethrough/highlight)
  7. output_manager     → get_output_path, save_document
  8. run_logger         → RunTracker per document
  9. results_generator  → generate_results (for API response)
"""

import copy
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from input_layer import list_docx_files, read_csv, read_docx
from document_processor import extract_sections_from_docx, docx_to_html
from relevance_engine import get_relevant_rows, rows_to_context_string
from multi_agents import multi_agent_pipeline
from edit_engine import apply_edits
from change_tracker import apply_tracked_changes
from output_manager import get_output_path, save_document
from run_logger import configure_run_logger, RunTracker
from results_generator import generate_results

configure_run_logger()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------

def run_pipeline(
    run_id: str,
    doc_folder: str,
    excel_file: str,
    team_name: str,
    leader_name: str,
    branch_name: str,
    runs: dict,
) -> dict:
    """
    Production Document Update Pipeline.

    For each .docx in doc_folder:
      - Extracts and filters sections
      - Selects relevant CSV rows per section (Relevance Engine)
      - Runs multi-agent LangGraph pipeline
      - Applies edits with 3-tier fallback (Edit Engine)
      - Applies inline change tracking (Change Tracker)
      - Saves versioned output to output/ folder (Output Manager)
      - Backs up original to .backup/
    """
    start_time = datetime.now(timezone.utc)
    live = runs[run_id]["live"]
    all_fixes = []

    def update_live(phase: str = None, message: str = None, append_terminal: str = None):
        if phase:
            live["phase"] = phase
        if message:
            live["message"] = message
        if append_terminal:
            live["terminal_output"] = live.get("terminal_output", "") + append_terminal + "\n"
        if phase or message:
            logger.info(f"[{run_id}] [{phase}] {message}")

    try:
        folder_path = Path(doc_folder)
        excel_path  = Path(excel_file)

        # ── Validate inputs ───────────────────────────────────────────────
        if not folder_path.exists() or not folder_path.is_dir():
            update_live("error", "Folder path does not exist or is not a directory.")
            return {}
        if not excel_path.exists() or not excel_path.is_file():
            update_live("error", "Excel file does not exist.")
            return {}

        update_live("discovery", "Scanning folder for Word documents…")

        # ── Discover files ────────────────────────────────────────────────
        docx_files = list_docx_files(folder_path)
        if not docx_files:
            update_live("done", "No Word documents found in the selected folder.")
            return {}
        update_live("discovery", f"Found {len(docx_files)} Word document(s).")

        # ── Load CSV data (cached) ────────────────────────────────────────
        update_live("execution", f"Loading context data from {excel_path.name}…")
        all_rows = read_csv(excel_path)
        if not all_rows:
            update_live("done", "No data rows found in the Excel/CSV file.")
            return {}
        update_live(append_terminal=f">>> Loaded {len(all_rows)} context row(s).")

        # ── Setup output and backup dirs ──────────────────────────────────
        backup_dir = folder_path / ".backup"
        backup_dir.mkdir(exist_ok=True)

        output_dir = folder_path / "output"
        output_dir.mkdir(exist_ok=True)

        live["files"] = [{"path": f.name, "type": "file"} for f in docx_files]
        ci_timeline = []

        # ══════════════════════════════════════════════════════════════════
        # Document loop
        # ══════════════════════════════════════════════════════════════════
        for doc_idx, doc_file in enumerate(docx_files):
            doc_rel_path = str(doc_file.relative_to(folder_path)).replace("\\", "/")
            update_live("execution", f"Processing {doc_file.name} [{doc_idx + 1}/{len(docx_files)}]")

            tracker = RunTracker(run_id, doc_file.name)

            # Backup original if not already backed up
            backup_path = backup_dir / doc_file.name
            if not backup_path.exists():
                shutil.copy2(doc_file, backup_path)
                logger.info(f"[{run_id}] Backup: {backup_path}")

            # ── Extract sections ──────────────────────────────────────────
            sections = extract_sections_from_docx(str(doc_file))
            if not sections:
                update_live(append_terminal=f">>> No sections found in {doc_file.name}. Skipping.")
                continue

            for sec in sections:
                tracker.section_detected(sec["name"])

            update_live(append_terminal=f">>> {len(sections)} section(s) detected in {doc_file.name}.")

            ci_timeline.append({
                "iteration":       doc_idx + 1,
                "status":          "PROCESS",
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "problems_count":  len(all_rows),
                "message":         f"Processing {len(sections)} sections",
            })
            live["iterations"] = ci_timeline

            # Load fresh in-memory copy for edit application
            working_doc = read_docx(doc_file)
            doc_edits_total = []

            # ══════════════════════════════════════════════════════════════
            # Section loop
            # ══════════════════════════════════════════════════════════════
            for section in sections:
                s_name = section["name"]

                if section["skip"]:
                    tracker.section_skipped(s_name)
                    update_live(append_terminal=f"[SKIP] {s_name}")
                    continue

                # ── Relevance Engine ──────────────────────────────────────
                relevant_rows = get_relevant_rows(
                    section_name=s_name,
                    section_content=section["content"],
                    all_rows=all_rows,
                    top_k=30,
                )
                if not relevant_rows:
                    tracker.section_skipped(s_name, reason="no relevant data")
                    update_live(append_terminal=f"[SKIP] {s_name} — no relevant context rows.")
                    continue

                context_str = rows_to_context_string(relevant_rows)
                update_live("fixing", f"Running Agent Pipeline on: {s_name}")

                # ── LangGraph Pipeline ────────────────────────────────────
                tracker.llm_call("retriever+editor+reviewer+refiner", s_name)
                initial_state = {
                    "section_name":      s_name,
                    "original_section":  section["content"],
                    "all_relevant_rows": context_str,
                    "focused_rows":      "",
                    "pass1_edits":       [],
                    "final_edits":       [],
                    "explanation":       "",
                    "errors":            [],
                }

                try:
                    result_state = multi_agent_pipeline.invoke(initial_state)
                except Exception as e:
                    tracker.error(f"LangGraph invoke [{s_name}]", e)
                    update_live(append_terminal=f"[ERROR] {s_name}: LLM pipeline error → {e}. Retaining original.")
                    continue

                final_edits = result_state.get("final_edits", [])
                explanation = result_state.get("explanation", "No explanation.")

                if not final_edits:
                    update_live(append_terminal=f"[{s_name}] No updates needed → retained.")
                    tracker.section_processed(s_name, 0)
                    continue

                # ── Apply edits to working document ───────────────────────
                applied = apply_edits(working_doc, final_edits)

                # ── Apply inline change tracking ──────────────────────────
                apply_tracked_changes(working_doc, final_edits)

                doc_edits_total.extend(applied)
                tracker.section_processed(s_name, len(applied))
                update_live(append_terminal=f"[{s_name}] {len(applied)} edit(s) applied. {explanation}")

                all_fixes.append({
                    "file":          doc_rel_path,
                    "section":       s_name,
                    "problem":       f"Section Review: {s_name}",
                    "bug_type":      "DOCUMENT_EDIT",
                    "error_message": explanation,
                    "status":        "applied",
                    "agent":         "DOCAI-LangGraph",
                    "edits":         applied,
                })

            # ── Save output document ──────────────────────────────────────
            if doc_edits_total:
                output_path = get_output_path(doc_file, folder_path)
                save_document(working_doc, output_path)
                update_live(append_terminal=f">>> Saved: {output_path.relative_to(folder_path)}")
            else:
                update_live(append_terminal=f">>> No changes for {doc_file.name}.")

            tracker.summary()
            update_live(
                append_terminal=(
                    f"\n[STATS] {doc_file.name}: "
                    f"processed={tracker.sections_processed}, "
                    f"skipped={tracker.sections_skipped}, "
                    f"llm_calls={tracker.llm_calls}, "
                    f"errors={len(tracker.errors)}\n"
                )
            )

        # ── Refresh file listing ──────────────────────────────────────────
        live["files"] = [
            {"path": str(f.relative_to(folder_path)).replace("\\", "/"), "type": "file"}
            for f in folder_path.rglob("*.*")
            if not f.name.startswith("~$") and f.exists()
        ]

        # ── Write edits log (internal only) ──────────────────────────────
        edits_log_path = folder_path / "edits_log.json"
        with open(edits_log_path, "w", encoding="utf-8") as f:
            json.dump(all_fixes, f, indent=4, ensure_ascii=False)

        runs[run_id]["status"] = "completed"
        update_live("done", "✅ All documents processed successfully.")

        end_time = datetime.now(timezone.utc)
        results = generate_results(
            run_id=run_id,
            repo_url=doc_folder,
            team_name=team_name,
            leader_name=leader_name,
            branch_name=branch_name,
            fixes=all_fixes,
            ci_iterations=ci_timeline,
            start_time=start_time,
            end_time=end_time,
            final_status="PASSED",
            output_dir=str(folder_path),
        )
        return results

    except Exception as e:
        logger.exception(f"[{run_id}] Pipeline error: {e}")
        update_live("error", f"Pipeline failed: {e}")
        return {}
