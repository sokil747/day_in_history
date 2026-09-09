from django.db import models


class Event(models.Model):
    month = models.IntegerField(db_index=True)
    day = models.IntegerField(db_index=True)
    order = models.IntegerField(default=0)
    year = models.IntegerField(default=0, blank=True)
    emoji = models.CharField(max_length=32, blank=True)
    category = models.CharField(max_length=255, blank=True)
    text = models.TextField()
    source = models.URLField(max_length=2048, blank=True)
    auto_publish = models.BooleanField(
        default=False,
        verbose_name="Auto publish",
        help_text="If enabled and today's day/week matches, bot will publish this event day to the channel at the scheduled time.",
    )

    class Meta:
        ordering = ["month", "day", "order"]
        unique_together = [("month", "day", "order", "year", "text")]

    def __str__(self):
        return f"{self.day:02d}/{self.month:02d} #{self.order} — {self.text[:60]}"


class Advertisement(models.Model):
    logo = models.URLField(max_length=2048, blank=True, help_text="Google Drive link or direct image URL (source)")
    logo_image = models.ImageField(
        upload_to="ads/",
        blank=True,
        null=True,
        help_text="Uploaded image or copy from Google Drive — served from VPS. Preferred over URL.",
    )
    text = models.TextField()
    link = models.URLField(max_length=2048, blank=True)
    start_date = models.CharField(max_length=32, blank=True, help_text="DD/MM/YYYY or empty")
    finish_date = models.CharField(max_length=32, blank=True, help_text="DD/MM/YYYY, empty or 'unlimited'")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.text[:80]


class PremiumUser(models.Model):
    telegram_id = models.BigIntegerField(unique=True, db_index=True, help_text="Telegram user ID")
    full_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Premium user"
        verbose_name_plural = "Premium users"

    def __str__(self):
        return f"{self.telegram_id} — {self.full_name or self.email or '—'}"


class BotSettings(models.Model):
    class PremiumLockMode(models.TextChoices):
        INACTIVE = "inactive", "Visible but inactive (shows «преміум доступ» suffix)"
        HIDDEN = "hidden", "Hidden for non-premium"

    week_requires_premium = models.BooleanField(
        default=True,
        verbose_name="Week in history — premium only",
        help_text="If enabled, only premium users and admins can open 'Week in history'.",
    )
    month_requires_premium = models.BooleanField(
        default=True,
        verbose_name="Month in history — premium only",
        help_text="If enabled, only premium users and admins can open 'Month in history'.",
    )
    premium_lock_mode = models.CharField(
        max_length=16,
        choices=PremiumLockMode.choices,
        default=PremiumLockMode.INACTIVE,
        verbose_name="Premium lock mode",
        help_text="How premium-only buttons look for non-premium users.",
    )
    premium_button_suffix = models.CharField(
        max_length=64,
        default="преміум доступ",
        blank=True,
        verbose_name="Premium button suffix",
        help_text=(
            "Text in parentheses added to locked buttons, e.g. «преміум доступ». "
            "Edit here to translate to other languages."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bot settings"
        verbose_name_plural = "Bot settings"

    def __str__(self):
        return "Bot settings"

    @classmethod
    def get_solo(cls) -> "BotSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AutoPublishSettings(models.Model):
    class DayOfWeek(models.IntegerChoices):
        MON = 0, "Пн"
        TUE = 1, "Вт"
        WED = 2, "Ср"
        THU = 3, "Чт"
        FRI = 4, "Пт"
        SAT = 5, "Сб"
        SUN = 6, "Нд"

    enabled = models.BooleanField(
        default=True,
        verbose_name="Auto publish enabled",
        help_text="Master switch for daily auto publishing to the channel.",
    )
    publish_time = models.TimeField(
        default="09:00",
        verbose_name="Publish time (UTC)",
        help_text="Daily check time (server/UTC time).",
    )
    days_of_week = models.CharField(
        max_length=20,
        default="0,1,2,3,4,5,6",
        verbose_name="Days of week",
        help_text="Comma-separated weekday numbers: 0=Пн … 6=Нд, e.g. '0,2,4'",
    )
    channel = models.CharField(
        max_length=64,
        default="@InsiderKidsNews",
        verbose_name="Telegram channel",
        help_text="Channel username (@name) or numeric chat id. Bot must be admin with post rights.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Auto publish settings"
        verbose_name_plural = "Auto publish settings"

    def __str__(self):
        return "Auto publish settings"

    @classmethod
    def get_solo(cls) -> "AutoPublishSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def days_list(self) -> list[int]:
        out: list[int] = []
        for raw in (self.days_of_week or "").split(","):
            raw = raw.strip()
            if raw.isdigit() and 0 <= int(raw) <= 6:
                out.append(int(raw))
        return out

    def is_scheduled_now(self, now) -> bool:
        if not self.enabled:
            return False
        if now.weekday() not in self.days_list():
            return False
        return (
            now.hour == self.publish_time.hour
            and now.minute == self.publish_time.minute
        )
