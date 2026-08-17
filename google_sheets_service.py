from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime, timedelta

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


@dataclass
class AdRecord:
    logo: str
    text: str
    link: str
    start_date: str
    finish_date: str


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


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered == "unlimited":
        return None
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def get_advertisements() -> list[AdRecord]:
    client = get_client()
    spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    worksheet = None
    for ws in spreadsheet.worksheets():
        if ws.id == 971995155 or (ws.title and ws.title.lower() == "advertisements"):
            worksheet = ws
            break
    if worksheet is None:
        return []
    rows = worksheet.get_all_values()
    if not rows:
        return []
    headers = [str(h).strip().lower() for h in rows[0]]
    ads = []
    for raw_row in rows[1:]:
        if not raw_row or not raw_row[0].strip():
            continue
        data = dict(zip(headers, raw_row))
        text = data.get("text", "").strip()
        link = data.get("link", "").strip()
        logo = data.get("logo", "").strip()
        if not (text or link or logo):
            continue
        ads.append(
            AdRecord(
                logo=logo,
                text=html.unescape(text),
                link=link,
                start_date=data.get("start_date", "").strip(),
                finish_date=data.get("finish_date", "").strip(),
            )
        )
    return ads


def active_ads_on(target: date) -> list[AdRecord]:
    ads = []
    for ad in get_advertisements():
        start = _parse_date(ad.start_date)
        finish = _parse_date(ad.finish_date)
        if start is not None and target < start:
            continue
        if finish is not None and target > finish:
            continue
        ads.append(ad)
    return ads