from django.db import models


class PrivacyRequestLog(models.Model):
    """Minimal durable audit trail for member privacy requests."""

    REQUEST_EXPORT = "export"
    REQUEST_DELETE = "delete"
    REQUEST_TYPE_CHOICES = [
        (REQUEST_EXPORT, "Export"),
        (REQUEST_DELETE, "Delete"),
    ]

    STATUS_COMPLETED = "completed"
    STATUS_BLOCKED = "blocked"
    STATUS_CHOICES = [
        (STATUS_COMPLETED, "Completed"),
        (STATUS_BLOCKED, "Blocked"),
    ]

    BLOCKER_ACTIVE_SUBSCRIPTION = "active_subscription"
    BLOCKER_STAFF_ACCOUNT = "staff_account"
    BLOCKER_BAD_CONFIRMATION = "bad_confirmation"
    BLOCKER_BAD_PASSWORD = "bad_password"

    request_type = models.CharField(max_length=16, choices=REQUEST_TYPE_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    old_user_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    normalized_email_hash = models.CharField(max_length=64, db_index=True)
    email_domain = models.CharField(max_length=255, blank=True, default="")
    requested_at = models.DateTimeField(auto_now_add=True)
    row_count_summary = models.JSONField(default=dict, blank=True)
    blocker_reason = models.CharField(max_length=64, blank=True, default="")
    request_ip_hash = models.CharField(max_length=64, blank=True, default="")
    user_agent_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["request_type", "status", "-requested_at"]),
            models.Index(fields=["blocker_reason"]),
        ]

    def __str__(self):
        return (
            f"{self.request_type}:{self.status}:"
            f"{self.old_user_id or 'unknown'}"
        )
