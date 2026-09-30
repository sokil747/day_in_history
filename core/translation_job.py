"""Background translation job runner shared by cron and admin.

State lives in files under data/translate/ so independent processes
(admin web app, cron job) see one consistent picture:

  running  - "1" while the job is active
  stop     - presence requests graceful stop
  started  - unix timestamp of job start (ETA calculation)
  total    - number of events to translate
  done     - translated so far
  fail     - failed so far
  last     - unix timestamp of last finished event
  log      - failure lines
"""
from __future__ import annotations

import threading
import traceback
from pathlib import Path

from django.db import close_old_connections

STATE_DIR = Path("data/translate")
RUNNING_FILE = STATE_DIR / "running"
STOP_FILE = STATE_DIR / "stop"
STARTED_FILE = STATE_DIR / "started"
LAST_FILE = STATE_DIR / "last"
DONE_FILE = STATE_DIR / "done"
FAIL_FILE = STATE_DIR / "fail"
TOTAL_FILE = STATE_DIR / "total"
LOG_FILE = STATE_DIR / "log"

_lock = threading.Lock()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _read_int(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def _read_float(path: Path) -> float:
    try:
        return float(path.read_text().strip())
    except (OSError, ValueError):
        return 0.0


def read_status() -> dict:
    running = RUNNING_FILE.exists()
    done = _read_int(DONE_FILE)
    failed = _read_int(FAIL_FILE)
    total = _read_int(TOTAL_FILE)
    started = _read_float(STARTED_FILE)
    last = _read_float(LAST_FILE)

    import time

    elapsed = (last or time.time()) - started if running and started > 0 else 0.0
    eta_s = None
    if running and done > 0 and total > done:
        per_event = elapsed / done
        eta_s = per_event * (total - done)
    return {
        "running": running,
        "stopping": running and STOP_FILE.exists(),
        "done": done if running else 0,
        "failed": failed,
        "total": total,
        "elapsed_s": round(elapsed, 1) if running else 0.0,
        "eta_s": round(eta_s, 1) if eta_s is not None else None,
    }


def request_stop() -> None:
    if RUNNING_FILE.exists():
        _write(STOP_FILE, "1")


def is_running() -> bool:
    return RUNNING_FILE.exists()


def reset_state() -> None:
    STATE_DIR.parent.mkdir(parents=True, exist_ok=True)
    for f in (DONE_FILE, FAIL_FILE, STOP_FILE, LOG_FILE):
        f.unlink(missing_ok=True)


class _Runner:
    """Translates events with text_en empty, updating state files."""

    def run(
        self,
        model: str | None = None,
        base_url: str | None = None,
        skip_setup: bool = False,
    ) -> None:
        import time

        from core import llm
        from core.models import Event

        close_old_connections()
        if not skip_setup:
            reset_state()
            _write(RUNNING_FILE, "1")
            _write(STARTED_FILE, str(time.time()))
        stopped = False
        try:
            ids = list(
                Event.objects.filter(text_en="").values_list("id", flat=True)
            )
            _write(TOTAL_FILE, str(len(ids)))
            for event_id in ids:
                if STOP_FILE.exists():
                    stopped = True
                    break
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
            _append_log("stopped" if stopped else "finished")
        finally:
            close_old_connections()
            RUNNING_FILE.unlink(missing_ok=True)
            STOP_FILE.unlink(missing_ok=True)


def _bump(path: Path) -> None:
    with _lock:
        _write(path, str(_read_int(path) + 1))


def _append_log(line: str) -> None:
    with _lock:
        old = LOG_FILE.read_text() if LOG_FILE.exists() else ""
        _write(LOG_FILE, old + line + "\n")


def start_in_background(model: str | None = None, base_url: str | None = None) -> bool:
    """Start the job in a daemon thread; returns False if already running.

    A stale `running` file (process died mid-run) is cleared first: within the
    admin process there is at most one job thread, and its liveness is checked
    directly.
    """
    with _lock:
        if is_running():
            alive = any(t.name == "translate-job" for t in threading.enumerate())
            if alive:
                return False
            RUNNING_FILE.unlink(missing_ok=True)  # stale lock from dead process
        reset_state()
        _write(RUNNING_FILE, "1")
        import time

        _write(STARTED_FILE, str(time.time()))

    def _target():
        try:
            runner = _Runner()
            runner.run(model=model, base_url=base_url, skip_setup=True)
        except Exception:
            RUNNING_FILE.unlink(missing_ok=True)
            STOP_FILE.unlink(missing_ok=True)
            traceback.print_exc()

    threading.Thread(target=_target, daemon=True, name="translate-job").start()
    return True