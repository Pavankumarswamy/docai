"""
output_manager.py – Output File Management for DOCAI

Rules:
  - All output goes to output/ folder relative to the doc folder
  - Filename: {stem}_v{YYYY-MM-DD}.docx
  - Never writes to the input directory
  - Never overwrites existing files (appends _2, _3 etc.)
  - Original file is NEVER modified — output is always a new file
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from docx import Document

logger = logging.getLogger(__name__)


def get_output_path(doc_path: Path, doc_folder: Path) -> Path:
    """
    Build the output file path:
      <doc_folder>/output/<stem>_v<YYYY-MM-DD>.docx
    If that file already exists, increments a counter suffix.
    """
    output_dir = doc_folder / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base_name = f"{doc_path.stem}_v{date_str}"
    candidate = output_dir / f"{base_name}.docx"

    # Avoid overwriting — increment suffix
    counter = 2
    while candidate.exists():
        candidate = output_dir / f"{base_name}_{counter}.docx"
        counter += 1

    return candidate


def save_document(doc: Document, output_path: Path) -> Path:
    """
    Save an in-memory Document object to the output path.
    Returns the saved path.
    Raises on failure.
    """
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
        logger.info(f"[OutputManager] Saved: {output_path.name}")
        return output_path
    except Exception as e:
        logger.error(f"[OutputManager] Failed to save {output_path}: {e}")
        raise


def ensure_not_in_input_dir(output_path: Path, doc_path: Path) -> bool:
    """
    Safety check: output must not be inside the same file directory
    in a way that risks overwriting an original.
    """
    return output_path.resolve() != doc_path.resolve()
