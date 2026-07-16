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
    WebhookDeliveryJob,
)


@admin.register(TriggerSubscription)
class TriggerSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "target_url", "is_active", "created_at")
    list_filter = ("is_active", "event_type")
    search_fields = ("target_url", "description")
    # Never expose the signing secret in the admin changelist/detail.
    exclude = (
        "encrypted_secret",
        "legacy_secret",
        "previous_encrypted_secret",
        "previous_secret_valid_until",
    )
    readonly_fields = ("secret_version",)


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
        "job",
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


@admin.register(WebhookDeliveryJob)
class WebhookDeliveryJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "subscription",
        "status",
        "attempt_count",
        "max_attempts",
        "secret_version",
        "updated_at",
    )
    list_filter = ("status",)
    readonly_fields = (
        "emission",
        "subscription",
        "target_url",
        "secret_version",
        "request_body",
        "status",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "lease_expires_at",
        "last_error",
        "created_at",
        "updated_at",
    )
    exclude = ("encrypted_secret",)
