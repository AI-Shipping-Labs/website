"""Claim/dedup log and outbound delivery log (issue #1070).

``EventEmission`` is the DB-layer idempotency guarantee: one row per
successful emit, with a unique ``(user, event_name)`` constraint so a
second claim by the same user for the same event is a no-op that returns
the "already claimed" state, not an error.

``WebhookDelivery`` is a per-attempt log of the signed outbound POST,
distinct from the inbound ``integrations.WebhookLog``.
"""

from django.conf import settings
from django.db import models
from django.db.models.functions import Now
from django.utils import timezone


class EventEmission(models.Model):
    """One row per successful ``emit_event`` call (the dedup ledger)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="event_emissions",
        help_text="The user who triggered the event (null for system emits).",
    )
    event_name = models.CharField(max_length=100)
    properties = models.JSONField(default=dict, blank=True)
    envelope_id = models.CharField(
        max_length=64,
        unique=True,
        help_text="The 'evt_<uuid>' id put on the wire.",
    )
    occurred_at = models.DateTimeField(default=timezone.now, db_default=Now())
    envelope = models.JSONField(default=dict, blank=True, db_default={})
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "event_name"],
                name="uniq_emission_user_event",
            ),
        ]
        verbose_name = "Event emission"
        verbose_name_plural = "Event emissions"

    def __str__(self):
        return f"{self.event_name} ({self.envelope_id})"


class WebhookDelivery(models.Model):
    """A single outbound delivery attempt for an emission/subscription pair."""

    emission = models.ForeignKey(
        EventEmission,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    subscription = models.ForeignKey(
        "triggers.TriggerSubscription",
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    job = models.ForeignKey(
        "triggers.WebhookDeliveryJob",
        on_delete=models.CASCADE,
        related_name="attempts",
        null=True,
        blank=True,
    )
    target_url = models.URLField(max_length=500)
    request_body = models.TextField(
        blank=True,
        default="",
        help_text="The signed JSON envelope sent to the handler.",
    )
    response_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True, default="")
    attempt = models.IntegerField(default=1)
    succeeded = models.BooleanField(default=False)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Webhook delivery"
        verbose_name_plural = "Webhook deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "attempt"],
                name="uniq_webhook_delivery_job_attempt",
            ),
        ]

    def __str__(self):
        status = "ok" if self.succeeded else "fail"
        return f"delivery #{self.pk} ({status})"


class WebhookDeliveryJob(models.Model):
    """Durable delivery state; django-q is only the wake-up mechanism."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_PAUSED = "paused"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    ]

    emission = models.ForeignKey(
        EventEmission,
        on_delete=models.CASCADE,
        related_name="delivery_jobs",
    )
    subscription = models.ForeignKey(
        "triggers.TriggerSubscription",
        on_delete=models.CASCADE,
        related_name="delivery_jobs",
    )
    target_url = models.URLField(max_length=500)
    encrypted_secret = models.TextField()
    secret_version = models.PositiveIntegerField()
    request_body = models.TextField()
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=4)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["emission", "subscription"],
                name="uniq_webhook_job_emission_subscription",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"delivery job #{self.pk} ({self.status})"
