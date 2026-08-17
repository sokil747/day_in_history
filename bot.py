import asyncio
import html
import json
import logging
import re
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from formatting import build_day_events, build_grouped_events
from google_sheets_service import (
    AdRecord,
    active_ads_on,
    find_records_for_date,
    find_records_for_month,
    find_records_for_week,
)
import stats_store

logging.basicConfig(level=logging.INFO)

with open("config.json", encoding="utf-8") as _f:
    welcome_config = json.load(_f)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

START_CALLBACK = "start"
DAY_IN_HISTORY_CALLBACK = "day_in_history"
WEEK_EVENTS_CALLBACK = "week_events"
MONTH_EVENTS_CALLBACK = "month_events"

TEXT_COMMANDS = {
    "day": {"day in history", "день в історії", "день"},
    "week": {"week in history", "important events of the week", "важливі події тижня", "тиждень"},
    "month": {"month in history", "important events of the month", "важливі події місяця", "місяць"},
}

chat_responses: dict[int, list[int]] = {}


def _track_subscriber(user_id: int | None) -> None:
    stats_store.record_interaction(user_id)


def _admin_footer(user_id: int | None, user_name: str | None) -> str:
    if not user_id or user_id not in config.ADMIN_IDS:
        return ""
    name = html.escape(user_name or "Адміністратор")
    stats = stats_store.get_stats()

    def _line(label: str, period: dict) -> str:
        return (
            f"{label}: <b>{period['all']}</b> користувачів "
            f"(унікальних - <b>{period['unique']}</b>)"
        )

    t, w, total = stats["today"], stats["week"], stats["total"]
    return (
        f"\n\n———————————————\n"
        f"👋 Вітаємо, <b>{name}</b>!\n"
        f"📊 {_line('Сьогодні', t)}\n"
        f"📈 {_line('За тиждень', w)}\n"
        f"📚 {_line('Всього', total)}"
    )


def _effective_today() -> date:
    test_mode = welcome_config.get("test_mode", False)
    test_day = welcome_config.get("test_day", "")
    if test_mode and test_day:
        try:
            return datetime.strptime(test_day, "%d/%m/%Y").date()
        except ValueError:
            pass
    return date.today()


def _build_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=welcome_config["day_button_text"],
                    callback_data=DAY_IN_HISTORY_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text=welcome_config["week_button_text"],
                    callback_data=WEEK_EVENTS_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text=welcome_config["month_button_text"],
                    callback_data=MONTH_EVENTS_CALLBACK,
                )
            ],
        ]
    )


def _welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=welcome_config["start_button_text"],
                    callback_data=START_CALLBACK,
                )
            ]
        ]
    )


