"""Read-only admin for ConversionAttribution.

Snapshots are append-only and immutable by design — staff can browse and
search them but cannot add, edit, or delete rows from the admin. This
enforces the "frozen at conversion time" semantics at the UI layer so
manual edits can't silently break campaign attribution reports.
"""

from django.contrib import admin

from payments.models import ConversionAttribution


@admin.register(ConversionAttribution)
class ConversionAttributionAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "user",
        "tier",
        "billing_period",
        "amount_eur",
        "mrr_eur",
        "first_touch_utm_campaign",
        "last_touch_utm_campaign",
        "stripe_session_id",
    ]
    list_filter = [
        "tier",
        "billing_period",
        "first_touch_utm_source",
        "last_touch_utm_source",
    ]
    search_fields = [
        "user__email",
        "stripe_session_id",
        "stripe_subscription_id",
        "first_touch_utm_campaign",
        "last_touch_utm_campaign",
    ]
    ordering = ["-created_at"]
    readonly_fields = [
        "user",
        "stripe_session_id",
        "stripe_subscription_id",
        "tier",
        "billing_period",
        "amount_eur",
        "mrr_eur",
        "first_touch_utm_source",
        "first_touch_utm_medium",
        "first_touch_utm_campaign",
        "first_touch_utm_content",
        "first_touch_utm_term",
        "first_touch_campaign",
        "last_touch_utm_source",
        "last_touch_utm_medium",
        "last_touch_utm_campaign",
        "last_touch_utm_content",
        "last_touch_utm_term",
        "last_touch_campaign",
        "created_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
