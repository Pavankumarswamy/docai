"""
html_exporter.py — DOCAI HTML Preview Generator
=================================================

Converts a processed .docx file (with inline change-tracking runs applied by
change_tracker.py) into a self-contained, styled HTML file that can be opened
in any browser.

Visual conventions
------------------
  Unchanged text  → normal black text, white background
  Inserted text   → bold blue text, light-blue background  (#D6EEFF / #004EA6)
  Deleted text    → strikethrough red text, pink background (#FFE0E0 / #C00000)

Run detection heuristics (must match change_tracker.py constants):
  Deleted  → run.font.strike == True  AND  red-ish colour
  Inserted → run.font.bold  == True   AND  blue-ish colour  (without strike)

A change legend is rendered at the top of every exported HTML file.
"""

from __future__ import annotations

import html as _html_mod
import logging
import os
import tempfile
import webbrowser
from pathlib import Path
from typing import List, Optional, Tuple

from docx import Document
from docx.document import Document as _Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.shared import RGBColor
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)

# ─── colour thresholds for run classification ─────────────────────────────────
# A run is "deleted"  if it has strike and its red channel dominates
# A run is "inserted" if it has bold  and its blue channel dominates
_RED_THRESHOLD  = 150   # R channel ≥ this and B channel < 100 → deleted
_BLUE_THRESHOLD = 100   # B channel ≥ this and R channel < 100 → inserted


# ─── CSS template ─────────────────────────────────────────────────────────────

_CSS = """
/* ── Reset & base ─────────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'Calibri', 'Segoe UI', Arial, sans-serif;
  font-size: 11pt;
  background: #EAECEF;
  color: #1A1A1A;
  padding: 40px 20px;
  line-height: 1.6;
}

/* ── Document shell ────────────────────────────────────────────────────── */
.doc-wrapper {
  max-width: 900px;
  margin: 0 auto;
}

.doc-topbar {
  background: #1A2B45;
  color: #FFFFFF;
  border-radius: 8px 8px 0 0;
  padding: 16px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.doc-topbar .title { font-size: 14pt; font-weight: 700; letter-spacing: 0.03em; }
.doc-topbar .meta  { font-size: 9pt;  color: #9FB3C8; }

.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  background: #2C4A72;
  color: #C5D8F0;
  border: 1.5px solid #4A7AAF;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 9pt;
  font-family: 'Segoe UI', Arial, sans-serif;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  white-space: nowrap;
  user-select: none;
}
.copy-btn:hover  { background: #3A6499; color: #FFFFFF; border-color: #7AAEDD; }
.copy-btn.copied { background: #1A5C2E; color: #8DFFC0; border-color: #3DBA7A; }
.copy-btn svg    { flex-shrink: 0; }

.doc-legend {
  background: #F0F4FF;
  border: 1px solid #C5D5F0;
  border-top: none;
  padding: 12px 28px;
  display: flex;
  gap: 32px;
  flex-wrap: wrap;
  align-items: center;
  font-size: 9.5pt;
}

.legend-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.badge-unchanged {
  background: #FFFFFF;
  color: #333;
  border: 1.5px solid #CCC;
  padding: 2px 10px;
  border-radius: 4px;
}

.badge-inserted {
  background: #D6EEFF;
  color: #004EA6;
  border: 1.5px solid #80BFFF;
  padding: 2px 10px;
  border-radius: 4px;
  font-weight: 700;
}

.badge-deleted {
  background: #FFE0E0;
  color: #C00000;
  border: 1.5px solid #FF9999;
  padding: 2px 10px;
  border-radius: 4px;
  text-decoration: line-through;
  font-weight: 700;
}

.doc-body {
  background: #FFFFFF;
  border: 1px solid #D0D7E3;
  border-top: none;
  border-radius: 0 0 8px 8px;
  padding: 56px 72px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.08);
  min-height: 400px;
}

/* ── Heading styles ────────────────────────────────────────────────────── */
h1, h2, h3, h4, h5 {
  color: #1A2B45;
  margin-top: 1.4em;
  margin-bottom: 0.4em;
  line-height: 1.3;
}
h1 { font-size: 18pt; border-bottom: 2px solid #C5D5F0; padding-bottom: 6px; }
h2 { font-size: 14pt; border-left: 4px solid #004EA6; padding-left: 10px; }
h3 { font-size: 12pt; color: #2D4A7A; }
h4, h5 { font-size: 11pt; color: #3D5A8A; }

/* ── Body content ──────────────────────────────────────────────────────── */
p {
  margin-bottom: 0.55em;
}

ul, ol {
  margin: 0.4em 0 0.4em 2em;
}

li { margin-bottom: 0.25em; }

/* ── Table styles ──────────────────────────────────────────────────────── */
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
  font-size: 10pt;
}
th, td {
  border: 1px solid #C5D5F0;
  padding: 7px 12px;
  vertical-align: top;
}
th {
  background: #E8EFF8;
  font-weight: 700;
  color: #1A2B45;
}
tr:nth-child(even) td { background: #F7FAFF; }

/* ── Change tracking runs ──────────────────────────────────────────────── */
.ins {
  background-color: #D6EEFF;
  color: #004EA6;
  font-weight: 700;
  border-radius: 3px;
  padding: 0 3px;
  border-bottom: 2px solid #80BFFF;
}

.del {
  background-color: #FFE0E0;
  color: #C00000;
  text-decoration: line-through;
  font-weight: 700;
  border-radius: 3px;
  padding: 0 3px;
}

/* ── Changed paragraph highlight ──────────────────────────────────────── */
.para-changed {
  border-left: 4px solid #004EA6;
  padding-left: 10px;
  background: #F5FAFF;
  border-radius: 0 4px 4px 0;
}

.para-deleted-ctx {
  border-left: 4px solid #C00000;
  padding-left: 10px;
  background: #FFF8F8;
  border-radius: 0 4px 4px 0;
}

/* ── Footer ────────────────────────────────────────────────────────────── */
.doc-footer {
  text-align: center;
  color: #9FB3C8;
  font-size: 8.5pt;
  margin-top: 20px;
  padding: 10px;
}
"""

