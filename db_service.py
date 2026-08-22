"""DB-backed replacements for google_sheets_service find_* helpers."""
from __future__ import annotations

import os
from datetime import date, timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "admin_site.settings")
import django

django.setup()

from core.models import Advertisement, Event, PremiumUser

from google_sheets_service import AdRecord, HistoryRecord, _parse_date


def _event_to_record(e: Event) -> HistoryRecord:
    return HistoryRecord(
        month=e.month,
        day=e.day,
        order=e.order,
        year=e.year,
        emoji=e.emoji,
        category=e.category,
        text=e.text,
        source=e.source,
    )


def find_records_for_date(target: date) -> list[HistoryRecord]:
    qs = Event.objects.filter(month=target.month, day=target.day).order_by("order", "month", "day")
    return [_event_to_record(e) for e in qs]


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
    qs = Event.objects.filter(month=anchor.month).order_by("month", "day", "order")
    records = [e for e in qs if (e.month, e.day) in pairs]
    return [_event_to_record(e) for e in records]


def find_records_for_month(anchor: date) -> list[HistoryRecord]:
    qs = Event.objects.filter(month=anchor.month).order_by("day", "order")
    return [_event_to_record(e) for e in qs]


def get_advertisements() -> list[AdRecord]:
    return [
        AdRecord(logo=a.logo, text=a.text, link=a.link, start_date=a.start_date, finish_date=a.finish_date)
        for a in Advertisement.objects.all()
    ]


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


def is_premium(user_id: int | None) -> bool:
    if not user_id:
        return False
    import config

    if user_id in getattr(config, "ADMIN_IDS", []):
        return True
    return PremiumUser.objects.filter(telegram_id=user_id).exists()


def _get_bot_settings():
    try:
        from core.models import BotSettings

        return BotSettings.get_solo()
    except Exception:
        return None


def week_requires_premium() -> bool:
    s = _get_bot_settings()
    return s.week_requires_premium if s else True


def month_requires_premium() -> bool:
    s = _get_bot_settings()
    return s.month_requires_premium if s else True


def can_access_week(user_id: int | None) -> bool:
    if not week_requires_premium():
        return True
    return is_premium(user_id)


def can_access_month(user_id: int | None) -> bool:
    if not month_requires_premium():
        return True
    return is_premium(user_id)


# Async wrappers for use in aiogram (async) handlers — avoids
# django.core.exceptions.SynchronousOnlyOperation
try:
    from asgiref.sync import sync_to_async as _sync_to_async
except ImportError:  # fallback if asgiref missing
    _sync_to_async = None

if _sync_to_async:
    a_find_records_for_date = _sync_to_async(find_records_for_date)
    a_find_records_for_week = _sync_to_async(find_records_for_week)
    a_find_records_for_month = _sync_to_async(find_records_for_month)
    a_active_ads_on = _sync_to_async(active_ads_on)
    a_is_premium = _sync_to_async(is_premium)
    a_can_access_week = _sync_to_async(can_access_week)
    a_can_access_month = _sync_to_async(can_access_month)
    a_week_requires_premium = _sync_to_async(week_requires_premium)
    a_month_requires_premium = _sync_to_async(month_requires_premium)
    a_get_bot_settings = _sync_to_async(_get_bot_settings)
else:
    a_find_records_for_date = find_records_for_date  # type: ignore
    a_find_records_for_week = find_records_for_week  # type: ignore
    a_find_records_for_month = find_records_for_month  # type: ignore
    a_active_ads_on = active_ads_on  # type: ignore
    a_is_premium = is_premium  # type: ignore
    a_can_access_week = can_access_week  # type: ignore
    a_can_access_month = can_access_month  # type: ignore
    a_week_requires_premium = week_requires_premium  # type: ignore
    a_month_requires_premium = month_requires_premium  # type: ignore
    a_get_bot_settings = _get_bot_settings  # type: ignore
