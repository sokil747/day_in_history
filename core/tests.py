from io import StringIO
import time
import threading
from pathlib import Path
import urllib.error
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import Event
from core import llm as llm_mod


class StripHashtagsTests(TestCase):
    def test_prompt_forbids_hashtags(self):
        self.assertIn("hashtag", llm_mod._system_prompt.lower())

    def test_removes_trailing_hashtag_block(self):
        self.assertEqual(
            llm_mod.strip_hashtags("Kyiv Fortress celebrates 180 years #Kyiv #Ukraine #History"),
            "Kyiv Fortress celebrates 180 years",
        )

    def test_removes_hashtags_inside_text(self):
        self.assertEqual(
            llm_mod.strip_hashtags("Great event #history happened today"),
            "Great event happened today",
        )

    def test_keeps_text_without_hashtags(self):
        self.assertEqual(llm_mod.strip_hashtags("Plain sentence."), "Plain sentence.")

    def test_keeps_html_entities(self):
        src = 'Founded &lt;b&gt;1900&lt;/b&gt; #retro'
        self.assertEqual(llm_mod.strip_hashtags(src), "Founded &lt;b&gt;1900&lt;/b&gt;")


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


class UserLangTests(TestCase):
    def test_default_uk_and_get_or_create(self):
        from core.models import UserLang

        lang = UserLang.get_lang(12345)
        self.assertEqual(lang, "uk")
        self.assertEqual(UserLang.get_lang(12345), "uk")
        UserLang.set_lang(12345, "en")
        self.assertEqual(UserLang.get_lang(12345), "en")
        self.assertEqual(UserLang.objects.filter(telegram_id=12345).count(), 1)

    def test_returns_uk_for_none_user(self):
        from core.models import UserLang

        self.assertEqual(UserLang.get_lang(None), "uk")
        self.assertEqual(UserLang.set_lang(None, "en"), "uk")
        self.assertEqual(UserLang.objects.count(), 0)


class HistoryRecordTextEnTests(TestCase):
    def test_find_records_for_date_includes_text_en(self):
        from db_service import find_records_for_date
        import datetime

        Event.objects.create(month=9, day=21, order=1, text="укр", text_en="en")
        records = find_records_for_date(datetime.date(2026, 9, 21))
        self.assertEqual(records[0].text, "укр")
        self.assertEqual(records[0].text_en, "en")

    def test_find_records_for_month_and_week_carry_text_en(self):
        from db_service import find_records_for_month, find_records_for_week
        import datetime

        Event.objects.create(month=9, day=21, order=1, text="укр", text_en="en1")
        Event.objects.create(month=9, day=22, order=1, text="укр2", text_en="en2")
        month = find_records_for_month(datetime.date(2026, 9, 15))
        self.assertEqual({r.text_en for r in month}, {"en1", "en2"})
        week = find_records_for_week(datetime.date(2026, 9, 21))
        self.assertEqual({r.text_en for r in week}, {"en1", "en2"})


class FormattingLangTests(TestCase):
    @staticmethod
    def _rec(month, day, order, year, text, text_en, emoji="🎉", source=""):
        from google_sheets_service import HistoryRecord

        return HistoryRecord(
            month=month, day=day, order=order, year=year,
            emoji=emoji, category="cat", text=text, source=source, text_en=text_en,
        )

    def test_day_events_uk_default_unchanged(self):
        from formatting import build_day_events

        out = build_day_events([self._rec(9, 21, 1, 1905, "Україна стає незалежною", "Ukraine becomes independent")])
        self.assertIn("21 вересня", out)
        self.assertIn("україна стає незалежною", out)  # existing behavior: 1st letter lowered after prefix
        self.assertIn("У 1905 році", out)

    def test_day_events_en_uses_translation(self):
        from formatting import build_day_events

        out = build_day_events(
            [self._rec(9, 21, 1, 1905, "Україна стає незалежною", "Ukraine becomes independent", source="https://x.org")],
            lang="en",
        )
        self.assertIn("21 September", out)
        self.assertIn("ukraine becomes independent", out)  # existing behavior: 1st letter lowered after prefix
        self.assertIn("In 1905", out)
        self.assertIn("In 1905", out)

    def test_en_falls_back_to_ukrainian_when_no_translation(self):
        from formatting import build_day_events

        out = build_day_events(
            [self._rec(9, 21, 1, 0, "немає перекладу", "")], lang="en"
        )
        self.assertIn("немає перекладу", out)

    def test_grouped_events_en(self):
        from formatting import build_grouped_events

        out = build_grouped_events(
            [self._rec(9, 21, 1, 1905, "текст", "text"), self._rec(9, 22, 1, 1906, "т2", "t2")],
            lang="en",
        )
        self.assertIn("21 September", out)
        self.assertIn("22 September", out)
        self.assertIn("In 1906 t2", out)


