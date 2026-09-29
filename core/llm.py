"""Gemma (Ollama) translation client + context manager for tests."""
from __future__ import annotations

import json
import os
import urllib.request
from contextlib import contextmanager

_DEFAULT_BASE_URL = "http://127.0.0.1:11435"
_DEFAULT_MODEL = "gemma3"
_TIMEOUT_S = 120

_system_prompt = (
    "You are a translator. Translate the user's Ukrainian text into English. "
    "Keep emojis, HTML entities (&lt; &gt; &amp; &quot;), hashtags and proper nouns intact. "
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


def translate(text: str, model: str | None = None, base_url: str | None = None) -> str:
    model = model or os.environ.get("GEMMA_MODEL", _DEFAULT_MODEL)
    base_url = base_url or os.environ.get("GEMMA_BASE_URL", _DEFAULT_BASE_URL)
    return _call_ollama(text, model, base_url)


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