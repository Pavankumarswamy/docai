# DOCAI — Document Intelligence System

<p align="center">
  <img src="https://img.shields.io/badge/Version-3.0.0-gold?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/Interface-Interactive%20Terminal-blueviolet?style=for-the-badge&logo=windowsterminal" alt="Interface">
  <img src="https://img.shields.io/badge/Pipeline-LangGraph%20Multi--Agent-green?style=for-the-badge" alt="Pipeline">
  <img src="https://img.shields.io/badge/LLM-Ollama%20Local-orange?style=for-the-badge" alt="LLM">
  <img src="https://img.shields.io/badge/Safety-Rollback%20%2B%20Dedup-red?style=for-the-badge" alt="Safety">
  <img src="https://img.shields.io/badge/Platform-Windows%20x64-blue?style=for-the-badge&logo=windows" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.11%2B-yellow?style=for-the-badge&logo=python" alt="Python">
</p>

> **Automatically update outdated process-flow Word documents using structured CSV/Excel context data.**
> DOCAI is a production-grade, terminal-driven system powered by **LangGraph** and **Ollama** that parses documents section-by-section, runs a deterministic multi-agent pipeline fully locally, and writes reviewed, change-tracked `.docx` files with complete rollback safety.

---

## What It Does

Given a folder of `.docx` process-flow documents and a CSV/Excel file containing updates (user stories, features, tasks), DOCAI:

1. Lists all available **Ollama models** on startup — select one interactively
2. Parses each document and splits it into logical sections starting from **Introduction**
3. Pre-filters CSV rows to high-quality context before the AI sees anything
4. Runs each section through a **5-stage LangGraph agent pipeline** — fully local
5. Deduplicates edits and applies them with a **5-stage block locator** (exact → fuzzy → keyword-cosine → semantic → insert)
6. Takes a **snapshot before every edit** and rolls back automatically on structure violation
7. Marks every change inline — **blue bold** for inserted text, **red strikethrough** for deleted text
8. Saves a versioned `.docx` to `output/` and generates a styled HTML preview opened in your browser
9. Emits per-document **batch metrics** (applied, skipped, avg confidence, low-confidence rate)

---

## Architecture

### Module Map

```
backend/
├── cli.py                ← Interactive terminal (entry point)
├── agents.py             ← Pipeline orchestrator
│
├── input_layer.py        ← Preprocessor   — reads .docx + CSV/Excel (cached)
├── document_processor.py ← Section Manager — heading/regex section extraction
├── relevance_engine.py   ← Matcher         — keyword scoring + pre_filter_rows()
│
├── multi_agents.py       ← LangGraph graph (5 nodes — see below)
├── planner.py            ← Planner node    — structured JSON edit plan
├── validator.py          ← Validator node  — deterministic rule checks (no LLM)
│
├── scorer.py             ← 5-stage block locator + confidence scoring
├── edit_engine.py        ← Applier         — section-scoped, rollback-safe
├── dedup.py              ← Global deduplication before apply
├── metrics.py            ← Per-document batch metrics
│
├── change_tracker.py     ← Inline change marking (blue / red)
├── output_manager.py     ← Versioned save to output/ only
├── html_exporter.py      ← .docx → styled HTML browser preview
│
├── llm_client.py         ← Ollama client — model list, hot-swap, chat API
├── run_logger.py         ← Rotating log  — backend/logs/run.log
├── state.py              ← Global config (used by llm_client)
├── results_generator.py  ← Run summary dict
└── requirements.txt
run_cli.bat               ← Windows double-click launcher
```

### Full Pipeline

