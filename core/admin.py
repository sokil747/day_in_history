from django.contrib import admin, messages
from django.core.management import call_command

from .models import Advertisement, BotSettings, Event, PremiumUser


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("month", "day", "order", "year", "emoji", "category", "short_text", "source")
    list_filter = ("month", "category")
    search_fields = ("text", "category", "emoji")
    ordering = ("month", "day", "order")
    list_per_page = 50

    @admin.display(description="Text")
    def short_text(self, obj):
        return obj.text[:80]

    actions = ["sync_from_sheets"]

    @admin.action(description="Sync events from Google Sheet")
    def sync_from_sheets(self, request, queryset):
        call_command("sync_from_sheets")
        self.message_user(request, "Events synced from Google Sheet.", messages.SUCCESS)


@admin.register(Advertisement)
class AdvertisementAdmin(admin.ModelAdmin):
    list_display = ("id", "short_text", "link", "start_date", "finish_date")
    search_fields = ("text", "link")
    list_per_page = 20

    @admin.display(description="Text")
    def short_text(self, obj):
        return obj.text[:80]

    actions = ["sync_ads_from_sheets"]

    @admin.action(description="Sync ads from Google Sheet")
    def sync_ads_from_sheets(self, request, queryset):
        call_command("sync_from_sheets")
        self.message_user(request, "Advertisements synced from Google Sheet.", messages.SUCCESS)


@admin.register(PremiumUser)
class PremiumUserAdmin(admin.ModelAdmin):
    list_display = ("telegram_id", "full_name", "email", "phone", "created_at")
    search_fields = ("telegram_id", "full_name", "email", "phone")
    ordering = ("-created_at",)


@admin.register(BotSettings)
class BotSettingsAdmin(admin.ModelAdmin):
    list_display = ("week_requires_premium", "month_requires_premium", "updated_at")

    def has_add_permission(self, request):
        return not BotSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
