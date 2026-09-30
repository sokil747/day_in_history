"""Gemma (Ollama) translation client + context manager for tests."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

_DEFAULT_BASE_URL = "http://127.0.0.1:11435"
_DEFAULT_MODEL = "gemma3"
_TIMEOUT_S = 120
_MAX_ATTEMPTS = 3
_RETRY_PAUSE_S = 2.0


class OllamaUnavailable(RuntimeError):
    """Ollama temporarily can't serve the model (reloading, restart, 404/5xx)."""

_system_prompt = (
    "You are a translator. Translate the user's Ukrainian text into English. "
    "Keep emojis, HTML entities (&lt; &gt; &amp; &quot;), hashtags and proper nouns intact. "
    "Never add hashtags, comments or any text that is not in the source. "
    "Reply with the translation only — no explanations, no quotes, no extra text."
)


def _call_ollama(text: str, model: str, base_url: str) -> str:
    payload = json.dumps(
        {
            "model": model,
            "system": _system_prompt,
            "prompt": text,
            "stream": False,
            "options": {"temperature": 0.2},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("response", "").strip()


def strip_hashtags(text: str) -> str:
    """Remove #hashtags (LLM tends to append them) and tidy leftover spaces."""
    text = re.sub(r"#[A-Za-z0-9_]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,!?;:])", r"\1", text)
    return text.strip()


def translate(text: str, model: str | None = None, base_url: str | None = None) -> str:
    model = model or os.environ.get("GEMMA_MODEL", _DEFAULT_MODEL)
    base_url = base_url or os.environ.get("GEMMA_BASE_URL", _DEFAULT_BASE_URL)

    pause = _RETRY_PAUSE_S
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return strip_hashtags(_call_ollama(text, model, base_url))
        except OllamaUnavailable:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < _MAX_ATTEMPTS:
                time.sleep(pause * attempt)
    raise last_exc  # type: ignore[misc]


@contextmanager
def override_translator(fn):
    """Swap translator used by translate(); used in tests."""
    global translate
    original = translate
    translate = fn
    try:
        yield
    finally:
        translate = original