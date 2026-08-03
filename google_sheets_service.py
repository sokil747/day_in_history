from datetime import date

import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

IMAGE_COLUMN = "image_link"


class SheetRecordNotFound(Exception):
    pass


def get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS_PATH, scopes=SCOPES
    )
    return gspread.authorize(creds)


def find_record_for_date(target: date) -> dict:
    client = get_client()
    sheet = client.open_by_key(config.GOOGLE_SHEET_ID).worksheet(
        config.GOOGLE_SHEET_NAME
    )
    rows = sheet.get_all_values()
    if not rows:
        raise SheetRecordNotFound("Sheet is empty")

    headers = [str(h).strip() for h in rows[0]]

    target_str = target.strftime("%d/%m/%Y")
    for raw_row in rows[1:]:
        if not raw_row:
            continue
        date_cell = str(raw_row[0]).strip()
        if date_cell == target_str:
            return dict(zip(headers, raw_row))

    raise SheetRecordNotFound(f"No record found for {target_str}")


def format_record(record: dict) -> str:
    return "\n".join(
        str(value).strip()
        for key, value in record.items()
        if value and value.strip() and key != IMAGE_COLUMN
    )
