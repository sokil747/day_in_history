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
from google_sheets_service import (
    IMAGE_COLUMN,
    SheetRecordNotFound,
    find_record_for_date,
    format_record,
)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

DAY_IN_HISTORY_CALLBACK = "day_in_history"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
MAX_CAPTION_LEN = 1024

chat_responses: dict[int, list[int]] = {}


def _build_text(record: dict) -> str:
    body = format_record(record)
    return f"<b>День в Історії</b>\n\n{body}"


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


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="День в Історії", callback_data=DAY_IN_HISTORY_CALLBACK
                )
            ]
        ]
    )
    await message.answer("Натисніть кнопку, щоб дізнатися, що сталося цього дня в історії.", reply_markup=keyboard)


async def _send_photo_with_caption(message: Message, photo, text: str) -> None:
    caption, rest = text[:MAX_CAPTION_LEN], text[MAX_CAPTION_LEN:]
    sent = await message.answer_photo(
        photo=photo, caption=caption, parse_mode=ParseMode.HTML
    )
    _remember(message.chat.id, sent)
    if rest:
        _remember(message.chat.id, await message.answer(rest))


async def _send_image(message: Message, url: str, text: str) -> bool:
    try:
        await _send_photo_with_caption(message, url, text)
        return True
    except TelegramBadRequest:
        logging.info("Telegram rejected URL photo, converting: %s", url)

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


@dp.callback_query(F.data == DAY_IN_HISTORY_CALLBACK)
async def on_day_in_history(callback: CallbackQuery) -> None:
    await callback.answer()
    chat_id = callback.message.chat.id
    await _clear_previous(chat_id)

    try:
        record = find_record_for_date(date.today())
    except SheetRecordNotFound as exc:
        await callback.message.answer(str(exc))
        return

    text = _build_text(record)
    image_url = record.get(IMAGE_COLUMN, "").strip()

    if image_url and await _send_image(callback.message, image_url, text):
        return

    _remember(chat_id, await callback.message.answer(text, parse_mode=ParseMode.HTML))


async def main() -> None:
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
