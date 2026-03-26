#!/usr/bin/env python3
"""
cli.py — DOCAI Interactive Terminal CLI
========================================

Production-grade terminal interface for the Document Intelligence System.

Features
--------
  • Rich interactive menus with live navigation
  • Real-time pipeline progress (per-document, per-section)
  • LangGraph agent status panel (Retriever → Editor → Reviewer → Refiner)
  • Inline statistics tracking (edits, skips, LLM calls, errors)
  • Final results summary table
  • Log viewer with colour-coded lines
  • Output directory browser
  • Settings panel
  • Config persistence (remembers last used paths)
  • Command-line argument support for semi-automated runs

Usage
-----
    python cli.py                         # fully interactive
    python cli.py --docs /path/to/docs \\
                  --csv  /path/to/data.csv \\
                  --team "Team Alpha"    \\
                  --leader "Jane Smith"  # pre-fill inputs, still confirms
    python cli.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Ensure backend directory is on sys.path ───────────────────────────────────
_BACKEND_DIR = Path(__file__).parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# ── Rich (hard requirement) ───────────────────────────────────────────────────
try:
    from rich.align import Align
    from rich.columns import Columns
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.markup import escape
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.style import Style
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError:
    print(
        "\n[ERROR] The 'rich' package is required.\n"
        "Install it with:  pip install rich\n"
    )
    sys.exit(1)

# ── DOCAI backend imports ─────────────────────────────────────────────────────
try:
    from agents import run_pipeline
    from input_layer import list_docx_files
    from run_logger import configure_run_logger
    from html_exporter import docx_to_html_preview, open_in_browser
    from llm_client import list_ollama_models, set_active_model, get_active_model
except ImportError as _e:
    print(f"\n[ERROR] Cannot import DOCAI modules: {_e}\n"
          "Make sure you are running from the backend/ directory.\n")
    sys.exit(1)

configure_run_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

VERSION   = "2.0.0"
APP_NAME  = "DOCAI"

CONFIG_FILE = _BACKEND_DIR / ".docai_cli_config.json"
LOG_FILE    = _BACKEND_DIR / "logs" / "run.log"

# Colour aliases used throughout
_C_PRIMARY  = "bold cyan"
_C_SUCCESS  = "bold green"
_C_WARN     = "bold yellow"
_C_ERROR    = "bold red"
_C_DIM      = "dim white"
_C_ACCENT   = "magenta"
_C_HEADING  = "bold cyan"

BANNER = r"""
  ██████╗  ██████╗  ██████╗  █████╗ ██╗
  ██╔══██╗██╔═══██╗██╔════╝ ██╔══██╗██║
  ██║  ██║██║   ██║██║      ███████║██║
  ██║  ██║██║   ██║██║      ██╔══██║██║
  ██████╔╝╚██████╔╝╚██████╗ ██║  ██║██║
  ╚═════╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝"""

SUBTITLE = (
    f"  Document Intelligence System  v{VERSION}  "
    "─  Intelligent Process Flow Updater"
)

# Sections that must NEVER be modified (used only for display annotation)
RESTRICTED = {
    "scope of process note",
    "systems involved",
    "list of stakeholders",
    "block diagram",
    "roles and responsibilities",
}

# ─────────────────────────────────────────────────────────────────────────────
# Console (shared singleton)
# ─────────────────────────────────────────────────────────────────────────────

console = Console(highlight=False)

# ─────────────────────────────────────────────────────────────────────────────
# Config persistence
# ─────────────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load saved CLI configuration (best-effort)."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_config(cfg: dict) -> None:
    """Persist CLI configuration (best-effort)."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Banner / UI chrome
# ─────────────────────────────────────────────────────────────────────────────

def _show_banner() -> None:
    console.clear()
    console.print(Text(BANNER, style=_C_PRIMARY, justify="center"))
    console.print(Text(SUBTITLE, style=_C_DIM, justify="center"))
    console.print()
    console.print(Rule(style="dim cyan"))
    console.print()


def _section_header(title: str) -> None:
    console.print(Rule(f"[{_C_HEADING}]  {title}  [/]", style="cyan"))
    console.print()


# ─────────────────────────────────────────────────────────────────────────────
# Main menu
# ─────────────────────────────────────────────────────────────────────────────

