# DOCAI — Document Intelligence System

<p align="center">
  <img src="https://img.shields.io/badge/Version-2.0.0-gold?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/Interface-Interactive%20Terminal-blueviolet?style=for-the-badge&logo=windowsterminal" alt="Interface">
  <img src="https://img.shields.io/badge/Pipeline-LangGraph%20Multi--Agent-green?style=for-the-badge" alt="Pipeline">
  <img src="https://img.shields.io/badge/LLM-Ollama%20Local-orange?style=for-the-badge" alt="LLM">
  <img src="https://img.shields.io/badge/Platform-Windows%20x64-blue?style=for-the-badge&logo=windows" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.11%2B-yellow?style=for-the-badge&logo=python" alt="Python">
</p>

> **Automatically update outdated process-flow Word documents using structured CSV/Excel context data.**
> DOCAI is a production-grade, terminal-driven system powered by **LangGraph** and **Ollama** that parses documents section-by-section, runs a four-stage AI pipeline locally, and writes reviewed, change-tracked `.docx` files — then opens a styled browser preview automatically.

---

## What It Does

Given a folder of `.docx` process-flow documents and a CSV/Excel file containing updates (user stories, features, tasks), DOCAI:

1. Lists all available **Ollama models** on startup and asks you to select one
2. Parses each document and splits it into logical sections starting from **Introduction**
3. Skips restricted sections that must never be changed
4. For every editable section, selects the most relevant CSV rows (keyword scoring)
5. Runs the section through a **4-stage LangGraph agent pipeline** — fully local, no cloud API needed
6. Applies the resulting edits with a 3-tier fallback (exact → partial → append)
7. Marks every change inline — **blue bold** for inserted text, **red strikethrough** for deleted text
8. Saves a versioned `.docx` to `output/` and generates a styled HTML preview
9. Opens the preview directly in your browser with a **Copy Output Path** button

---

## Architecture

```
backend/
├── cli.py                ← Interactive terminal interface (entry point)
├── agents.py             ← Pipeline orchestrator — wires all modules together
├── input_layer.py        ← Reads .docx files and CSV/Excel (cached)
├── document_processor.py ← Section extraction with heading + regex detection
├── relevance_engine.py   ← Keyword-based CSV-to-section matching (top 30 rows)
├── multi_agents.py       ← LangGraph graph: Retriever → Editor → Reviewer → Refiner
├── edit_engine.py        ← Applies edits with 3-tier fallback (no silent skips)
├── change_tracker.py     ← Inline change marking (blue inserted / red deleted)
├── output_manager.py     ← Versioned file saving to output/ only
├── html_exporter.py      ← Converts output .docx to styled HTML browser preview
├── llm_client.py         ← Ollama client with model listing + hot-swap
├── run_logger.py         ← Rotating internal log (backend/logs/run.log)
└── requirements.txt
run_cli.bat               ← Windows double-click launcher
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

### 1. Install Ollama

Download and install from [ollama.com](https://ollama.com) then pull a model:

```bash
ollama pull gemma3:1b       # fast, lightweight (~0.8 GB)
ollama pull qwen3:4b        # better quality  (~2.5 GB)
```

Ollama must be running (`ollama serve`) before launching DOCAI.

### 2. Install Python dependencies

```bat
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
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

## Model Selection

On first launch (or from **menu → 5 — Select Ollama Model**), DOCAI lists every model currently installed in Ollama:

```
  #   Model Name     Family   Params    Size
  1   gemma3:1b      gemma3   999.89M   0.8 GB
  2   qwen3:4b       qwen3    4.0B      2.5 GB
```

Type the number or model name and press **Enter**. The selected model is saved to `.docai_cli_config.json` and reused on future runs until you change it.

> **Recommendation:** `qwen3:4b` produces noticeably better edits. Use `gemma3:1b` for speed on low-RAM machines.

---

## Terminal Interface

```
  ██████╗  ██████╗  ██████╗  █████╗ ██╗
  ██╔══██╗██╔═══██╗██╔════╝ ██╔══██╗██║
  ██║  ██║██║   ██║██║      ███████║██║
  ██║  ██║██║   ██║██║      ██╔══██║██║
  ██████╔╝╚██████╔╝╚██████╗ ██║  ██║██║
  ╚═════╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝

  Document Intelligence System  v2.0.0
  ──────────────────────────────────────

  Main Menu
  ┌──────────────────────────────────────────────────────┐
  │  1   Process Documents   Update .docx files via CSV  │
  │  2   Browse Output       View generated output files │
  │  3   View Logs           Inspect the internal run log│
  │  4   Settings            Configuration               │
  │  5   Select Ollama Model Change the active LLM       │
  │  Q   Quit                                            │
  └──────────────────────────────────────────────────────┘
```

### Live Processing View

While a run is active the terminal splits into five live panels:

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
- Any folder containing `.docx` files (~161 files tested)
- Documents must have an **Introduction** section (processing starts there)
- All content before Introduction is treated as front matter and ignored

Valid Introduction heading formats: `Introduction`, `INTRODUCTION`, `1. Introduction`, `1 INTRODUCTION`

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
    ├── <DocName>_v2026-03-26.docx          ← Updated Word document
    └── <DocName>_v2026-03-26_preview.html  ← Browser preview
```

Original files are **never modified**. A `.backup/` copy is made automatically before processing.

### Inline change tracking (in the .docx)

| Content | Formatting |
|---|---|
| Inserted / updated text | **Bold royal-blue** (`#004EA6`) + cyan highlight + light-blue background |
| Deleted / replaced text | **Bold dark-red** (`#C00000`) + strikethrough + pink background |
| Unchanged text | Normal — untouched |

### HTML Browser Preview

After each run DOCAI generates a self-contained HTML file and opens it in your default browser:

- **Change legend** at the top of every page
- Blue highlighted spans for inserted content
- Red strikethrough spans for deleted content
- Left-border accent on any paragraph containing a change
- **"Copy Output Path" button** — copies the full folder path to clipboard, shows green "Copied!" confirmation

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

The log is **never written to the output folder**. View it from **menu → 3 — View Logs** inside the CLI.

---

## Performance

Tested on a 12-section SRS document with 247 CSV rows:

| Metric | `gemma3:1b` | `qwen3:4b` |
|---|---|---|
| Total time | ~1–2 min / doc | ~3–5 min / doc |
| Sections processed | 6 of 12 | 6 of 12 |
| LLM calls | 6 | 6 |
| RAM usage | ~2 GB | ~5 GB |

---

## Requirements

- Python 3.11 or higher
- Windows 10/11 (tested on Windows 11)
- [Ollama](https://ollama.com) running locally with at least one model pulled
- `rich >= 13.7.0` (terminal UI)
- `python-docx`, `pandas`, `langgraph`, `openai` (see `requirements.txt`)

---

## Sample Data

`sample/` contains a ready-to-test example:

```
sample/
├── bug_list.xlsx         ← sample Excel input (CSV columns)
└── docs/
    └── srsdoc.docx       ← sample 12-section SRS document
```

Run DOCAI, point it at `sample/docs` and `sample/bug_list.xlsx`, and see the output in `sample/docs/output/`.

---

## License

MIT
