from django.contrib import admin

from payments.models import WebhookEvent


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ["stripe_event_id", "event_type", "status", "processed_at"]
    list_filter = ["event_type", "status"]
    search_fields = ["stripe_event_id"]
    readonly_fields = [
        "stripe_event_id",
        "event_type",
        "processed_at",
        "payload",
        "status",
        "error_message",
    ]
    ordering = ["-processed_at"]