async def _send_welcome(message: Message) -> None:
    user = message.from_user
    footer = _admin_footer(user.id if user else None, (user.full_name or user.username) if user else None)
    caption = (
        f"{welcome_config['welcome_text']}\n\n{welcome_config['welcome_footer']}{footer}"
    )
    try:
        await message.answer_photo(
            FSInputFile(welcome_config["welcome_img"]),
            caption=caption,
            reply_markup=_welcome_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logging.warning("Failed to send intro image: %s", exc)
        await message.answer(
            caption, reply_markup=_welcome_keyboard(), parse_mode=ParseMode.HTML
        )


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    _track_subscriber(message.from_user.id if message.from_user else None)
    await _send_welcome(message)


@dp.callback_query(F.data == START_CALLBACK)
async def on_start(callback: CallbackQuery) -> None:
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    try:
        await callback.message.answer_photo(
            FSInputFile(welcome_config["about_img"]),
            caption=welcome_config["about_text"],
            reply_markup=_build_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logging.warning("Failed to send about image: %s", exc)
        await callback.message.answer(
            welcome_config["about_text"],
            reply_markup=_build_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    finally:
        await callback.answer()


async def _clear_previous(chat_id: int) -> None:
    for msg_id in chat_responses.pop(chat_id, []):
        try:
            await bot.delete_message(chat_id, msg_id)
        except TelegramBadRequest:
            pass


def _remember(chat_id: int, message: Message) -> None:
    chat_responses.setdefault(chat_id, []).append(message.message_id)


async def _send_photo_then_text(
    message: Message, image_key: str, caption: str, body: str, reply_markup=None
) -> None:
    full_caption = f"{caption}\n\n{body}" if body else caption
    try:
        sent = await message.answer_photo(
            FSInputFile(welcome_config[image_key]),
            caption=full_caption,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logging.warning("Failed to send combined caption %s: %s", image_key, exc)
        try:
            sent = await message.answer_photo(
                FSInputFile(welcome_config[image_key]),
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
            _remember(message.chat.id, sent)
            sent = await message.answer(body, parse_mode=ParseMode.HTML)
        except Exception as exc2:
            logging.warning("Failed to send image %s: %s", image_key, exc2)
            sent = await message.answer(
                full_caption, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
    _remember(message.chat.id, sent)


def _drive_direct_url(url: str) -> str:
    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", url or "")
    if match:
        return f"https://drive.google.com/uc?export=view&id={match.group(1)}"
    return url


def _build_ad_caption(ad: AdRecord, with_separator: bool) -> str:
    parts = []
    if with_separator:
        parts.append("──────────────")
    if ad.text:
        parts.append(ad.text)
    if ad.link:
        parts.append(
            f'🔗 <a href="{html.escape(ad.link, quote=True)}">Детальніше</a>'
        )
    return "\n\n".join(parts)


def _split_footer(footer: str) -> tuple[str, str]:
    marker = "<b>РЕКЛАМА :</b>"
    idx = footer.find(marker)
    if idx == -1:
        return footer, ""
    head = footer[: idx + len(marker)]
    tail = footer[idx + len(marker) :]
    if tail.startswith("\n"):
        tail = tail[1:]
    return head, tail


async def _send_ads(message: Message) -> None:
    try:
        ads = active_ads_on(_effective_today())
    except Exception as exc:
        logging.warning("Failed to load advertisements: %s", exc)
        return
    for i, ad in enumerate(ads):
        caption = _build_ad_caption(ad, with_separator=i > 0)
        photo_url = _drive_direct_url(ad.logo)
        try:
            sent = await message.answer_photo(
                photo=photo_url, caption=caption, parse_mode=ParseMode.HTML
            )
        except Exception as exc:
            logging.warning("Failed to send ad photo %s: %s", photo_url, exc)
            sent = await message.answer(caption, parse_mode=ParseMode.HTML)
        _remember(message.chat.id, sent)


async def _send_footer_tail(message: Message, footer_tail: str) -> None:
    if footer_tail:
        _remember(
            message.chat.id,
            await message.answer(footer_tail, parse_mode=ParseMode.HTML),
        )


async def _send_day_screen(message: Message, records) -> None:
    day_footer = welcome_config.get("day_footer", "")
    footer_head, footer_tail = _split_footer(day_footer)
    if records:
        events_text = build_day_events(records)
    else:
        events_text = "✅  Записів на сьогодні не знайдено."
    if footer_head:
        events_text = f"{events_text}\n\n{footer_head}"
    await _send_photo_then_text(
        message, "day_img", welcome_config["day_header"], events_text
    )
    await _send_ads(message)
    await _send_footer_tail(message, footer_tail)


async def _send_grouped_screen(
    message: Message, image_key: str, records, empty_text: str
) -> None:
    day_footer = welcome_config.get("day_footer", "")
    footer_head, footer_tail = _split_footer(day_footer)
    if records:
        events_text = build_grouped_events(records)
    else:
        events_text = empty_text
    if footer_head:
        events_text = f"{events_text}\n\n\n{footer_head}"
    await _send_photo_then_text(
        message, image_key, welcome_config["day_header"], events_text
    )
    await _send_ads(message)
    await _send_footer_tail(message, footer_tail)


@dp.callback_query(F.data == DAY_IN_HISTORY_CALLBACK)
async def on_day_in_history(callback: CallbackQuery) -> None:
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    await _clear_previous(callback.message.chat.id)
    try:
        records = find_records_for_date(_effective_today())
        await _send_day_screen(callback.message, records)
    finally:
        await callback.answer()


@dp.callback_query(F.data == WEEK_EVENTS_CALLBACK)
async def on_week_events(callback: CallbackQuery) -> None:
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    await _clear_previous(callback.message.chat.id)
    try:
        records = find_records_for_week(_effective_today())
        await _send_grouped_screen(
            callback.message, "week_img", records, "Цього тижня записів не знайдено."
        )
    finally:
        await callback.answer()


@dp.callback_query(F.data == MONTH_EVENTS_CALLBACK)
async def on_month_events(callback: CallbackQuery) -> None:
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    await _clear_previous(callback.message.chat.id)
    try:
        records = find_records_for_month(_effective_today())
        await _send_grouped_screen(
            callback.message, "month_img", records, "Цього місяця записів не знайдено."
        )
    finally:
        await callback.answer()


@dp.message(F.text)
async def on_text(message: Message) -> None:
    _track_subscriber(message.from_user.id if message.from_user else None)
    if message.text.startswith("/"):
        return

    text = message.text.strip().lower()
    if text in TEXT_COMMANDS["day"]:
        records = find_records_for_date(_effective_today())
        await _send_day_screen(message, records)
    elif text in TEXT_COMMANDS["week"]:
        records = find_records_for_week(_effective_today())
        await _send_grouped_screen(
            message, "week_img", records, "Цього тижня записів не знайдено."
        )
    elif text in TEXT_COMMANDS["month"]:
        records = find_records_for_month(_effective_today())
        await _send_grouped_screen(
            message, "month_img", records, "Цього місяця записів не знайдено."
        )
    else:
        await _send_welcome(message)


async def main() -> None:
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())