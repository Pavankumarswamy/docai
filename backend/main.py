import sys
import asyncio
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass # Handle cases where the loop is already running

"""
main.py – FastAPI backend for the Autonomous CI/CD Healing Agent
Endpoints:
  POST /analyze  – start an agent pipeline run
  GET  /results/{run_id} – poll for live status / final results
"""

import os
import uuid
import shutil
import logging
import zipfile
import threading
import sqlite3
import json
import asyncio
import subprocess
import time
import platform
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents import run_pipeline
from llm_client import _call_nvidia, _strip_markdown
from document_processor import docx_to_html

# ---------------------------------------------------------------------------
# Database & Path Initialization
# ---------------------------------------------------------------------------
from state import ROOT_DIR, runs, RUN_PATHS, save_projects, load_projects

if getattr(sys, 'frozen', False):
    APP_DATA = ROOT_DIR
    DB_PATH = APP_DATA / "chat_history.db"
    EXE_DIR = Path(sys.executable).parent
else:
    BACKEND_DIR = Path(__file__).parent
    DB_PATH = BACKEND_DIR / "chat_history.db"

CLONES_DIR = ROOT_DIR / "cloned_repos"
CLONES_DIR.mkdir(parents=True, exist_ok=True)



def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            session_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# State and Persistence are now handled in state.py


# ---------------------------------------------------------------------------
# App Initialization
# ---------------------------------------------------------------------------
app = FastAPI(title="AI Document Healing Agent API", version="1.0.0")

@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/debug/state")
async def debug_state():
    return {
        "RUN_PATHS": {k: str(v) for k, v in RUN_PATHS.items()},
        "runs_keys": list(runs.keys())
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ConfigUpdate(BaseModel):
    github_pat: Optional[str] = None
    nvidia_api_key: Optional[str] = None

class AnalyzeRequest(BaseModel):
    doc_folder: str
    excel_file: str
    team_name: str
    leader_name: str

class SaveFileRequest(BaseModel):
    run_id: str
    file_path: str
    content: str

class CreateItemRequest(BaseModel):
    run_id: str
    parent_path: str
    name: str
    type: str

class TerminalRequest(BaseModel):
    run_id: str
    command: str
    cwd: Optional[str] = None

class LocalOpenRequest(BaseModel):
    path: str
    team_name: str
    leader_name: str

class BrowseFolderRequest(BaseModel):
    path: Optional[str] = None

# ... (inside models section) ...

@app.get("/config")
async def get_config():
    from state import GLOBAL_CONFIG
    return {
        "github_pat_set": bool(GLOBAL_CONFIG.get("github_pat")),
        "nvidia_api_key_set": bool(GLOBAL_CONFIG.get("nvidia_api_key"))
    }

@app.post("/config")
async def update_config(req: ConfigUpdate):
    from state import GLOBAL_CONFIG, save_projects
    if req.github_pat:
        GLOBAL_CONFIG["github_pat"] = req.github_pat
    if req.nvidia_api_key:
        GLOBAL_CONFIG["nvidia_api_key"] = req.nvidia_api_key
    save_projects()
    return {"status": "ok", "message": "Configuration updated"}

# ...

class DeleteItemRequest(BaseModel):
    run_id: str
    path: str

class AnalyzeResponse(BaseModel):
    run_id: str
    message: str
    branch_name: str

class ChatRequest(BaseModel):
    message: str
    run_id: Optional[str] = None
    file_path: Optional[str] = None
    file_content: Optional[str] = None
    api_data: Optional[dict] = None
    repo_context: Optional[list[dict]] = None
    session_id: Optional[str] = "default"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_repo_path(run_id: str) -> Path | None:
    """Resolve the physical disk path for a given run_id."""
    logger.info(f"[DEBUG] get_repo_path checking run_id: '{run_id}'")
    if run_id in RUN_PATHS:
        logger.info(f"[DEBUG] Found in RUN_PATHS: {RUN_PATHS[run_id]}")
        return RUN_PATHS[run_id]
    
    logger.info(f"[DEBUG] Not in RUN_PATHS. Keys: {list(RUN_PATHS.keys())}")
    
    # Fallback to scanning CLONES_DIR (for past sessions or cloned repos)
    if not CLONES_DIR.exists():
        return None
        
    try:
        # Check if run_id is a prefix of any directory in CLONES_DIR
        for item in CLONES_DIR.iterdir():
            if item.is_dir():
                prefix = item.name.split('_')[0]
                if prefix == run_id:
                    return item
        return None
    except Exception:
        return None

def add_chat_message(run_id: str, session_id: str, role: str, content: str):
    """Save a chat message to both the central DB and the workspace-specific history file."""
    try:
        # 1. Central SQLite DB
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO chat_messages (run_id, session_id, role, content) VALUES (?, ?, ?, ?)",
                    (run_id, session_id, role, content))
        conn.commit()
        conn.close()

        # 2. Workspace-specific JSON file (mirror)
        repo_path = get_repo_path(run_id)
        if repo_path and repo_path.exists():
            history_dir = repo_path / ".agent"
            history_dir.mkdir(exist_ok=True)
            history_file = history_dir / "chat_history.json"
            
            history = []
            if history_file.exists():
                try:
                    history = json.loads(history_file.read_text(encoding="utf-8"))
                except: pass
            
            history.append({
                "role": role,
                "content": content,
                "session_id": session_id,
                "timestamp": datetime.now().isoformat()
            })
            history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to add chat message for {run_id}: {e}")

def detect_project_type(repo_path: Path) -> dict:
    """Analyze the root directory and subdirectories to identify project type and its true root."""
    if not repo_path.exists(): return {"type": "Unknown", "root": "."}
    
    indicators = {
        "pubspec.yaml": "Flutter/Dart",
        "package.json": "JavaScript/TypeScript (Node.js/React/Vite)",
        "requirements.txt": "Python",
        "pyproject.toml": "Python (Modern)",
        "setup.py": "Python (Legacy)",
        "gradlew": "Android/Java/Kotlin",
        "build.gradle": "Java/Kotlin (Gradle)",
        "pom.xml": "Java (Maven)",
        "go.mod": "Go",
        "CMakeLists.txt": "C/C++",
        "Solution.sln": ".NET/C#"
    }
    
    # 1. Primary Check (Root)
    for file, name in indicators.items():
        if (repo_path / file).exists():
            # Sub-detection for JS/TS
            project_type = name
            if name == "JavaScript/TypeScript (Node.js/React/Vite)":
                try:
                    content = (repo_path / "package.json").read_text(encoding="utf-8")
                    if "react" in content.lower(): project_type += ", React"
                    if "next" in content.lower(): project_type += ", Next.js"
                except: pass
            return {"type": project_type, "root": "."}
            
    # 2. Shallow Search (Subdirectories)
    try:
        for item in repo_path.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                for file, name in indicators.items():
                    if (item / file).exists():
                        project_type = name
                        if name == "JavaScript/TypeScript (Node.js/React/Vite)":
                            try:
                                content = (item / "package.json").read_text(encoding="utf-8")
                                if "react" in content.lower(): project_type += ", React"
                            except: pass
                        return {"type": project_type, "root": item.name}
    except: pass
        
    return {"type": "Generic / Unknown", "root": "."}
    
def refresh_run_files(run_id: str, repo_path: Path) -> list[dict]:
    """Helper to refresh the file list for a run, supporting both git and non-git projects."""
    from git_utils import get_all_files
    try:
        from git import Repo
        try:
            repo_obj = Repo(repo_path)
        except Exception:
            class MockRepo:
                def __init__(self, p): self.working_dir = str(p)
            repo_obj = MockRepo(repo_path)
            
        files = get_all_files(repo_obj)
        if run_id in runs:
            runs[run_id].setdefault("live", {})["files"] = files
        return files
    except Exception as e:
        logger.error(f"Failed to refresh files for {run_id}: {e}")
        return []