# ─── HTML page template ───────────────────────────────────────────────────────

_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DOCAI Preview — {doc_name}</title>
  <style>
{css}
  </style>
</head>
<body>
  <div class="doc-wrapper">

    <div class="doc-topbar">
      <span class="title">&#128196; {doc_name}</span>
      <span class="meta">Generated by DOCAI &nbsp;|&nbsp; {generated_at}</span>

      <!-- ── Copy output folder path button ──────────────────────────── -->
      <button
        class="copy-btn"
        id="copyPathBtn"
        title="Copy the updated document folder path to clipboard"
        onclick="copyFolderPath()"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round" stroke-linejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
        </svg>
        <span id="copyBtnLabel">Copy Output Path</span>
      </button>
    </div>

    <div class="doc-legend">
      <strong>Change Legend:</strong>
      <span class="legend-badge"><span class="badge-unchanged">Unchanged text</span> — original document content</span>
      <span class="legend-badge"><span class="badge-inserted">Inserted / Updated</span> — new or revised content</span>
      <span class="legend-badge"><span class="badge-deleted">Deleted / Replaced</span> — content that was removed</span>
    </div>

    <div class="doc-body">
{body}
    </div>

  </div>
  <div class="doc-footer">
    DOCAI Document Intelligence System &nbsp;&bull;&nbsp; Review copy only
  </div>

  <script>
    // The output folder path — written at export time by html_exporter.py
    var OUTPUT_FOLDER_PATH = "{output_folder}";

    function copyFolderPath() {{
      var btn   = document.getElementById("copyPathBtn");
      var label = document.getElementById("copyBtnLabel");

      navigator.clipboard.writeText(OUTPUT_FOLDER_PATH).then(function() {{
        btn.classList.add("copied");
        label.textContent = "Copied!";
        setTimeout(function() {{
          btn.classList.remove("copied");
          label.textContent = "Copy Output Path";
        }}, 2000);
      }}).catch(function() {{
        // Fallback for browsers that block clipboard without HTTPS
        var ta = document.createElement("textarea");
        ta.value = OUTPUT_FOLDER_PATH;
        ta.style.position = "fixed";
        ta.style.opacity  = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        btn.classList.add("copied");
        label.textContent = "Copied!";
        setTimeout(function() {{
          btn.classList.remove("copied");
          label.textContent = "Copy Output Path";
        }}, 2000);
      }});
    }}
  </script>
