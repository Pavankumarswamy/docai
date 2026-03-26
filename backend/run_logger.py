"""
run_logger.py – Structured Internal Logging for DOCAI

Writes to: backend/logs/run.log
NOT exposed to the frontend or output directory.
"""

import logging
import logging.handlers
from pathlib import Path


_configured = False


def configure_run_logger():
    """
    Set up the file handler to backend/logs/run.log.
    Safe to call multiple times (idempotent).
    """
    global _configured
    if _configured:
        return

    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "run.log"

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler: max 5MB, keep 3 backups
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    root_logger = logging.getLogger()
    # Avoid duplicate handlers on reload
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root_logger.handlers):
        root_logger.addHandler(file_handler)

    _configured = True


class RunTracker:
    """
    Lightweight per-document context tracker.
    Logs structured events to run.log with consistent formatting.
    """

    def __init__(self, run_id: str, doc_name: str):
        self.run_id = run_id
        self.doc_name = doc_name
        self.logger = logging.getLogger("docai.run")
        self.sections_detected: int = 0
        self.sections_skipped: int = 0
        self.sections_processed: int = 0
        self.llm_calls: int = 0
        self.errors: list[str] = []

    def section_detected(self, name: str):
        self.sections_detected += 1
        self.logger.debug(f"[{self.run_id}][{self.doc_name}] SECTION DETECTED: {name}")

    def section_skipped(self, name: str, reason: str = "restricted or pre-intro"):
        self.sections_skipped += 1
        self.logger.info(f"[{self.run_id}][{self.doc_name}] SKIP: {name} ({reason})")

    def section_processed(self, name: str, edits_count: int):
        self.sections_processed += 1
        self.logger.info(f"[{self.run_id}][{self.doc_name}] PROCESSED: {name} → {edits_count} edit(s)")

    def llm_call(self, node: str, section: str):
        self.llm_calls += 1
        self.logger.debug(f"[{self.run_id}][{self.doc_name}] LLM CALL: node={node}, section={section}")

    def error(self, context: str, exc: Exception):
        msg = f"[{self.run_id}][{self.doc_name}] ERROR in {context}: {exc}"
        self.errors.append(str(exc))
        self.logger.error(msg)

    def summary(self):
        self.logger.info(
            f"[{self.run_id}][{self.doc_name}] SUMMARY → "
            f"detected={self.sections_detected}, "
            f"skipped={self.sections_skipped}, "
            f"processed={self.sections_processed}, "
            f"llm_calls={self.llm_calls}, "
            f"errors={len(self.errors)}"
        )
