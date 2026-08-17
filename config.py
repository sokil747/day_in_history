import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Sheet1")
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH")
ADMIN_IDS = [
    int(raw)
    for raw in os.getenv("ADMIN_IDS", "").split(",")
    if raw.strip().isdigit()
]

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env")
if not GOOGLE_CREDENTIALS_PATH:
    raise ValueError("GOOGLE_CREDENTIALS_PATH is not set in .env")
