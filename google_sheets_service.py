from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import gspread
import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
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
    logo_image: str = ""  # local relative path e.g. ads/ad_1.jpg
    logo_image_path: str = ""  # absolute filesystem path if available


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


def extract_drive_file_id(url: str) -> str | None:
    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", url or "")
    if match:
        return match.group(1)
    return None


def download_drive_file(file_id: str) -> bytes | None:
    creds = Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS_PATH, scopes=DRIVE_SCOPES
    )
    session = AuthorizedSession(creds)
    response = session.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    )
    if response.status_code != 200:
        return None
    return response.content


def download_ad_logo(url: str) -> bytes | None:
    file_id = extract_drive_file_id(url)
    if file_id:
        content = download_drive_file(file_id)
        if content:
            return content
    for candidate in (
        url,
        f"https://drive.google.com/uc?export=view&id={file_id}" if file_id else None,
    ):
        if not candidate:
            continue
        try:
            response = requests.get(candidate, timeout=20)
        except requests.RequestException:
            continue
        if response.status_code == 200 and response.content:
            return response.content
    return None