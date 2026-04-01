"""
metrics.py – Per-document batch metrics for DOCAI

Accumulates edit outcomes during a single document run and emits a
structured summary log line + dict at the end.

Usage:
    m = DocMetrics()
    m.record(status="replaced",  confidence=0.95)
    m.record(status="inserted_fallback", confidence=0.3)
    m.record(status="skipped_low_confidence", confidence=0.2)
    m.log("srsdoc.docx")
    d = m.to_dict()
"""

import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

# Statuses that count as "applied" (content was written)
_APPLIED_STATUSES = {"replaced", "inserted", "appended", "overridden",
                     "inserted_fallback", "replaced_coerced"}
# Statuses that count as "inserted new content" (fallback path)
_INSERT_STATUSES  = {"inserted", "inserted_fallback"}
# Statuses that count as "skipped" (edit was dropped)
_SKIP_STATUSES    = {"skipped_low_confidence", "skipped_empty",
                     "skipped_oob", "skipped_type_mismatch",
                     "skipped_duplicate_insert", "no_match"}


@dataclass
class DocMetrics:
    """Accumulates and reports edit-level metrics for one document."""
    confidences:    List[float] = field(default_factory=list)
    status_counts:  dict        = field(default_factory=dict)

    # ── Record one edit outcome ───────────────────────────────────────────
    def record(self, status: str, confidence: float) -> None:
        self.confidences.append(max(0.0, min(1.0, float(confidence))))
        self.status_counts[status] = self.status_counts.get(status, 0) + 1

    # ── Derived counts ────────────────────────────────────────────────────
    @property
    def total(self) -> int:
        return sum(self.status_counts.values())

    @property
    def applied(self) -> int:
        return sum(v for k, v in self.status_counts.items() if k in _APPLIED_STATUSES)

    @property
    def skipped(self) -> int:
        return sum(v for k, v in self.status_counts.items() if k in _SKIP_STATUSES)

    @property
    def inserted(self) -> int:
        return sum(v for k, v in self.status_counts.items() if k in _INSERT_STATUSES)

    @property
    def avg_confidence(self) -> float:
        if not self.confidences:
            return 0.0
        return round(sum(self.confidences) / len(self.confidences), 3)

    @property
    def low_confidence_rate(self) -> float:
        """Fraction of edits whose confidence was below CONF_WARN (0.50)."""
        if not self.confidences:
            return 0.0
        low = sum(1 for c in self.confidences if c < 0.50)
        return round(low / len(self.confidences), 3)

    # ── Emit structured log line ──────────────────────────────────────────
    def log(self, doc_name: str) -> None:
        logger.info(
            f"[METRICS] {doc_name} | "
            f"total={self.total} applied={self.applied} "
            f"skipped={self.skipped} inserted={self.inserted} "
            f"avg_conf={self.avg_confidence} "
            f"low_conf_rate={self.low_confidence_rate}"
        )
        if self.status_counts:
            logger.debug(f"[METRICS] status breakdown: {self.status_counts}")

    # ── Return as plain dict (for JSON export) ────────────────────────────
    def to_dict(self) -> dict:
        return {
            "total_edits":        self.total,
            "applied":            self.applied,
            "skipped":            self.skipped,
            "inserted":           self.inserted,
            "avg_confidence":     self.avg_confidence,
            "low_confidence_rate": self.low_confidence_rate,
            "status_breakdown":   dict(self.status_counts),
        }
