"""Translate events without English text using local Gemma (Ollama)."""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.models import Event
from core import llm


class Command(BaseCommand):
    help = "Translate Event.text -> text_en via Gemma LLM for events missing translation."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Max events to translate this run")
        parser.add_argument("--model", type=str, default=None, help="Ollama model tag (default $GEMMA_MODEL)")
        parser.add_argument("--base-url", type=str, default=None, help="Ollama base URL (default $GEMMA_BASE_URL)")
        parser.add_argument("--quiet", action="store_true", help="Only print errors and final line")

    def handle(self, *args, **options):
        limit = options["limit"]
        quiet = options["quiet"]
        qs = Event.objects.filter(text_en="").order_by("id")
        if limit is not None:
            qs = qs[:limit]
        total = qs.count()
        translated = failed = 0
        for e in qs.iterator():
            try:
                en = llm.translate(e.text, model=options["model"], base_url=options["base_url"])
            except Exception as exc:
                failed += 1
                self.stderr.write(f"FAIL #{e.pk} {e.month:02d}/{e.day:02d}: {type(exc).__name__}: {exc}")
                continue
            if not en:
                failed += 1
                self.stderr.write(f"FAIL #{e.pk} {e.month:02d}/{e.day:02d}: empty response")
                continue
            e.text_en = en
            e.save(update_fields=["text_en"])
            translated += 1
            if not quiet:
                self.stdout.write(f"OK  #{e.pk} {e.month:02d}/{e.day:02d}: {e.text[:40]}... -> {en[:60]}...")
        if failed:
            raise CommandError(
                f"translate_events: {translated} translated, {failed} failed. Retried on next run."
            )
        self.stdout.write(self.style.SUCCESS(f"Translated {translated}/{total} events."))