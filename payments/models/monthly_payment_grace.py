"""Durable monthly failed-payment grace state (issue #1413)."""

import uuid

from django.conf import settings
from django.db import models


class MonthlyPaymentGrace(models.Model):
    SOURCE_WEBHOOK = "webhook"
    SOURCE_RECONCILIATION = "reconciliation"
    SOURCE_CHOICES = [
        (SOURCE_WEBHOOK, "Webhook"),
        (SOURCE_RECONCILIATION, "Reconciliation"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_RECOVERED = "recovered"
    STATUS_EXPIRED = "expired"
    STATUS_SUPERSEDED = "superseded"
    STATUS_REVIEW = "review"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_RECOVERED, "Recovered"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_SUPERSEDED, "Superseded"),
        (STATUS_REVIEW, "Review"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monthly_payment_graces",
    )
    base_tier_at_start = models.ForeignKey(
        "payments.Tier",
        on_delete=models.PROTECT,
        related_name="monthly_payment_graces",
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255)
    stripe_invoice_id = models.CharField(max_length=255)
    livemode = models.BooleanField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE,
    )
    interval = models.CharField(max_length=16)
    interval_count = models.PositiveIntegerField()
    grace_started_at = models.DateTimeField()
    grace_expires_at = models.DateTimeField()
    policy_enforced_at = models.DateTimeField(null=True, blank=True)
    recovered_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    recovery_event_id = models.CharField(max_length=255, blank=True, default="")
    last_failure_event_id = models.CharField(max_length=255, blank=True, default="")
    last_error_code = models.CharField(max_length=64, blank=True, default="")
    last_error_message = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-grace_started_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="active"),
                name="payments_one_active_monthly_grace_per_user",
            ),
            models.UniqueConstraint(
                fields=["stripe_invoice_id", "livemode"],
                condition=(~models.Q(stripe_invoice_id="") & models.Q(livemode__isnull=False)),
                name="payments_unique_monthly_grace_invoice_mode",
            ),
            models.UniqueConstraint(
                fields=["stripe_invoice_id"],
                condition=(~models.Q(stripe_invoice_id="") & models.Q(livemode__isnull=True)),
                name="payments_unique_monthly_grace_invoice_unknown_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(interval_count__gt=0),
                name="payments_monthly_grace_interval_count_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "grace_expires_at"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["stripe_subscription_id"]),
            models.Index(fields=["stripe_invoice_id"]),
        ]

    @property
    def effective_expires_at(self):
        """Rollout-safe deadline after observe-mode grace is enforced."""
        from datetime import timedelta

        if self.policy_enforced_at:
            return max(
                self.grace_expires_at,
                self.policy_enforced_at + timedelta(hours=168),
            )
        return self.grace_expires_at

    def __str__(self):
        return f"{self.user_id}:{self.stripe_invoice_id}:{self.status}"


class MonthlyPaymentGraceDelivery(models.Model):
    KIND_FAILURE_MEMBER = "failure_member"
    KIND_FAILURE_TEAM = "failure_team"
    KIND_REMINDER_MEMBER = "reminder_member"
    KIND_EXPIRED_MEMBER = "expired_member"
    KIND_CHOICES = [
        (KIND_FAILURE_MEMBER, "Initial member failure"),
        (KIND_FAILURE_TEAM, "Initial team failure"),
        (KIND_REMINDER_MEMBER, "Member reminder"),
        (KIND_EXPIRED_MEMBER, "Member expiry"),
    ]

    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    ]

    grace = models.ForeignKey(
        MonthlyPaymentGrace,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    recipient = models.EmailField()
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    email_log = models.ForeignKey(
        "email_app.EmailLog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="monthly_payment_grace_deliveries",
    )
    last_error = models.CharField(max_length=500, blank=True, default="")
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_token = models.UUIDField(null=True, blank=True, editable=False)
    transport_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["grace_id", "kind", "recipient"]
        constraints = [
            models.UniqueConstraint(
                fields=["grace", "kind", "recipient"],
                name="payments_unique_monthly_grace_delivery",
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_count__gte=0),
                name="payments_monthly_grace_attempts_nonnegative",
            ),
        ]
        indexes = [models.Index(fields=["status", "claimed_at"])]

    def save(self, *args, **kwargs):
        self.recipient = (self.recipient or "").strip().lower()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.grace_id}:{self.kind}:{self.recipient}"
