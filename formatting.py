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

MONTH_NAMES_EN = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

LABELS = {
    "uk": {"year_prefix": "У {year} році", "source": "Джерело"},
    "en": {"year_prefix": "In {year}", "source": "Source"},
}


def _event_text(record: HistoryRecord, lang: str) -> str:
    if lang == "en" and record.text_en.strip():
        text = record.text_en.strip()
    else:
        text = record.text.strip()
    year_prefix = LABELS[lang]["year_prefix"].format(year=record.year)
    if not record.year or text.startswith(year_prefix):
        return text
    lowered = text[:1].lower() + text[1:] if text else text
    return f"{year_prefix} {lowered}"


def _event_line(record: HistoryRecord, lang: str, with_source: bool = False) -> str:
    line = _event_text(record, lang)
    if record.emoji:
        line = f"{record.emoji} {line}"
    if with_source and record.source:
        line = f'{line} <a href="{html.escape(record.source, quote=True)}">{LABELS[lang]["source"]}</a>'
    return line


def _date_line(month: int, day: int, lang: str) -> str:
    if lang == "en":
        return f"✅  <b>{day} {MONTH_NAMES_EN[month - 1]}</b>"
    return f"✅  <b>{day} {MONTH_GENITIVE[month - 1]}</b>"


def build_day_events(records: list[HistoryRecord], lang: str = "uk") -> str:
    if not records:
        return ""
    body = "\n".join(_event_line(r, lang) for r in records)
    return f"{_date_line(records[0].month, records[0].day, lang)}\n\n{body}"


def build_grouped_events(records: list[HistoryRecord], lang: str = "uk") -> str:
    if not records:
        return ""
    groups: dict[tuple[int, int], list[HistoryRecord]] = {}
    for record in records:
        groups.setdefault((record.month, record.day), []).append(record)

    blocks = []
    for month, day in sorted(groups):
        body = "\n".join(_event_line(r, lang, with_source=True) for r in groups[(month, day)])
        blocks.append(f"{_date_line(month, day, lang)}\n\n{body}")
    return "\n\n".join(blocks)