from django.contrib import admin

from email_app.models import EmailLog


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    """Read-only admin for sent emails.

    Issue #495: surface bounce/complaint correlation fields so staff can
    answer "did this email bounce, and why?" from the EmailLog row alone.
    """

    list_display = [
        "email_type",
        "user",
        "sent_at",
        "ses_message_id",
        "bounced_at",
        "bounce_type",
        "complained_at",
    ]
    list_filter = ["email_type", "bounce_type", "sent_at", "bounced_at"]
    search_fields = ["user__email", "ses_message_id", "bounce_diagnostic"]
    readonly_fields = [
        "campaign",
        "user",
        "email_type",
        "sent_at",
        "ses_message_id",
        "opened_at",
        "opens",
        "clicked_at",
        "clicks",
        "bounced_at",
        "bounce_type",
        "bounce_subtype",
        "bounce_diagnostic",
        "complained_at",
    ]
    ordering = ["-sent_at"]
