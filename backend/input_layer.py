"""
input_layer.py – Input Layer for DOCAI Production System

Responsibilities:
- Read Word documents (in-memory, never mutates originals)
- Read and cache CSV / Excel data
- Preprocessing: strip NaN, normalize whitespace
"""

import logging
from pathlib import Path
from functools import lru_cache
from typing import List

import pandas as pd
from docx import Document

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Word Document Reader
# ---------------------------------------------------------------------------

def read_docx(path: str | Path) -> Document:
    """
    Load a .docx file into memory without modifying the original.
    Returns a python-docx Document object.
    """
    doc_path = Path(path)
    if not doc_path.exists():
        raise FileNotFoundError(f"Document not found: {doc_path}")
    doc = Document(str(doc_path))
    logger.debug(f"[InputLayer] Loaded document: {doc_path.name}")
    return doc


def list_docx_files(folder: str | Path) -> List[Path]:
    """
    Recursively find all .docx files in a folder.
    Excludes hidden directories and Office temp files (~$).
    """
    folder_path = Path(folder)
    files = [
        f for f in folder_path.rglob("*.docx")
        if not f.name.startswith("~$")
        and not any(part.startswith(".") for part in f.parts)
    ]
    logger.info(f"[InputLayer] Found {len(files)} .docx file(s) in {folder_path.name}")
    return sorted(files)


# ---------------------------------------------------------------------------
# CSV / Excel Reader (cached)
# ---------------------------------------------------------------------------

_csv_cache: dict[str, List[dict]] = {}


def read_csv(path: str | Path) -> List[dict]:
    """
    Read a CSV or Excel file and return rows as list of dicts.
    Results are cached by absolute path — reading once per run.
    Preprocessing:
      - Drop fully-empty rows
      - Fill NaN with empty string
      - Strip whitespace from all string values
    """
    abs_path = str(Path(path).resolve())

    if abs_path in _csv_cache:
        logger.debug(f"[InputLayer] Cache hit for: {Path(path).name}")
        return _csv_cache[abs_path]

    try:
        suffix = Path(path).suffix.lower()
        if suffix in (".xlsx", ".xls"):
            df = pd.read_excel(path)
        elif suffix == ".csv":
            df = pd.read_csv(path, encoding="utf-8", errors="replace")
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

        df = df.dropna(how="all")
        df = df.fillna("")

        # Normalize all string fields
        for col in df.select_dtypes(include=["object"]).columns:
            df[col] = df[col].astype(str).str.strip()

        rows = df.to_dict("records")
        _csv_cache[abs_path] = rows
        logger.info(f"[InputLayer] Loaded {len(rows)} rows from {Path(path).name}")
        return rows

    except Exception as e:
        logger.error(f"[InputLayer] Failed to read {path}: {e}")
        return []


def clear_cache():
    """Clear the CSV cache (for testing or multi-run scenarios)."""
    _csv_cache.clear()