class TranslationJobTests(TestCase):
    def setUp(self):
        import tempfile

        from core import translation_job

        self._job = translation_job
        self._orig_state = translation_job.STATE_DIR
        self._tmp = tempfile.mkdtemp()
        translation_job.STATE_DIR = Path(self._tmp)
        # patch module-level path constants to point at temp dir
        self._files = {}
        for name in ("RUNNING_FILE", "STOP_FILE", "DONE_FILE", "FAIL_FILE", "TOTAL_FILE", "LOG_FILE"):
            f = getattr(translation_job, name)
            self._files[name] = f
            setattr(translation_job, name, Path(self._tmp) / f.name)

    def tearDown(self):
        self._job.STATE_DIR = self._orig_state
        for name, f in self._files.items():
            setattr(self._job, name, f)

    def _run(self):
        import threading

        from core import llm as llm_mod

        result = {}

        def fake_translate(text, model=None, base_url=None):
            result["translated_text"] = text
            return f"EN:{text}"

        with patch.object(llm_mod, "translate", fake_translate):
            runner = self._job._Runner()
            runner.run()

        return result

    def test_run_translates_missing_only(self):
        Event.objects.create(month=9, day=21, order=1, text="a", text_en="")
        Event.objects.create(month=9, day=22, order=1, text="b", text_en="keep")
        self._run()
        self.assertEqual(Event.objects.get(month=9, day=21).text_en, "EN:a")
        self.assertEqual(Event.objects.get(month=9, day=22).text_en, "keep")

    def test_run_updates_progress_files(self):
        Event.objects.create(month=9, day=21, order=1, text="a")
        Event.objects.create(month=9, day=22, order=1, text="b")
        self._run()
        self.assertEqual((Path(self._tmp) / "total").read_text(), "2")
        self.assertEqual((Path(self._tmp) / "done").read_text(), "2")
        self.assertFalse((Path(self._tmp) / "running").exists())

    def test_stop_stops_between_events_and_cleans_files(self):
        from core import llm as llm_mod

        Event.objects.create(month=9, day=21, order=1, text="a")
        Event.objects.create(month=9, day=22, order=1, text="b")
        calls = []

        def fake(text, model=None, base_url=None):
            calls.append(text)
            if len(calls) == 1:
                # user presses Stop after first event done
                (Path(self._tmp) / "stop").write_text("1")
            return "EN"

        with patch.object(llm_mod, "translate", fake):
            runner = self._job._Runner()
            runner.run()
        self.assertEqual(len(calls), 1)  # second event never attempted
        self.assertFalse((Path(self._tmp) / "running").exists())
        self.assertFalse((Path(self._tmp) / "stop").exists())
        self.assertEqual((Path(self._tmp) / "done").read_text(), "1")

    def test_status_reports_totals_and_eta_fields(self):
        Event.objects.create(month=9, day=21, order=1, text="a")
        self._run()
        # after finish: running cleared; status reports not running
        st = self._job.read_status()
        self.assertFalse(st["running"])
        self.assertEqual(st["total"], 1)

    def test_start_in_background_refuses_second_start(self):
        import time as _t
        from unittest.mock import patch as _patch

        started = threading.Event()

        class FakeRunner:
            def run(self, model=None, base_url=None, skip_setup=False):
                started.set()
                from core import translation_job as tj

                tj._write(tj.RUNNING_FILE, "1")  # like real runner
                _t.sleep(0.4)
                tj.RUNNING_FILE.unlink(missing_ok=True)  # like real runner finally-block

        with _patch.object(self._job, "_Runner", FakeRunner):
            ok = self._job.start_in_background()
            self.assertTrue(ok)
            self.assertTrue(started.wait(3))
            deadline = _t.time() + 3
            while not self._job.is_running() and _t.time() < deadline:
                _t.sleep(0.02)
            # second start refused while job active
            self.assertFalse(self._job.start_in_background())
        deadline = _t.time() + 5
        while self._job.is_running() and _t.time() < deadline:
            _t.sleep(0.05)
        self.assertFalse(self._job.is_running())

    def test_request_stop_writes_stop_file_only_when_running(self):
        from unittest.mock import patch as _patch

        # no job running -> request_stop is a no-op
        self._job.request_stop()
        self.assertFalse((Path(self._tmp) / "stop").exists())

        # running job -> stop file written
        (Path(self._tmp) / "running").write_text("1")
        self._job.request_stop()
        self.assertTrue((Path(self._tmp) / "stop").exists())


class TranslateRetryTests(TestCase):
    def test_retry_on_server_error_once(self):
        from core import llm as llm_mod

        calls = []

        def flaky_call(text, model, base_url):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError("u", 404, "nf", {}, None)
            return "EN ok"

        with patch.object(llm_mod, "_call_ollama", flaky_call):
            out = llm_mod.translate("т")
        self.assertEqual(out, "EN ok")
        self.assertEqual(len(calls), 2)

    def test_no_retry_on_success(self):
        from core import llm as llm_mod

        calls = []

        def ok_call(text, model, base_url):
            calls.append(1)
            return "EN"

        with patch.object(llm_mod, "_call_ollama", ok_call):
            llm_mod.translate("т")
        self.assertEqual(len(calls), 1)

    def test_gives_up_after_max_retries(self):
        from core import llm as llm_mod

        def always_down(text, model, base_url):
            raise llm_mod.OllamaUnavailable("404")

        with patch.object(llm_mod, "_call_ollama", always_down), patch.object(
            llm_mod, "_RETRY_PAUSE_S", 0.01
        ):
            with self.assertRaises(llm_mod.OllamaUnavailable):
                llm_mod.translate("т")


