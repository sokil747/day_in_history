from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


@dataclass
class HistoryRecord:
    month: int
    day: int
    order: int
    year: int
    emoji: str
    category: str
    text: str
    source: str


class SheetRecordNotFound(Exception):
    pass


def get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS_PATH, scopes=SCOPES
    )
    return gspread.authorize(creds)


def _as_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value.strip()))
    except (ValueError, AttributeError):
        return default


def get_records() -> list[HistoryRecord]:
    client = get_client()
    sheet = client.open_by_key(config.GOOGLE_SHEET_ID).worksheet(
        config.GOOGLE_SHEET_NAME
    )
    rows = sheet.get_all_values()
    if not rows:
        return []

    headers = [str(h).strip() for h in rows[0]]
    records = []
    for raw_row in rows[1:]:
        if not raw_row or not raw_row[0].strip():
            continue
        data = dict(zip(headers, raw_row))
        month = _as_int(data.get("Month", ""))
        day = _as_int(data.get("Day", ""))
        if month <= 0 or day <= 0:
            continue
        records.append(
            HistoryRecord(
                month=month,
                day=day,
                order=_as_int(data.get("Order", "")),
                year=_as_int(data.get("Year", "")),
                emoji=data.get("Emoji", "").strip(),
                category=data.get("Category", "").strip(),
                text=data.get("Event_UA", "").strip(),
                source=data.get("Джерело", "").strip(),
            )
        )
    return records


def find_records_for_date(target: date) -> list[HistoryRecord]:
    return sorted(
        (
            r
            for r in get_records()
            if r.month == target.month and r.day == target.day
        ),
        key=lambda r: (r.order, r.month, r.day),
    )


def _pairs_in_range(start: date, end: date) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    current = start
    while current <= end:
        pairs.add((current.month, current.day))
        current += timedelta(days=1)
    return pairs


def find_records_for_week(anchor: date) -> list[HistoryRecord]:
    start = anchor - timedelta(days=anchor.weekday())
    end = start + timedelta(days=6)
    pairs = _pairs_in_range(start, end)
    pairs = {p for p in pairs if p[0] == anchor.month}
    records = [r for r in get_records() if (r.month, r.day) in pairs]
    return sorted(records, key=lambda r: (r.month, r.day, r.order))


def find_records_for_month(anchor: date) -> list[HistoryRecord]:
    records = [r for r in get_records() if r.month == anchor.month]
    return sorted(records, key=lambda r: (r.day, r.order))