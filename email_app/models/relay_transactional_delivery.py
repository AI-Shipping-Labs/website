from django.db import models


class RelayTransactionalDelivery(models.Model):
    """Privacy-safe audit state for a direct Relay transactional handoff."""

    STATUS_SENDING = "sending"
    STATUS_ACCEPTED = "accepted"
    STATUS_FAILED = "failed"
    STATUS_OUTCOME_UNKNOWN = "outcome_unknown"
    STATUS_CHOICES = [
        (STATUS_SENDING, "Sending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_FAILED, "Failed"),
        (STATUS_OUTCOME_UNKNOWN, "Outcome unknown"),
    ]

    idempotency_key = models.CharField(max_length=255, unique=True)
    template_key = models.CharField(max_length=100)
    safe_correlation = models.CharField(max_length=255)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES)
    message_id = models.CharField(max_length=255, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    alerted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "-created_at"],
                name="email_app_r_status_0a120f_idx",
            ),
        ]

    def __str__(self):
        return f"relay-transactional:{self.template_key}:{self.status}"
