import asyncio
import html
import json
import logging
import re
import random
import time
from datetime import date, datetime
from pathlib import Path

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

from asgiref.sync import sync_to_async
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
from core.models import AutoPublishSettings
from google_sheets_service import AdRecord, download_ad_logo
import auto_publish
import stats_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

with open("config.json", encoding="utf-8") as _f:
    welcome_config = json.load(_f)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

START_CALLBACK = "start"
START_UK_CALLBACK = "start_uk"
START_EN_CALLBACK = "start_en"
LANG_TOGGLE_CALLBACK = "lang_toggle"
DAY_IN_HISTORY_CALLBACK = "day_in_history"
WEEK_EVENTS_CALLBACK = "week_events"
MONTH_EVENTS_CALLBACK = "month_events"
RANDOM_DAY_CALLBACK = "random_day"
BACK_CALLBACK = "back_to_main"
READ_NEXT_CALLBACK = "read_next"

TEXT_COMMANDS = {
    "day": {"day in history", "день в історії", "день"},
    "week": {"week in history", "important events of the week", "важлі події тижня", "важливі події тижня", "тиждень"},
    "month": {"month in history", "important events of the month", "важливі події місяця", "місяць"},
}

SERVICE_STRINGS = {
    "uk": {
        "premium_only": "🔒 Ця функція доступна лише для <b>преміум</b> користувачів.\nЗверніться до адміністратора, щоб отримати доступ.",
        "premium_alert": "Доступно лише для преміум користувачів",
        "dev": None,  # from config
        "no_pages": "Більше немає сторінок",
        "empty": (
            "За цей період нічого цікавого не сталося, "
            "але більше цікавої інформації ви можете знайти на нашому каналі:\n\n"
        ),
        "placeholder": "Оберіть кнопку ⤵️",
    },
    "en": {
        "premium_only": "🔒 This feature is available to <b>premium</b> users only.\nContact the administrator to get access.",
        "premium_alert": "Available to premium users only",
        "dev": None,  # from config
        "no_pages": "No more pages",
        "empty": (
            "Nothing interesting happened in this period, "
            "but you can find more interesting information on our channel:\n\n"
        ),
        "placeholder": "Pick a button ⬇️",
    },
}

chat_responses: dict[int, list[int]] = {}

# Pending "Читати далі" pages: chat_id -> list of remaining page texts
read_next_pages: dict[int, list[str]] = {}

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


def _dev_mode_config() -> dict:
    cfg = welcome_config.get("dev_mode", {})
    return cfg if isinstance(cfg, dict) else {}


def _dev_message(lang: str = "uk") -> str:
    dev_en = _dev_mode_config().get("message_en")
    if lang == "en" and dev_en:
        return dev_en
    return _dev_mode_config().get(
        "message", "🚧 Ця функція в розробці. Скоро буде доступна!"
    )


def _is_dev_button(kind: str) -> bool:
    return bool(_dev_mode_config().get("buttons", {}).get(kind, False))


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


def _cfg(key: str, lang: str = "uk") -> str:
    if lang == "en":
        return welcome_config.get(f"{key}_en") or welcome_config.get(key, "")
    return welcome_config.get(key, "")


def _cfg_img(key: str, lang: str = "uk") -> str:
    """Image path by language: key_uk/key_en, falling back to plain key;
    if the chosen file is missing on disk, fall back to the UK variant."""
    from pathlib import Path

    candidates = (
        [f"{key}_en", key, f"{key}_uk"]
        if lang == "en"
        else [f"{key}_uk", key, f"{key}_en"]
    )
    for cand_key in candidates:
        path = welcome_config.get(cand_key)
        if path and Path(path).exists():
            return path
    return welcome_config.get(f"{key}_uk") or welcome_config.get(key, "")


def _svc(key: str, lang: str) -> str:
    val = SERVICE_STRINGS[lang].get(key)
    if val is None and key == "dev":
        return _dev_message(lang)
    return val if val is not None else ""