def _show_main_menu() -> None:
    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    t.add_column("Key",  style=_C_PRIMARY, width=5)
    t.add_column("Option", style="bold white", width=26)
    t.add_column("Description", style="dim")

    t.add_row("  1", "Process Documents",
              "Update .docx files using CSV / Excel context data")
    t.add_row("  2", "Browse Output",
              "View generated output files and sizes")
    t.add_row("  3", "View Logs",
              "Inspect the internal run log (colour-coded)")
    t.add_row("  4", "Settings",
              "Show configuration and paths")
    t.add_row("  5", "Select Ollama Model",
              f"Choose LLM model  [dim](active: {get_active_model()})[/]")
    t.add_row("  Q", "Quit", "Exit DOCAI")

    console.print(Panel(
        t,
        title=f"[{_C_HEADING}]  Main Menu  [/]",
        border_style="cyan",
        padding=(1, 2),
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Input helpers
# ─────────────────────────────────────────────────────────────────────────────

def _prompt_folder(label: str, default: str = "") -> Optional[str]:
    """Prompt for a folder path that must exist.  Returns path string or None."""
    while True:
        val = Prompt.ask(
            f"[{_C_PRIMARY}]{label}[/]",
            default=default or None,
            console=console,
        )
        if val is None:
            return None
        val = val.strip().strip('"').strip("'")
        p = Path(val)
        if p.is_dir():
            return str(p)
        console.print(f"  [{_C_ERROR}]✗[/] Directory not found: [dim]{p}[/]")
        _hint_directory(p.parent, files=False)


def _prompt_file(label: str, default: str = "") -> Optional[str]:
    """Prompt for a file path that must exist.  Returns path string or None."""
    while True:
        val = Prompt.ask(
            f"[{_C_PRIMARY}]{label}[/]",
            default=default or None,
            console=console,
        )
        if val is None:
            return None
        val = val.strip().strip('"').strip("'")
        p = Path(val)
        if p.is_file():
            return str(p)
        console.print(f"  [{_C_ERROR}]✗[/] File not found: [dim]{p}[/]")
        _hint_directory(p.parent, files=True)


def _prompt_text(label: str, default: str = "", required: bool = True) -> str:
    """Simple required text prompt."""
    while True:
        val = Prompt.ask(
            f"[{_C_PRIMARY}]{label}[/]",
            default=default or None,
            console=console,
        )
        val = (val or "").strip()
        if val or not required:
            return val
        console.print(f"  [{_C_ERROR}]✗[/] This field is required.")


def _hint_directory(parent: Path, files: bool = False, limit: int = 10) -> None:
    """Show a compact listing of a directory to help the user navigate."""
    if not parent.exists():
        return
    try:
        items = sorted(parent.iterdir())[:limit]
    except PermissionError:
        return
    names = []
    for item in items:
        if files and item.is_file():
            names.append(f"[dim cyan]{escape(item.name)}[/]")
        elif not files and item.is_dir():
            names.append(f"[dim yellow]{escape(item.name)}/[/]")
    if names:
        console.print("  [dim]Nearby:[/] " + "  ".join(names))


# ─────────────────────────────────────────────────────────────────────────────
# Input collection
# ─────────────────────────────────────────────────────────────────────────────

def _collect_inputs(
    cfg: dict,
    *,
    pre_docs: str = "",
    pre_csv: str = "",
    pre_team: str = "",
    pre_leader: str = "",
) -> Optional[dict]:
    """
    Gather all required inputs interactively.

    Pre-fill values come from CLI arguments or saved config.
    Returns a validated dict or None if the user cancels.
    """
    console.print(Panel(
        "[bold]Enter the documents folder and CSV file below.[/]\n"
        "[dim]Press Enter to accept a [default] value shown in brackets.[/]",
        title=f"[{_C_HEADING}]  Setup — Document Processor  [/]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()

    # ── Documents folder ──────────────────────────────────────────────────────
    doc_folder = _prompt_folder(
        "Documents folder (contains .docx files)",
        default=pre_docs or cfg.get("doc_folder", ""),
    )
    if doc_folder is None:
        return None

    docs = list_docx_files(doc_folder)
    if not docs:
        console.print(f"\n  [{_C_WARN}]⚠[/]  No .docx files found in the selected folder.")
        if not Confirm.ask("  Continue anyway?", console=console, default=False):
            return None
    else:
        console.print(f"\n  [{_C_SUCCESS}]✓[/]  {len(docs)} document(s) detected:")
        for d in docs[:6]:
            console.print(f"    [dim]•[/] {escape(d.name)}", style="dim white")
        if len(docs) > 6:
            console.print(f"    [dim]… and {len(docs) - 6} more[/]")
    console.print()

    # ── CSV / Excel file ──────────────────────────────────────────────────────
    csv_file = _prompt_file(
        "CSV / Excel file (update context data)",
        default=pre_csv or cfg.get("csv_file", ""),
    )
    if csv_file is None:
        return None
    console.print()

    # Team / leader are fixed — not collected from user
    team_name   = "DOCAI"
    leader_name = "PROJECT"

    # ── Confirmation ──────────────────────────────────────────────────────────
    summary = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    summary.add_column("Field", style="dim",       width=22)
    summary.add_column("Value", style="bold white")

    summary.add_row("Documents folder", escape(doc_folder))
    summary.add_row("CSV / Excel file",  escape(csv_file))
    summary.add_row("Documents found",   str(len(docs)))

    console.print(Panel(
        summary,
        title=f"[{_C_HEADING}]  Confirm  [/]",
        border_style="cyan",
        padding=(0, 1),
    ))
    console.print()

    if not Confirm.ask(f"  [{_C_PRIMARY}]Start processing?[/]",
                       console=console, default=True):
        console.print("  [yellow]Cancelled.[/]")
        return None

    return {
        "doc_folder":  doc_folder,
        "csv_file":    csv_file,
        "team_name":   team_name,
        "leader_name": leader_name,
        "doc_count":   len(docs),
        "doc_names":   [d.name for d in docs],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Live display — layout & panel renderers
# ─────────────────────────────────────────────────────────────────────────────

def _build_layout() -> Layout:
    """Construct the 5-zone layout used during pipeline execution."""
    root = Layout(name="root")
    root.split_column(
        Layout(name="header",  size=3),
        Layout(name="body"),
        Layout(name="footer",  size=3),
    )
    root["body"].split_row(
        Layout(name="left",  ratio=3),
        Layout(name="right", ratio=2),
    )
    root["left"].split_column(
        Layout(name="status",   size=5),
        Layout(name="terminal"),
    )
    root["right"].split_column(
        Layout(name="agents",  size=10),
        Layout(name="stats"),
    )
    return root


def _panel_header(run_id: str, start: datetime) -> Panel:
    elapsed   = int((datetime.now(timezone.utc) - start).total_seconds())
    h, rem    = divmod(elapsed, 3600)
    m, s      = divmod(rem, 60)
    time_str  = f"{h:02d}:{m:02d}:{s:02d}"
    content   = Align.center(
        f"[{_C_PRIMARY}]{APP_NAME}[/]  [dim]│[/]  "
        f"Run: [{_C_HEADING}]{run_id}[/]  [dim]│[/]  "
        f"Elapsed: [bold white]{time_str}[/]"
    )
    return Panel(content, border_style="cyan")


def _panel_status(live: dict, doc_count: int) -> Panel:
    phase   = live.get("phase", "initializing")
    message = live.get("message", "Starting…")

    colour = {
        "discovery":  "yellow",
        "execution":  "cyan",
        "fixing":     "magenta",
        "done":       "green",
        "error":      "red",
    }.get(phase, "white")

    files     = live.get("files", [])
    out_files = sum(1 for f in files if "output/" in f.get("path", ""))

    t = Text()
    t.append(f"  Phase   ", style="dim")
    t.append(f"{phase.upper()}\n", style=f"bold {colour}")
    t.append(f"  Status  ", style="dim")
    t.append(f"{escape(message)}\n", style="white")
    t.append(f"  Docs    ", style="dim")
    t.append(f"{out_files} / {doc_count} complete", style="bold white")

    return Panel(t, title="[bold]Pipeline Status[/]",
                 border_style=colour, padding=(0, 1))


def _panel_terminal(live: dict, max_lines: int = 20) -> Panel:
    """Scrolling terminal output — last N lines, colour-coded."""
    raw   = live.get("terminal_output", "")
    lines = [l for l in raw.splitlines() if l.strip()][-max_lines:]

    text = Text(overflow="fold")
    for line in lines:
        if line.startswith("[ERROR]") or "Pipeline error" in line:
            text.append(line + "\n", style="bold red")
        elif line.startswith("[SKIP]"):
            text.append(line + "\n", style="dim yellow")
        elif line.startswith(">>>"):
            text.append(line + "\n", style="bold white")
        elif "[STATS]" in line:
            text.append(line + "\n", style=f"bold {_C_PRIMARY}")
        elif "edit(s) applied" in line and "0 edit" not in line:
            text.append(line + "\n", style="green")
        elif "No updates needed" in line or "No changes" in line:
            text.append(line + "\n", style="dim")
        elif "Saved:" in line:
            text.append(line + "\n", style=f"bold {_C_SUCCESS}")
        elif "LLM pipeline error" in line:
            text.append(line + "\n", style="red")
        else:
            text.append(line + "\n", style=_C_DIM)

    placeholder = Text("  Waiting for pipeline output…", style="dim")
    return Panel(
        text if lines else placeholder,
        title="[bold]Live Output[/]",
        border_style="dim blue",
    )


_AGENT_ORDER = ["Retriever", "Editor", "Reviewer", "Refiner"]


def _active_agent_index(live: dict) -> int:
    """Infer which LangGraph agent is currently active from live data."""
    phase   = live.get("phase", "")
    message = live.get("message", "").lower()
    term    = live.get("terminal_output", "").lower()

    if phase == "done":
        return len(_AGENT_ORDER)          # all done
    if "[refiner]" in term[-300:]:
        return 3
    if "[reviewer]" in term[-300:]:
        return 2
    if phase == "fixing" or "[editor]" in term[-300:]:
        return 1
    if phase in ("discovery", "execution"):
        return 0
    return -1


def _panel_agents(live: dict) -> Panel:
    """LangGraph agent pipeline — show active / done / waiting."""
    active = _active_agent_index(live)
    phase  = live.get("phase", "")

    text = Text()
    text.append("\n")
    for i, name in enumerate(_AGENT_ORDER):
        if phase == "done" or i < active:
            text.append(f"   ✓  {name}\n", style=f"bold {_C_SUCCESS}")
        elif i == active:
            text.append(f"   ►  {name}  ", style=f"bold {_C_ACCENT}")
            text.append("processing…\n", style=f"dim {_C_ACCENT}")
        else:
            text.append(f"   ○  {name}\n", style="dim")

    if phase == "error":
        text = Text("\n   ✗  Pipeline Error", style="bold red")
    elif phase == "done":
        text.append("\n   Pipeline complete.\n", style=f"dim {_C_SUCCESS}")

    return Panel(
        text,
        title=f"[bold]LangGraph  Retriever → Refiner[/]",
        border_style="magenta",
        padding=(0, 1),
    )


def _panel_stats(live: dict, counters: dict) -> Panel:
    """Running statistics extracted from terminal output."""
    raw = live.get("terminal_output", "")

    # Count events from raw output (accumulated)
    counters["skip"]  = raw.count("[SKIP]")
    counters["saved"] = raw.count("Saved:")
    counters["error"] = raw.lower().count("[error]")
    counters["edit"]  = sum(
        int(tok)
        for line in raw.splitlines()
        if "edit(s) applied" in line
        for tok in [line.split("edit(s)")[0].strip().split()[-1]]
        if tok.isdigit()
    )

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column("Metric", style="dim",        width=20)
    t.add_column("Value",  style="bold white",  justify="right")

    t.add_row("Sections skipped",  str(counters["skip"]))
    t.add_row("Total edits applied", str(counters["edit"]))
    t.add_row("Output files saved",  str(counters["saved"]))
    t.add_row("Errors encountered",
              f"[red]{counters['error']}[/]" if counters["error"] else "0")

    return Panel(t, title="[bold]Statistics[/]",
                 border_style="dim cyan", padding=(1, 0))


def _panel_footer(live: dict) -> Panel:
    phase = live.get("phase", "")
    if phase == "done":
        msg = Align.center(
            f"[{_C_SUCCESS}]✓  Processing complete[/]  [dim]│[/]  "
            "Press [bold]Ctrl+C[/] or wait to return to menu"
        )
    elif phase == "error":
        msg = Align.center(f"[{_C_ERROR}]✗  Error — check logs for details[/]")
    else:
        msg = Align.center("[dim]Running…   Press [bold]Ctrl+C[/] to abort[/]")
    return Panel(msg, border_style="dim")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline executor
# ─────────────────────────────────────────────────────────────────────────────

def _run_with_live_display(inputs: dict) -> dict:
    """
    Start run_pipeline in a background thread and render a Live layout
    in the main thread.  Returns the runs dict on completion.
    """
    run_id      = str(uuid.uuid4())[:8].upper()
    branch_name = (
        f"{inputs['team_name'].upper().replace(' ', '_')}_"
        f"{inputs['leader_name'].upper().replace(' ', '_')}_DOC_UPDATE"
    )

    # Shared state dict (mirrors the FastAPI pattern — agents.py reads/writes this)
    runs: dict = {
        run_id: {
            "status":      "running",
            "team_name":   inputs["team_name"],
            "leader_name": inputs["leader_name"],
            "repo_url":    inputs["doc_folder"],
            "excel_file":  inputs["csv_file"],
            "live": {
                "phase":           "initializing",
                "message":         "Starting pipeline…",
                "files":           [],
                "terminal_output": "",
                "iterations":      [],
            },
        }
    }

    _result: dict   = {}
    _abort          = threading.Event()

    def _thread_target():
        try:
            result = run_pipeline(
                run_id=run_id,
                doc_folder=inputs["doc_folder"],
                excel_file=inputs["csv_file"],
                team_name=inputs["team_name"],
                leader_name=inputs["leader_name"],
                branch_name=branch_name,
                runs=runs,
            )
            _result.update(result or {})
        except Exception as exc:
            runs[run_id]["status"]           = "failed"
            runs[run_id]["live"]["phase"]    = "error"
            runs[run_id]["live"]["message"]  = f"Pipeline error: {exc}"
            runs[run_id]["live"]["terminal_output"] += f"\n[ERROR] {exc}"

    worker = threading.Thread(target=_thread_target, daemon=True)
    worker.start()

    start_time = datetime.now(timezone.utc)
    layout     = _build_layout()
    counters: dict = {"skip": 0, "edit": 0, "saved": 0, "error": 0}

    console.print()
    console.print(Rule(
        f"[{_C_PRIMARY}]  Run {run_id} — Starting  [/]",
        style="cyan",
    ))
    console.print()

    try:
        with Live(layout, console=console, refresh_per_second=4, screen=True):
            while True:
                live_data = runs[run_id]["live"]
                status    = runs[run_id].get("status", "running")

                layout["header"].update(_panel_header(run_id, start_time))
                layout["status"].update(_panel_status(live_data, inputs["doc_count"]))
                layout["terminal"].update(_panel_terminal(live_data))
                layout["agents"].update(_panel_agents(live_data))
                layout["stats"].update(_panel_stats(live_data, counters))
                layout["footer"].update(_panel_footer(live_data))

                if status in ("completed", "failed") and not worker.is_alive():
                    # Final render before exit
                    time.sleep(1.5)
                    layout["header"].update(_panel_header(run_id, start_time))
                    layout["status"].update(_panel_status(live_data, inputs["doc_count"]))
                    layout["terminal"].update(_panel_terminal(live_data))
                    layout["agents"].update(_panel_agents(live_data))
                    layout["stats"].update(_panel_stats(live_data, counters))
                    layout["footer"].update(_panel_footer(live_data))
                    time.sleep(1.0)
                    break

                time.sleep(0.25)

    except KeyboardInterrupt:
        _abort.set()
        console.print(f"\n  [{_C_WARN}]⚠[/]  Processing interrupted by user.")

    worker.join(timeout=8)
    return runs, run_id, _result, start_time, counters


# ─────────────────────────────────────────────────────────────────────────────
# Final results summary
# ─────────────────────────────────────────────────────────────────────────────

def _show_results(
    run_id: str,
    runs: dict,
    inputs: dict,
    start_time: datetime,
    counters: dict,
) -> None:
    _show_banner()
    _section_header("Run Summary")

    run    = runs[run_id]
    status = run.get("status", "unknown")
    live   = run.get("live", {})
    raw    = live.get("terminal_output", "")

    elapsed   = int((datetime.now(timezone.utc) - start_time).total_seconds())
    h, rem    = divmod(elapsed, 3600)
    m, s      = divmod(rem, 60)

    if status == "completed":
        console.print(Panel(
            Align.center(
                f"[{_C_SUCCESS}]✓  All documents processed successfully[/]\n\n"
                f"Run ID: [{_C_HEADING}]{run_id}[/]  │  "
                f"Duration: [bold white]{h:02d}:{m:02d}:{s:02d}[/]"
            ),
            border_style="green",
            padding=(1, 4),
        ))
    else:
        err = live.get("message", "Unknown error")
        console.print(Panel(
            Align.center(
                f"[{_C_ERROR}]✗  Processing encountered errors[/]\n\n"
                f"[dim]{escape(err)}[/]\n\n"
                f"See: [dim]{LOG_FILE}[/]"
            ),
            border_style="red",
            padding=(1, 4),
        ))

    console.print()

    # ── Per-document table (parse STATS lines) ────────────────────────────────
    stat_lines = [l for l in raw.splitlines() if "[STATS]" in l]
    if stat_lines:
        tbl = Table(
            title="[bold]Per-Document Results[/]",
            box=box.ROUNDED,
            border_style="cyan",
            padding=(0, 1),
            show_lines=True,
        )
        tbl.add_column("Document",          style="bold white",  no_wrap=True)
        tbl.add_column("Processed",         justify="center",    style="green")
        tbl.add_column("Skipped",           justify="center",    style="yellow")
        tbl.add_column("LLM Calls",         justify="center",    style="magenta")
        tbl.add_column("Errors",            justify="center",    style="red")

        for line in stat_lines:
            try:
                after_tag = line.split("[STATS]", 1)[-1].strip()
                doc_name, rest = after_tag.split(":", 1)
                kv = {}
                for pair in rest.strip().split(","):
                    pair = pair.strip()
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        kv[k.strip()] = v.strip()

                processed = kv.get("processed", "?")
                skipped   = kv.get("skipped",   "?")
                llm_calls = kv.get("llm_calls",  "?")
                errors    = kv.get("errors",     "0")
                err_style = "bold red" if errors != "0" else "dim"

                tbl.add_row(
                    escape(doc_name.strip()),
                    processed,
                    skipped,
                    llm_calls,
                    f"[{err_style}]{errors}[/]",
                )
            except Exception:
                continue

        if tbl.row_count:
            console.print(tbl)
            console.print()

    # ── Overall stats ─────────────────────────────────────────────────────────
    overall = Table(
        title="[bold]Run Totals[/]",
        box=box.SIMPLE_HEAVY,
        border_style="cyan",
        padding=(0, 2),
    )
    overall.add_column("Metric",  style="dim",        width=28)
    overall.add_column("Value",   style="bold white",  justify="right")

    overall.add_row("Documents queued",     str(inputs["doc_count"]))
    overall.add_row("Sections skipped",     str(counters["skip"]))
    overall.add_row("Total edits applied",  str(counters["edit"]))
    overall.add_row("Output files saved",   str(counters["saved"]))
    overall.add_row(
        "Errors",
        f"[red]{counters['error']}[/]" if counters["error"] else "[green]0[/]",
    )
    overall.add_row("Elapsed time", f"{h:02d}:{m:02d}:{s:02d}")
    console.print(overall)
    console.print()

    # ── Output location + HTML preview generation ─────────────────────────────
    out_dir = Path(inputs["doc_folder"]) / "output"
    previews_opened: list[Path] = []

    if out_dir.exists():
        out_files = sorted(
            out_dir.glob("*.docx"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if out_files:
            console.print(Panel(
                f"[bold]{len(out_files)}[/] file(s) written to:\n"
                f"[{_C_PRIMARY}]{out_dir}[/]\n\n" +
                "\n".join(f"  • {escape(f.name)}" for f in out_files[:8]) +
                (f"\n  [dim]… and {len(out_files) - 8} more[/]"
                 if len(out_files) > 8 else ""),
                title=f"[{_C_SUCCESS}]  Output Files  [/]",
                border_style="green",
                padding=(0, 2),
            ))
            console.print()

            # ── Auto-generate HTML previews ───────────────────────────────────
            console.print(
                f"  [{_C_PRIMARY}]Generating browser previews…[/]  "
                "(changed text = blue, deleted = red strikethrough)"
            )
            console.print()

            html_dir = out_dir / "previews"
            gen_ok: list[Path]   = []
            gen_err: list[str]   = []

            for docx_file in out_files:
                try:
                    html_path = docx_to_html_preview(
                        docx_path   = docx_file,
                        output_html = html_dir / (docx_file.stem + "_preview.html"),
                    )
                    gen_ok.append(html_path)
                    console.print(
                        f"  [{_C_SUCCESS}]✓[/] {escape(docx_file.name)} "
                        f"→ [dim]{escape(html_path.name)}[/]"
                    )
                except Exception as exc:
                    gen_err.append(f"{docx_file.name}: {exc}")
                    console.print(
                        f"  [{_C_ERROR}]✗[/] {escape(docx_file.name)}: {exc}"
                    )

            console.print()

            if gen_ok:
                console.print(Panel(
                    f"[{_C_SUCCESS}]✓[/]  {len(gen_ok)} HTML preview(s) ready\n"
                    f"[dim]Location: {html_dir}[/]\n\n"
                    "[bold]Colour key in the preview:[/]\n"
                    f"  [{_C_PRIMARY}]Blue bold text[/]         → inserted / updated content\n"
                    f"  [{_C_ERROR}]Red strikethrough[/]     → replaced / deleted content\n"
                    "  Normal black text      → unchanged original content",
                    title=f"[{_C_SUCCESS}]  HTML Preview Ready  [/]",
                    border_style="green",
                    padding=(1, 2),
                ))
                console.print()

                # ── Open in browser ───────────────────────────────────────────
                open_choice = Prompt.ask(
                    f"  [{_C_PRIMARY}]Open preview(s) in browser?[/]  "
                    "[dim]all / first / no[/]",
                    choices=["all", "first", "no", "a", "f", "n"],
                    default="all",
                    show_choices=False,
                    console=console,
                )
                console.print()

                to_open = []
                if open_choice in ("all", "a"):
                    to_open = gen_ok
                elif open_choice in ("first", "f"):
                    to_open = gen_ok[:1]

                for html_path in to_open:
                    open_in_browser(html_path)
                    previews_opened.append(html_path)
                    console.print(
                        f"  [{_C_SUCCESS}]↗[/] Opened: [dim]{escape(html_path.name)}[/]"
                    )

                if previews_opened:
                    console.print()
                    console.print(
                        f"  [dim]Browser tab(s) opened.  "
                        "If nothing appeared, open the files manually from:[/]\n"
                        f"  [{_C_PRIMARY}]{html_dir}[/]"
                    )
                    console.print()

    Prompt.ask(
        f"[dim]Press Enter to return to the main menu[/]",
        default="",
        console=console,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main flow: process documents
# ─────────────────────────────────────────────────────────────────────────────

def _flow_process_documents(
    pre_docs: str = "",
    pre_csv: str  = "",
    pre_team: str = "",
    pre_leader: str = "",
) -> None:
    """End-to-end document processing flow."""
    cfg    = _load_config()
    inputs = _collect_inputs(
        cfg,
        pre_docs=pre_docs,
        pre_csv=pre_csv,
        pre_team=pre_team,
        pre_leader=pre_leader,
    )
    if not inputs:
        return

    # Persist config for next run
    _save_config({
        "doc_folder":  inputs["doc_folder"],
        "csv_file":    inputs["csv_file"],
        "team_name":   inputs["team_name"],
        "leader_name": inputs["leader_name"],
    })

    runs, run_id, _result, start_time, counters = _run_with_live_display(inputs)
    _show_results(run_id, runs, inputs, start_time, counters)


# ─────────────────────────────────────────────────────────────────────────────
# Log viewer
# ─────────────────────────────────────────────────────────────────────────────

def _flow_view_logs() -> None:
    _section_header("Run Log Viewer")

    if not LOG_FILE.exists():
        console.print(Panel(
            f"[{_C_WARN}]No log file found yet.[/]\n"
            f"[dim]{LOG_FILE}[/]\n\n"
            "Run a processing job first to generate logs.",
            border_style="yellow",
            padding=(1, 2),
        ))
        Prompt.ask("[dim]Press Enter to return[/]", default="", console=console)
        return

    n_str = Prompt.ask(
        f"[{_C_PRIMARY}]Lines to display[/]",
        default="60",
        console=console,
    )
    try:
        n_lines = max(10, min(500, int(n_str)))
    except ValueError:
        n_lines = 60

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
        all_lines = fh.readlines()

    recent = all_lines[-n_lines:]
    text   = Text(overflow="fold")

    for raw_line in recent:
        line = raw_line.rstrip()
        if "ERROR" in line:
            text.append(line + "\n", style="bold red")
        elif "WARNING" in line:
            text.append(line + "\n", style="yellow")
        elif "SKIP" in line:
            text.append(line + "\n", style="dim yellow")
        elif "PROCESSED" in line or "SUMMARY" in line:
            text.append(line + "\n", style=f"bold {_C_SUCCESS}")
        elif "LLM CALL" in line:
            text.append(line + "\n", style=_C_ACCENT)
        elif "DEBUG" in line:
            text.append(line + "\n", style="dim")
        else:
            text.append(line + "\n", style=_C_DIM)

    total_lines = len(all_lines)
    console.print(Panel(
        text,
        title=f"[bold]Run Log  —  last {n_lines} of {total_lines} lines[/]",
        subtitle=f"[dim]{LOG_FILE}[/]",
        border_style="cyan",
    ))
    console.print()

    # Offer to clear or continue
    action = Prompt.ask(
        f"[{_C_PRIMARY}]Action[/]",
        choices=["enter", "clear", "c"],
        default="enter",
        show_choices=True,
        console=console,
    )
    if action in ("clear", "c"):
        if Confirm.ask("  Clear the entire log file?", console=console, default=False):
            with open(LOG_FILE, "w", encoding="utf-8") as fh:
                fh.write("")
            console.print(f"  [{_C_SUCCESS}]✓[/] Log cleared.")
            time.sleep(0.8)


# ─────────────────────────────────────────────────────────────────────────────
# Output browser
# ─────────────────────────────────────────────────────────────────────────────

def _flow_browse_output() -> None:
    _section_header("Output Browser")
    cfg = _load_config()

    doc_folder = cfg.get("doc_folder", "")
    if not doc_folder:
        doc_folder = _prompt_folder("Documents folder") or ""

    if not doc_folder:
        return

    out_dir = Path(doc_folder) / "output"
    if not out_dir.exists():
        console.print(Panel(
            f"[{_C_WARN}]Output directory does not exist yet.[/]\n"
            f"[dim]{out_dir}[/]",
            border_style="yellow",
            padding=(1, 2),
        ))
        Prompt.ask("[dim]Press Enter to return[/]", default="", console=console)
        return

    files = sorted(out_dir.glob("*.docx"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        console.print(Panel(
            f"[{_C_WARN}]No output .docx files found.[/]\n[dim]{out_dir}[/]",
            border_style="yellow",
            padding=(1, 2),
        ))
        Prompt.ask("[dim]Press Enter to return[/]", default="", console=console)
        return

    tbl = Table(
        title=f"[bold]Output Files[/]  [dim]({out_dir})[/]",
        box=box.ROUNDED,
        border_style="cyan",
        padding=(0, 1),
        show_lines=False,
    )
    tbl.add_column("#",          width=4,  justify="right", style="dim")
    tbl.add_column("Filename",   style="bold white")
    tbl.add_column("Size",       justify="right", style=_C_PRIMARY,  width=12)
    tbl.add_column("Modified",   style="dim",                        width=18)

    for i, f in enumerate(files, 1):
        size_kb = f.stat().st_size / 1024
        mtime   = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d  %H:%M")
        tbl.add_row(str(i), escape(f.name), f"{size_kb:.1f} KB", mtime)

    console.print(tbl)
    console.print()
    console.print(f"  [dim]Total: {len(files)} file(s)  │  Path: {out_dir}[/]")
    console.print()
    Prompt.ask("[dim]Press Enter to return[/]", default="", console=console)


# ─────────────────────────────────────────────────────────────────────────────
# Model selector
# ─────────────────────────────────────────────────────────────────────────────

def _flow_select_model(*, startup: bool = False) -> None:
    """
    Fetch available Ollama models, display them as a numbered table,
    and let the user pick one.  Updates the active model in llm_client.

    If *startup* is True and a model is already saved in config,
    auto-apply it silently (user can change it via menu option 5).
    """
    _section_header("Select Ollama Model")

    cfg        = _load_config()
    saved_model = cfg.get("ollama_model", "")

    # ── Check Ollama is reachable ──────────────────────────────────────────────
    console.print(f"  [{_C_DIM}]Connecting to Ollama…[/]")

    try:
        import requests as _req
        models = list_ollama_models()
    except Exception as exc:
        console.print(Panel(
            f"[{_C_ERROR}]✗  Cannot reach Ollama[/]\n\n"
            f"[dim]{exc}[/]\n\n"
            "Make sure Ollama is installed and running:\n"
            f"  [bold]ollama serve[/]\n\n"
            f"Then restart DOCAI or select option [bold]5[/] again.",
            title=f"[{_C_ERROR}]  Ollama Unavailable  [/]",
            border_style="red",
            padding=(1, 2),
        ))
        console.print()
        Prompt.ask("[dim]Press Enter to continue[/]", default="", console=console)
        return

    if not models:
        console.print(Panel(
            f"[{_C_WARN}]No models found in Ollama.[/]\n\n"
            "Pull a model first, e.g.:\n"
            "  [bold]ollama pull llama3[/]\n"
            "  [bold]ollama pull mistral[/]\n"
            "  [bold]ollama pull phi3[/]",
            title=f"[{_C_WARN}]  No Models Available  [/]",
            border_style="yellow",
            padding=(1, 2),
        ))
        console.print()
        Prompt.ask("[dim]Press Enter to continue[/]", default="", console=console)
        return

    # ── If called at startup and a saved model still exists, restore silently ──
    if startup and saved_model:
        model_names = [m.get("name", "") for m in models]
        if saved_model in model_names:
            set_active_model(saved_model)
            console.print(
                f"  [{_C_SUCCESS}]✓[/]  Restored model from last session: "
                f"[bold cyan]{saved_model}[/]"
            )
            time.sleep(0.6)
            return
        # saved model was deleted — fall through to picker

    # ── Display model table ────────────────────────────────────────────────────
    tbl = Table(
        title=f"[bold]Available Ollama Models[/]  "
              f"[dim]({len(models)} installed)[/]",
        box=box.ROUNDED,
        border_style="cyan",
        padding=(0, 1),
        show_lines=False,
        highlight=False,
    )
    tbl.add_column("#",            width=4,  justify="right",  style="dim")
    tbl.add_column("Model Name",   style="bold white",          min_width=22)
    tbl.add_column("Family",       style="cyan",                width=16)
    tbl.add_column("Params",       justify="right", style=_C_PRIMARY, width=10)
    tbl.add_column("Quant",        style="dim",                 width=10)
    tbl.add_column("Size",         justify="right", style="dim", width=10)

    active = get_active_model()

    for i, m in enumerate(models, 1):
        name    = m.get("name", "?")
        details = m.get("details", {})
        family  = details.get("family", details.get("families", ["?"])[0]
                              if details.get("families") else "?")
        params  = details.get("parameter_size", "?")
        quant   = details.get("quantization_level", "?")
        size_gb = m.get("size", 0) / 1_073_741_824  # bytes → GB

        # Highlight the currently active model
        name_display = (
            f"[bold green]{name} ✓[/]"
            if name == active
            else escape(name)
        )
        tbl.add_row(
            str(i),
            name_display,
            escape(str(family)),
            escape(str(params)),
            escape(str(quant)),
            f"{size_gb:.1f} GB",
        )

    console.print(tbl)
    console.print()

    # ── Prompt for selection ───────────────────────────────────────────────────
    console.print(
        f"  [dim]Enter a number [bold]1–{len(models)}[/] or the exact model name.  "
        f"Current: [bold cyan]{active}[/][/]"
    )
    console.print()

    while True:
        raw = Prompt.ask(
            f"[{_C_PRIMARY}]Select model[/]",
            default=active,
            console=console,
        )
        raw = raw.strip()

        # Accept numeric index
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(models):
                chosen = models[idx]["name"]
                break
            console.print(
                f"  [{_C_ERROR}]✗[/]  Number out of range (1–{len(models)})."
            )
            continue

        # Accept exact model name
        model_names = [m.get("name", "") for m in models]
        if raw in model_names:
            chosen = raw
            break

        # Accept partial match (e.g. "llama3" matches "llama3:latest")
        partial = [n for n in model_names if n.startswith(raw)]
        if len(partial) == 1:
            chosen = partial[0]
            break
        if len(partial) > 1:
            console.print(
                f"  [{_C_WARN}]Ambiguous — matches:[/] "
                + ", ".join(f"[bold]{n}[/]" for n in partial)
            )
            continue

        console.print(
            f"  [{_C_ERROR}]✗[/]  Model not found: [dim]{escape(raw)}[/]"
        )

    # ── Apply and persist ──────────────────────────────────────────────────────
    set_active_model(chosen)
    cfg["ollama_model"] = chosen
    _save_config(cfg)

    console.print()
    console.print(Panel(
        Align.center(
            f"[{_C_SUCCESS}]✓  Active model set to:[/]\n\n"
            f"[bold cyan]{chosen}[/]"
        ),
        border_style="green",
        padding=(1, 4),
    ))
    console.print()
    Prompt.ask("[dim]Press Enter to continue[/]", default="", console=console)


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

def _flow_settings() -> None:
    _section_header("Settings & Configuration")
    cfg     = _load_config()
    env_p   = _BACKEND_DIR / ".env"

    tbl = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    tbl.add_column("Key",   style="dim cyan",  width=28)
    tbl.add_column("Value", style="bold white")

    tbl.add_row("[bold]Application[/]", "")
    tbl.add_row("  Version",       VERSION)
    tbl.add_row("  Active model",  f"[bold cyan]{get_active_model()}[/]")
    tbl.add_row("  Backend dir",   str(_BACKEND_DIR))
    tbl.add_row("  Log file",      str(LOG_FILE))
    tbl.add_row("  Config file",   str(CONFIG_FILE))

    tbl.add_row("", "")
    tbl.add_row("[bold]Last Run[/]", "")
    tbl.add_row("  Documents folder", cfg.get("doc_folder", "[dim]not set[/]"))
    tbl.add_row("  CSV / Excel file", cfg.get("csv_file",   "[dim]not set[/]"))
    tbl.add_row("  Team name",        cfg.get("team_name",  "[dim]not set[/]"))
    tbl.add_row("  Leader name",      cfg.get("leader_name","[dim]not set[/]"))

    if env_p.exists():
        tbl.add_row("", "")
        tbl.add_row("[bold]API / Environment[/]", "")
        try:
            from dotenv import dotenv_values
            env_vals = dotenv_values(env_p)
            for k, v in sorted(env_vals.items()):
                v = v or ""
                if any(x in k.upper() for x in ("KEY", "PAT", "SECRET", "TOKEN")):
                    v = (v[:6] + "…" + v[-4:]) if len(v) > 10 else "***"
                tbl.add_row(f"  {k}", escape(v))
        except Exception:
            tbl.add_row("  (could not parse .env)", "")

    tbl.add_row("", "")
    tbl.add_row("[bold]Restricted Sections[/]", "[dim](never modified)[/]")
    for sec in sorted(RESTRICTED):
        tbl.add_row(f"  •  {sec.title()}", "")

    console.print(Panel(
        tbl,
        title=f"[{_C_HEADING}]  DOCAI Settings  [/]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()
    Prompt.ask("[dim]Press Enter to return[/]", default="", console=console)


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="docai",
        description=(
            f"DOCAI v{VERSION} — Intelligent Document Update System\n"
            "Automatically updates process-flow Word documents using CSV context data."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python cli.py\n"
            "  python cli.py --docs /path/to/docs --csv data.csv\n"
            "  python cli.py --docs D:\\docs --csv D:\\data.csv "
            '--team "Operations" --leader "Alice"\n'
        ),
    )
    p.add_argument(
        "--docs", metavar="FOLDER",
        help="Path to the folder containing .docx files",
    )
    p.add_argument(
        "--csv", metavar="FILE",
        help="Path to the CSV or Excel update-context file",
    )
    p.add_argument(
        "--team", metavar="NAME", default="",
        help='Team name (default: "DOCAI")',
    )
    p.add_argument(
        "--leader", metavar="NAME", default="",
        help='Leader name (default: "PROJECT")',
    )
    p.add_argument(
        "--no-confirm", action="store_true",
        help="Skip the confirmation prompt and start immediately "
             "(only valid when --docs and --csv are supplied)",
    )
    p.add_argument(
        "--version", action="version",
        version=f"DOCAI {VERSION}",
    )
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = _build_arg_parser()
    args   = parser.parse_args()

    # ── Non-interactive fast-path ─────────────────────────────────────────────
    # If both --docs and --csv are provided, jump straight to processing.
    if args.docs and args.csv:
        _show_banner()
        _flow_select_model(startup=True)
        _flow_process_documents(
            pre_docs=args.docs,
            pre_csv=args.csv,
            pre_team=args.team,
            pre_leader=args.leader,
        )
        return

    # ── Startup: restore or select Ollama model ───────────────────────────────
    _show_banner()
    _flow_select_model(startup=True)

    # ── Interactive main loop ─────────────────────────────────────────────────
    try:
        while True:
            _show_banner()
            _show_main_menu()
            console.print()

            choice = Prompt.ask(
                f"[{_C_PRIMARY}]Select option[/]",
                choices=["1", "2", "3", "4", "5", "q", "Q"],
                default="1",
                show_choices=False,
                console=console,
            )
            console.print()

            if choice == "1":
                _show_banner()
                _flow_process_documents(
                    pre_docs=args.docs   or "",
                    pre_csv=args.csv     or "",
                    pre_team=args.team   or "",
                    pre_leader=args.leader or "",
                )
            elif choice == "2":
                _show_banner()
                _flow_browse_output()
            elif choice == "3":
                _show_banner()
                _flow_view_logs()
            elif choice == "4":
                _show_banner()
                _flow_settings()
            elif choice == "5":
                _show_banner()
                _flow_select_model()
            elif choice.lower() == "q":
                console.print(
                    f"\n  [{_C_PRIMARY}]Goodbye![/]  "
                    "Thank you for using DOCAI.\n"
                )
                break

    except KeyboardInterrupt:
        console.print(f"\n\n  [{_C_WARN}]Interrupted.[/]  Goodbye!\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
