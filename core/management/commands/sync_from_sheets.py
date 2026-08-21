from django.core.management.base import BaseCommand

from core.models import Advertisement, Event
from google_sheets_service import get_advertisements, get_records


class Command(BaseCommand):
    help = "Sync Events and Advertisements from Google Sheets into DB (clears and reimports)."

    def handle(self, *args, **options):
        records = get_records()
        Event.objects.all().delete()
        objs = [
            Event(
                month=r.month,
                day=r.day,
                order=r.order,
                year=r.year,
                emoji=r.emoji,
                category=r.category,
                text=r.text,
                source=r.source,
            )
            for r in records
        ]
        Event.objects.bulk_create(objs, batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Imported {len(objs)} events."))

        ads = get_advertisements()
        Advertisement.objects.all().delete()
        ad_objs = [
            Advertisement(
                logo=a.logo,
                text=a.text,
                link=a.link,
                start_date=a.start_date,
                finish_date=a.finish_date,
            )
            for a in ads
        ]
        Advertisement.objects.bulk_create(ad_objs)
        self.stdout.write(self.style.SUCCESS(f"Imported {len(ad_objs)} advertisements."))