async def _build_keyboard(user_id: int | None = None, lang: str = "uk"):
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
        _cfg("week_button_text", lang) if week_ok else _locked_text(_cfg("week_button_text", lang))
    )
    month_text = (
        _cfg("month_button_text", lang) if month_ok else _locked_text(_cfg("month_button_text", lang))
    )

    # bottom row: language toggle
    toggle_row: list = [
        InlineKeyboardButton(text=_cfg("lang_toggle_text", "uk"), callback_data=LANG_TOGGLE_CALLBACK)
    ]

    if _sticky_enabled():
        # narrow mobile buttons: split the long word so the emoji is not
        # alone on the first line — «🎲 Випадко⏎вий день» (uk only)
        random_text = _cfg("random_day_text", lang)
        if lang == "uk":
            random_text = random_text.replace("Випадковий", "Випадко\nвий")
        buttons = [
            KeyboardButton(text=random_text),
            KeyboardButton(text=_cfg("day_button_text", lang)),
        ]
        if show_week:
            buttons.append(KeyboardButton(text=week_text))
        if show_month:
            buttons.append(KeyboardButton(text=month_text))
        return ReplyKeyboardMarkup(
            keyboard=[buttons],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder=_svc("placeholder", lang),
        )

    rows = [
        [
            InlineKeyboardButton(
                text=_cfg("random_day_text", lang),
                callback_data=RANDOM_DAY_CALLBACK,
            )
        ],
        [
            InlineKeyboardButton(
                text=_cfg("day_button_text", lang),
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
    rows.append(toggle_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _premium_required_text(lang: str = "uk") -> str:
    return _svc("premium_only", lang)


def _back_button_text(lang: str = "uk") -> str:
    return _cfg("back_to_menu_text", lang)


def _read_next_enabled() -> bool:
    return bool(welcome_config.get("read_next_enabled", False))


def _read_next_text(lang: str = "uk") -> str:
    return _cfg("read_next_text", lang)


def _read_next_keyboard(chat_id: int, lang: str = "uk") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if read_next_pages.get(chat_id):
        rows.append(
            [InlineKeyboardButton(text=_read_next_text(lang), callback_data=READ_NEXT_CALLBACK)]
        )
    # «Назад в Головне меню» sits directly under the En-Ua switcher text-wise:
    # toggle row above the back row, both last — bottom of the screen message
    rows.append(
        [InlineKeyboardButton(text=_cfg("lang_toggle_text", "uk"), callback_data=LANG_TOGGLE_CALLBACK)]
    )
    rows.append(
        [InlineKeyboardButton(text=_back_button_text(lang), callback_data=BACK_CALLBACK)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    # clicked read-next strips its row; keep toggle under-switcher order there too
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_cfg("lang_toggle_text", "uk"),
                    callback_data=LANG_TOGGLE_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text=_back_button_text(lang),
                    callback_data=BACK_CALLBACK,
                )
            ],
        ]
    )


def _user_lang_sync(user_id: int | None) -> str:
    from core.models import UserLang

    return UserLang.get_lang(user_id)


async def _user_lang(user_id: int | None) -> str:
    return await sync_to_async(_user_lang_sync)(user_id)


def _welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_cfg("start_button_uk_text", "uk"),
                    callback_data=START_UK_CALLBACK,
                ),
                InlineKeyboardButton(
                    text=_cfg("start_button_en_text", "uk"),
                    callback_data=START_EN_CALLBACK,
                ),
            ]
        ]
    )


