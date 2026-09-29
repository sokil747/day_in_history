from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from core.models import Advertisement, Event
from google_sheets_service import download_ad_logo, get_advertisements, get_records


class Command(BaseCommand):
    help = "Sync Events and Advertisements from Google Sheets into DB (clears and reimports). Copies ad images to VPS media/."

    def handle(self, *args, **options):
        records = get_records()
        # Preserve per-row state (text_en, auto_publish) across syncs: match old
        # rows by (month, day, order, year, text) — sheet text unchanged => carry over.
        new_keys = set()
        old_state = {
            (e.month, e.day, e.order, e.year, e.text): (e.text_en, e.auto_publish)
            for e in Event.objects.all().iterator()
        }
        Event.objects.all().delete()
        objs = []
        for r in records:
            new_keys.add((r.month, r.day, r.order, r.year, r.text))
            state = old_state.get(
                (r.month, r.day, r.order, r.year, r.text), ("", False)
            )
            objs.append(
                Event(
                    month=r.month,
                    day=r.day,
                    order=r.order,
                    year=r.year,
                    emoji=r.emoji,
                    category=r.category,
                    text=r.text,
                    text_en=state[0],
                    auto_publish=state[1],
                    source=r.source,
                )
            )
        Event.objects.bulk_create(objs, batch_size=500)

        dropped = len(old_state) - len(new_keys & set(old_state))
        if dropped > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"{dropped} DB rows not matched in sheet; their "
                    "translations/auto_publish were dropped."
                )
            )
        self.stdout.write(self.style.SUCCESS(f"Imported {len(objs)} events."))

        ads = get_advertisements()
        Advertisement.objects.all().delete()
        # Create one-by-one to download images to logo_image
        count = 0
        for idx, a in enumerate(ads, start=1):
            ad = Advertisement(
                logo=a.logo,
                text=a.text,
                link=a.link,
                start_date=a.start_date,
                finish_date=a.finish_date,
            )
            ad.save()  # need PK before saving file
            if a.logo:
                try:
                    content = download_ad_logo(a.logo)
                    if content:
                        # guess extension from content or URL
                        suffix = ".jpg"
                        if ".png" in a.logo.lower():
                            suffix = ".png"
                        elif ".webp" in a.logo.lower():
                            suffix = ".webp"
                        ad.logo_image.save(f"ad_{ad.pk}{suffix}", ContentFile(content), save=True)
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Ad {idx}: failed to copy image {a.logo!r}: {e}"))
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Imported {count} advertisements (images copied to media/ads/)."))
