import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(os.getcwd())
os.chdir('backend')
sys.path.append(os.getcwd())

from state import runs, save_projects

# Find the most recent run with files
latest_run_id = None
if runs:
    # Sort by timestamp or just take the last one if it's a dict ordered by insertion
    latest_run_id = list(runs.keys())[-1]

if latest_run_id:
    print(f"Patching run {latest_run_id}...")
    run = runs[latest_run_id]
    if "live" in run and "files" in run["live"]:
        old_files = run["live"]["files"]
        new_files = []
        for f in old_files:
            p = f.get("path", "")
            # Simplify path to filename only for fixing the tree
            new_p = os.path.basename(p.replace("\\", "/"))
            new_files.append({"path": new_p, "type": "file"})
        
        # Add the log files if not present
        if not any(f["path"] == "edits_log.json" for f in new_files):
            new_files.append({"path": "edits_log.json", "type": "file"})
        if not any(f["path"] == "results.json" for f in new_files):
            new_files.append({"path": "results.json", "type": "file"})
            
        run["live"]["files"] = new_files
        print(f"Updated file list: {[f['path'] for f in new_files]}")
        save_projects()
        print("State saved.")
else:
    print("No runs found to patch.")