async def _send_welcome(message: Message, lang: str = "uk") -> None:
    user = message.from_user
    footer = _admin_footer(user.id if user else None, (user.full_name or user.username) if user else None)
    caption = (
        f"{_cfg('welcome_text', lang)}\n\n{_cfg('welcome_footer', lang)}{footer}"
    )
    try:
        await message.answer_photo(
            FSInputFile(_cfg_img("welcome_img", lang)),
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


@dp.callback_query(F.data.in_({START_UK_CALLBACK, START_EN_CALLBACK}))
async def on_start_lang(callback: CallbackQuery) -> None:
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    uid = callback.from_user.id if callback.from_user else None
    lang = "en" if callback.data == START_EN_CALLBACK else "uk"
    if uid:
        from core.models import UserLang

        await sync_to_async(UserLang.set_lang)(uid, lang)
    kb = await _build_keyboard(uid, lang)
    try:
        await callback.message.answer_photo(
            FSInputFile(_cfg_img("about_img", lang)),
            caption=_cfg("about_text", lang),
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logging.warning("Failed to send about image: %s", exc)
        await callback.message.answer(
            _cfg("about_text", lang),
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    finally:
        await callback.answer()


@dp.callback_query(F.data == START_CALLBACK)
async def on_start(callback: CallbackQuery) -> None:
    # legacy single-start callback: open in saved or default language
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    uid = callback.from_user.id if callback.from_user else None
    lang = await _user_lang(uid)
    kb = await _build_keyboard(uid, lang)
    try:
        await callback.message.answer_photo(
            FSInputFile(_cfg_img("about_img", lang)),
            caption=_cfg("about_text", lang),
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logging.warning("Failed to send about image: %s", exc)
        await callback.message.answer(
            _cfg("about_text", lang),
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    finally:
        await callback.answer()


@dp.callback_query(F.data == LANG_TOGGLE_CALLBACK)
async def on_lang_toggle(callback: CallbackQuery) -> None:
    uid = callback.from_user.id if callback.from_user else None
    if uid:
        from core.models import UserLang

        current = await _user_lang(uid)
        new_lang = await sync_to_async(UserLang.set_lang)(uid, "en" if current == "uk" else "uk")
    else:
        new_lang = "uk"
    kb = await _build_keyboard(uid, new_lang)
    try:
        await callback.message.answer_photo(
            FSInputFile(_cfg_img("about_img", new_lang)),
            caption=_cfg("about_text", new_lang),
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logging.warning("Failed to send about image: %s", exc)
        await callback.message.answer(
            _cfg("about_text", new_lang),
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    finally:
        await callback.answer()


async def _clear_previous(chat_id: int) -> None:
    read_next_pages.pop(chat_id, None)
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


def _balance_html(text: str) -> str:
    tags = re.findall(r"<([a-z][a-z0-9]*)(?:\s[^>]*)?>", text)
    open_tags = [
        t for t in tags if text.count(f"<{t}>") > text.count(f"</{t}>")
    ]
    for tag in reversed(open_tags):
        text = f"{text}</{tag}>"
    return text


async def _send_photo_then_text(
    message: Message, image_key: str, caption: str, body: str, reply_markup=None,
    lang: str = "uk",
) -> None:
    full_caption = f"{caption}\n\n{body}" if body else caption
    try:
        if len(full_caption) <= MAX_CAPTION:
            sent = await message.answer_photo(
                FSInputFile(_cfg_img(image_key, lang)),
                caption=full_caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
            _remember(message.chat.id, sent)
            return
        # Pagination: pack as many event lines into the photo caption as fit,
        # continue the rest in numbered follow-up messages.
        budget = MAX_CAPTION - len(caption) - 2
        lines = body.split("\n") if body else []
        keep: list[str] = []
        used = 0
        for line in lines:
            add = len(line) + (1 if keep else 0)
            if used + add > budget:
                break
            keep.append(line)
            used += add
        caption_part = f"{caption}\n\n" + "\n".join(keep) if keep else caption
        rest = "\n".join(lines[len(keep):]) if len(keep) < len(lines) else ""
        pieces = _chunk_html(rest, MAX_MESSAGE) if rest else []
        total = 1 + len(pieces)
        if total > 1:
            caption_part = _balance_html(caption_part) + f"\n\n📄 1/{total}"
        if pieces and _read_next_enabled():
            read_next_pages[message.chat.id] = [
                f"📄 {i + 2}/{total}\n\n{p}" for i, p in enumerate(pieces)
            ]
            sent = await message.answer_photo(
                FSInputFile(_cfg_img(image_key, lang)),
                caption=caption_part,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
            _remember(message.chat.id, sent)
            return
        sent = await message.answer_photo(
            FSInputFile(_cfg_img(image_key, lang)),
            caption=caption_part,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_LINK_PREVIEW,
        )
        _remember(message.chat.id, sent)
        for i, piece in enumerate(pieces):
            _remember(
                message.chat.id,
                await message.answer(
                    f"📄 {i + 2}/{total}\n\n{piece}",
                    parse_mode=ParseMode.HTML,
                    link_preview_options=NO_LINK_PREVIEW,
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


def _channel_footer(lang: str = "uk") -> str:
    return _cfg("welcome_footer", lang)


def _empty_events_text(lang: str = "uk") -> str:
    return f"{_svc('empty', lang)}{_channel_footer(lang)}"


async def _send_day_screen(message: Message, records, lang: str = "uk") -> None:
    day_footer = _cfg("day_footer", lang)
    footer_head, ad_header, footer_tail = _split_footer(day_footer)
    if records:
        events_text = build_day_events(records, lang=lang)
    else:
        events_text = _empty_events_text(lang)
    if not _ads_enabled() and footer_head:
        # ads off — keep only the channel-link part of the footer
        marker = _ad_inquiry_marker(lang)
        cut = footer_head.find(marker)
        footer_head = footer_head[:cut].rstrip() if cut != -1 else footer_head
    if footer_head:
        events_text = f"{events_text}\n\n{footer_head}"
    await _send_photo_then_text(
        message, "day_img", _cfg("day_header", lang), events_text, lang=lang
    )
    if _ads_enabled():
        await _send_ads(message, ad_header)
        await _send_footer_tail(message, footer_tail)
    await _send_back_button(message, lang)


async def _send_grouped_screen(
    message: Message, image_key: str, records, lang: str = "uk"
) -> None:
    day_footer = _cfg("day_footer", lang)
    footer_head, ad_header, footer_tail = _split_footer(day_footer)
    if records:
        events_text = build_grouped_events(records, lang=lang)
    else:
        events_text = _empty_events_text(lang)
    if not _ads_enabled() and footer_head:
        marker = _ad_inquiry_marker(lang)
        cut = footer_head.find(marker)
        footer_head = footer_head[:cut].rstrip() if cut != -1 else footer_head
    if footer_head:
        events_text = f"{events_text}\n\n\n{footer_head}"
    await _send_photo_then_text(
        message, image_key, _cfg("day_header", lang), events_text, lang=lang
    )
    if _ads_enabled():
        await _send_ads(message, ad_header)
        await _send_footer_tail(message, footer_tail)
    await _send_back_button(message, lang)


def _ad_inquiry_marker(lang: str = "uk") -> str:
    return "Want to place an ad" if lang == "en" else "Хочете розмістити рекламу"


async def _send_back_button(message: Message, lang: str = "uk") -> None:
    # attach back button to the last message of the screen — no extra message
    ids = chat_responses.get(message.chat.id) or []
    if not ids:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=ids[-1],
            reply_markup=_read_next_keyboard(message.chat.id, lang),
        )
    except TelegramBadRequest as exc:
        logging.warning("Failed to attach back button: %s", exc)


@dp.callback_query(F.data == READ_NEXT_CALLBACK)
async def on_read_next(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id
    queue = read_next_pages.get(chat_id)
    lang = await _user_lang(callback.from_user.id if callback.from_user else None)
    if not queue:
        await callback.answer(_svc("no_pages", lang), show_alert=True)
        return
    piece = queue.pop(0)
    # every revealed page carries «Назад»; «Читати далі» only while pages remain
    kb = _read_next_keyboard(chat_id, lang)
    sent = await callback.message.answer(
        piece,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_LINK_PREVIEW,
        reply_markup=kb,
    )
    _remember(chat_id, sent)
    if not queue:
        read_next_pages.pop(chat_id, None)
    # strip the read-next row from the clicked message (keep its back row)
    try:
        await callback.message.edit_reply_markup(reply_markup=_back_keyboard(lang))
    except TelegramBadRequest:
        pass
    await callback.answer()


@dp.callback_query(F.data == BACK_CALLBACK)
async def on_back_to_main(callback: CallbackQuery) -> None:
    # delete everything the events screen produced, incl. the back-button msg
    await _clear_previous(callback.message.chat.id)
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await callback.answer()


@dp.callback_query(F.data == DAY_IN_HISTORY_CALLBACK)
async def on_day_in_history(callback: CallbackQuery) -> None:
    started = time.perf_counter()
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    lang = await _user_lang(callback.from_user.id if callback.from_user else None)
    await _clear_previous(callback.message.chat.id)
    try:
        records = await _get_records_cached(
            "day", _effective_today(), a_find_records_for_date
        )
        await _send_day_screen(callback.message, records, lang)
        await _send_timing(callback.message, started)
    finally:
        await callback.answer()


@dp.callback_query(F.data == WEEK_EVENTS_CALLBACK)
async def on_week_events(callback: CallbackQuery) -> None:
    started = time.perf_counter()
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    uid = callback.from_user.id if callback.from_user else None
    lang = await _user_lang(uid)
    if _is_dev_button("week") and uid not in config.ADMIN_IDS:
        await callback.answer(_dev_message(lang), show_alert=True)
        return
    if not await a_can_access_week(uid):
        await callback.answer(_svc("premium_alert", lang), show_alert=True)
        return
    await _clear_previous(callback.message.chat.id)
    try:
        records = await _get_records_cached(
            "week", _effective_today(), a_find_records_for_week
        )
        await _send_grouped_screen(callback.message, "week_img", records, lang)
        await _send_timing(callback.message, started)
    finally:
        await callback.answer()


@dp.callback_query(F.data == MONTH_EVENTS_CALLBACK)
async def on_month_events(callback: CallbackQuery) -> None:
    started = time.perf_counter()
    _track_subscriber(callback.from_user.id if callback.from_user else None)
    uid = callback.from_user.id if callback.from_user else None
    lang = await _user_lang(uid)
    if _is_dev_button("month") and uid not in config.ADMIN_IDS:
        await callback.answer(_dev_message(lang), show_alert=True)
        return
    if not await a_can_access_month(uid):
        await callback.answer(_svc("premium_alert", lang), show_alert=True)
        return
    await _clear_previous(callback.message.chat.id)
    try:
        records = await _get_records_cached(
            "month", _effective_today(), a_find_records_for_month
        )
        await _send_grouped_screen(callback.message, "month_img", records, lang)
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
    lang = await _user_lang(callback.from_user.id if callback.from_user else None)
    await _clear_previous(callback.message.chat.id)
    records = await _random_day_records()
    await _send_grouped_screen(callback.message, "random_date", records, lang)
    await _send_timing(callback.message, started)
    await callback.answer()


def _resolve_button_command(text: str, lang: str = "uk") -> str | None:
    """Map a (sticky) button text, incl. 🔒 suffix / line breaks, to a command kind."""

    def _norm(s: str) -> str:
        # drop ALL whitespace (newlines are inserted mid-word for mobile layout)
        return "".join(s.split()).lower().replace("🔒", "")

    mapping = {}
    for key, kind in (
        ("random_day_text", "random"),
        ("day_button_text", "day"),
        ("week_button_text", "week"),
        ("month_button_text", "month"),
    ):
        for l in ("uk", "en"):
            val = _cfg(key, l)
            if val:
                mapping[_norm(val)] = kind
    t = _norm(text)
    return mapping.get(t)


@dp.message(F.text)
async def on_text(message: Message) -> None:
    started = time.perf_counter()
    _track_subscriber(message.from_user.id if message.from_user else None)
    uid = message.from_user.id if message.from_user else None
    lang = await _user_lang(uid)
    if message.text.startswith("/"):
        return

    text = message.text.strip().lower()
    kind = _resolve_button_command(text, lang)
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
        await _send_day_screen(message, records, lang)
        await _send_timing(message, started)
    elif kind == "week":
        if _is_dev_button("week") and uid not in config.ADMIN_IDS:
            await message.answer(_dev_message(lang), parse_mode=ParseMode.HTML)
            return
        if not await a_can_access_week(uid):
            await message.answer(_svc("premium_only", lang), parse_mode=ParseMode.HTML)
            return
        records = await _get_records_cached(
            "week", _effective_today(), a_find_records_for_week
        )
        await _send_grouped_screen(message, "week_img", records, lang)
        await _send_timing(message, started)
    elif kind == "month":
        if _is_dev_button("month") and uid not in config.ADMIN_IDS:
            await message.answer(_dev_message(lang), parse_mode=ParseMode.HTML)
            return
        if not await a_can_access_month(uid):
            await message.answer(_svc("premium_only", lang), parse_mode=ParseMode.HTML)
            return
        records = await _get_records_cached(
            "month", _effective_today(), a_find_records_for_month
        )
        await _send_grouped_screen(message, "month_img", records, lang)
        await _send_timing(message, started)
    elif kind == "random":
        records = await _random_day_records()
        await _send_grouped_screen(message, "random_date", records, lang)
        await _send_timing(message, started)
    else:
        await _send_welcome(message)


async def _auto_publish_loop() -> None:
    """Plan once per day's screen for the configured publish time.

    Resilient to restarts: after waking (startup or 00:01 check) it plans
    today's publish if it hasn't been sent yet (state file per date); if the
    planned time already passed while the bot was down — publish immediately.
    """
    import logging
    from asgiref.sync import sync_to_async
    from datetime import datetime as dt, timedelta

    state_dir = Path("data/auto_publish")
    state_dir.mkdir(parents=True, exist_ok=True)
    sent_marker = state_dir / "last_sent_date"

    def _already_sent(target: date) -> bool:
        try:
            return sent_marker.read_text().strip() == target.isoformat()
        except OSError:
            return False

    def _mark_sent(target: date) -> None:
        sent_marker.write_text(target.isoformat())

    log.info("Auto-publish scheduler started (daily check at 00:01 UTC)")
    while True:
        try:
            now_utc = dt.utcnow()
            settings = await sync_to_async(AutoPublishSettings.get_solo)()
            target = _effective_today()
            has_events = await sync_to_async(auto_publish.has_auto_publish_events)(target)
            today_planned = now_utc.replace(
                hour=settings.publish_time.hour,
                minute=settings.publish_time.minute,
                second=0,
                microsecond=0,
            )

            if has_events and settings.enabled and not _already_sent(target):
                wait_publish = (today_planned - now_utc).total_seconds()
                if wait_publish > 0:
                    log.info("Auto-publish: planned for %s -> %s", today_planned, settings.channel)
                    await asyncio.sleep(wait_publish)
                    # settings/flags may have changed during the wait — re-check
                    settings = await sync_to_async(AutoPublishSettings.get_solo)()
                    has_events = await sync_to_async(auto_publish.has_auto_publish_events)(target)
                    if not has_events or not settings.enabled or _already_sent(target):
                        continue
                await auto_publish.publish_day(bot, settings.channel, target)
                _mark_sent(target)
                log.info("Auto-publish: sent to %s for %s", settings.channel, target)

            # sleep until next 00:01 UTC check
            now_utc = dt.utcnow()
            check_at = now_utc.replace(hour=0, minute=1, second=0, microsecond=0)
            if now_utc >= check_at:
                check_at += timedelta(days=1)
            wait_s = (check_at - now_utc).total_seconds()
            log.info("Auto-publish: next daily check at %s (in %.0f s)", check_at, wait_s)
            await asyncio.sleep(max(wait_s, 1))
        except Exception as exc:
            log.warning("Auto-publish loop error: %s", exc)
            await asyncio.sleep(60)


async def main() -> None:
    from core.translate_watch import watch_loop

    asyncio.create_task(watch_loop())
    asyncio.create_task(_auto_publish_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())