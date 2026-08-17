from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "stats.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS interactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, ts TEXT NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions (user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_ts ON interactions (ts)")
    return conn


def record_interaction(user_id: int | None) -> None:
    if not user_id:
        return
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO interactions (user_id, ts) VALUES (?, ?)", (user_id, now)
        )


def _week_start(d: date) -> str:
    start = d - timedelta(days=d.weekday())
    return datetime.combine(start, datetime.min.time()).isoformat(timespec="seconds")


def get_stats() -> dict[str, dict[str, int]]:
    today = datetime.combine(date.today(), datetime.min.time()).isoformat(
        timespec="seconds"
    )
    week = _week_start(date.today())
    with _connect() as conn:
        def rows_since(since: str):
            return conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM interactions WHERE ts >= ?",
                (since,),
            ).fetchone()

        def rows_total():
            return conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM interactions"
            ).fetchone()

        today_all, today_unique = rows_since(today)
        week_all, week_unique = rows_since(week)
        total_all, total_unique = rows_total()
    return {
        "today": {"all": today_all, "unique": today_unique},
        "week": {"all": week_all, "unique": week_unique},
        "total": {"all": total_all, "unique": total_unique},
    }