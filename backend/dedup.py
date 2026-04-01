"""
dedup.py – Edit List Deduplication for DOCAI

Problem: multiple plan items may generate edits whose new_text is near-identical,
causing the same content to be written twice to the same section.

Algorithm
---------
1. Compute pairwise TF cosine between all new_text values.
2. Build clusters: two edits are in the same cluster if
   cosine(new_text_i, new_text_j) >= CLUSTER_THRESHOLD (0.80).
3. Within each cluster keep the edit with the highest confidence.
   Ties broken by edit order (first one wins).
4. Return the surviving edits in original order.

Also removes:
- Edits whose new_text is empty after strip.
- Exact duplicates (same original_text AND same new_text).

All logic is deterministic (no LLM).
"""

import logging
from typing import List

from scorer import keyword_cosine

logger = logging.getLogger(__name__)

CLUSTER_THRESHOLD = 0.80


def dedup_edits(edits: List[dict]) -> List[dict]:
    """
    Remove duplicate / redundant edits before they are applied.

    Args:
        edits: List of edit dicts (from Refiner output).

    Returns:
        Deduplicated list in original order.
    """
    if len(edits) <= 1:
        return edits

    # ── Pass 1: remove empty / exact duplicates ───────────────────────────
    seen_exact: set = set()
    clean: List[dict] = []
    for e in edits:
        new_text  = str(e.get("new_text",      "")).strip()
        orig_text = str(e.get("original_text", "")).strip()
        if not new_text:
            logger.debug("[Dedup] Dropped — empty new_text.")
            continue
        key = (orig_text.lower()[:100], new_text.lower()[:100])
        if key in seen_exact:
            logger.debug(f"[Dedup] Dropped exact duplicate: '{new_text[:60]}'")
            continue
        seen_exact.add(key)
        clean.append(e)

    if len(clean) <= 1:
        return clean

    # ── Pass 2: cluster by new_text cosine similarity ─────────────────────
    n          = len(clean)
    cluster_id = list(range(n))   # each edit starts in its own cluster

    def find(i):
        while cluster_id[i] != i:
            cluster_id[i] = cluster_id[cluster_id[i]]
            i = cluster_id[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            cluster_id[ri] = rj

    # Only compute upper triangle — O(n²) but n is at most ~30
    for i in range(n):
        for j in range(i + 1, n):
            a = str(clean[i].get("new_text", ""))
            b = str(clean[j].get("new_text", ""))
            if keyword_cosine(a, b) >= CLUSTER_THRESHOLD:
                union(i, j)

    # ── Group by cluster root ─────────────────────────────────────────────
    clusters: dict = {}
    for i, edit in enumerate(clean):
        root = find(i)
        clusters.setdefault(root, []).append((i, edit))

    # ── Keep highest confidence per cluster ───────────────────────────────
    survivors: List[tuple] = []   # (original_index, edit)
    for root, members in clusters.items():
        best_idx, best_edit = max(
            members,
            key=lambda x: float(x[1].get("confidence", 0.5)),
        )
        survivors.append((best_idx, best_edit))
        if len(members) > 1:
            dropped = [str(e.get("new_text", ""))[:50] for i, e in members if i != best_idx]
            logger.info(f"[Dedup] Cluster of {len(members)}: kept conf={best_edit.get('confidence',0):.2f} "
                        f"dropped={dropped}")

    # ── Restore original order ────────────────────────────────────────────
    survivors.sort(key=lambda x: x[0])
    result = [e for _, e in survivors]

    removed = len(edits) - len(result)
    if removed:
        logger.info(f"[Dedup] {len(edits)} edits → {len(result)} after dedup ({removed} removed).")

    return result
