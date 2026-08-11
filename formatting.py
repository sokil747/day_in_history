from __future__ import annotations

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


def _event_line(record: HistoryRecord) -> str:
    if record.emoji:
        return f"{record.emoji} {record.text}"
    return record.text


def build_day_events(records: list[HistoryRecord]) -> str:
    if not records:
        return ""
    first = records[0]
    date_line = f"✅  {first.day} {MONTH_GENITIVE[first.month - 1]}"
    body = "\n".join(_event_line(r) for r in records)
    return f"{date_line}\n\n{body}"


def build_grouped_events(records: list[HistoryRecord]) -> str:
    groups: dict[tuple[int, int], list[HistoryRecord]] = {}
    for record in records:
        groups.setdefault((record.month, record.day), []).append(record)

    parts = []
    for month, day in sorted(groups):
        date_line = f"✅  {day} {MONTH_GENITIVE[month - 1]}"
        block = [date_line, *( _event_line(r) for r in groups[(month, day)] )]
        parts.append("\n".join(block))
    return "\n\n".join(parts)