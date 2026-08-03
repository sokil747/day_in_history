import asyncio
import io
import logging
from datetime import date

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from PIL import Image

import config
from formatting import build_message
from google_sheets_service import (
    IMAGE_COLUMN,
    HistoryRecord,
    find_records_for_date,
    find_records_for_month,
    find_records_for_week,
)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

DAY_IN_HISTORY_CALLBACK = "day_in_history"
WEEK_EVENTS_CALLBACK = "week_events"
MONTH_EVENTS_CALLBACK = "month_events"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
MAX_CAPTION_LEN = 1024

chat_responses: dict[int, list[int]] = {}


def _build_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="День в Історії", callback_data=DAY_IN_HISTORY_CALLBACK
                )
            ],
            [
                InlineKeyboardButton(
                    text="Важливі події тижня", callback_data=WEEK_EVENTS_CALLBACK
                )
            ],
            [
                InlineKeyboardButton(
                    text="Важливі події місяця", callback_data=MONTH_EVENTS_CALLBACK
                )
            ],
        ]
    )


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Оберіть, що показати.", reply_markup=_build_keyboard()
    )


async def _clear_previous(chat_id: int) -> None:
    for msg_id in chat_responses.pop(chat_id, []):
        try:
            await bot.delete_message(chat_id, msg_id)
        except TelegramBadRequest:
            pass


def _remember(chat_id: int, message: Message) -> None:
    chat_responses.setdefault(chat_id, []).append(message.message_id)


def _convert_to_jpeg(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


async def _send_photo_with_caption(message: Message, photo, text: str) -> None:
    caption, rest = text[:MAX_CAPTION_LEN], text[MAX_CAPTION_LEN:]
    try:
        sent = await message.answer_photo(
            photo=photo, caption=caption, parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        sent = await message.answer_photo(photo=photo, caption=caption)
    _remember(message.chat.id, sent)
    if rest:
        _remember(message.chat.id, await message.answer(rest))


async def _send_image(message: Message, url: str, text: str) -> bool:
    try:
        await _send_photo_with_caption(message, url, text)
        return True
    except TelegramBadRequest as exc:
        logging.info("Telegram rejected URL photo (%s), converting: %s", exc, url)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, headers={"User-Agent": USER_AGENT}, timeout=30
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        jpeg = _convert_to_jpeg(resp.content)
        await _send_photo_with_caption(
            message, BufferedInputFile(jpeg, filename="image.jpg"), text
        )
        return True
    except Exception as exc:
        logging.warning("Failed to download/convert image %s: %s", url, exc)
        return False


async def _send_record(message: Message, record: HistoryRecord) -> None:
    text = build_message(record)
    image_url = record.data.get(IMAGE_COLUMN, "").strip()
    if image_url and await _send_image(message, image_url, text):
        return
    _remember(message.chat.id, await message.answer(text, parse_mode=ParseMode.HTML))


async def _send_records(message: Message, records: list[HistoryRecord], empty_text: str) -> None:
    if not records:
        _remember(message.chat.id, await message.answer(empty_text))
        return
    for record in records:
        await _send_record(message, record)


@dp.callback_query(F.data == DAY_IN_HISTORY_CALLBACK)
async def on_day_in_history(callback: CallbackQuery) -> None:
    await callback.answer()
    await _clear_previous(callback.message.chat.id)
    records = find_records_for_date(date.today())
    await _send_records(callback.message, records, "Записів на сьогодні не знайдено.")


@dp.callback_query(F.data == WEEK_EVENTS_CALLBACK)
async def on_week_events(callback: CallbackQuery) -> None:
    await callback.answer()
    await _clear_previous(callback.message.chat.id)
    records = find_records_for_week(date.today())
    await _send_records(callback.message, records, "Цього тижня записів не знайдено.")


@dp.callback_query(F.data == MONTH_EVENTS_CALLBACK)
async def on_month_events(callback: CallbackQuery) -> None:
    await callback.answer()
    await _clear_previous(callback.message.chat.id)
    records = find_records_for_month(date.today())
    await _send_records(callback.message, records, "Цього місяця записів не знайдено.")


async def main() -> None:
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
