import json
import logging
from pathlib import Path
import os
import sys

logger = logging.getLogger(__name__)

# Shared state
class RUN_PATHS_DICT(dict):
    def __setitem__(self, key, value):
        logger.info(f"[RUN_PATHS] SET {key} -> {value}")
        super().__setitem__(key, value)

runs = {}
RUN_PATHS = RUN_PATHS_DICT()
GLOBAL_CONFIG = {
    "github_pat": os.getenv("GITHUB_PAT", ""),
    "nvidia_api_key": os.getenv("NVIDIA_API_KEY", "")
}

# Paths
if getattr(sys, 'frozen', False):
    APP_DATA = Path(os.getenv("APPDATA", os.path.expanduser("~"))) / "GGU AI-CICD-Healing-Agent"
    ROOT_DIR = APP_DATA
else:
    BACKEND_DIR = Path(__file__).parent
    ROOT_DIR = BACKEND_DIR.parent

DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_FILE = DATA_DIR / "projects.json"

def save_projects():
    try:
        data = {
            "GLOBAL_CONFIG": GLOBAL_CONFIG,
            "RUN_PATHS": {k: str(v) for k, v in RUN_PATHS.items()},
            "RUN_META": {
                k: {
                    "team_name": v.get("team_name"),
                    "leader_name": v.get("leader_name"),
                    "status": v.get("status"),
                    "terminal_output": v.get("live", {}).get("terminal_output", ""),
                } for k, v in runs.items()
            }
        }
        PROJECTS_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save projects: {e}")

def load_projects(import_chat_history_callback=None):
    if PROJECTS_FILE.exists():
        try:
            data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
            
            # Load Global Config
            saved_config = data.get("GLOBAL_CONFIG", {})
            for k, v in saved_config.items():
                if v: GLOBAL_CONFIG[k] = v
            
            saved_paths = data.get("RUN_PATHS", {})
            for k, v in saved_paths.items():
                logger.info(f"[STATE] Loading RUN_PATH {k} -> {v}")
                RUN_PATHS[k] = Path(v)
            
            meta = data.get("RUN_META", {})
            legacy_history = data.get("TERMINAL_HISTORY", {})
            
            for k in RUN_PATHS.keys():
                v = meta.get(k, {})
                legacy_term = legacy_history.get(k, "")
                
                runs[k] = {
                    "status": v.get("status", "completed"),
                    "team_name": v.get("team_name") or "DOCAI",
                    "leader_name": v.get("leader_name") or "PROJECT",
                    "live": {
                        "phase": "done",
                        "message": "Project restored",
                        "terminal_output": v.get("terminal_output") or legacy_term,
                        "files": [],
                        "iterations": []
                    }
                }
                if import_chat_history_callback:
                    import_chat_history_callback(k, RUN_PATHS[k])
        except Exception as e:
            logger.warning(f"Failed to load projects: {e}")
