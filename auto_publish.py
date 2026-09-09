"""Daily auto-publish of 'Day in history' to a Telegram channel."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile, LinkPreviewOptions

import config
from formatting import build_day_events
from db_service import a_active_ads_on, a_find_records_for_date

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auto_publish")

NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)
MAX_CAPTION = 1000
MAX_MESSAGE = 4000


def _ad_inquiry_marker() -> str:
    return "Хочете розмістити рекламу"


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


def _ads_enabled() -> bool:
    import json

    with open("config.json", encoding="utf-8") as f:
        return bool(json.load(f).get("ads_enabled", False))


def _effective_today() -> date:
    import json

    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    test_mode = cfg.get("test_mode", False)
    test_day = cfg.get("test_day", "")
    if test_mode and test_day:
        try:
            from datetime import datetime as dt

            return dt.strptime(test_day, "%d/%m/%Y").date()
        except ValueError:
            pass
    return date.today()


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


def _balance_html(text: str) -> str:
    tags = re.findall(r"<([a-z][a-z0-9]*)(?:\s[^>]*)?>", text)
    open_tags = [
        t for t in tags if text.count(f"<{t}>") > text.count(f"</{t}>")
    ]
    for tag in reversed(open_tags):
        text = f"{text}</{tag}>"
    return text


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


def _day_caption(records, cfg: dict) -> str:
    day_footer = cfg.get("day_footer", "")
    footer_head, ad_header, footer_tail = _split_footer(day_footer)
    events_text = build_day_events(records) if records else (
        "За цей день подій немає."
    )
    if not _ads_enabled() and footer_head:
        cut = footer_head.find(_ad_inquiry_marker())
        footer_head = footer_head[:cut].rstrip() if cut != -1 else footer_head
    if footer_head:
        events_text = f"{events_text}\n\n{footer_head}"
    caption = cfg.get("day_header", "") + "\n\n" + events_text
    return _balance_html(caption), ad_header, footer_tail


def _ad_caption(ad, with_separator: bool) -> str:
    import html

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


async def publish_day(bot: Bot, channel: str, target: date | None = None) -> int:
    """Post today's (or given date's) day-in-history screen to the channel.

    Returns number of messages sent.
    """
    import json
    from pathlib import Path

    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)

    target = target or _effective_today()
    records = await a_find_records_for_date(target)
    events_caption, ad_header, footer_tail = _day_caption(records, cfg)

    sent_count = 0
    photo = cfg.get("day_img", "assets/day.jpg")
    if not Path(photo).exists():
        photo = None  # text-only fallback

    # Pack caption within Telegram's 1024-char photo-caption limit; overflow pages
    caption = events_caption
    pages: list[str] = []
    if len(caption) > MAX_CAPTION:
        pieces_full = _chunk_html(events_caption, MAX_CAPTION)
        caption = pieces_full[0]
        pages = pieces_full[1:]
    try:
        if photo:
            sent = await bot.send_photo(
                channel,
                FSInputFile(photo),
                caption=caption,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
            sent_count += 1
        else:
            sent = await bot.send_message(
                channel,
                caption,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
            sent_count += 1
    except Exception as exc:
        log.warning("Auto-publish: failed to send day screen: %s", exc)
        return sent_count

    for i, piece in enumerate(pages):
        try:
            await bot.send_message(
                channel, piece, parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
            sent_count += 1
        except Exception as exc:
            log.warning("Auto-publish: page send failed: %s", exc)

    # ads
    if _ads_enabled() and ad_header:
        try:
            ads = await a_active_ads_on(target)
            for i, ad in enumerate(ads):
                cap = _ad_caption(ad, with_separator=i > 0)
                if i == 0 and ad_header:
                    cap = f"{ad_header}\n\n{cap}"
                try:
                    await bot.send_message(
                        channel, cap, parse_mode=ParseMode.HTML,
                        link_preview_options=NO_LINK_PREVIEW,
                    )
                    sent_count += 1
                except Exception as exc:
                    log.warning("Auto-publish ad send failed: %s", exc)
        except Exception as exc:
            log.warning("Auto-publish ads load failed: %s", exc)

    if _ads_enabled() and footer_tail:
        try:
            await bot.send_message(
                channel, footer_tail, parse_mode=ParseMode.HTML,
                link_preview_options=NO_LINK_PREVIEW,
            )
            sent_count += 1
        except Exception as exc:
            log.warning("Auto-publish tail send failed: %s", exc)

    return sent_count


def has_auto_publish_events(target: date) -> bool:
    from core.models import Event  # django models

    return Event.objects.filter(
        month=target.month, day=target.day, auto_publish=True
    ).exists()


def should_publish_now(now: datetime) -> bool:
    from core.models import AutoPublishSettings

    s = AutoPublishSettings.get_solo()
    if not s.is_scheduled_now(now):
        return False
    return has_auto_publish_events(_effective_today())


def run_test(target: date | None = None) -> int:
    """Publish immediately ignoring time/days — returns messages sent."""
    import asyncio
    from core.models import AutoPublishSettings

    s = AutoPublishSettings.get_solo()
    bot = Bot(token=config.BOT_TOKEN)
    count = asyncio.run(publish_day(bot, s.channel, target))
    log.info("Auto-publish test sent %s messages to %s", count, s.channel)
    return count