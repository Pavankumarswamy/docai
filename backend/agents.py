import os
import json
import logging
import shutil
from pathlib import Path
from datetime import datetime, timezone

from document_processor import (
    extract_problems_from_excel,
    extract_text_and_tables_from_docx,
    generate_document_fix,
    apply_edits_to_docx
)
from results_generator import generate_results

logger = logging.getLogger(__name__)

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
    Autonomous Document Healing pipeline.
    repo_url here is actually the local path to the folder.
    """
    start_time = datetime.now(timezone.utc)
    live = runs[run_id]["live"]
    all_fixes = []
    
    def update_live(phase: str | None = None, message: str | None = None, append_terminal: str | None = None):
        if phase: live["phase"] = phase
        if message: live["message"] = message
        if append_terminal: live["terminal_output"] += (append_terminal + "\n")
        logger.info(f"[{run_id}] [{phase}] {message}")

    try:
        folder_path = Path(doc_folder)
        excel_path = Path(excel_file)
        if not folder_path.exists() or not folder_path.is_dir():
            update_live("error", "The provided folder path does not exist or is not a directory.")
            return {}
        if not excel_path.exists() or not excel_path.is_file():
            update_live("error", "The provided Excel file path does not exist or is not a file.")
            return {}

        update_live("discovery", "Scanning folder for Word files...")
        
        # 1. Scan for files
        docx_files = [f for f in folder_path.rglob("*.docx") if not any(part.startswith(".") for part in f.parts)]
        
        # filter out temporary office files starting with ~$
        docx_files = [f for f in docx_files if not f.name.startswith("~$")]
        excel_files = [excel_path]
        
        live["files"] = [{"path": f.name, "type": "file"} for f in docx_files] + \
                          [{"path": "edits_log.json", "type": "file"}, {"path": "results.json", "type": "file"}]
            
        update_live("discovery", f"Found {len(docx_files)} Word doc(s). Using explicit Excel file: {excel_path.name}")
        
        if not docx_files:
            update_live("done", "No Word documents found in the selected folder.")
            return {}
            
        # 2. Extract problems from Excel
        update_live("execution", f"Extracting problems from {excel_path.name}...")
        
        problems = extract_problems_from_excel(excel_path)
        update_live(append_terminal=f">>> Found {len(problems)} problem(s) in Excel.")
        
        if not problems:
            update_live("done", "No problems found in Excel.")
            return {}
            
        ci_timeline = []
        
        # Determine Backup folder
        backup_dir = folder_path / ".ggu_backup"
        backup_dir.mkdir(exist_ok=True)
        
        # 3. For each Word Document, process problems
        final_status = "PASSED"
        
        for doc_idx, doc_file in enumerate(docx_files):
            doc_rel_path = str(doc_file.relative_to(folder_path)).replace("\\", "/")
            update_live("execution", f"Processing {doc_file.name} [{doc_idx+1}/{len(docx_files)}]...")
            
            # Backup original document for 'before' view
            backup_path = backup_dir / doc_file.name
            if not backup_path.exists():
                shutil.copy2(doc_file, backup_path)
                
            doc_content = extract_text_and_tables_from_docx(str(doc_file))
            if not doc_content:
                update_live(append_terminal=f">>> Failed to extract content from {doc_file.name}")
                continue
                
            iteration_failures = len(problems)
            iter_start = datetime.now(timezone.utc)
            
            ci_timeline.append({
                "iteration": doc_idx + 1,
                "status": "PROCESS",
                "timestamp": iter_start.isoformat(),
                "problems_count": iteration_failures,
                "message": f"Processing {doc_file.name} against {len(problems)} problems",
            })
            live["iterations"] = ci_timeline
            
            for p_idx, problem_row in enumerate(problems):
                # problem_row is a dict from excel. Convert to string description
                problem_desc = "\\n".join([f"{k}: {v}" for k, v in problem_row.items() if str(v).strip()])
                update_live("fixing", f"Applying Agent LLM Fix for Problem {p_idx+1} on {doc_file.name}...")
                
                # generate fix using LLM
                fix_data = generate_document_fix(problem_desc, doc_content, run_id)
                edits = fix_data.get("edits", [])
                explanation = fix_data.get("explanation", "No explanation provided.")
                
                update_live(append_terminal=f"--- Problem {p_idx+1} ---")
                update_live(append_terminal=explanation)
                
                if edits:
                    # Apply changes back to document
                    applied_edits = apply_edits_to_docx(str(doc_file), edits, str(doc_file))
                    update_live(append_terminal=f">>> Applied {len(applied_edits)} edit(s) to {doc_file.name}")
                    
                    # Store fix info for json report
                    all_fixes.append({
                        "file": doc_rel_path,
                        "problem": problem_desc,
                        "bug_type": "DOCUMENT_EDIT",
                        "error_message": explanation,
                        "status": "fixed" if applied_edits else "failed",
                        "agent": "GGU AI-Doc-Heal-Agent",
                        "edits": applied_edits
                    })
                    
                    # Refresh doc_content for next problem
                    doc_content = extract_text_and_tables_from_docx(str(doc_file))
                else:
                    update_live(append_terminal=f">>> No edits returned for Problem {p_idx+1}")
                    
        # Write edits log
        edits_log_path = folder_path / "edits_log.json"
        with open(edits_log_path, "w", encoding="utf-8") as f:
            json.dump(all_fixes, f, indent=4)
            
        # Re-index files for frontend
        live["files"] = [{"path": str(f.relative_to(folder_path)).replace("\\", "/"), "type": "file"} for f in list(folder_path.rglob("*.*")) if not str(f.name).startswith("~$")]
            
        update_live("done", f"✅ Document processing complete. Log saved to edits_log.json")
        
        end_time = datetime.now(timezone.utc)
        results = generate_results(
            run_id=run_id,
            repo_url=doc_folder,
            team_name=team_name,
            leader_name=leader_name,
            branch_name="DOC_UPDATE",
            fixes=all_fixes,
            ci_iterations=ci_timeline,
            start_time=start_time,
            end_time=end_time,
            final_status="PASSED",
            output_dir=str(folder_path),
        )
        return results

    except Exception as e:
        logger.exception(f"Pipeline error: {e}")
        update_live("error", f"Pipeline failed: {e}")
        return {}


