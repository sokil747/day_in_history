from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import Event
from core import llm as llm_mod


class TranslateEventsCommandTests(TestCase):
    def setUp(self):
        self.e1 = Event.objects.create(month=9, day=21, order=1, text="укр раз")
        self.e2 = Event.objects.create(month=9, day=22, order=1, text="укр два")
        self.done = Event.objects.create(month=9, day=23, order=1, text="укр три", text_en="done")

    def test_translates_all_missing(self):
        calls = []

        def fake(text, model=None, base_url=None):
            calls.append(text)
            return f"EN[{text}]"

        out = StringIO()
        with llm_mod.override_translator(fake):
            call_command("translate_events", stdout=out)
        self.e1.refresh_from_db(); self.e2.refresh_from_db(); self.done.refresh_from_db()
        self.assertEqual(self.e1.text_en, "EN[укр раз]")
        self.assertEqual(self.e2.text_en, "EN[укр два]")
        self.assertEqual(self.done.text_en, "done")
        self.assertEqual(calls, ["укр раз", "укр два"])

    def test_failure_skips_and_raises_commanderror(self):
        def fake(text, model=None, base_url=None):
            if text == "укр раз":
                raise RuntimeError("ollama down")
            return "EN OK"

        out = StringIO()
        with llm_mod.override_translator(fake):
            with self.assertRaises(CommandError):
                call_command("translate_events", stdout=out)
        self.e1.refresh_from_db(); self.e2.refresh_from_db()
        self.assertEqual(self.e2.text_en, "EN OK")
        self.assertEqual(self.e1.text_en, "")

    def test_limit_option(self):
        calls = []

        def fake(text, model=None, base_url=None):
            calls.append(text)
            return "X"

        out = StringIO()
        with llm_mod.override_translator(fake):
            call_command("translate_events", limit=1, stdout=out)
        self.assertEqual(len(calls), 1)
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.text_en, "X")


class EventTextEnFieldTests(TestCase):
    def test_event_has_text_en_default_empty(self):
        e = Event.objects.create(month=9, day=21, order=1, text="Київській фортеці 180 років")
        fetched = Event.objects.get(pk=e.pk)
        self.assertEqual(fetched.text_en, "")

    def test_event_saves_translation(self):
        e = Event.objects.create(month=9, day=21, order=1, text="текст", text_en="text")
        fetched = Event.objects.get(pk=e.pk)
        self.assertEqual(fetched.text_en, "text")


class SyncPreservesTranslationsTests(TestCase):
    class _Rec:
        def __init__(self, month, day, order, year, emoji, category, text, source):
            self.month, self.day, self.order, self.year = month, day, order, year
            self.emoji, self.category, self.text, self.source = emoji, category, text, source

    @classmethod
    def setUpTestData(cls):
        cls.prev = Event.objects.create(
            month=9, day=21, order=1, year=1900, text="укр текст", text_en="kept en", auto_publish=True
        )

    def _call_sync(self, records):
        from unittest.mock import patch
        from core.management.commands import sync_from_sheets as mod
        import core.management.commands.sync_from_sheets as cmd_mod

        with patch.object(mod, "get_records", lambda: records), patch.object(
            cmd_mod.download_ad_logo, "__call__", lambda *a, **k: None
        ), patch.object(mod, "get_advertisements", lambda: []):
            call_command("sync_from_sheets", stdout=StringIO())

    def test_carry_over_when_text_unchanged(self):
        Event.objects.all().delete()
        Event.objects.create(month=9, day=21, order=1, year=1900, text="укр текст", text_en="kept en", auto_publish=True)
        Event.objects.create(month=9, day=22, order=1, year=1901, text="інший", text_en="other en")

        recs = [
            self._Rec(9, 21, 1, 1900, "🎉", "cat", "укр текст", ""),
            self._Rec(9, 22, 1, 1901, "🎈", "cat", "інший", ""),
        ]
        self._call_sync(recs)
        self.assertEqual(Event.objects.count(), 2)
        e = Event.objects.get(month=9, day=21)
        self.assertEqual(e.text_en, "kept en")
        self.assertEqual(e.auto_publish, True)
        self.assertEqual(Event.objects.get(month=9, day=22).text_en, "other en")

    def test_reset_when_text_changed(self):
        Event.objects.all().delete()
        Event.objects.create(month=9, day=21, order=1, year=1900, text="старий укр", text_en="old en")

        recs = [self._Rec(9, 21, 1, 1900, "🎉", "cat", "новий укр", "")]
        self._call_sync(recs)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(Event.objects.first().text_en, "")

    def test_new_and_removed_rows(self):
        Event.objects.all().delete()
        Event.objects.create(month=9, day=21, order=1, year=1900, text="тільки в дб", text_en="gone en")

        recs = [self._Rec(9, 22, 2, 1905, "🎈", "cat", "листок новий", "")]
        self._call_sync(recs)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(Event.objects.first().text, "листок новий")
        self.assertEqual(Event.objects.first().text_en, "")
        self.assertFalse(Event.objects.filter(text="тільки в дб").exists())


class EventAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User

        cls.admin = User.objects.create_superuser("a", "a@example.com", "pw")
        cls.translated = Event.objects.create(month=9, day=21, order=1, text="текст", text_en="text")
        cls.untranslated = Event.objects.create(month=9, day=22, order=1, text="ще текст")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_changelist_shows_text_en_column(self):
        resp = self.client.get("/admin/core/event/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Text (EN)")

    def test_filter_missing_translation(self):
        resp = self.client.get("/admin/core/event/?translation=missing")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ще текст")
        self.assertNotContains(resp, "ще інший")

    def test_filter_translated(self):
        resp = self.client.get("/admin/core/event/?translation=translated")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, ">текст<")
        self.assertNotContains(resp, "ще текст")