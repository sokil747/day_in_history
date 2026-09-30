"""File-signalled translation loop, run inside the persistent bot process.

The admin UI flips command files (start/stop) under data/translate/; this loop
watches them and executes the job there — survives gunicorn worker recycling
and page reloads, single-place execution avoids double-run races.
"""
from __future__ import annotations

import asyncio
import time

from core import translation_job as tj
from core.translation_job import (
    DONE_FILE,
    FAIL_FILE,
    LAST_FILE,
    LOG_FILE,
    RUNNING_FILE,
    STARTED_FILE,
    STOP_FILE,
    TOTAL_FILE,
    _bump,
    _append_log,
    _read_float,
    _read_int,
    reset_state,
)

WATCH_INTERVAL_S = 10.0
START_SIGNAL = tj.STATE_DIR / "start"


def read_start_signal() -> bool:
    return START_SIGNAL.exists()


def clear_start_signal() -> None:
    START_SIGNAL.unlink(missing_ok=True)


async def _translate_once(model: str | None, base_url: str | None) -> None:
    """One full pass in a worker thread (DB is async-unsafe from loop)."""
    from asgiref.sync import sync_to_async
    from core import llm
    from core.models import Event

    def _sync_run() -> None:
        ids = list(Event.objects.filter(text_en="").values_list("id", flat=True))
        _write(TOTAL_FILE, str(len(ids)))
        for event_id in ids:
            if STOP_FILE.exists():
                return
            try:
                e = Event.objects.get(pk=event_id)
                en = llm.translate(e.text, model=model, base_url=base_url)
                if not en:
                    _bump(FAIL_FILE)
                    _append_log(f"FAIL #{e.pk}: empty response")
                    continue
                e.text_en = en
                e.save(update_fields=["text_en"])
                _bump(DONE_FILE)
                _write(LAST_FILE, str(time.time()))
            except Exception as exc:
                _bump(FAIL_FILE)
                _append_log(f"FAIL #{event_id}: {type(exc).__name__}: {exc}")

    await sync_to_async(_sync_run)()


def _write(path, text):  # re-exported helper (file write)
    tj._write(path, text)


async def watch_loop(model: str | None = None, base_url: str | None = None) -> None:
    """Endless asyncio task: wait for start signal, run job, repeat."""
    import logging

    log = logging.getLogger("bot")
    while True:
        try:
            if not read_start_signal():
                await asyncio.sleep(WATCH_INTERVAL_S)
                continue
            clear_start_signal()
            if RUNNING_FILE.exists():
                log.warning("translate watch: start requested but already running")
                continue
            reset_state()
            _write(RUNNING_FILE, "1")
            _write(STARTED_FILE, str(time.time()))
            log.info("translate watch: starting translation pass")
            await _translate_once(model, base_url)
        except Exception as exc:
            log.warning("translate watch error: %s", exc)
        finally:
            RUNNING_FILE.unlink(missing_ok=True)
            STOP_FILE.unlink(missing_ok=True)