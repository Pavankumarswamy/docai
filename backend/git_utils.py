"""
git_utils.py – GitPython helpers for clone, branch, commit, push
"""

import os
import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from git import Repo, GitCommandError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Standardize the clones directory to an absolute path in the project root
CLONES_DIR = Path("c:/Users/shese/Desktop/CICD_AA/cloned_repos")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_clone_path(repo_url: str, run_id: str, team_name: str = "", leader_name: str = "") -> Path:
    """Return a unique local path for this run's clone with premium branding."""
    import re
    # Clean team/leader names for file system
    team = re.sub(r"[^A-Za-z0-9 ]", "", team_name).strip().replace(" ", "_")
    leader = re.sub(r"[^A-Za-z0-9 ]", "", leader_name).strip().replace(" ", "_")
    
    # Premium folder name per user request
    premium_name = f"AGENT_{team}_{leader}_Fix"
    return CLONES_DIR / f"{run_id}_{premium_name}"


def clone_repo(repo_url: str, run_id: str, pat: str | None = None, team_name: str = "", leader_name: str = "") -> Repo:
    """
    Clone *repo_url* to a local directory using a premium naming convention.
    """
    clone_path = get_clone_path(repo_url, run_id, team_name, leader_name)

    # Clean up any leftover clone from a previous (crashed) run
    if clone_path.exists():
        shutil.rmtree(clone_path, ignore_errors=True)

    CLONES_DIR.mkdir(parents=True, exist_ok=True)

    # Embed PAT for private repos
    auth_url = _inject_pat(repo_url, pat) if pat else repo_url

    logger.info(f"Cloning {repo_url} → {clone_path}")
    # Set non-interactive environment for clone
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}
    repo = Repo.clone_from(auth_url, str(clone_path), depth=50, env=env)
    
    # Configure local repo to NEVER use credential manager
    with repo.config_writer() as cw:
        cw.set_value("credential", "helper", "")
    
    logger.info("Clone complete.")
    
    # Register the path in state.RUN_PATHS immediately after successful clone
    try:
        from state import RUN_PATHS, save_projects
        RUN_PATHS[run_id] = Path(clone_path)
        save_projects()
        logger.info(f"[Git] Registered RUN_PATH for {run_id}: {clone_path}")
    except Exception as e:
        logger.warning(f"Could not register RUN_PATH for {run_id}: {e}")

    return repo


def create_branch(repo: Repo, branch_name: str) -> None:
    """Create and checkout a new branch. Raises if it already exists."""
    # Ensure we're on the default branch first
    try:
        origin_head = repo.remotes.origin.refs["HEAD"].reference.name.split("/")[-1]
    except Exception:
        origin_head = "main"

    try:
        repo.git.checkout(origin_head)
    except GitCommandError:
        pass  # already on default branch

    logger.info(f"Creating branch: {branch_name}")
    new_branch = repo.create_head(branch_name)
    new_branch.checkout()


def commit_changes(
    repo: Repo,
    changed_files: list[str],
    commit_message: str,
) -> str:
    """
    Stage *changed_files*, commit with *commit_message*.
    Prepends '[AI-AGENT] ' to the message automatically.
    Returns the short commit SHA.
    """
    if not commit_message.startswith("[AI-AGENT]"):
        commit_message = f"[AI-AGENT] {commit_message}"

    if not changed_files:
        # If no files specified, stage all changes
        repo.git.add(A=True)
    else:
        repo.index.add(changed_files)
    
    commit = repo.index.commit(commit_message)
    sha = commit.hexsha[:7]
    logger.info(f"Committed {sha}: {commit_message}")
    return sha


def push_changes(
    repo: Repo,
    pat: str | None = None,
) -> None:
    """
    Push current branch to origin.
    """
    # Configure remote URL with PAT if provided
    if pat:
        remote_url = repo.remotes.origin.url
        auth_url = _inject_pat(remote_url, pat)
        repo.remotes.origin.set_url(auth_url)

    # Aggressively suppress interactive prompts
    non_interactive_env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "echo",
        "SSH_ASKPASS": "echo",
        "DISPLAY": ":0",
        "GCM_INTERACTIVE": "never",  # Disable Git Credential Manager popups
        "GIT_CONFIG_PARAMETERS": "'credential.helper='", # Override system helper
    }

    with repo.git.custom_environment(**non_interactive_env):
        try:
            # Re-verify remote URL with PAT
            if pat:
                remote_url = repo.remotes.origin.url
                auth_url = _inject_pat(remote_url, pat)
                repo.remotes.origin.set_url(auth_url)
            
            # Ensure local config survives nested calls
            repo.git.config("credential.helper", "")
            
            current_branch = repo.active_branch.name
            logger.info(f"Pushing {current_branch} to origin...")
            repo.remotes.origin.push(refspec=f"{current_branch}:{current_branch}", force=True)
            logger.info(f"Pushed to origin/{current_branch}")
        except GitCommandError as exc:
            logger.error(f"Push failed: {exc}")
            raise


def commit_and_push(
    repo: Repo,
    changed_files: list[str],
    commit_message: str,
    pat: str | None = None,
) -> str:
    """
    Backward compatible helper.
    """
    sha = commit_changes(repo, changed_files, commit_message)
    push_changes(repo, pat)
    return sha


def get_all_files(repo: Repo, extensions: tuple = None) -> list[dict]:
    """
    Walk the repo working tree and return a list of
    { path: str, content: str } dicts for the frontend Monaco viewer.
    Skips .git, node_modules, __pycache__, binary files, and hidden dirs.
    """
    results = []
    root = Path(repo.working_dir)
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox"}

    for file_path in root.rglob("*"):
        if file_path.is_file():
            # Skip unwanted directories
            if any(part in skip_dirs or part.startswith(".") for part in file_path.parts):
                if not (file_path.name == ".env" or file_path.name == ".gitignore"): # Allow specific useful hidden files
                    continue
            
            # Extension filter (optional, if none provided we show everything text-based)
            if extensions and file_path.suffix not in extensions:
                continue

            rel = str(file_path.relative_to(root)).replace("\\", "/")
            try:
                # Optimized for speed: check if file is likely binary before reading
                with open(file_path, "rb") as f:
                    chunk = f.read(1024)
                    if b'\0' in chunk: # Simplistic binary check
                        continue
                
                content = file_path.read_text(encoding="utf-8", errors="replace")
                results.append({
                    "path": rel, 
                    "content": content,
                    "original_content": content # Preserve original for diff
                })
            except Exception as exc:
                logger.warning(f"Could not read {rel}: {exc}")
    return results


def cleanup_clone(repo: Repo) -> None:
    """Remove the cloned directory after the run."""
    try:
        shutil.rmtree(repo.working_dir, ignore_errors=True)
        logger.info(f"Cleaned up {repo.working_dir}")
    except Exception as exc:
        logger.warning(f"Cleanup failed: {exc}")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _inject_pat(url: str, pat: str) -> str:
    """Inject GitHub PAT into an HTTPS URL: https://PAT@github.com/..."""
    parsed = urlparse(url)
    authed = parsed._replace(netloc=f"{pat}@{parsed.hostname}")
    return urlunparse(authed)

