"""Django admin registration for the triggers subsystem (issue #1070).

These admin pages are a low-level backstop for superusers; the primary
operator surface is the Studio screens. Secrets are never displayed.
"""

from django.contrib import admin

from triggers.models import (
    EventEmission,
    EventWidget,
    TriggerSubscription,
    WebhookDelivery,
)


@admin.register(TriggerSubscription)
class TriggerSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "target_url", "is_active", "created_at")
    list_filter = ("is_active", "event_type")
    search_fields = ("target_url", "description")
    # Never expose the signing secret in the admin changelist/detail.
    exclude = ("secret",)


@admin.register(EventWidget)
class EventWidgetAdmin(admin.ModelAdmin):
    list_display = ("slug", "event_name", "min_level", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("slug", "event_name")
    prepopulated_fields = {"slug": ("event_name",)}


@admin.register(EventEmission)
class EventEmissionAdmin(admin.ModelAdmin):
    list_display = ("envelope_id", "event_name", "user", "created_at")
    list_filter = ("event_name",)
    search_fields = ("envelope_id", "event_name", "user__email")
    readonly_fields = ("envelope_id", "event_name", "user", "properties", "created_at")


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "subscription",
        "target_url",
        "attempt",
        "succeeded",
        "response_status",
        "created_at",
    )
    list_filter = ("succeeded",)
    search_fields = ("target_url", "error")
    readonly_fields = (
        "emission",
        "subscription",
        "target_url",
        "request_body",
        "response_status",
        "response_body",
        "attempt",
        "succeeded",
        "error",
        "created_at",
    )
