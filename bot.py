import asyncio
import html
import json
import logging
import re
import random
import time
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
)

import config
from formatting import build_day_events, build_grouped_events
from db_service import (
    a_active_ads_on,
    a_can_access_month,
    a_can_access_week,
    a_find_records_for_date,
    a_find_records_for_month,
    a_find_records_for_week,
    active_ads_on,
    can_access_month,
    can_access_week,
    find_records_for_date,
    find_records_for_month,
    find_records_for_week,
    is_premium,
)
from google_sheets_service import AdRecord, download_ad_logo
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
RANDOM_DAY_CALLBACK = "random_day"

TEXT_COMMANDS = {
    "day": {"day in history", "день в історії", "день"},
    "week": {"week in history", "important events of the week", "важливі події тижня", "тиждень"},
    "month": {"month in history", "important events of the month", "важливі події місяця", "місяць"},
}

chat_responses: dict[int, list[int]] = {}

# Records cache: kind -> (effective_date, records). Valid until midnight
# (date rollover forces refetch). Random day is never cached.
_records_cache: dict[str, tuple[date, list]] = {}


def _cached_records(kind: str, today: date) -> list | None:
    hit = _records_cache.get(kind)
    if hit is not None and hit[0] == today:
        return hit[1]
    return None


def _store_records(kind: str, today: date, records: list) -> None:
    _records_cache[kind] = (today, records)


async def _get_records_cached(kind: str, today: date, fetch) -> list:
    records = _cached_records(kind, today)
    if records is None:
        records = await fetch(today)
        _store_records(kind, today, records)
    return records


def _track_subscriber(user_id: int | None) -> None:
    stats_store.record_interaction(user_id)


def _admin_timing_enabled() -> bool:
    return bool(welcome_config.get("admin_timing", False))


def _sticky_enabled() -> bool:
    return bool(welcome_config.get("sticky_buttons", False))


def _ads_enabled() -> bool:
    return bool(welcome_config.get("ads_enabled", True))


async def _send_timing(message: Message, started: float) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _admin_timing_enabled() or not uid or uid not in config.ADMIN_IDS:
        return
    elapsed = time.perf_counter() - started
    _remember(
        message.chat.id,
        await message.answer(
            f"⏱ <b>{elapsed:.4f}</b> с", parse_mode=ParseMode.HTML
        ),
    )


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


