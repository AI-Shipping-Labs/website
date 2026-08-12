"""Persistent run + finding models for Stripe subscription reconciliation.

Issue #1308. The recurring, cohort-wide reconciliation compares live Stripe
subscription truth against local website membership for every current or
former Stripe subscriber. Each run persists a summary
(:class:`SubscriptionReconciliationRun`) plus a bounded set of non-OK
findings (:class:`SubscriptionReconciliationFinding`) so operators can browse
history, detect newly actionable drift, and de-duplicate daily alerts.

In-sync active rows contribute to the run counts but do NOT create finding
rows — only scheduled cancellations, mismatches, warnings, errors,
would-change, and changed rows are persisted, keeping report size bounded.

These rows never store API secrets or full Stripe payloads. Only safe Stripe
identifiers and the mapped classification/action are recorded.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

__all__ = [
    "SubscriptionReconciliationRun",
    "SubscriptionReconciliationFinding",
]


class SubscriptionReconciliationRun(models.Model):
    """One cohort-wide reconciliation run (diagnostic or confirmed apply)."""

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    MODE_DIAGNOSTIC = "diagnostic"
    MODE_APPLY = "apply"
    MODE_CHOICES = [
        (MODE_DIAGNOSTIC, "Diagnostic (read-only)"),
        (MODE_APPLY, "Apply (confirmed writes)"),
    ]

    SOURCE_SCHEDULED = "scheduled"
    SOURCE_STUDIO = "studio"
    SOURCE_API = "api"
    SOURCE_CHOICES = [
        (SOURCE_SCHEDULED, "Scheduled"),
        (SOURCE_STUDIO, "Studio"),
        (SOURCE_API, "API"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED,
    )
    mode = models.CharField(
        max_length=16, choices=MODE_CHOICES, default=MODE_DIAGNOSTIC,
    )
    source = models.CharField(
        max_length=16, choices=SOURCE_CHOICES, default=SOURCE_SCHEDULED,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="subscription_reconciliation_runs",
    )
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    cohort_count = models.IntegerField(default=0)
    ok_count = models.IntegerField(default=0)
    scheduled_cancellation_count = models.IntegerField(default=0)
    actionable_count = models.IntegerField(default=0)
    warning_count = models.IntegerField(default=0)
    changed_count = models.IntegerField(default=0)

    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["status", "started_at"]),
            models.Index(fields=["mode", "started_at"]),
        ]

    def __str__(self):
        return f"{self.mode} run {self.id} ({self.status})"


class SubscriptionReconciliationFinding(models.Model):
    """One non-OK finding for a single local user within a run."""

    OUTCOME_REPORTED = "reported"
    OUTCOME_WOULD_CHANGE = "would_change"
    OUTCOME_CHANGED = "changed"
    OUTCOME_SKIPPED = "skipped"
    OUTCOME_WARNING = "warning"
    OUTCOME_ERROR = "error"
    OUTCOME_CHOICES = [
        (OUTCOME_REPORTED, "Reported"),
        (OUTCOME_WOULD_CHANGE, "Would change"),
        (OUTCOME_CHANGED, "Changed"),
        (OUTCOME_SKIPPED, "Skipped"),
        (OUTCOME_WARNING, "Warning"),
        (OUTCOME_ERROR, "Error"),
    ]

    WEBHOOK_PROCESSED = "processed"
    WEBHOOK_FAILED_PERMANENT = "failed_permanent"
    WEBHOOK_MISSING = "missing"
    WEBHOOK_NOT_APPLICABLE = "not_applicable"
    WEBHOOK_EVIDENCE_CHOICES = [
        (WEBHOOK_PROCESSED, "Processed"),
        (WEBHOOK_FAILED_PERMANENT, "Failed (permanent)"),
        (WEBHOOK_MISSING, "Missing"),
        (WEBHOOK_NOT_APPLICABLE, "Not applicable"),
    ]

    run = models.ForeignKey(
        SubscriptionReconciliationRun,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="subscription_reconciliation_findings",
    )
    email = models.CharField(max_length=255, blank=True, default="")

    # Local website snapshot.
    current_tier = models.CharField(max_length=64, blank=True, default="")
    current_subscription_id = models.CharField(
        max_length=255, blank=True, default="",
    )
    stripe_customer_id = models.CharField(
        max_length=255, blank=True, default="",
    )

    # Live Stripe truth.
    stripe_subscription_id = models.CharField(
        max_length=255, blank=True, default="",
    )
    stripe_status = models.CharField(max_length=64, blank=True, default="")
    cancel_at_period_end = models.BooleanField(default=False)
    stripe_period_end = models.DateTimeField(null=True, blank=True)
    stripe_tier = models.CharField(max_length=64, blank=True, default="")
    effective_tier = models.CharField(max_length=64, blank=True, default="")
    latest_invoice_id = models.CharField(max_length=255, blank=True, default="")
    latest_invoice_status = models.CharField(max_length=32, blank=True, default="")
    latest_invoice_paid = models.BooleanField(null=True, blank=True)
    latest_invoice_created = models.DateTimeField(null=True, blank=True)
    collection_method = models.CharField(max_length=32, blank=True, default="")
    interval = models.CharField(max_length=16, blank=True, default="")
    interval_count = models.PositiveIntegerField(null=True, blank=True)
    payment_grace = models.ForeignKey(
        "payments.MonthlyPaymentGrace",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reconciliation_findings",
    )
    payment_grace_status = models.CharField(max_length=16, blank=True, default="")
    payment_grace_started_at = models.DateTimeField(null=True, blank=True)
    payment_grace_expires_at = models.DateTimeField(null=True, blank=True)

    # Latest correlated webhook evidence.
    webhook_event_id = models.CharField(max_length=255, blank=True, default="")
    webhook_event_type = models.CharField(
        max_length=255, blank=True, default="",
    )
    webhook_event_status = models.CharField(
        max_length=64, blank=True, default="",
    )
    webhook_processed_at = models.DateTimeField(null=True, blank=True)
    webhook_evidence = models.CharField(
        max_length=32,
        choices=WEBHOOK_EVIDENCE_CHOICES,
        default=WEBHOOK_NOT_APPLICABLE,
    )

    classification = models.CharField(max_length=64)
    action = models.CharField(max_length=64, blank=True, default="")
    outcome = models.CharField(
        max_length=16, choices=OUTCOME_CHOICES, default=OUTCOME_REPORTED,
    )
    message = models.TextField(blank=True, default="")

    # Complete list of conflicting local user ids for duplicate ownership.
    conflicting_user_ids = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["classification", "email"]
        indexes = [
            models.Index(fields=["run", "classification"]),
            models.Index(fields=["user", "run"]),
        ]

    def __str__(self):
        return f"{self.email}: {self.classification} ({self.outcome})"
