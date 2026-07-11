from django.contrib import admin

from accounts.models import PrivacyRequestLog


@admin.register(PrivacyRequestLog)
class PrivacyRequestLogAdmin(admin.ModelAdmin):
    list_display = [
        "requested_at",
        "request_type",
        "status",
        "old_user_id",
        "email_domain",
        "blocker_reason",
    ]
    list_filter = ["request_type", "status", "blocker_reason", "email_domain"]
    search_fields = ["old_user_id", "normalized_email_hash"]
    readonly_fields = [
        "request_type",
        "status",
        "old_user_id",
        "normalized_email_hash",
        "email_domain",
        "requested_at",
        "row_count_summary",
        "blocker_reason",
        "request_ip_hash",
        "user_agent_hash",
    ]
    ordering = ["-requested_at"]
