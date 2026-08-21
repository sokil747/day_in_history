from __future__ import annotations

import html

from google_sheets_service import HistoryRecord

MONTH_GENITIVE = [
    "січня",
    "лютого",
    "березня",
    "квітня",
    "травня",
    "червня",
    "липня",
    "серпня",
    "вересня",
    "жовтня",
    "листопада",
    "грудня",
]


def _event_text(record: HistoryRecord) -> str:
    text = record.text.strip()
    year_prefix = f"У {record.year} році"
    if not record.year or text.startswith(year_prefix):
        return text
    lowered = text[:1].lower() + text[1:] if text else text
    return f"{year_prefix} {lowered}"


def _event_line(record: HistoryRecord, with_source: bool = False) -> str:
    line = _event_text(record)
    if record.emoji:
        line = f"{record.emoji} {line}"
    if with_source and record.source:
        line = f'{line} <a href="{html.escape(record.source, quote=True)}">Джерело</a>'
    return line


def _date_line(month: int, day: int) -> str:
    return f"✅  <b>{day} {MONTH_GENITIVE[month - 1]}</b>"


def build_day_events(records: list[HistoryRecord]) -> str:
    if not records:
        return ""
    body = "\n".join(_event_line(r) for r in records)
    return f"{_date_line(records[0].month, records[0].day)}\n\n{body}"


def build_grouped_events(records: list[HistoryRecord]) -> str:
    if not records:
        return ""
    groups: dict[tuple[int, int], list[HistoryRecord]] = {}
    for record in records:
        groups.setdefault((record.month, record.day), []).append(record)

    blocks = []
    for month, day in sorted(groups):
        body = "\n".join(_event_line(r, with_source=True) for r in groups[(month, day)])
        blocks.append(f"{_date_line(month, day)}\n\n{body}")
    return "\n\n".join(blocks)