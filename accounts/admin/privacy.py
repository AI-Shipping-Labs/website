from django.contrib import admin

from accounts.models import PrivacyCompletionDelivery, PrivacyRequestLog


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
        "originating_request",
        "operator_user_id",
        "error_code",
        "workflow_version",
    ]
    ordering = ["-requested_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PrivacyCompletionDelivery)
class PrivacyCompletionDeliveryAdmin(admin.ModelAdmin):
    list_display = ["request", "status", "attempt_count", "created_at", "sent_at"]
    list_filter = ["status", "created_at", "sent_at"]
    search_fields = ["request__old_user_id", "dedupe_key", "normalized_email_hash"]
    readonly_fields = [
        "request",
        "email_log",
        "status",
        "recipient_ciphertext",
        "normalized_email_hash",
        "email_domain",
        "dedupe_key",
        "attempt_count",
        "claim_token",
        "last_attempt_at",
        "transport_started_at",
        "sent_at",
        "error_code",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
