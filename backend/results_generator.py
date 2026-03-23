"""
results_generator.py – Build the final results.json payload.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_results(
    run_id: str,
    repo_url: str,
    team_name: str,
    leader_name: str,
    branch_name: str,
    fixes: list[dict],
    ci_iterations: list[dict],
    start_time: datetime,
    end_time: datetime,
    final_status: str,           # "PASSED" | "FAILED"
    output_dir: str | None = None,
) -> dict:
    """
    Build and optionally save results.json.
    Returns the results dict.
    """
    total_seconds = (end_time - start_time).total_seconds()
    total_minutes = total_seconds / 60

    # --- Score Breakdown ---
    base_score = 100
    time_bonus = 10 if total_minutes < 5 else 0
    total_score = base_score + time_bonus

    score_breakdown = {
        "base": base_score,
        "time_bonus": time_bonus,
        "total": max(0, total_score),
        "breakdown_notes": [
            f"Base score: {base_score}",
            f"Time bonus (+10 if <5 min): +{time_bonus}" if time_bonus else "No time bonus (run > 5 min)",
        ],
    }

    failure_count = sum(1 for f in fixes if f.get("status") == "failed")
    fix_count = sum(1 for f in fixes if f.get("status") == "fixed")

    results = {
        "run_id": run_id,
        "run_summary": {
            "doc_folder": repo_url,
            "team_name": team_name,
            "leader_name": leader_name,
            "session": branch_name,
            "problems_found": len(fixes),
            "edits_applied": fix_count,
            "edits_failed": failure_count,
            "final_status": final_status,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "total_time_seconds": round(total_seconds, 1),
            "total_time_human": _format_duration(total_seconds),
        },
        "score_breakdown": score_breakdown,
        "fixes_table": fixes,
        "processing_timeline": ci_iterations,
    }

    # Optionally save to disk
    if output_dir:
        out_path = Path(output_dir) / "results.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"results.json saved to {out_path}")

    return results


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _format_duration(seconds: float) -> str:
    """Convert seconds to human-readable '2m 34s' format."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s}s"
