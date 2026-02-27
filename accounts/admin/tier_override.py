from django.contrib import admin

from accounts.models import TierOverride


@admin.register(TierOverride)
class TierOverrideAdmin(admin.ModelAdmin):
    """Admin configuration for the TierOverride model."""

    list_display = [
        "user",
        "override_tier",
        "original_tier",
        "expires_at",
        "is_active",
        "granted_by",
        "created_at",
    ]
    list_filter = ["is_active", "override_tier"]
    search_fields = ["user__email", "granted_by__email"]
    raw_id_fields = ["user", "granted_by"]
    readonly_fields = ["created_at"]
    ordering = ["-created_at"]
