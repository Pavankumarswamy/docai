"""
llm_client.py — Ollama LLM client for DOCAI

All inference goes through a local Ollama instance.
Model is selected interactively at startup via cli.py and stored in
_ACTIVE_MODEL; every call then uses that model unless overridden.

Public surface used by the rest of the system
----------------------------------------------
  list_ollama_models()       → list[dict]   — fetch /api/tags
  set_active_model(name)     → None         — store selected model
  get_active_model()         → str          — current model name
  _call_ollama(messages)     → str          — chat completion
  _strip_markdown(text)      → str          — strip fences from LLM output
"""

import os
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Default model — overridden at runtime by set_active_model()
_ACTIVE_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3")


# ── Model management ──────────────────────────────────────────────────────────

def set_active_model(model_name: str) -> None:
    """Set the Ollama model used for all subsequent LLM calls."""
    global _ACTIVE_MODEL
    _ACTIVE_MODEL = model_name
    logger.info(f"[LLM] Active model → {model_name}")


def get_active_model() -> str:
    """Return the currently selected Ollama model name."""
    return _ACTIVE_MODEL


def list_ollama_models() -> list[dict]:
    """
    Return the list of locally available Ollama models.

    Each dict contains at minimum:
      "name"     : str   — full model tag  (e.g. "llama3:latest")
      "size"     : int   — model size in bytes
      "details"  : dict  — family, parameter_size, quantization_level …

    Raises:
      requests.exceptions.ConnectionError  — if Ollama is not running.
      requests.exceptions.HTTPError        — on non-2xx response.
    """
    resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=6)
    resp.raise_for_status()
    return resp.json().get("models", [])


# ── Core LLM call ─────────────────────────────────────────────────────────────

def _call_ollama(
    messages: list[dict],
    model: Optional[str] = None,
    *,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    """
    Send *messages* to Ollama /api/chat and return the assistant reply.

    Args:
        messages   : OpenAI-style list of {"role": ..., "content": ...}
        model      : Override _ACTIVE_MODEL for this call only.
        temperature: Sampling temperature (lower = more deterministic).
        max_tokens : Maximum tokens in the reply.

    Returns:
        The assistant's text content (stripped).

    Raises:
        requests.exceptions.ConnectionError  — Ollama not running.
        ValueError                           — empty response.
    """
    m = model or _ACTIVE_MODEL
    payload = {
        "model":   m,
        "messages": messages,
        "stream":  False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=180,           # generous — local models can be slow
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise requests.exceptions.ConnectionError(
            f"Cannot reach Ollama at {OLLAMA_BASE_URL}. "
            "Is Ollama running?  (ollama serve)"
        ) from exc

    content = resp.json().get("message", {}).get("content", "").strip()
    if not content:
        raise ValueError(f"Ollama ({m}) returned an empty response.")

    logger.debug(f"[LLM] {m} → {len(content)} chars")
    return content


# ── Markdown stripper ─────────────────────────────────────────────────────────

def _strip_markdown(text: str) -> str:
    """
    Remove markdown code fences that Ollama models sometimes emit despite
    being instructed not to, and strip common leaked header lines.
    """
    text = text.strip()

    # ── Remove ```...``` fences ────────────────────────────────────────────────
    if "```" in text:
        try:
            parts = text.split("```")
            if len(parts) >= 3:
                # content lives between the first and second fence
                inner = parts[1]
                lines = inner.splitlines()
                # strip optional language tag  (e.g.  ```json)
                if lines and lines[0].strip().lower() in (
                    "python", "javascript", "js", "ts", "typescript",
                    "jsx", "tsx", "html", "css", "json", ""
                ):
                    text = "\n".join(lines[1:]).strip()
                else:
                    text = inner.strip()
        except Exception:
            pass

    # ── Remove leaked context-marker lines ────────────────────────────────────
    bad_prefixes = (
        "--- File:",
        "[[[ CONTEXT_FILE:",
        "Full Project Source Code Context:",
        "Here is the",
        "```",
    )
    cleaned = [
        line for line in text.splitlines()
        if not any(line.strip().startswith(p) for p in bad_prefixes)
    ]
    return "\n".join(cleaned).strip()