```
CSV rows (all)
  ↓ get_relevant_rows()      top-30 keyword-scored rows
  ↓ pre_filter_rows()        drop closed/empty → top-18 quality rows
  ↓
┌─────────────────────────────────────────────────────────────┐
│  LangGraph: Retriever → Planner → Editor → Validator → Refiner │
│                                                             │
│  Retriever  narrow to 20 most relevant rows                 │
│  Planner    structured JSON plan (what/where/why per edit)  │
│  Editor     generate edits guided strictly by plan          │
│  Validator  7 deterministic rules — no LLM call             │
│  Refiner    ≤5 edits → rule-based  |  >5 edits → LLM        │
└─────────────────────────────────────────────────────────────┘
  ↓
dedup_edits()              cluster by TF cosine, keep highest confidence
  ↓
snapshot_doc()             BytesIO snapshot for rollback
  ↓
apply_edits()              5-stage scoped locator (section blocks only)
  ↓
structure_integrity_check() headings unchanged / paras not reduced >25% / tables intact
  ↓ fail → restore_doc()   automatic rollback, section retained
apply_tracked_changes()    inline blue/red marking
  ↓
save_document()            output/<name>_vYYYY-MM-DD.docx
html_exporter()            browser preview with Copy Path button
```

### 5-Stage Block Locator (`scorer.py`)

| Stage | Method | Threshold | Confidence |
|---|---|---|---|
| 1 | **Exact** substring match | — | 1.0 |
| 2 | **Fuzzy** (SequenceMatcher) | ≥ 0.80 | ratio |
| 3 | **Keyword-cosine** (TF, pure Python) | ≥ 0.72 | cosine |
| 4 | **Semantic** (sentence-transformers, optional) | ≥ 0.75 | cosine |
| 5 | **Safe insert fallback** — never drops an edit | — | 0.3 |

Stage 4 only triggers when stages 1–3 all fail. If `sentence-transformers` is not installed, stage 4 is silently skipped.

### Confidence Gate

| Confidence | Action |
|---|---|
| ≥ 0.85 | Apply silently |
| ≥ 0.50 | Apply + log WARNING |
| < 0.50 | Skip + log WARNING (never silent) |

---

## Quick Start

### 1. Install Ollama

```bash
# Download from https://ollama.com, then:
ollama pull gemma3:1b       # fast, lightweight  (~0.8 GB)
ollama pull qwen3:4b        # better quality     (~2.5 GB)
```

Ollama must be running (`ollama serve`) before launching DOCAI.

### 2. Install Python dependencies

```bat
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**Optional — enable semantic matching (Stage 4):**
```bat
.venv\Scripts\pip install sentence-transformers
```

### 3. Launch

```bat
run_cli.bat                          # double-click launcher (recommended)
```

or from terminal:
```bat
cd backend
.venv\Scripts\python cli.py
.venv\Scripts\python cli.py --docs "D:\SRS Docs" --csv "D:\data.csv"
```

---

## Model Selection

On first launch DOCAI lists every locally installed Ollama model:

```
  #   Model Name     Family   Params    Size
  1   gemma3:1b      gemma3   999.89M   0.8 GB
  2   qwen3:4b       qwen3    4.0B      2.5 GB
```

Type the number or model name and press **Enter**. Re-select anytime via **menu → 5**.

> **Recommendation:** `qwen3:4b` produces higher-quality edits. Use `gemma3:1b` on low-RAM machines.

---

## Terminal Interface

```
  ██████╗  ██████╗  ██████╗  █████╗ ██╗
  ██╔══██╗██╔═══██╗██╔════╝ ██╔══██╗██║
  ██║  ██║██║   ██║██║      ███████║██║
  ██║  ██║██║   ██║██║      ██╔══██║██║
  ██████╔╝╚██████╔╝╚██████╗ ██║  ██║██║
  ╚═════╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝

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

### Live Processing View (5 panels)

| Panel | Content |
|---|---|
| **Header** | Run ID + elapsed timer |
| **Pipeline Status** | Current phase, documents complete count |
| **Live Output** | Colour-coded log: `[SKIP]` / `[section] N edit(s)` / `[ROLLBACK]` / `[METRICS]` |
| **LangGraph Agents** | ✓ done / ► active / ○ waiting per agent |
| **Statistics** | Sections skipped, total edits, files saved, errors |

---

## Inputs

### Word Documents
- Any folder of `.docx` files — tested on 161 documents
- Processing starts at the **Introduction** heading; everything before it is ignored
- Valid heading formats: `Introduction`, `INTRODUCTION`, `1. Introduction`, `1 INTRODUCTION`

