from django.contrib import admin

from payments.models import (
    StripeWebhookDeliveryAttempt,
    StripeWebhookEndpointCheck,
)


@admin.register(StripeWebhookDeliveryAttempt)
class StripeWebhookDeliveryAttemptAdmin(admin.ModelAdmin):
    list_display = [
        "stripe_event_id", "event_type", "attempt_number", "outcome",
        "http_status", "source", "received_at",
    ]
    list_filter = ["outcome", "event_type", "source"]
    search_fields = [
        "stripe_event_id", "stripe_customer_id", "stripe_subscription_id",
    ]
    readonly_fields = [f.name for f in StripeWebhookDeliveryAttempt._meta.fields]
    ordering = ["-received_at"]


@admin.register(StripeWebhookEndpointCheck)
class StripeWebhookEndpointCheckAdmin(admin.ModelAdmin):
    list_display = ["checked_at", "status", "key_mode", "error_code", "source"]
    list_filter = ["status", "key_mode", "source"]
    readonly_fields = [f.name for f in StripeWebhookEndpointCheck._meta.fields]
    ordering = ["-checked_at"]