</body>
</html>
"""


# ─── Run classification ───────────────────────────────────────────────────────

def _classify_run(run) -> str:
    """
    Return 'inserted', 'deleted', or 'normal' based on the run's font formatting.

    Detection is based on markers applied by change_tracker.py:
      deleted  → strike=True  + red-dominant colour
      inserted → bold=True    + blue-dominant colour   (no strike)
    """
    try:
        colour = run.font.color.rgb   # may raise if None
    except Exception:
        colour = None

    has_strike = bool(run.font.strike)
    has_bold   = bool(run.font.bold)

    if colour is not None:
        r, g, b = colour[0], colour[1], colour[2]
        if has_strike and r >= _RED_THRESHOLD and b < 100:
            return "deleted"
        if has_bold and b >= _BLUE_THRESHOLD and r < 100:
            return "inserted"

    # Fallback: use strike alone
    if has_strike:
        return "deleted"

    return "normal"


# ─── Paragraph → HTML ─────────────────────────────────────────────────────────

_HEADING_STYLES = {
    "heading 1": "h1",
    "heading 2": "h2",
    "heading 3": "h3",
    "heading 4": "h4",
    "heading 5": "h5",
    "title":     "h1",
}

_LIST_STYLES = {"list paragraph", "list bullet", "list number"}


def _para_to_html(para: Paragraph) -> str:
    """Convert a single paragraph to an HTML string."""
    style_name = (para.style.name or "").lower()
    tag = _HEADING_STYLES.get(style_name, "p")

    # Build inner HTML from runs
    inner_parts: list[str] = []
    has_change = False

    for run in para.runs:
        text = _html_mod.escape(run.text or "")
        if not text:
            continue

        kind = _classify_run(run)
        if kind == "inserted":
            inner_parts.append(f'<span class="ins">{text}</span>')
            has_change = True
        elif kind == "deleted":
            inner_parts.append(f'<span class="del">{text}</span>')
            has_change = True
        else:
            # Apply any existing bold/italic that isn't a change-tracking marker
            if run.bold:
                text = f"<strong>{text}</strong>"
            if run.italic:
                text = f"<em>{text}</em>"
            inner_parts.append(text)

    if not inner_parts:
        return ""   # skip blank paragraphs

    inner = "".join(inner_parts)

    # Wrap lists
    if style_name in _LIST_STYLES:
        return f"<li>{inner}</li>\n"

    # Add left-border highlight to paragraphs that contain changes
    if has_change:
        # Determine dominant change type for border colour
        has_ins = any('<span class="ins">' in p for p in inner_parts)
        css_cls = "para-changed" if has_ins else "para-deleted-ctx"
        return f'<{tag} class="{css_cls}">{inner}</{tag}>\n'

    return f"<{tag}>{inner}</{tag}>\n"


# ─── Table → HTML ─────────────────────────────────────────────────────────────

def _cell_to_html(cell: _Cell) -> str:
    parts = []
    for para in cell.paragraphs:
        for run in para.runs:
            text = _html_mod.escape(run.text or "")
            if not text:
                continue
            kind = _classify_run(run)
            if kind == "inserted":
                parts.append(f'<span class="ins">{text}</span>')
            elif kind == "deleted":
                parts.append(f'<span class="del">{text}</span>')
            else:
                parts.append(text)
    return "".join(parts)


def _table_to_html(table: Table) -> str:
    rows_html: list[str] = []
    for row_idx, row in enumerate(table.rows):
        cells_html = []
        for cell in row.cells:
            inner = _cell_to_html(cell)
            tag = "th" if row_idx == 0 else "td"
            cells_html.append(f"    <{tag}>{inner}</{tag}>")
        rows_html.append("  <tr>\n" + "\n".join(cells_html) + "\n  </tr>")
    return "<table>\n" + "\n".join(rows_html) + "\n</table>\n"


# ─── Document → HTML body ─────────────────────────────────────────────────────

def _docx_to_html_body(doc: Document) -> str:
    """Walk the document body and convert to HTML."""
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    parts: list[str] = []
    in_list = False

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            para = Paragraph(child, doc)
            style_name = (para.style.name or "").lower()

            is_list = style_name in _LIST_STYLES
            if is_list and not in_list:
                parts.append("<ul>\n")
                in_list = True
            elif not is_list and in_list:
                parts.append("</ul>\n")
                in_list = False

            html_line = _para_to_html(para)
            if html_line:
                parts.append(html_line)

        elif isinstance(child, CT_Tbl):
            if in_list:
                parts.append("</ul>\n")
                in_list = False
            tbl = Table(child, doc)
            parts.append(_table_to_html(tbl))

    if in_list:
        parts.append("</ul>\n")

    return "".join(parts)


# ─── Public API ───────────────────────────────────────────────────────────────

def docx_to_html_preview(docx_path: Path | str, output_html: Optional[Path] = None) -> Path:
    """
    Convert a processed .docx file to a self-contained HTML file.

    Parameters
    ----------
    docx_path   : Path to the .docx file (with change-tracking runs).
    output_html : Where to write the HTML file.  Defaults to same folder as
                  the docx, replacing the .docx extension with _preview.html.

    Returns
    -------
    Path to the written HTML file.
    """
    from datetime import datetime, timezone

    docx_path = Path(docx_path)
    if not docx_path.exists():
        raise FileNotFoundError(f"docx not found: {docx_path}")

    if output_html is None:
        output_html = docx_path.parent / (docx_path.stem + "_preview.html")

    doc       = Document(str(docx_path))
    body_html = _docx_to_html_body(doc)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # The folder path copied when the user clicks "Copy Output Path"
    output_folder_path = str(docx_path.parent.resolve()).replace("\\", "\\\\")

    page = _PAGE_TEMPLATE.format(
        doc_name      = _html_mod.escape(docx_path.name),
        css           = _CSS,
        generated_at  = generated,
        output_folder = output_folder_path,
        body          = body_html,
    )

    output_html = Path(output_html)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(page, encoding="utf-8")
    logger.info(f"[HTMLExporter] Written: {output_html}")
    return output_html


def open_in_browser(html_path: Path | str) -> None:
    """
    Open an HTML file in the system default browser (Chrome on most Windows
    setups).  Falls back gracefully if the browser cannot be launched.
    """
    html_path = Path(html_path).resolve()
    url = html_path.as_uri()          # file:///C:/...
    try:
        webbrowser.open(url, new=2)   # new=2 → new tab if browser already open
        logger.info(f"[HTMLExporter] Opened in browser: {url}")
    except Exception as exc:
        logger.warning(f"[HTMLExporter] Could not open browser: {exc}")