class TranslationJobStatusTests(TestCase):
    def setUp(self):
        import tempfile

        from core import translation_job

        self._job = translation_job
        self._orig_state = translation_job.STATE_DIR
        self._tmp = tempfile.mkdtemp()
        self._job.STATE_DIR = Path(self._tmp)
        self._files = {}
        for name in ("RUNNING_FILE", "STOP_FILE", "DONE_FILE", "FAIL_FILE", "TOTAL_FILE", "LOG_FILE", "STARTED_FILE", "LAST_FILE"):
            f = getattr(translation_job, name)
            self._files[name] = f
            setattr(translation_job, name, Path(self._tmp) / f.name)

    def tearDown(self):
        self._job.STATE_DIR = self._orig_state
        for name, f in self._files.items():
            setattr(self._job, name, f)

    def test_eta_fields_in_status(self):
        # simulate job mid-run: running, started 100s ago, 10/100 done
        (Path(self._tmp) / "running").write_text("1")
        (Path(self._tmp) / "started").write_text(str(time.time() - 100))
        (Path(self._tmp) / "done").write_text("10")
        (Path(self._tmp) / "total").write_text("100")
        st = self._job.read_status()
        self.assertTrue(st["running"])
        self.assertEqual(st["done"], 10)
        self.assertEqual(st["total"], 100)
        self.assertAlmostEqual(st["elapsed_s"], 100, delta=3)
        # rate = 10/100 events/min -> remaining 90 events -> 900 s
        self.assertAlmostEqual(st["eta_s"], 900, delta=30)


class TranslationAdminEndpointsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User

        cls.admin = User.objects.create_superuser("a", "a@example.com", "pw")

    def setUp(self):
        import tempfile

        from core import translation_job

        self._job = translation_job
        self._orig = {n: getattr(translation_job, n) for n in
                      ("STATE_DIR", "RUNNING_FILE", "STOP_FILE", "STARTED_FILE", "LAST_FILE", "DONE_FILE", "FAIL_FILE", "TOTAL_FILE", "LOG_FILE")}
        import tempfile

        tmp = tempfile.mkdtemp()
        translation_job.STATE_DIR = Path(tmp)
        for name in ("RUNNING_FILE", "STOP_FILE", "STARTED_FILE", "LAST_FILE", "DONE_FILE", "FAIL_FILE", "TOTAL_FILE", "LOG_FILE"):
            f = getattr(translation_job, name)
            setattr(translation_job, name, Path(tmp) / f.name)
        self.client.force_login(self.admin)

    def tearDown(self):
        from core import translation_job

        for n, v in self._orig.items():
            setattr(translation_job, n, v)

    def test_status_endpoint(self):
        resp = self.client.get("/admin/core/event/translate/status/")
        self.assertEqual(resp.status_code, 200)
        import json

        data = resp.json()
        self.assertIn("running", data)
        self.assertIn("done", data)
        self.assertIn("total", data)
        self.assertIn("eta_s", data)
        self.assertIn("elapsed_s", data)

    def test_start_endpoint_launches_job(self):
        from unittest.mock import patch as _patch

        with _patch.object(self._job, "_Runner", lambda: _BlockingRunner()):
            resp = self.client.post("/admin/core/event/translate/start/")
            self.assertTrue(self._job.is_running())
            import time as _t

            self.assertTrue(self._job.is_running())

    def test_stop_endpoint(self):
        from unittest.mock import patch as _patch

        with _patch.object(self._job, "_Runner", lambda: _BlockingRunner()):
            self.client.post("/admin/core/event/translate/start/")
            self.assertTrue(self._job.is_running())
            resp = self.client.post("/admin/core/event/translate/stop/")
            self.assertEqual(resp.status_code, 302)
            self.assertTrue((self._job.STATE_DIR / "stop").exists())
            self._job.RUNNING_FILE.unlink(missing_ok=True)

    def test_endpoints_require_admin(self):
        self.client.logout()
        resp = self.client.get("/admin/core/event/translate/status/")
        self.assertIn(resp.status_code, (302, 403))


class _BlockingRunner:
    """Runner fake that stays 'running' until stopped; no DB access from thread."""



    def run(self, model=None, base_url=None, skip_setup=False):
        import time as _t

        deadline = _t.time() + 5
        while _t.time() < deadline:
            if _tj_mod.STOP_FILE.exists():
                break
            _t.sleep(0.05)
        _tj_mod.RUNNING_FILE.unlink(missing_ok=True)


from core import translation_job as _tj_mod


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