### CSV / Excel File

| Column | Role |
|---|---|
| `Title` | Primary relevance match (weight 3.0) |
| `Tags` | Tag matching (weight 2.5) |
| `Description` | Content matching (weight 1.5) |
| `Acceptance Criteria` | Supplementary match (weight 1.0) |
| `Work Item Type` | Score multiplier (User Story ×1.2, Bug ×0.6) |
| `ID`, `State` | Passed to LLM as context |

Rows with `State = Closed/Resolved/Done`, empty Title, or no Description AND no AC are dropped by `pre_filter_rows()` before the planner sees them.

---

## Restricted Sections

Never modified regardless of CSV content:

- Scope of Process Note
- Systems Involved
- List of Stakeholders
- Block Diagram
- Roles and Responsibilities

---

## Safety Guarantees

| Guarantee | Mechanism |
|---|---|
| No cross-section edits | `get_section_blocks()` scopes every locator call to current section only |
| No document corruption | `snapshot_doc()` before every section; `restore_doc()` on structure violation |
| No duplicate content | `dedup_edits()` (exact + TF cosine clustering) + `_safe_insert_guard()` |
| No silent edit drops | Every skipped edit logged with named reason; insert fallback always fires |
| No forbidden language | Validator + `sanitize_new_text()` hard-reject "bug", "issue", "defect", "fix"… |
| No heading rewrites | Validator rule 3 — edit targeting heading text is rejected |

---

## Output

```
<your-docs-folder>/
└── output/
    ├── <DocName>_vYYYY-MM-DD.docx          ← Updated Word document
    └── <DocName>_vYYYY-MM-DD_preview.html  ← Browser preview
```

Original files are never modified. A `.backup/` copy is made before processing.

### Inline change tracking

| Content | Formatting in .docx |
|---|---|
| Inserted / updated | **Bold royal-blue** `#004EA6` + cyan highlight + light-blue shading |
| Deleted / replaced | **Bold dark-red** `#C00000` + strikethrough + pink shading |
| Unchanged | Normal — untouched |

### HTML Preview

- Blue/red spans match the .docx change colours
- Change legend at top of page
- **Copy Output Path** button — one click copies the folder path, shows green "Copied!"

---

## Observability

### Live metrics per document
```
[METRICS] srsdoc.docx | total=8 applied=6 skipped=1 inserted=1 avg_conf=0.87 low_conf_rate=0.125
```

### Internal log — `backend/logs/run.log`

Rotating log (5 MB max, 3 backups). Never written to output folder. View from **menu → 3**.

Logged per run:
- Sections detected / skipped / processed
- LLM calls per agent node
- Block locator stage + confidence per edit
- Dedup removed count
- Rollback events
- Full metrics dict per document

### Content rules

| Never write | Always write instead |
|---|---|
| "bug fixed" | "The system now supports…" |
| "issue resolved" | "The process has been enhanced to…" |
| "defect corrected" | "Validation has been introduced to…" |

---

## Performance

Tested on a 12-section SRS document, 247 CSV rows:

| Metric | `gemma3:1b` | `qwen3:4b` |
|---|---|---|
| Total time | ~1–2 min / doc | ~3–5 min / doc |
| LLM calls saved (rule-based refiner) | ~40% | ~40% |
| Sections processed | 6 of 12 | 6 of 12 |
| RAM usage | ~2 GB | ~5 GB |

---

## Requirements

- Python 3.11+
- Windows 10/11
- [Ollama](https://ollama.com) running locally with at least one model
- `rich`, `python-docx`, `pandas`, `langgraph`, `requests` (see `requirements.txt`)
- `sentence-transformers` — **optional**, enables Stage 4 semantic block matching

---

## Sample Data

```
sample/
├── bug_list.xlsx     ← sample Excel with ID/Title/Description/AC columns
└── docs/
    └── srsdoc.docx   ← 12-section SRS document
```

Run DOCAI → point to `sample/docs` + `sample/bug_list.xlsx` → output appears in `sample/docs/output/`.

---

## License

MIT
