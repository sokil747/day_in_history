from django import forms
from django.contrib import admin, messages
from django.core.management import call_command
from django.shortcuts import redirect
from django.urls import path
from django.utils.html import mark_safe

from .models import Advertisement, AutoPublishSettings, BotSettings, Event, PremiumUser


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("month", "day", "order", "year", "emoji", "category", "short_text", "source", "auto_publish")
    list_filter = ("month", "category", "auto_publish")
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
    list_display = ("id", "short_text", "logo_preview", "link", "start_date", "finish_date")
    list_display_links = ("id", "short_text")
    search_fields = ("text", "link")
    list_per_page = 20
    readonly_fields = ("logo_preview",)
    fields = ("logo", "logo_image", "logo_preview", "text", "link", "start_date", "finish_date")

    @admin.display(description="Text")
    def short_text(self, obj):
        return obj.text[:80]

    @admin.display(description="Image")
    def logo_preview(self, obj):
        if obj.logo_image:
            return mark_safe(f'<img src="{obj.logo_image.url}" style="max-height:80px;max-width:160px;" />')
        if obj.logo:
            return mark_safe(f'<a href="{obj.logo}" target="_blank">URL</a>')
        return "—"

    actions = ["sync_ads_from_sheets"]

    @admin.action(description="Sync ads from Google Sheet (copies images to VPS)")
    def sync_ads_from_sheets(self, request, queryset):
        call_command("sync_from_sheets")
        self.message_user(request, "Advertisements synced from Google Sheet (images copied to VPS).", messages.SUCCESS)


@admin.register(PremiumUser)
class PremiumUserAdmin(admin.ModelAdmin):
    list_display = ("telegram_id", "full_name", "email", "phone", "created_at")
    search_fields = ("telegram_id", "full_name", "email", "phone")
    ordering = ("-created_at",)


@admin.register(BotSettings)
class BotSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "week_requires_premium",
        "month_requires_premium",
        "premium_lock_mode",
        "premium_button_suffix",
        "updated_at",
    )

    def has_add_permission(self, request):
        return not BotSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class AutoPublishAdminForm(forms.ModelForm):
    days = forms.MultipleChoiceField(
        choices=AutoPublishSettings.DayOfWeek.choices,
        widget=forms.CheckboxSelectMultiple,
        label="Дні публікації",
        required=False,
    )

    class Meta:
        model = AutoPublishSettings
        fields = ("enabled", "publish_time", "channel")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial["days"] = self.instance.days_list()

    def save(self, commit=True):
        obj = super().save(commit=False)
        picked = self.cleaned_data.get("days") or []
        obj.days_of_week = ",".join(str(d) for d in picked)
        if commit:
            obj.save()
        return obj


@admin.register(AutoPublishSettings)
class AutoPublishSettingsAdmin(admin.ModelAdmin):
    form = AutoPublishAdminForm
    list_display = ("enabled", "publish_time", "days_of_week", "channel", "updated_at")
    change_form_template = "admin/autopublish_change_form.html"

    def has_add_permission(self, request):
        return not AutoPublishSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "test/",
                self.admin_site.admin_view(self.test_publish),
                name="core_autopublish_test",
            ),
        ]
        return custom + urls

    def test_publish(self, request):
        import threading

        def _run():
            try:
                import auto_publish as ap

                ap.run_test()
            except Exception as e:  # pragma: no cover
                print(f"Auto-publish test failed: {e}")

        threading.Thread(target=_run, daemon=True).start()
        self.message_user(
            request,
            "Тестова публікація запущена — перевірте канал протягом кількох секунд.",
            messages.SUCCESS,
        )
        return redirect("/admin/core/autopublishsettings/1/change/")
