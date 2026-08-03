from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

DATE_COLUMN = "date"
TITLE_COLUMN = "title"
IMAGE_COLUMN = "image_link"
TEXT_COLUMN = "text"
SOURCE_COLUMN = "source"

DATE_FORMAT = "%d/%m/%Y"


class SheetRecordNotFound(Exception):
    pass


@dataclass
class HistoryRecord:
    event_date: date
    data: dict


def get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS_PATH, scopes=SCOPES
    )
    return gspread.authorize(creds)


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
        try:
            event_date = datetime.strptime(data[DATE_COLUMN].strip(), DATE_FORMAT).date()
        except (ValueError, KeyError):
            continue
        records.append(HistoryRecord(event_date=event_date, data=data))
    return records


def find_records_for_date(target: date) -> list[HistoryRecord]:
    return [r for r in get_records() if r.event_date == target]


def _records_in_range(start: date, end: date) -> list[HistoryRecord]:
    return sorted(
        (r for r in get_records() if start <= r.event_date <= end),
        key=lambda r: r.event_date,
    )


def find_records_for_week(anchor: date) -> list[HistoryRecord]:
    start = anchor - timedelta(days=anchor.weekday())
    end = start + timedelta(days=6)
    return _records_in_range(start, end)


def find_records_for_month(anchor: date) -> list[HistoryRecord]:
    start = anchor.replace(day=1)
    end = anchor.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
    return _records_in_range(start, end)
