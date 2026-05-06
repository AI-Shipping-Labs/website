from django.contrib import admin

from email_app.models import SesEvent


@admin.register(SesEvent)
class SesEventAdmin(admin.ModelAdmin):
    """Read-only admin for the SES/SNS webhook audit log."""

    list_display = [
        "received_at",
        "event_type",
        "recipient_email",
        "user",
        "action_taken",
    ]
    list_filter = ["event_type", "received_at"]
    search_fields = ["recipient_email", "message_id", "user__email"]
    readonly_fields = [
        "received_at",
        "event_type",
        "message_id",
        "raw_payload",
        "recipient_email",
        "user",
        "action_taken",
    ]
    ordering = ["-received_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
