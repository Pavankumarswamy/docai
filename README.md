# DOCAI — Document Intelligence System

<p align="center">
  <img src="https://img.shields.io/badge/Version-2.0.0-gold?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/Interface-Interactive%20Terminal-blueviolet?style=for-the-badge&logo=windowsterminal" alt="Interface">
  <img src="https://img.shields.io/badge/Pipeline-LangGraph%20Multi--Agent-green?style=for-the-badge" alt="Pipeline">
  <img src="https://img.shields.io/badge/Platform-Windows%20x64-blue?style=for-the-badge&logo=windows" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.11%2B-yellow?style=for-the-badge&logo=python" alt="Python">
</p>

> **Automatically update outdated process-flow Word documents using structured CSV context data.**
> DOCAI is a production-grade, terminal-driven system powered by **LangGraph** that parses documents section-by-section, runs a four-stage AI pipeline, and writes reviewed, change-tracked `.docx` files — then opens a styled browser preview with one click.

---

## What It Does

Given a folder of `.docx` process-flow documents and a CSV/Excel file containing updates (user stories, features, tasks), DOCAI:

1. Parses each document and splits it into logical sections starting from **Introduction**
2. Skips restricted sections that must never be changed
3. For every editable section, selects the most relevant CSV rows (keyword scoring)
4. Runs the section through a **4-stage LangGraph agent pipeline**
5. Applies the resulting edits with a 3-tier fallback (exact → partial → append)
6. Marks every change inline — **blue bold** for inserted text, **red strikethrough** for deleted text
7. Saves a versioned `.docx` to `output/` and generates a styled HTML preview
8. Opens the preview directly in your browser with a **Copy Output Path** button

---

## Architecture

```
backend/
├── cli.py              ← Interactive terminal interface (entry point)
├── agents.py           ← Pipeline orchestrator — wires all 9 modules together
├── input_layer.py      ← Reads .docx files and CSV/Excel (cached)
├── document_processor.py ← Section extraction with heading + regex detection
├── relevance_engine.py ← Keyword-based CSV-to-section matching (top 30 rows)
├── multi_agents.py     ← LangGraph graph: Retriever → Editor → Reviewer → Refiner
├── edit_engine.py      ← Applies edits with 3-tier fallback (no silent skips)
├── change_tracker.py   ← Inline change marking (blue inserted / red deleted)
├── output_manager.py   ← Versioned file saving to output/ only
├── html_exporter.py    ← Converts output .docx to styled HTML browser preview
├── run_logger.py       ← Rotating internal log (backend/logs/run.log)
└── requirements.txt
run_cli.bat             ← Windows double-click launcher
```

### LangGraph Agent Pipeline

```
Retriever ──► Editor ──► Reviewer ──► Refiner ──► END
```

| Agent | Responsibility |
|---|---|
| **Retriever** | Narrows the pre-filtered CSV rows to the 20 most relevant for the section |
| **Editor** | Pass 1 — generates structured JSON edits from section content + context |
| **Reviewer** | Enforces language rules (no "bug fixed"), removes heading edits, validates structure |
| **Refiner** | Pass 2 — improves clarity, removes duplication, produces final edit list |

Linear flow only — no loops, minimal latency.

---

## Quick Start

### 1. Install dependencies

```bat
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 2. Configure API key

Edit `backend/.env`:

```env
NVIDIA_API_KEY=nvapi-...
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=mistralai/mixtral-8x22b-instruct-v0.1
```

### 3. Launch

**Option A — double-click launcher (recommended)**
```
run_cli.bat
```

**Option B — terminal**
```bat
cd backend
.venv\Scripts\python cli.py
```

**Option C — pre-fill paths (skips folder/file prompts)**
```bat
.venv\Scripts\python cli.py --docs "D:\SRS Docs" --csv "D:\data (10).csv"
```

---

## Terminal Interface

```
  ██████╗  ██████╗  ██████╗  █████╗ ██╗
  ██╔══██╗██╔═══██╗██╔════╝ ██╔══██╗██║
  ██║  ██║██║   ██║██║      ███████║██║
  ██║  ██║██║   ██║██║      ██╔══██║██║
  ██████╔╝╚██████╔╝╚██████╗ ██║  ██║██║
  ╚═════╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝

  Document Intelligence System  v2.0.0  ─  Intelligent Process Flow Updater
  ──────────────────────────────────────────────────────────────────────────

  Main Menu
  ┌─────────────────────────────────────────────────────────┐
  │  1   Process Documents   Update .docx files using CSV   │
  │  2   Browse Output       View generated output files    │
  │  3   View Logs           Inspect the internal run log   │
  │  4   Settings            Configuration and API keys     │
  │  Q   Quit                                               │
  └─────────────────────────────────────────────────────────┘
