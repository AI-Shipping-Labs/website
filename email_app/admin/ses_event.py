from django.contrib import admin

from email_app.models import SesEvent


@admin.register(SesEvent)
class SesEventAdmin(admin.ModelAdmin):
    """Read-only admin for the SES/SNS webhook audit log.

    Issue #495: surface the new bounce-correlation fields so staff can
    trace any incoming bounce or complaint back to the campaign or
    transactional EmailLog that produced it. Search now covers
    ``email_log__email_type`` (e.g. "email_verification", "campaign")
    and ``email_log__campaign__subject`` for newsletter triage, and the
    list adds bounce classification + correlated email metadata.
    """

    list_display = [
        "received_at",
        "event_type",
        "recipient_email",
        "user",
        "bounce_type",
        "bounce_subtype",
        "email_log_email_type",
        "email_log_campaign",
        "action_taken",
    ]
    list_filter = ["event_type", "bounce_type", "received_at"]
    search_fields = [
        "recipient_email",
        "message_id",
        "user__email",
        "email_log__email_type",
        "email_log__ses_message_id",
        "email_log__campaign__subject",
    ]
    readonly_fields = [
        "received_at",
        "event_type",
        "message_id",
        "raw_payload",
        "recipient_email",
        "user",
        "email_log",
        "bounce_type",
        "bounce_subtype",
        "diagnostic_code",
        "action_taken",
    ]
    ordering = ["-received_at"]
    list_select_related = ("user", "email_log", "email_log__campaign")

    @admin.display(description="email type", ordering="email_log__email_type")
    def email_log_email_type(self, obj):
        if obj.email_log is None:
            return ""
        return obj.email_log.email_type

    @admin.display(description="campaign", ordering="email_log__campaign__subject")
    def email_log_campaign(self, obj):
        if obj.email_log is None or obj.email_log.campaign is None:
            return ""
        return obj.email_log.campaign.subject

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
