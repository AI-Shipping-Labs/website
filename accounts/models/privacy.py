from django.db import models


class PrivacyRequestLog(models.Model):
    """Minimal durable audit trail for member privacy requests."""

    REQUEST_EXPORT = "export"
    REQUEST_DELETE = "delete"
    REQUEST_DELETION_REQUEST = "deletion_request"
    REQUEST_TYPE_CHOICES = [
        (REQUEST_EXPORT, "Export"),
        (REQUEST_DELETE, "Delete"),
        (REQUEST_DELETION_REQUEST, "Deletion request"),
    ]

    STATUS_COMPLETED = "completed"
    STATUS_BLOCKED = "blocked"
    STATUS_PENDING_DELIVERY = "pending_delivery"
    STATUS_REQUESTED = "requested"
    STATUS_DELIVERY_FAILED = "delivery_failed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_COMPLETED, "Completed"),
        (STATUS_BLOCKED, "Blocked"),
        (STATUS_PENDING_DELIVERY, "Pending delivery"),
        (STATUS_REQUESTED, "Requested"),
        (STATUS_DELIVERY_FAILED, "Delivery failed"),
        (STATUS_FAILED, "Failed"),
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
    originating_request = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="execution_audits",
    )
    operator_user_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    error_code = models.CharField(max_length=64, blank=True, default="", db_default="")
    workflow_version = models.PositiveIntegerField(default=0, db_default=0)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["request_type", "status", "-requested_at"]),
            models.Index(fields=["blocker_reason"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["old_user_id"],
                condition=(
                    models.Q(request_type="deletion_request")
                    & models.Q(status__in=["pending_delivery", "requested"])
                    & models.Q(old_user_id__isnull=False)
                ),
                name="unique_active_deletion_request",
            ),
            models.UniqueConstraint(
                fields=["originating_request"],
                condition=(
                    models.Q(request_type="delete")
                    & models.Q(status="completed")
                    & models.Q(originating_request__isnull=False)
                ),
                name="unique_completed_delete_per_request",
            ),
        ]

    def __str__(self):
        return f"{self.request_type}:{self.status}:{self.old_user_id or 'unknown'}"


class PrivacyCompletionDelivery(models.Model):
    """Privacy-safe, at-most-once completion-message workflow."""

    STATUS_PENDING = "pending"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_OUTCOME_UNKNOWN = "outcome_unknown"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENDING, "Sending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_OUTCOME_UNKNOWN, "Outcome unknown"),
    ]

    request = models.OneToOneField(
        PrivacyRequestLog,
        on_delete=models.PROTECT,
        related_name="completion_delivery",
    )
    email_log = models.ForeignKey(
        "email_app.EmailLog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="privacy_completion_deliveries",
    )
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING)
    recipient_ciphertext = models.TextField()
    normalized_email_hash = models.CharField(max_length=64, db_index=True)
    email_domain = models.CharField(max_length=255, blank=True, default="")
    dedupe_key = models.CharField(max_length=255, unique=True)
    attempt_count = models.PositiveIntegerField(default=0)
    claim_token = models.UUIDField(null=True, blank=True, unique=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    transport_started_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"privacy-completion:{self.request_id}:{self.status}"
