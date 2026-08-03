from __future__ import annotations

import html

from google_sheets_service import (
    SOURCE_COLUMN,
    TEXT_COLUMN,
    TITLE_COLUMN,
    HistoryRecord,
)

MAX_WORDS = 120
LINE_WIDTH = 30
SOURCE_LINK_LONG = "Детальніше"
SOURCE_LINK_NORMAL = "Джерело"


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def truncate_text(text: str, limit: int = MAX_WORDS) -> tuple[str, bool]:
    words = text.split()
    if len(words) <= limit:
        return text, False
    return " ".join(words[:limit]) + " ...", True


def _center(line: str, width: int = LINE_WIDTH) -> str:
    pad = max(width - len(line), 0)
    return " " * (pad // 2) + line + " " * (pad - pad // 2)


def _right_align(line: str, width: int = LINE_WIDTH) -> str:
    pad = max(width - len(line), 0)
    return " " * pad + line


def _build_source_link(record: HistoryRecord, truncated: bool) -> str:
    source = record.data.get(SOURCE_COLUMN, "").strip()
    if not source:
        return ""
    label = SOURCE_LINK_LONG if truncated else SOURCE_LINK_NORMAL
    return f'<a href="{_esc(source)}">{_esc(label)}</a>'


def build_message(record: HistoryRecord) -> str:
    date_line = _right_align(record.event_date.strftime("%d.%m.%Y"))
    title = record.data.get(TITLE_COLUMN, "").strip() or "День в Історії"
    title_line = _center(f"<b>{_esc(title)}</b>")

    text = record.data.get(TEXT_COLUMN, "").strip()
    body, truncated = truncate_text(text)

    parts = [date_line, title_line, body]
    link = _build_source_link(record, truncated)
    if link:
        parts.append(link)
    return "\n\n".join(parts)
