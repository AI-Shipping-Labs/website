from django.contrib import admin

from accounts.models import EmailChangeRequest


@admin.register(EmailChangeRequest)
class EmailChangeRequestAdmin(admin.ModelAdmin):
    """Admin visibility for member-initiated email change requests."""

    list_display = [
        "user",
        "old_email",
        "new_email",
        "expires_at",
        "confirmed_at",
        "invalidated_at",
        "last_sent_at",
    ]
    list_filter = ["confirmed_at", "invalidated_at", "expires_at"]
    search_fields = ["user__email", "old_email", "new_email"]
    raw_id_fields = ["user"]
    readonly_fields = [
        "user",
        "old_email",
        "new_email",
        "token_hash",
        "expires_at",
        "confirmed_at",
        "invalidated_at",
        "created_at",
        "last_sent_at",
    ]
    ordering = ["-created_at"]
