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
    week_requires_premium = models.BooleanField(
        default=True,
        verbose_name="Week in history — premium only",
        help_text="If enabled, only premium users and admins see 'Week in history'. If disabled, visible to everyone.",
    )
    month_requires_premium = models.BooleanField(
        default=True,
        verbose_name="Month in history — premium only",
        help_text="If enabled, only premium users and admins see 'Month in history'. If disabled, visible to everyone.",
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