def import_chat_history(run_id: str, repo_path: Path):
    """Try to import chat history from the workspace folder if empty in the central DB."""
    history_file = repo_path / ".agent" / "chat_history.json"
    if not history_file.exists(): return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT count(*) FROM chat_messages WHERE run_id = ?", (run_id,))
        if c.fetchone()[0] == 0:
            history = json.loads(history_file.read_text(encoding="utf-8"))
            for msg in history:
                c.execute("INSERT INTO chat_messages (run_id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                            (run_id, msg.get("session_id", "default"), msg["role"], msg["content"], msg.get("timestamp")))
            conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Import history failed for {run_id}: {e}")

def maybe_auto_commit(run_id: str, message: str):
    """If run_id is a GitHub repo, commit all changes locally."""
    repo_path = get_repo_path(run_id)
    if not repo_path: return
    
    try:
        from git import Repo
        repo = Repo(repo_path)
        # Check if it has a remote called 'origin' to determine if it's a cloned repo
        if not repo.remotes or 'origin' not in [r.name for r in repo.remotes]:
            return
            
        commit_changes(repo, [], message)
        logger.info(f"[Auto-Commit] Committed changes for {run_id}: {message}")
    except Exception as e:
        logger.warning(f"[Auto-Commit] Failed for {run_id}: {e}")

def derive_branch_name(team_name: str, leader_name: str) -> str:
    import re
    team = re.sub(r"[^A-Za-z0-9 ]", "", team_name).strip().upper().replace(" ", "_")
    leader = re.sub(r"[^A-Za-z0-9 ]", "", leader_name).strip().upper().replace(" ", "_")
    return f"{team}_{leader}_DOC_UPDATE"

def _background_run(run_id: str, doc_folder: str, excel_file: str, team_name: str, leader_name: str, branch_name: str):
    runs[run_id]["status"] = "running"
    try:
        logger.info(f"[{run_id}] Starting pipeline for {doc_folder}")
        result = run_pipeline(
            run_id=run_id,
            doc_folder=doc_folder,
            excel_file=excel_file,
            team_name=team_name,
            leader_name=leader_name,
            branch_name=branch_name,
            runs=runs,
        )
        runs[run_id]["status"] = "completed"
        runs[run_id]["result"] = result
        logger.info(f"[{run_id}] Pipeline completed.")
    except Exception as exc:
        logger.exception(f"[{run_id}] Pipeline failed: {exc}")
        runs[run_id]["status"] = "failed"
        runs[run_id]["error"] = str(exc)
    finally:
        save_projects()

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    # Allow local paths or URLs for doc processing
    run_id = str(uuid.uuid4())[:8]
    branch_name = derive_branch_name(req.team_name, req.leader_name)
    logger.info(f">>> New Analysis Request [run_id={run_id}]")
    logger.info(f"    Doc Folder: {req.doc_folder}")
    logger.info(f"    Excel File: {req.excel_file}")

    # Use requested path directly since we work on local folders now
    folder_path = Path(req.doc_folder)
    if not folder_path.exists() or not folder_path.is_dir():
        pass

    RUN_PATHS[run_id] = folder_path
    save_projects()

    runs[run_id] = {
        "status": "running",
        "team_name": req.team_name,
        "leader_name": req.leader_name,
        "repo_url": req.doc_folder, # Legacy UI compatibility mapping
        "excel_file": req.excel_file,
        "live": {
            "phase": "initializing",
            "message": "Starting pipeline...",
            "files": [],
            "terminal_output": "",
            "iterations": []
        }
    }

    threading.Thread(target=_background_run, args=(run_id, req.doc_folder, req.excel_file, req.team_name, req.leader_name, branch_name), daemon=True).start()
    return AnalyzeResponse(run_id=run_id, message="Agent started", branch_name=branch_name)

@app.get("/api/document/{run_id}/html")
async def get_document_html(run_id: str, type: str = "after", file: str = ""):
    repo_path = get_repo_path(run_id)
    if not repo_path:
        raise HTTPException(status_code=404, detail="Run not found")
        
    # By default `before` doc is the original, `after` is the modified.
    # We expect `agents.py` to create a `fixed_docs/` or `before_docs/` logic.
    prefix = ""
    # Since we need to know what file to get, we use the `file` param (e.g. `example.docx`)
    
    if type == "before":
        # Look for the copy saved
        doc_path = repo_path / ".backup" / Path(file).name
    else:
        doc_path = repo_path / file
        
    if not doc_path.exists():
        # Fallback appropriately
        if type == "before":
            doc_path = repo_path / file
        if not doc_path.exists():
            return JSONResponse({"html": f"File not found: {doc_path}"})
            
    highlight_edits = None
    h_type = None
    if run_id in runs:
        res_path = repo_path / "results.json"
        if res_path.exists():
            try:
                with open(res_path, "r") as f:
                    results_data = json.load(f).get("fixes_table", [])
                    # Filter edits for THIS specific file
                    current_file_results = [r for r in results_data if r.get("file") == file]
                    if current_file_results:
                        highlight_edits = current_file_results[0].get("edits", [])
                        h_type = type # 'before' or 'after'
            except: pass

    html_content = docx_to_html(str(doc_path), highlight_type=h_type, highlight_edits=highlight_edits)
    return JSONResponse({"html": html_content})

@app.post("/local/open")
async def open_local_folder(req: LocalOpenRequest):
    """Mount an existing local folder as a project."""
    path = Path(req.path).resolve()
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path not found or not a directory: {req.path}")
        
    run_id = f"local_{str(uuid.uuid4())[:6]}"
    RUN_PATHS[run_id] = path
    save_projects()
    
    from git_utils import get_all_files
    try:
        class MockRepo:
            def __init__(self, p): self.working_dir = str(p)
        
        files = get_all_files(MockRepo(path))
        
        runs[run_id] = {
            "status": "completed",
            "team_name": req.team_name,
            "leader_name": req.leader_name,
            "live": {
                "phase": "done",
                "message": f"Local project mounted: {path.name}",
                "files": files,
                "terminal_output": f">>> Mounted local folder: {path}\n",
                "iterations": []
            }
        }
        return {"run_id": run_id, "message": "Local folder mounted", "files": files}
    except Exception as e:
        logger.exception(f"Local mount failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/local/browse")
async def browse_local_folders(req: BrowseFolderRequest):
    """List subdirectories for a folder picker UI."""
    try:
        if not req.path:
            # Default to User home or current project root
            start_path = Path.home()
        else:
            start_path = Path(req.path).resolve()

        if not start_path.exists() or not start_path.is_dir():
             # If path invalid, fallback to home
             start_path = Path.home()

        folders = []
        # Filter: Skip hidden folders and common system dirs for speed/safety
        skip_patterns = {".", "$", "AppData", "Program Files", "Windows", "node_modules", "vendor"}
        
        try:
            for item in start_path.iterdir():
                if item.is_dir():
                    if any(item.name.startswith(p) for p in [".", "$"]): continue
                    if item.name in skip_patterns: continue
                    folders.append({
                        "name": item.name,
                        "path": str(item.absolute()),
                        "type": "directory"
                    })
        except PermissionError:
            pass # Skip folders we can't read

        # Sort alphabetically
        folders.sort(key=lambda x: x["name"].lower())

        return {
            "current_path": str(start_path.absolute()),
            "parent_path": str(start_path.parent.absolute()) if start_path.parent != start_path else None,
            "folders": folders
        }
    except Exception as e:
        logger.error(f"Browse failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat")
async def chat_with_agent(req: ChatRequest):
    try:
        msg_lower = req.message.lower()
        current_os = platform.system()
        is_windows = (current_os == "Windows")
        
        # Project Type Detection
        project_type_info = {"type": "Unknown", "root": "."}
        repo_path = None
        if req.run_id:
            repo_path = get_repo_path(req.run_id)
            if repo_path:
                project_type_info = detect_project_type(repo_path)

        project_type = project_type_info["type"]
        project_root = project_type_info["root"]

        system_instruction = (
            f"You are **DOCAI**, a high-performance **Senior Software Engineer (10+ years exp)** living inside the code editor. Current OS: {current_os}\n"
            f"Detected Project Type: {project_type}\n"
            f"Detected Project Root folder: `{project_root}`\n\n"
            "--- IDENTITY & EXPERTISE ---\n"
            "You are a master of Software, App, and Web development. You take full ownership of the project's health and architecture. "
            "You are proactive—you don't wait to be asked to fix infrastructure. You ensure best practices for `.gitignore`, `.env`, `README.md`, and dependencies.\n\n"
            "--- OPERATION PROTOCOL (VIBE -> THINK -> PLAN -> ACT -> VALIDATE -> REVIEW) ---\n"
            "You MUST follow these steps for every request:\n"
            "1. **Vibe Refinement**: Start with `#### Refined Request`. Structure the intent like a Senior Engineer.\n"
            "2. **Plan (Checklist)**: Create a `#### Technical Plan` with a clear checklist. Include infrastructure needs (e.g., '- [ ] Update .gitignore').\n"
            "3. **Iterative Action & Validation**: \n"
            "   - Execute tasks one by one. \n"
            "   - **PROACTIVE CONFIG**: If you see missing `.gitignore` or `.env` patterns, include their creation in your plan.\n"
            "   - **CONTINUOUS VALIDATION**: Explicitly state why each step is verified.\n"
            "   - **INLINE ERROR COMMENTS**: When you run a command and it fails, you MUST immediately go back and edit the code file where the error occurred and add an inline comment (e.g., `# FAILED: [error message]` or `// error: [error message]`) exactly on the line where the issue occurred. This helps the user see exactly what validation failed during your iteration loop.\n"
            "4. **Full Plan Review**: Verify 100% adherence to the `#### Refined Request` and best practices.\n"
            "5. **Finalize**: Provide a `#### Final Summary`.\n\n"
            "--- PREMIUM DISPLAY PATTERNS ---\n"
            "1. Use `#### Header Name` for sections.\n"
            "2. Cite files using `[File.ext]`.\n\n"
            "--- AUTONOMOUS ACTION PROTOCOL ---\n"
            "1. **LOCATE PROJECT ROOT**: Before running commands, inspect the `PROJECT REPOSITORY STRUCTURE` (Map) provided below. If the primary project files (e.g., `pubspec.yaml`, `package.json`, `requirements.txt`) are in a subdirectory (e.g., `/v/`), you MUST `cd` into that directory first.\n"
            "2. **CREATE FILES & FOLDERS**: To create or modify a file, you MUST use the following EXACT format on a new line:\n"
            "   CREATE_FILE: path/to/file.ext\n"
            "   ```\n"
            "   content here\n"
            "   ```\n"
            "   To create a new folder, use: `CREATE_FOLDER: path/to/folder` on a new line.\n"
            "   **CRITICAL**: You MUST wrap all file content in triple backticks (```). Do NOT include explanations or verification steps after the closing backticks on the same line or in the same block.\n"
            "   Important: The `CREATE_FILE:` line must be a separate line, immediately followed by the code block. Do NOT skip lines between the command and the code block. Use only forward slashes `/` in paths.\n"
            "3. **EXPLORE AND READ CODE**: You MUST follow the existing folder structure. To read existing files, use `RUN_COMMAND: cat path/to/file` (or `type` on Windows). To list folders, use `RUN_COMMAND: ls -la` (or `dir` on Windows). Never guess code; read it first and do changes accordingly.\n"
            "4. **EXECUTE TERMINAL COMMANDS**: To run a command, use: `RUN_COMMAND: command` on a new line.\n"
            "   - **CRITICAL**: Do NOT include any natural language, explanations, or 'and check that...' descriptions on the same line as `RUN_COMMAND`. The line must end exactly when the shell command ends.\n"
            f"   - **CRITICAL**: Current OS is {current_os}. " + 
            ("On Windows, you MUST use valid PowerShell syntax. Avoid bash-isms like `rm -rf !(path)`. " if is_windows else "Use standard Bash syntax. ") +
            "If the project is nested, use: `RUN_COMMAND: cd <subdir> && <command>` (or `cd <subdir>; <command>` in PowerShell).\n"
            "If a command fails, analyze the error and try a different syntax.\n\n"
            "--- SELF-VERIFICATION PROTOCOL (CRITICAL) ---\n"
            "After every action you take (CREATE_FILE, RUN_COMMAND), you will receive a [SYSTEM: ...] message telling you whether the action succeeded or failed.\n"
            "- If you see `[SYSTEM: Successfully created/modified file: X]` or `[SYSTEM: Successfully created directory: X]` → the action PASSED. Proceed to the next step.\n"
            "- If you see `[SYSTEM: VERIFICATION FAILED: ...]` or a non-zero exit code → the action FAILED. You MUST:\n"
            "  1. Analyze the error in the [SYSTEM:] message carefully.\n"
            "  2. Correct your approach (different command, fixed file content, correct path).\n"
            "  3. Retry the action with the corrected version.\n"
            "  4. Do NOT give up after one failure. You have up to 30 total iterations.\n"
            "- If exit code is non-zero from a terminal command, read the stderr and fix the issue before retrying.\n"
            "- NEVER hallucinate success. If the [SYSTEM:] says it failed, treat it as a real failure.\n"
            "- DO NOT output `#### Final Summary` or state `Task Complete` until the requested user task is fully implemented AND all verifications have succeeded.\n\n"
            "Think like a **10-year veteran**: **Vibe -> Thought -> Plan -> Step -> Validate -> Satisfy**.\n"
            "Do NOT hallucinate successful validation. Wait for SYSTEM confirmation after an action.\n"
        )

        if "summary" in msg_lower:
            system_instruction = (
                "You are DOCAI, an Autonomous Document Recovery Agent. "
                "Generate a high-level architectural summary of the provided repository context.\n"
                f"{system_instruction}"
                "Be sure to cite the main modules using the citation brackets [module_name.py]."
            )
        elif msg_lower.strip() in ["hi", "hi!", "hello", "hello!", "hey", "hey!", "hoe are you", "how are you", "how are you?", "what's up", "what's up?", "hii", "hiii","who are you","who is this","who are you?"]:
            system_instruction = (
                "You are **DOCAI**, a friendly Autonomous AI Agent. "
                "The user just sent a casual greeting. "
                "Respond with a very short and polite 1-2 sentence conversational reply (e.g., 'Hello! I am doing well, thanks. How can I help you with your project today?'). "
                "DO NOT output any Vibe/Plan/Act/Review protocol or lengthy technical responses."
            )
        elif any(k in msg_lower for k in ["create", "new file", "new folder", "generate code"]):
            system_instruction = (
                "You are DOCAI, the Autonomous Creator. Your goal is to help the user build new features by creating files and folders.\n"
                f"{system_instruction}"
            )
        else:
            system_instruction = (
                "You are **DOCAI**, a high-performance Autonomous AI Agent. "
                "Maintain your identity as a professional workspace companion. "
                "If the user greets you or asks who you are, prioritize a helpful, conversational response about your capabilities (bug fixing, scanning, test execution). "
                "Do NOT just output code snippets from the context unless specifically asked to fix or explain them.\n\n"
                f"{system_instruction}"
            )

        # Context logic
        context_parts = []
        repo_files = []
        if req.run_id and req.run_id in runs:
            repo_files = runs[req.run_id].get("live", {}).get("files", [])
        elif req.repo_context:
            repo_files = req.repo_context

        if repo_files:
            file_list = [f.get("path") for f in repo_files if f.get("path")]
            context_parts.append(f"### PROJECT REPOSITORY STRUCTURE (Map)\n- " + "\n- ".join(file_list))
        
        if req.file_path and req.file_content:
            context_parts.append(f"### CURRENT ACTIVE FILE (`{req.file_path}`)\n```\n{req.file_content}\n```")

        full_requested = any(k in msg_lower for k in ["full code", "entire repo", "all files", "architecture summary", "project overview"])
        
        # Explicitly look for [path] patterns from @ mentions
        import re
        mentions = re.findall(r"\[([a-zA-Z0-9_/.-]+)\]", req.message)
        
        if repo_files:
            for f in repo_files:
                path, content = f.get("path"), f.get("content", "")
                if not path or not content: continue
                
                # Check if this file is explicitly mentioned
                is_mentioned = any(m.lower() in path.lower() for m in mentions)
                
                # Or implicitly mentioned by name
                is_implicit = not mentions and (path.lower() in msg_lower or path.split("/")[-1].lower() in msg_lower)

                if full_requested or is_mentioned or is_implicit:
                    # Avoid duplication if it's already the active file
                    if path == req.file_path: continue
                    context_parts.append(f"### REFERENCED FILE: `{path}`\n```\n{content}\n```")
                    if len(context_parts) > 12: break

        context_str = "\n\n" + "\n\n".join(context_parts) if context_parts else "\n\n(No project context available.)"

        # LLM Logic with iterative tool use
        max_iterations = 30
        iteration = 0
        final_response = ""
        verification_log = []
        is_reiteration = False

        while iteration < max_iterations:
            iteration += 1
            tool_output_messages = [] # Collect system messages from tool outputs for this iteration

            # Prepare messages for this turn
            current_messages = [{"role": "system", "content": f"{system_instruction}\n\n{context_str}"}]

            # Get history (for the first turn, we add the user message)
            try:
                if iteration == 1:
                    add_chat_message(req.run_id or "unknown", req.session_id or "default", "user", req.message)

                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                # Fetch more history to provide better context for verification
                c.execute("SELECT role, content FROM chat_messages WHERE run_id = ? AND session_id = ? ORDER BY timestamp DESC LIMIT 20",
                          (req.run_id or "unknown", req.session_id or "default"))
                history_rows = c.fetchall()
                history_rows.reverse()
                for h in history_rows:
                    if h[0] == "agent":
                        role = "assistant"
                        content = h[1]
                    else:
                        role = "user"
                        content = f"[SYSTEM: {h[1]}]" if h[0] == "system" else h[1]

                    current_messages.append({"role": role, "content": content})
                conn.close()

                # Cleanup: Ensure strictly alternating user/assistant roles
                # Step 1: Separate system instruction from history
                system_msg = current_messages[0]  # Always the system instruction
                history = current_messages[1:]     # History messages (user/assistant only)

                # Step 2: Remove leading assistant messages (must start with user)
                while history and history[0]["role"] == "assistant":
                    history.pop(0)

                # Step 3: Merge consecutive same-role messages
                deduped = []
                for m in history:
                    if m["role"] not in ("user", "assistant"):
                        m["role"] = "user"  # Force any stray roles to user
                    if deduped and deduped[-1]["role"] == m["role"]:
                        deduped[-1]["content"] += "\n\n" + m["content"]
                    else:
                        deduped.append(m)

                # Step 4: Ensure the conversation ends with a user message
                # (the LLM needs the last message to be from the user to respond)
                if deduped and deduped[-1]["role"] != "user":
                    deduped.append({"role": "user", "content": "(Please continue.)"})

                # Step 5: Reassemble with system instruction first
                current_messages = [system_msg] + deduped
            except Exception as db_err:
                logger.error(f"DB error during chat loop: {db_err}")
                if iteration == 1:
                    current_messages.append({"role": "user", "content": req.message})

            # Call LLM
            response = _call_nvidia(current_messages, api_data=req.api_data)

            # Persist agent response
            add_chat_message(req.run_id or "unknown", req.session_id or "default", "agent", response)

            # Accumulate the response
            if response.strip():
                final_response += response + "\n\n"

            # Check for actions
            action_taken = False
            tool_success = True
            tool_feedback = []

            # --- ACTION A: FOLDER CREATION ---
            folder_pattern = r"(?:CREATE_FOLDER:|MKDIR:|CREATE_DIRECTORY:)\s*([a-zA-Z0-9_/.\\-]+)/?\s*(?:\n|$)|(?:CREATE_FILE:|WRITE_FILE:)\s*([a-zA-Z0-9_/.\\-]+[/\\])\s*(?:\n|$)"
            created_dirs = set()
            for f_match in re.finditer(folder_pattern, response, re.IGNORECASE):
                path_str = (f_match.group(1) or f_match.group(2)).strip()
                if not path_str: continue
                repo_path = get_repo_path(req.run_id)
                if repo_path:
                    full_p = (repo_path / path_str).resolve()
                    if str(full_p).startswith(str(repo_path.resolve())):
                        action_taken = True
                        try:
                            full_p.mkdir(parents=True, exist_ok=True)
                            created_dirs.add(str(full_p))
                            logger.info(f"[CHAT-AGENT] Created Directory: {path_str}")
                            tool_feedback.append(f"Successfully created directory: {path_str}")
                        except Exception as e:
                            tool_success = False
                            tool_feedback.append(f"Failed to create directory {path_str}: {str(e)}")
                    else:
                        tool_success = False
                        tool_feedback.append(f"Attempted to create directory outside project root: {path_str}")

            # --- ACTION B: FILE CREATION/MODIFICATION ---
            file_blocks = re.split(r"(?=CREATE_FILE:|WRITE_FILE:)", response, flags=re.IGNORECASE)
            for block in file_blocks:
                if not block.strip().lower().startswith(("create_file:", "write_file:")):
                    continue

                fn_match = re.search(r"(?:CREATE_FILE:|WRITE_FILE:)\s*([a-zA-Z0-9_/.\\-]+)", block, re.IGNORECASE)
                if not fn_match: continue
                target_file = fn_match.group(1).strip()

                if target_file.endswith("/") or target_file.endswith("\\"): continue

                content = ""
                md_match = re.search(r"```(?:\w+)?\n(.*?)\n```", block, re.DOTALL)
                if md_match:
                    content = md_match.group(1)
                else:
                    lines = block.split("\n")
                    useful_lines = []
                    start_collecting = False
                    for line in lines[1:]:
                        l_strip = line.strip().lower()
                        if l_strip in ["code", "content:", "code:", "```"]:
                            start_collecting = True
                            continue
                        # Expanded "breaker" patterns to stop natural language leakage
                        breakers = [
                            "run_command:", "####", "create_file:", "write_file:", 
                            "create_folder:", "mkdir:", "1.", "2.", "3.", "4.", "5.",
                            "verify", "expected output", "step ", "task complete", "final summary"
                        ]
                        if any(l_strip.startswith(x) for x in breakers):
                            break
                        useful_lines.append(line)
                    if useful_lines:
                        content = "\n".join(useful_lines).strip()

                if content and req.run_id:
                    repo_path = get_repo_path(req.run_id)
                    if repo_path:
                        full_p = (repo_path / target_file).resolve()
                        if str(full_p).startswith(str(repo_path.resolve())):
                            if full_p.is_dir():
                                tool_feedback.append(f"Warning: Skipping file write for {target_file} as it is a directory.")
                                continue

                            action_taken = True
                            try:
                                full_p.parent.mkdir(parents=True, exist_ok=True)
                                full_p.write_text(content, encoding="utf-8")
                                logger.info(f"[CHAT-AGENT] Created/Modified File: {target_file}")
                                maybe_auto_commit(req.run_id, f"Auto-commit: Modified {target_file}")
                                tool_feedback.append(f"Successfully created/modified file: {target_file}")
                            except Exception as e:
                                tool_success = False
                                tool_feedback.append(f"Failed to create/modify file {target_file}: {str(e)}")
                        else:
                            tool_success = False
                            tool_feedback.append(f"Attempted to create/modify file outside project root: {target_file}")

            # --- ACTION C: Push Action ---
            push_pattern = r"PUSH_TO_GITHUB:\s*(true|yes)"
            if re.search(push_pattern, response, re.IGNORECASE) and req.run_id:
                repo_path = get_repo_path(req.run_id)
                if repo_path:
                    action_taken = True
                    try:
                        from git import Repo
                        from state import GLOBAL_CONFIG
                        repo_obj = Repo(repo_path)
                        pat = GLOBAL_CONFIG.get("github_pat") or os.getenv("GITHUB_PAT")
                        push_changes(repo_obj, pat=pat)
                        tool_feedback.append("Successfully pushed changes to GitHub.")
                    except Exception as e:
                        tool_success = False
                        tool_feedback.append(f"Failed to push to GitHub: {str(e)}")

            # --- ACTION D: Terminal Commands ---
            command_pattern = r"RUN_COMMAND:\s*([^\n]+)"
            cmd_matches = list(re.finditer(command_pattern, response, re.IGNORECASE))

            if cmd_matches and req.run_id:
                repo_path = get_repo_path(req.run_id)
                if repo_path:
                    for cmd_match in cmd_matches:
                        action_taken = True
                        cmd = cmd_match.group(1).strip()
                        # Robust stripping of hallucinations (backticks, quotes, parentheses)
                        cmd = cmd.strip("`'\"()[] \t\r\n")
                        
                        # Hardening: If the command contains natural language conjunctions like "and check", "to verify", etc.
                        # we try to truncate it to avoid "The term 'dir and' is not recognized" errors.
                        stop_phrases = [" and check", " to verify", " and see", " for checking", " which results"]
                        for stop_p in stop_phrases:
                            if stop_p in cmd.lower():
                                idx = cmd.lower().find(stop_p)
                                logger.warning(f"[CHAT-AGENT] Truncating leaked natural language from command: '{cmd}' -> '{cmd[:idx]}'")
                                cmd = cmd[:idx].strip()
                        # Log hex of first char for debugging corruption
                        if cmd:
                            first_char_hex = hex(ord(cmd[0]))
                            logger.info(f"[CHAT-AGENT] Processed command: '{cmd}' (starts with {first_char_hex})")
                        
                        # --- PLATFORM SHIM (Bash -> PowerShell) ---
                        if is_windows:
                            # Translate common bashisms
                            if cmd.startswith("rm -rf "):
                                path = cmd[7:].strip()
                                cmd = f"Remove-Item -Recurse -Force {path}"
                            elif cmd.startswith("cp -r "):
                                parts = cmd[6:].strip().split()
                                if len(parts) >= 2:
                                    cmd = f"Copy-Item -Recurse -Force {parts[0]} {parts[1]}"
                            elif cmd.startswith("mkdir -p "):
                                path = cmd[9:].strip()
                                cmd = f"New-Item -ItemType Directory -Force {path}"
                            elif cmd == "ls -la" or cmd == "ls -l":
                                cmd = "ls"
                            elif "grep" in cmd and "|" in cmd:
                                cmd = cmd.replace("grep", "Select-String")

                        # --- FLUTTER HOT RELOAD SHIM ---
                        if "flutter hot" in cmd.lower():
                            logger.info(f"[CHAT-AGENT] Intercepted flutter hot command: {cmd}")
                            tool_feedback.append(f"--- TERMINAL OUTPUT ({cmd}) ---\n[INTERCEPTED] Flutter hot reload/restart is an interactive feature. Please ensure 'flutter run' is active in the terminal and use the interactive controls ('r' or 'R') or the UI buttons. Standalone hot reload is not supported by the Flutter CLI.")
                            # No further action for this command
                            continue

                        # --- INTERACTIVE COMMAND WARNING ---
                        if any(x in cmd.lower() for x in ["flutter run", "npm start", "npm run dev"]):
                            logger.info(f"[CHAT-AGENT] Warning: Intercepted interactive command: {cmd}")
                            tool_feedback.append(f"--- TERMINAL OUTPUT ({cmd}) ---\n[INTERCEPTED] '{cmd}' is an interactive command. In this chat loop, it would block the system. Please use the interactive Terminal View at the bottom to run the application.")
                            # No further action for this command
                            continue

                        logger.info(f"[CHAT-AGENT] Executing: {cmd}")
                        try:
                            exec_cmd = cmd
                            if is_windows:
                                exec_cmd = ["powershell", "-NoProfile", "-Command", cmd]

                            # Use detected project root as initial CWD, 
                            # UNLESS the agent is trying to 'cd' explicitly.
                            initial_cwd = repo_path
                            if not cmd.lower().startswith("cd ") and project_type_info.get("root") and project_type_info["root"] != ".":
                                initial_cwd = repo_path / project_type_info["root"]

                            # Non-blocking execution using asyncio.to_thread
                            import asyncio
                            proc = await asyncio.to_thread(
                                subprocess.run,
                                exec_cmd,
                                cwd=initial_cwd,
                                shell=not is_windows,
                                capture_output=True,
                                text=True,
                                timeout=20,  # Reduced to 20s as requested
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                            )
                            output = (proc.stdout + "\n" + proc.stderr).strip()
                            
                            # --- FALLBACK: If initial CWD failed, try clone root (only if we weren't already there) ---
                            if proc.returncode != 0 and initial_cwd != repo_path:
                                logger.info(f"[CHAT-AGENT] Command failed in nested root, retrying in clone root: {repo_path}")
                                proc_retry = await asyncio.to_thread(
                                    subprocess.run,
                                    exec_cmd,
                                    cwd=repo_path,
                                    shell=not is_windows,
                                    capture_output=True,
                                    text=True,
                                    timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                                )
                                if proc_retry.returncode == 0:
                                    proc = proc_retry
                                    output = (proc.stdout + "\n" + proc.stderr).strip()
                                    output = f"[HEALED: Automatically retried in clone root]\n" + output

                            tool_feedback.append(f"--- TERMINAL OUTPUT ({cmd}) ---\n{output}")
                            if proc.returncode != 0:
                                tool_success = False
                                tool_feedback.append(f"Command failed with exit code {proc.returncode}.")
                        except Exception as e:
                            tool_success = False
                            tool_feedback.append(f"Error executing command '{cmd}': {str(e)}")

            # Add tool feedback to chat history as system messages
            for feedback_msg in tool_feedback:
                add_chat_message(req.run_id or "unknown", req.session_id or "default", "system", feedback_msg)
                tool_output_messages.append(feedback_msg)

            # Update verification log
            verification_log.append({
                "iteration": iteration,
                "actions_taken": action_taken,
                "tool_success": tool_success,
                "feedback": tool_feedback
            })

            # If no action was taken or final summary, break
            if not action_taken or "#### Final Summary" in response or "Task Complete" in response:
                break

            # If actions were taken but some failed, set for reiteration
            if action_taken and not tool_success:
                is_reiteration = True
                # The next iteration will include the system feedback in its context
                continue
            elif action_taken and tool_success:
                # If actions were successful, but the agent didn't explicitly finish,
                # it might need to continue to the next step of its plan.
                # We don't set is_reiteration to True here, as it implies a failure.
                pass

        # Final state refresh
        updated_live = None
        if req.run_id:
            repo_path = get_repo_path(req.run_id)
            if repo_path:
                updated_live = refresh_run_files(req.run_id, repo_path)

        payload = {
            "response": final_response.strip(),
            "verification_log": verification_log,
            "is_reiteration": is_reiteration
        }
        if updated_live: payload["live"] = {"files": updated_live}

        save_projects() # Persist state after chat actions
        return payload

    except Exception as e:
        logger.exception(f"Chat failed: {e}")
        return {"response": f"⚠️ I hit an error: {str(e)}"}

@app.get("/chat/history/{run_id}")
async def get_chat_history(run_id: str, session_id: str = "default"):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT role, content, timestamp FROM chat_messages WHERE run_id = ? AND session_id = ? ORDER BY timestamp ASC", (run_id, session_id))
        rows = c.fetchall()
        conn.close()

        merged_history = []
        for r in rows:
            role, content, ts = r[0], r[1], r[2]

            # Merge 'system' messages into the preceding 'agent' message if possible
            if role == "system" and merged_history and merged_history[-1]["role"] == "agent":
                if content.strip():
                    # Format system output nicely within the agent bubble
                    merged_history[-1]["content"] += f"\n\n---\n**System Output:**\n```\n{content.strip()}\n```"
            else:
                # Map 'agent' to 'agent' for frontend (history role is already correct in DB)
                merged_history.append({"role": role, "content": content, "timestamp": ts})
                
        return {"history": merged_history}
    except Exception as e:
        logger.error(f"History fetch failed: {e}")
        return {"history": []}

@app.get("/chat/sessions/{run_id}")
async def get_chat_sessions(run_id: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT DISTINCT session_id FROM chat_messages WHERE run_id = ?", (run_id,))
        rows = c.fetchall()
        conn.close()
        sessions = [r[0] for r in rows if r[0]]
        if "default" not in sessions: sessions.insert(0, "default")
        return {"sessions": sessions}
    except Exception as e:
        logger.error(f"Sessions fetch failed: {e}")
        return {"sessions": ["default"]}

@app.delete("/chat/sessions/{run_id}/{session_id}")
async def delete_chat_session(run_id: str, session_id: str):
    if session_id == "default":
        raise HTTPException(status_code=400, detail="Cannot delete default session")
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM chat_messages WHERE run_id = ? AND session_id = ?", (run_id, session_id))
        conn.commit()
        conn.close()

        repo_path = get_repo_path(run_id)
        if repo_path and repo_path.exists():
            history_file = repo_path / ".gguai" / "chat_history.json"
            if history_file.exists():
                try:
                    history = json.loads(history_file.read_text(encoding="utf-8"))
                    new_history = [m for m in history if m.get("session_id", "default") != session_id]
                    history_file.write_text(json.dumps(new_history, indent=2), encoding="utf-8")
                except:
                    pass
        return {"status": "success", "message": f"Deleted session {session_id}"}
    except Exception as e:
        logger.error(f"Failed to delete session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results/{run_id}")
async def get_results(run_id: str):
    if run_id not in runs: raise HTTPException(status_code=404, detail="Run not found")
    run = runs[run_id]
    
    # If files list is empty, try to refresh it from disk
    if not run.get("live", {}).get("files"):
        repo_path = RUN_PATHS.get(run_id)
        if repo_path and repo_path.exists():
            from git_utils import get_all_files
            from git import Repo
            try:
                # We handle both real git repos and local folders (MockRepo)
                try:
                    repo_obj = Repo(repo_path)
                except:
                    class MockRepo:
                        def __init__(self, p): self.working_dir = str(p)
                    repo_obj = MockRepo(repo_path)
                
                run.setdefault("live", {})["files"] = get_all_files(repo_obj)
            except Exception as e:
                logger.warning(f"Auto-refresh files failed for {run_id}: {e}")

    return {
        "run_id": run_id,
        "status": run["status"],
        "branch_name": run.get("branch_name"),
        "repo_url": run.get("repo_url"),
        "team_name": run.get("team_name"),
        "leader_name": run.get("leader_name"),
        "started_at": run.get("started_at"),
        "live": run.get("live", {}),
        "result": run.get("result"),
        "error": run.get("error"),
    }

@app.get("/workspaces")
async def list_workspaces():
    """Return a list of all saved project workspaces."""
    workspace_list = []
    # Iterate over RUN_PATHS to ensure every saved project is listed
    for run_id, path in RUN_PATHS.items():
        data = runs.get(run_id, {})
        live = data.get("live", {})
        
        workspace_list.append({
            "run_id": run_id,
            "path": str(path),
            "status": data.get("status", "completed"),
            "phase": live.get("phase", "done"),
            "team_name": data.get("team_name") or "GGU AI",
            "leader_name": data.get("leader_name") or "AI_PROJECT"
        })
    return {"workspaces": workspace_list}

@app.post("/save_all")
async def manual_save():
    """Manually trigger project state persistence."""
    save_projects()
    return {"status": "success", "message": "Workspaces persisted to disk"}

@app.get("/config")
async def get_config():
    from llm_client import NVIDIA_MODEL
    return {
        "github_pat_set": bool(os.getenv("GITHUB_PAT") and "your_github" not in os.getenv("GITHUB_PAT", "").lower()),
        "nvidia_api_key_set": bool(os.getenv("NVIDIA_API_KEY")),
        "nvidia_model": os.getenv("NVIDIA_MODEL") or NVIDIA_MODEL,
    }

@app.post("/config")
async def update_config(conf: ConfigUpdate):
    env_path = Path(__file__).parent / ".env"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    updates = {}
    if conf.github_pat: updates["GITHUB_PAT"] = conf.github_pat
    if conf.nvidia_api_key: updates["NVIDIA_API_KEY"] = conf.nvidia_api_key
    
    new_lines = []
    seen = set()
    for line in lines:
        if "=" in line:
            key = line.split("=")[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        new_lines.append(line)
    for key, val in updates.items():
        if key not in seen: new_lines.append(f"{key}={val}")
    env_path.write_text("\n".join(new_lines) + "\n")
    for key, val in updates.items(): os.environ[key] = val
    return {"message": "Config updated"}

@app.get("/repos")
async def list_repos():
    clones_dir = CLONES_DIR
    if not clones_dir.exists():
        clones_dir.mkdir(parents=True, exist_ok=True)
        return []
    repo_list = []
    for item in clones_dir.iterdir():
        if item.is_dir():
            run_id = item.name.split("_")[0]
            repo_name = item.name.replace(f"{run_id}_", "")
            try:
                stat_info = item.stat()
                total_size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                repo_list.append({
                    "run_id": run_id,
                    "repo_name": repo_name,
                    "folder_name": item.name,
                    "size_mb": round(total_size / (1024 * 1024), 2),
                    "created_at": datetime.fromtimestamp(stat_info.st_ctime).isoformat(),
                })
            except Exception: continue
    repo_list.sort(key=lambda x: x["created_at"], reverse=True)
    return repo_list

@app.delete("/repos/{run_id}")
async def delete_repo(run_id: str):
    clones_dir = CLONES_DIR
    target = next((item for item in clones_dir.iterdir() if item.is_dir() and item.name.split('_')[0] == run_id), None) if clones_dir.exists() else None
    if target:
        try:
            def on_rm_error(func, path, exc_info):
                import stat
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception: pass
            shutil.rmtree(target, onerror=on_rm_error)
            zip_p = Path(__file__).parent / "downloads" / f"fixed_{run_id}.zip"
            if zip_p.exists(): os.remove(zip_p)
            return {"message": f"Deleted {run_id}"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=404, detail="Not found")

@app.post("/save")
async def save_file(req: SaveFileRequest):
    target = get_repo_path(req.run_id)
    if not target: raise HTTPException(status_code=404, detail="Project path not found")
    
    full_p = (target / req.file_path).resolve()
    # Path safety: Ensure we are inside the project
    if not str(full_p).startswith(str(target.resolve())): 
        raise HTTPException(status_code=403, detail="Illegal path traversal attempt")
        
    full_p.parent.mkdir(parents=True, exist_ok=True)
    full_p.write_text(req.content, encoding="utf-8")
    logger.info(f"[API] Saved file: {req.file_path} for run: {req.run_id}")
    return {"message": "Saved"}

@app.post("/create")
async def create_item(req: CreateItemRequest):
    target = get_repo_path(req.run_id)
    if not target: raise HTTPException(status_code=404, detail="Project path not found")
    
    rel_p = f"{req.parent_path}/{req.name}" if req.parent_path else req.name
    full_p = (target / rel_p).resolve()
    if not str(full_p).startswith(str(target.resolve())): 
        raise HTTPException(status_code=403, detail="Illegal path traversal attempt")
    try:
        if req.type == "folder": 
            full_p.mkdir(parents=True, exist_ok=True)
            logger.info(f"[API] Created folder: {rel_p} for run: {req.run_id}")
        else:
            full_p.parent.mkdir(parents=True, exist_ok=True)
            if not full_p.exists(): 
                full_p.write_text("", encoding="utf-8")
                logger.info(f"[API] Created empty file: {rel_p} for run: {req.run_id}")
        updated_live = refresh_run_files(req.run_id, target)
        return {"message": "Created", "files": updated_live}
    except Exception as e: 
        logger.error(f"[API] Failed to create {req.type} {rel_p}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/delete")
async def delete_item(req: DeleteItemRequest):
    target = get_repo_path(req.run_id)
    if not target: raise HTTPException(status_code=404, detail="Project path not found")
    
    full_p = (target / req.path).resolve()
    if not str(full_p).startswith(str(target.resolve())): 
        raise HTTPException(status_code=403, detail="Illegal path traversal attempt")
        
    try:
        if full_p.is_dir():
            shutil.rmtree(full_p)
        elif full_p.is_file():
            full_p.unlink()
            
        updated_live = refresh_run_files(req.run_id, target)
        return {"message": "Deleted", "files": updated_live}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

class RenameItemRequest(BaseModel):
    run_id: str
    old_path: str
    new_name: str

class CopyItemRequest(BaseModel):
    run_id: str
    src_path: str
    dest_path: str
    move: bool = False  # True = Cut+Paste (move), False = Copy+Paste

@app.post("/rename")
async def rename_item(req: RenameItemRequest):
    target = get_repo_path(req.run_id)
    if not target: raise HTTPException(status_code=404, detail="Project path not found")

    src = (target / req.old_path).resolve()
    dest = src.parent / req.new_name
    if not str(src).startswith(str(target.resolve())):
        raise HTTPException(status_code=403, detail="Illegal path traversal attempt")
    if dest.exists():
        raise HTTPException(status_code=400, detail=f"'{req.new_name}' already exists")
    try:
        src.rename(dest)
        updated_live = refresh_run_files(req.run_id, target)
        return {"message": "Renamed", "files": updated_live}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.post("/copy")
async def copy_item(req: CopyItemRequest):
    target = get_repo_path(req.run_id)
    if not target: raise HTTPException(status_code=404, detail="Project path not found")

    src = (target / req.src_path).resolve()
    dest = (target / req.dest_path).resolve()
    if not str(src).startswith(str(target.resolve())) or not str(dest).startswith(str(target.resolve())):
        raise HTTPException(status_code=403, detail="Illegal path traversal attempt")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if req.move:
            shutil.move(str(src), str(dest))
        elif src.is_dir():
            shutil.copytree(str(src), str(dest))
        else:
            shutil.copy2(str(src), str(dest))
        updated_live = refresh_run_files(req.run_id, target)
        return {"message": "Moved" if req.move else "Copied", "files": updated_live}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))



@app.websocket("/ws/terminal/{run_id}")
async def terminal_websocket(websocket: WebSocket, run_id: str):
    """Handle real-time interactive terminal with stdin support."""
    logger.info(f"[WS] Accepting terminal connection for run_id: '{run_id}'")
    await websocket.accept()

    repo_root = get_repo_path(run_id)
    if not repo_root:
        logger.warning(f"[WS] Project not found for run_id: '{run_id}'. Available keys in RUN_PATHS: {list(RUN_PATHS.keys())}")
        try:
            await websocket.send_json({"type": "error", "content": f"Project not found for run_id: {run_id}"})
            await websocket.close()
        except: pass
        return
    
    logger.info(f"[WS] Resolved repo_root for '{run_id}': {repo_root}")

    loop = asyncio.get_running_loop()
    logger.info(f"[WS] Session active: {run_id}")

    # Send existing history
    if run_id in runs and "live" in runs[run_id]:
        history = runs[run_id]["live"].get("terminal_output", "")
        if history:
            try: await websocket.send_json({"type": "output", "content": history[-10000:]})
            except: pass

    # State for the current interactive process
    active_process = None
    output_queue = asyncio.Queue()
    current_cwd = str(repo_root)

    def pipe_reader(pipe, msg_type):
        """Read from a pipe and push to output_queue."""
        try:
            while True:
                char = pipe.read(1)
                if not char:
                    break
                loop.call_soon_threadsafe(output_queue.put_nowait, {"type": msg_type, "content": char})
        except Exception:
            pass

    async def drain_output():
        """Drain output_queue and send to websocket continuously for the life of the connection."""
        nonlocal active_process
        buffer = ""
        try:
            while True:
                try:
                    # Wait for output from readers
                    msg = await asyncio.wait_for(output_queue.get(), timeout=0.1)
                    content = msg.get("content", "")
                    buffer += content
                    
                    # Flush on newline or limit
                    if "\n" in buffer or "\r" in buffer or len(buffer) > 200:
                        if run_id in runs:
                            old = runs[run_id]["live"].get("terminal_output", "")
                            runs[run_id]["live"]["terminal_output"] = (old + buffer)[-30000:]
                        await websocket.send_json({"type": msg["type"], "content": buffer})
                        buffer = ""
                except asyncio.TimeoutError:
                    # Periodic flush
                    if buffer:
                        if run_id in runs:
                            old = runs[run_id]["live"].get("terminal_output", "")
                            runs[run_id]["live"]["terminal_output"] = (old + buffer)[-30000:]
                        await websocket.send_json({"type": "output", "content": buffer})
                        buffer = ""
                    # We NO LONGER break here when process ends. We wait for more output from potentially NEW processes.
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[WS] Drainer error: {e}")

    async def run_command_task(cmd, start_dir):
        """Run a command in the background and notify when done."""
        nonlocal active_process
        try:
            chained_cmd = f"chcp 65001 >nul 2>&1 & cd /d \"{start_dir}\" & {cmd}"
            logger.info(f"[WS] Executing: {chained_cmd}")

            active_process = subprocess.Popen(
                chained_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                shell=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=0,
                cwd=str(start_dir),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )

            # Start readers
            threading.Thread(target=pipe_reader, args=(active_process.stdout, "output"), daemon=True).start()
            threading.Thread(target=pipe_reader, args=(active_process.stderr, "error"), daemon=True).start()

            # Wait for completion (blocking in a thread or using communicate)
            # Since we have readers, we just need to wait for the return code
            exit_code = active_process.wait()
            
            # Small delay to allow readers to finish putting everything in the queue
            await asyncio.sleep(0.5)
            
            await websocket.send_json({"type": "done", "exit_code": exit_code, "cwd": str(start_dir)})
            save_projects()
        except asyncio.CancelledError:
            if active_process and active_process.poll() is None:
                active_process.kill()
        except Exception as e:
            logger.warning(f"[WS] Command task error: {e}")
            await websocket.send_json({"type": "error", "content": str(e)})

    try:
        # Start the persistent drainer task
        drain_task = asyncio.create_task(drain_output())
        current_cmd_task = None

        while True:
            # Wait for data from frontend
            data = await websocket.receive_json()
            msg_type = data.get("type", "command")

            # ── Handle stdin injection ──────────────────────────────
            if msg_type == "stdin":
                stdin_data = data.get("data", "")
                # Special case: \x03 is Ctrl+C
                if stdin_data == '\x03':
                    if active_process and active_process.poll() is None:
                        active_process.kill()
                        await websocket.send_json({"type": "output", "content": "^C\n"})
                    continue

                if active_process and active_process.poll() is None and active_process.stdin:
                    try:
                        if not stdin_data.endswith("\n") and not stdin_data.endswith("\r"):
                            if len(stdin_data) == 1 and ord(stdin_data[0]) < 32: pass
                            else: stdin_data += "\n"
                        
                        active_process.stdin.write(stdin_data)
                        active_process.stdin.flush()
                        
                        if run_id in runs:
                            old = runs[run_id]["live"].get("terminal_output", "")
                            runs[run_id]["live"]["terminal_output"] = (old + stdin_data)[-30000:]
                        await websocket.send_json({"type": "output", "content": stdin_data})
                    except Exception as e:
                        await websocket.send_json({"type": "error", "content": f"stdin error: {e}"})
                else:
                    await websocket.send_json({"type": "error", "content": "No active process."})
                continue

            # ── Handle new command ──────────────────────────────────
            command = data.get("command", "")
            if not command: continue
            command = command.strip("`'\"()[] \t\r\n")
            if not command: continue

            cwd = data.get("cwd") or current_cwd
            try:
                start_dir = Path(cwd)
                if not str(start_dir.resolve()).startswith(str(repo_root.resolve())):
                    start_dir = repo_root
            except Exception:
                start_dir = repo_root

            # Kill existing
            if active_process and active_process.poll() is None:
                active_process.kill()
                active_process.wait()
            if current_cmd_task and not current_cmd_task.done():
                current_cmd_task.cancel()

            prompt_line = f"\n{start_dir}> {command}\n"
            if run_id in runs:
                old = runs[run_id]["live"].get("terminal_output", "")
                runs[run_id]["live"]["terminal_output"] = (old + prompt_line)[-30000:]
            await websocket.send_json({"type": "output", "content": prompt_line})

            # Start the command in the background
            current_cmd_task = asyncio.create_task(run_command_task(command, start_dir))

    except WebSocketDisconnect:
        logger.info(f"[WS] Terminal disconnected: {run_id}")
    except Exception as e:
        logger.warning(f"[WS] WebSocket Loop Error: {e}")
    finally:
        # Cleanup
        if active_process and active_process.poll() is None:
            try: active_process.kill()
            except: pass
        if 'drain_task' in locals():
            drain_task.cancel()
        if 'current_cmd_task' in locals() and current_cmd_task:
            current_cmd_task.cancel()
        save_projects()


@app.post("/terminal")
async def execute_terminal_command(req: TerminalRequest):
    """Execute a shell command with stateful CWD, supporting both cloned and local repos."""
    repo_root = get_repo_path(req.run_id)
    
    if repo_root:
        start_dir = Path(req.cwd) if req.cwd else repo_root
        # Path Safety: Ensure we don't 'cd' out of the project boundaries
        if not str(start_dir.resolve()).startswith(str(repo_root.resolve())):
            start_dir = repo_root
    else:
        # Global fallback if no project is active (allows basic commands in root)
        start_dir = Path(req.cwd) if req.cwd else CLONES_DIR.resolve()
        if not start_dir.exists(): start_dir = Path.cwd()

    import subprocess
    # start_dir is already resolved above based on project context or fallback
    try:
        # Use '&' instead of '&&' so that the second command (cd) ALWAYS runs even if the first fails.
        # This ensures we always get the new CWD back to the frontend.
        chained_cmd = f"cd /d \"{start_dir}\" & {req.command} & echo --END_OF_COMMAND-- & cd"
        
        process = subprocess.run(
            chained_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        
        full_output = process.stdout
        error = process.stderr
        
        # Split output to find the new CWD
        parts = full_output.split("--END_OF_COMMAND--")
        output = parts[0].strip()
        new_cwd = parts[1].strip() if len(parts) > 1 else str(start_dir)
        
        if error and not output:
             output = f"Error: Command execution returned non-zero status.\n{error}"

        # Sync manual command output to the live terminal_output for UI consistency
        if req.run_id in runs:
            # Append prompt indicator and command, then output
            prompt = f"\n{new_cwd}> {req.command}\n"
            current_output = runs[req.run_id]["live"].get("terminal_output", "")
            runs[req.run_id]["live"]["terminal_output"] = (current_output + prompt + output + "\n")[-20000:]
            save_projects()

        return {
            "output": output,
            "error": error if process.returncode != 0 else "",
            "exit_code": process.returncode,
            "cwd": new_cwd
        }
    except Exception as e:
        logger.error(f"[TERMINAL] Command failed: {e}")
        return {"output": "", "error": f"Internal Shell Error: {str(e)}", "exit_code": 1, "cwd": str(start_dir)}

@app.get("/download/{run_id}")
async def download_fixed_code(run_id: str, background_tasks: BackgroundTasks):
    target = get_repo_path(run_id)
    if not target: raise HTTPException(status_code=404, detail="Project not found")
    
    zip_root = Path(__file__).parent / "downloads"
    zip_root.mkdir(exist_ok=True)
    # Use unique filename to avoid "File in use" errors
    zip_filename = f"project_{run_id}_{int(time.time())}.zip"
    zip_full_path = zip_root / zip_filename
    
    try:
        with zipfile.ZipFile(zip_full_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(target):
                if '.git' in dirs: dirs.remove('.git')
                for file in files:
                    file_p = Path(root) / file
                    try: zipf.write(file_p, file_p.relative_to(target))
                    except: continue
        
        # Cleanup background task
        background_tasks.add_task(os.remove, str(zip_full_path))
        
        return FileResponse(
            path=zip_full_path,
            filename=f"project_{run_id}.zip",
            media_type="application/zip"
        )
    except Exception as e:
        if zip_full_path.exists():
            try: os.remove(zip_full_path)
            except: pass
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# Static Files (Pure HTML Frontend)
# ---------------------------------------------------------------------------
from fastapi.staticfiles import StaticFiles
frontend_dir = ROOT_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

# ---------------------------------------------------------------------------
# State Initialization
# ---------------------------------------------------------------------------
init_db()
load_projects(import_chat_history)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)