```

### Live Processing View

While a run is active, the terminal splits into five live panels:

| Panel | Content |
|---|---|
| **Header** | Run ID + elapsed timer |
| **Pipeline Status** | Current phase and documents complete count |
| **Live Output** | Colour-coded pipeline log (skip / edit / save / error) |
| **LangGraph Agents** | ✓ done / ► active / ○ waiting per agent |
| **Statistics** | Sections skipped, total edits, files saved, errors |

---

## Inputs

### Word Documents
- Any folder containing `.docx` files
- Documents must have an **Introduction** section (processing starts there)
- All content before Introduction is treated as front matter and ignored

### CSV / Excel File
Expected columns:

| Column | Used for |
|---|---|
| `Title` | Primary relevance matching (weight 3.0) |
| `Tags` | Tag-based matching (weight 2.5) |
| `Description` | Content matching (weight 1.5) |
| `Acceptance Criteria` | Supplementary matching (weight 1.0) |
| `Work Item Type` | Score multiplier (User Story ×1.2, Bug ×0.6) |
| `ID`, `State` | Included in LLM context |

---

## Restricted Sections

The following sections are **never modified**, regardless of what the CSV contains:

- Scope of Process Note
- Systems Involved
- List of Stakeholders
- Block Diagram
- Roles and Responsibilities

---

## Output

### File location
```
<your-docs-folder>/
└── output/
    ├── <DocName>_v2026-03-26.docx     ← Updated Word document
    └── previews/
        └── <DocName>_v2026-03-26_preview.html  ← Browser preview
```

Original files are **never modified**. A `.backup/` copy is made before processing.

### Inline change tracking (in the .docx)

| Content | Formatting |
|---|---|
| Inserted / updated text | **Bold royal-blue** (`#004EA6`) + cyan highlight + light-blue background |
| Deleted / replaced text | **Bold dark-red** (`#C00000`) + strikethrough + pink background |
| Unchanged text | Normal — untouched |

### HTML Browser Preview

After each run, DOCAI generates a self-contained HTML file and opens it in your default browser:

- **Change legend** at the top of every page
- Blue highlighted spans for inserted content
- Red strikethrough spans for deleted content
- Left-border accent on any paragraph containing a change
- **"Copy Output Path" button** in the top bar — copies the full folder path to your clipboard with one click, then shows a green "Copied!" confirmation

---

## Content Rules

All generated content follows strict business-language rules:

| Never write | Always write instead |
|---|---|
| "bug fixed" | "The system now supports…" |
| "issue resolved" | "The process has been enhanced to…" |
| "defect corrected" | "Validation has been introduced to…" |

Structure is always preserved: paragraphs stay paragraphs, lists stay lists, tables stay tables. Sections are merged intelligently — never overwritten wholesale.

---

## Logging

Internal log at `backend/logs/run.log` (rotating, max 5 MB, 3 backups).

Tracked per run:
- Sections detected / skipped / processed
- LLM calls per agent
- Edit application status (exact / partial / appended)
- Errors

The log is **never written to the output folder**. View it from menu option **3 — View Logs** inside the CLI.

---

## Performance

Tested on a 12-section SRS document with 247 CSV rows:

| Metric | Result |
|---|---|
| Total time | ~2–3 min per document |
| Sections processed | 6 of 12 (6 skipped — no relevant data or restricted) |
| LLM calls | 6 (one pipeline run per section) |
| Model | NVIDIA Mixtral-8x22B-Instruct |

---

## Requirements

- Python 3.11 or higher
- Windows (tested on Windows 11)
- NVIDIA API key **or** a local Ollama instance (`llama3`)
- `rich >= 13.7.0` (terminal UI)
- `python-docx`, `pandas`, `langgraph`, `openai` (see `requirements.txt`)

---

## License

MIT