async def _build_keyboard(user_id: int | None = None):
    from db_service import (
        a_premium_lock_mode,
    )

    week_ok = await a_can_access_week(user_id)
    month_ok = await a_can_access_month(user_id)
    lock_mode = await a_premium_lock_mode()

    def _locked_text(base: str) -> str:
        # Telegram has no button text colors — 🔒 marks a locked button
        return f"{base} 🔒"

    show_week = week_ok or lock_mode == "inactive"
    show_month = month_ok or lock_mode == "inactive"
    week_text = (
        welcome_config["week_button_text"] if week_ok else _locked_text(welcome_config["week_button_text"])
    )
    month_text = (
        welcome_config["month_button_text"] if month_ok else _locked_text(welcome_config["month_button_text"])
    )

    if _sticky_enabled():
        # narrow mobile buttons: split the long word so the emoji is not
        # alone on the first line — «🎲 Випадко⏎вий день»
        random_text = welcome_config["random_day_text"].replace(
            "Випадковий", "Випадко\nвий"
        )
        buttons = [
            KeyboardButton(text=random_text),
            KeyboardButton(text=welcome_config["day_button_text"]),
        ]
        if show_week:
            buttons.append(KeyboardButton(text=week_text))
        if show_month:
            buttons.append(KeyboardButton(text=month_text))
        return ReplyKeyboardMarkup(
            keyboard=[buttons],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="Оберіть кнопку ⤵️",
        )

    rows = [
        [
            InlineKeyboardButton(
                text=welcome_config["random_day_text"],
                callback_data=RANDOM_DAY_CALLBACK,
            )
        ],
        [
            InlineKeyboardButton(
                text=welcome_config["day_button_text"],
                callback_data=DAY_IN_HISTORY_CALLBACK,
            )
        ],
    ]
    if show_week:
        rows.append(
            [
                InlineKeyboardButton(
                    text=week_text,
                    callback_data=WEEK_EVENTS_CALLBACK,
                )
            ]
        )
    if show_month:
        rows.append(
            [
                InlineKeyboardButton(
                    text=month_text,
                    callback_data=MONTH_EVENTS_CALLBACK,
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _premium_required_text() -> str:
    return (
        "🔒 Ця функція доступна лише для <b>преміум</b> користувачів.\n"
        "Зверніться до адміністратора, щоб отримати доступ."
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
    uid = callback.from_user.id if callback.from_user else None
    kb = await _build_keyboard(uid)
    try:
        await callback.message.answer_photo(
            FSInputFile(welcome_config["about_img"]),
            caption=welcome_config["about_text"],
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logging.warning("Failed to send about image: %s", exc)
        await callback.message.answer(
            welcome_config["about_text"],
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    finally:
        await callback.answer()


async def _clear_previous(chat_id: int) -> None:
    if _sticky_enabled():
        return  # sticky mode keeps conversation history
    for msg_id in chat_responses.pop(chat_id, []):
        try:
            await bot.delete_message(chat_id, msg_id)
        except TelegramBadRequest:
            pass


def _remember(chat_id: int, message: Message) -> None:
    chat_responses.setdefault(chat_id, []).append(message.message_id)


MAX_CAPTION = 1000
MAX_MESSAGE = 4000

NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)


def _chunk_text(text: str, limit: int) -> list[str]:
    lines = text.split("\n")
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks


def _chunk_html(text: str, limit: int) -> list[str]:
    chunks = _chunk_text(text, limit)
    if len(chunks) == 1:
        return chunks
    result: list[str] = []
    open_tags: list[str] = []
    for part in chunks:
        if open_tags:
            part = "".join(open_tags) + part
        result.append(part)
        open_tags = re.findall(r"<([a-z][a-z0-9]*)(?:\s[^>]*)?>", part)
        open_tags = [
            t
            for t in open_tags
            if part.count(f"<{t}>") > part.count(f"</{t}>")
        ]
        for tag in reversed(open_tags):
            result[-1] = f"{result[-1]}</{tag}>"
    return result


async def _send_photo_then_text(
    message: Message, image_key: str, caption: str, body: str, reply_markup=None
) -> None:
    full_caption = f"{caption}\n\n{body}" if body else caption
    try:
        if len(full_caption) <= MAX_CAPTION:
            sent = await message.answer_photo(
                FSInputFile(welcome_config[image_key]),
                caption=full_caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
            _remember(message.chat.id, sent)
            return
        sent = await message.answer_photo(
            FSInputFile(welcome_config[image_key]),
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_LINK_PREVIEW,
        )
        _remember(message.chat.id, sent)
        for piece in _chunk_html(body, MAX_MESSAGE):
            _remember(
                message.chat.id,
                await message.answer(
                    piece, parse_mode=ParseMode.HTML, link_preview_options=NO_LINK_PREVIEW
                ),
            )
    except Exception as exc:
        logging.warning("Failed to send screen %s: %s", image_key, exc)
        try:
            sent = await message.answer(
                full_caption[:MAX_MESSAGE],
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
            _remember(message.chat.id, sent)
        except Exception as exc2:
            logging.warning("Failed to send plain text %s: %s", image_key, exc2)


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


def _split_footer(footer: str) -> tuple[str, str, str]:
    marker = "РЕКЛАМА :"
    idx = footer.find(marker)
    if idx == -1:
        return footer, "", ""
    line_start = footer.rfind("\n", 0, idx) + 1
    head = footer[:line_start].rstrip("\n")
    line_end = footer.find("\n", idx)
    if line_end == -1:
        marker_line = footer[line_start:].strip()
        tail = ""
    else:
        marker_line = footer[line_start:line_end].strip()
        tail = footer[line_end + 1 :]
    return head, marker_line, tail


async def _send_ads(message: Message, ad_header: str) -> None:
    try:
        ads = await a_active_ads_on(_effective_today())
    except Exception as exc:
        logging.warning("Failed to load advertisements: %s", exc)
        return
    if not ads:
        return
    for i, ad in enumerate(ads):
        caption = _build_ad_caption(ad, with_separator=i > 0)
        if i == 0 and ad_header:
            caption = f"{ad_header}\n\n{caption}"
        # Prefer local image copied to VPS (media/ads/) — fallback to URL/download
        photo = None
        local_path = getattr(ad, "logo_image_path", "") or getattr(ad, "logo_image", "")
        if local_path:
            # logo_image_path is absolute, logo_image is MEDIA-relative
            import os
            from pathlib import Path

            candidates = [local_path]
            if getattr(ad, "logo_image", ""):
                candidates.append(str(Path("media") / ad.logo_image))  # relative
                try:
                    from django.conf import settings

                    candidates.append(str(Path(settings.MEDIA_ROOT) / ad.logo_image))
                except Exception:
                    pass
            for cand in candidates:
                if cand and os.path.exists(cand):
                    photo = FSInputFile(cand)
                    break
        if photo is None:
            photo = _drive_direct_url(ad.logo)
            try:
                content = download_ad_logo(ad.logo)
                if content:
                    photo = BufferedInputFile(content, filename="ad.jpg")
            except Exception as exc:
                logging.warning("Failed to download logo %s: %s", ad.logo, exc)
        try:
            sent = await message.answer_photo(
                photo=photo,
                caption=caption,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
        except Exception as exc:
            logging.warning("Failed to send ad photo %s: %s", photo, exc)
            sent = await message.answer(
                caption,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
        _remember(message.chat.id, sent)


async def _send_footer_tail(message: Message, footer_tail: str) -> None:
    if footer_tail:
        _remember(
            message.chat.id,
            await message.answer(
                footer_tail,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            ),
        )


def _channel_footer() -> str:
    return welcome_config.get(
        "welcome_footer",
        "👉 <b>Пульс індустрії тут:</b>\n"
        '🔔 <a href="https://t.me/InsiderKidsNews">@InsiderKidsNews</a>',
    )


def _empty_events_text() -> str:
    return (
        "За цей період нічого цікавого не сталося, "
        "але більше цікавої інформації ви можете знайти на нашому каналі:\n\n"
        f"{_channel_footer()}"
    )


def _ad_inquiry_marker() -> str:
    return "Хочете розмістити рекламу"


async def _send_day_screen(message: Message, records) -> None:
    day_footer = welcome_config.get("day_footer", "")
    footer_head, ad_header, footer_tail = _split_footer(day_footer)
    if records:
        events_text = build_day_events(records)
    else:
        events_text = _empty_events_text()
    if not _ads_enabled() and footer_head:
        # ads off — keep only the channel-link part of the footer
        cut = footer_head.find(_ad_inquiry_marker())
        footer_head = footer_head[:cut].rstrip() if cut != -1 else footer_head
    if footer_head:
        events_text = f"{events_text}\n\n{footer_head}"
    await _send_photo_then_text(
        message, "day_img", welcome_config["day_header"], events_text
    )
    if _ads_enabled():
        await _send_ads(message, ad_header)
        await _send_footer_tail(message, footer_tail)


async def _send_grouped_screen(
    message: Message, image_key: str, records
) -> None:
    day_footer = welcome_config.get("day_footer", "")
    footer_head, ad_header, footer_tail = _split_footer(day_footer)
    if records:
        events_text = build_grouped_events(records)
    else:
        events_text = _empty_events_text()
    if not _ads_enabled() and footer_head:
        cut = footer_head.find(_ad_inquiry_marker())
        footer_head = footer_head[:cut].rstrip() if cut != -1 else footer_head
    if footer_head:
        events_text = f"{events_text}\n\n\n{footer_head}"
    await _send_photo_then_text(
        message, image_key, welcome_config["day_header"], events_text
    )
    if _ads_enabled():
        await _send_ads(message, ad_header)
        await _send_footer_tail(message, footer_tail)


@dp.callback_query(F.data == DAY_IN_HISTORY_CALLBACK)
async def on_day_in_history(callback: CallbackQuery) -> None:
    started = time.perf_counter()
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    await _clear_previous(callback.message.chat.id)
    try:
        records = await _get_records_cached(
            "day", _effective_today(), a_find_records_for_date
        )
        await _send_day_screen(callback.message, records)
        await _send_timing(callback.message, started)
    finally:
        await callback.answer()


@dp.callback_query(F.data == WEEK_EVENTS_CALLBACK)
async def on_week_events(callback: CallbackQuery) -> None:
    started = time.perf_counter()
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    uid = callback.from_user.id if callback.from_user else None
    if not await a_can_access_week(uid):
        await callback.answer("Доступно лише для преміум користувачів", show_alert=True)
        return
    await _clear_previous(callback.message.chat.id)
    try:
        records = await _get_records_cached(
            "week", _effective_today(), a_find_records_for_week
        )
        await _send_grouped_screen(callback.message, "week_img", records)
        await _send_timing(callback.message, started)
    finally:
        await callback.answer()


@dp.callback_query(F.data == MONTH_EVENTS_CALLBACK)
async def on_month_events(callback: CallbackQuery) -> None:
    started = time.perf_counter()
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    uid = callback.from_user.id if callback.from_user else None
    if not await a_can_access_month(uid):
        await callback.answer("Доступно лише для преміум користувачів", show_alert=True)
        return
    await _clear_previous(callback.message.chat.id)
    try:
        records = await _get_records_cached(
            "month", _effective_today(), a_find_records_for_month
        )
        await _send_grouped_screen(callback.message, "month_img", records)
        await _send_timing(callback.message, started)
    finally:
        await callback.answer()


async def _random_day_records() -> list:
    for _ in range(10):
        month = random.randint(1, 12)
        day = random.randint(1, 31)
        try:
            records = await a_find_records_for_date(
                date(_effective_today().year, month, day)
            )
            if records:
                return records
        except Exception:
            continue
    return await a_find_records_for_month(_effective_today())


@dp.callback_query(F.data == RANDOM_DAY_CALLBACK)
async def on_random_day(callback: CallbackQuery) -> None:
    started = time.perf_counter()
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    await _clear_previous(callback.message.chat.id)
    records = await _random_day_records()
    await _send_grouped_screen(callback.message, "random_date", records)
    await _send_timing(callback.message, started)
    await callback.answer()


def _resolve_button_command(text: str) -> str | None:
    """Map a (sticky) button text, incl. 🔒 suffix / line breaks, to a command kind."""

    def _norm(s: str) -> str:
        # drop ALL whitespace (newlines are inserted mid-word for mobile layout)
        return "".join(s.split()).lower()

    mapping = {
        _norm(welcome_config["random_day_text"]): "random",
        _norm(welcome_config["day_button_text"]): "day",
        _norm(welcome_config["week_button_text"]): "week",
        _norm(welcome_config["month_button_text"]): "month",
    }
    t = _norm(text)
    if t in mapping:
        return mapping[t]
    t = t.rstrip("🔒").strip()  # locked-button suffix
    return mapping.get(t)


@dp.message(F.text)
async def on_text(message: Message) -> None:
    started = time.perf_counter()
    _track_subscriber(message.from_user.id if message.from_user else None)
    if message.text.startswith("/"):
        return

    text = message.text.strip().lower()
    kind = _resolve_button_command(text)
    if kind is None:
        if text in TEXT_COMMANDS["day"]:
            kind = "day"
        elif text in TEXT_COMMANDS["week"]:
            kind = "week"
        elif text in TEXT_COMMANDS["month"]:
            kind = "month"

    if kind == "day":
        records = await _get_records_cached(
            "day", _effective_today(), a_find_records_for_date
        )
        await _send_day_screen(message, records)
        await _send_timing(message, started)
    elif kind == "week":
        if not await a_can_access_week(message.from_user.id if message.from_user else None):
            await message.answer(_premium_required_text(), parse_mode=ParseMode.HTML)
            return
        records = await _get_records_cached(
            "week", _effective_today(), a_find_records_for_week
        )
        await _send_grouped_screen(message, "week_img", records)
        await _send_timing(message, started)
    elif kind == "month":
        if not await a_can_access_month(message.from_user.id if message.from_user else None):
            await message.answer(_premium_required_text(), parse_mode=ParseMode.HTML)
            return
        records = await _get_records_cached(
            "month", _effective_today(), a_find_records_for_month
        )
        await _send_grouped_screen(message, "month_img", records)
        await _send_timing(message, started)
    elif kind == "random":
        records = await _random_day_records()
        await _send_grouped_screen(message, "random_date", records)
        await _send_timing(message, started)
    else:
        await _send_welcome(message)


async def main() -> None:
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())