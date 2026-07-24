"""Durable, secret-free evidence of Stripe webhook delivery attempts.

Issue #1314. Every signature-verified handled Stripe delivery persists one
``StripeWebhookDeliveryAttempt`` row after verification and before dispatch,
then updates it with the terminal outcome. Unlike ``WebhookEvent`` (which is
the *terminal* event-idempotency record and only exists for terminal
outcomes), an attempt row exists for EVERY delivery — including transient
failures and Stripe retries — so operators can prove whether a cancellation
callback was received, processed, retried, or rejected.

The row deliberately stores only safe Stripe identifiers. It NEVER stores the
raw payload, the ``Stripe-Signature`` header, API keys, webhook secrets, or
member email addresses.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

__all__ = ["StripeWebhookDeliveryAttempt"]


class StripeWebhookDeliveryAttempt(models.Model):
    """One delivery/replay attempt for a Stripe webhook event."""

    SOURCE_STRIPE_DELIVERY = "stripe_delivery"
    SOURCE_OPERATOR_REPLAY = "operator_replay"
    SOURCE_CHOICES = [
        (SOURCE_STRIPE_DELIVERY, "Stripe delivery"),
        (SOURCE_OPERATOR_REPLAY, "Operator replay"),
    ]

    # Initial state persisted before dispatch; every completed attempt is
    # updated to one of the terminal/observed outcomes below.
    OUTCOME_RECEIVED = "received"
    OUTCOME_PROCESSED = "processed"
    OUTCOME_ALREADY_PROCESSED = "already_processed"
    OUTCOME_IGNORED_STALE = "ignored_stale"
    OUTCOME_UNMATCHED_USER = "unmatched_user"
    OUTCOME_AMBIGUOUS_USER = "ambiguous_user"
    OUTCOME_FAILED_TRANSIENT = "failed_transient"
    OUTCOME_FAILED_PERMANENT = "failed_permanent"
    OUTCOME_CHOICES = [
        (OUTCOME_RECEIVED, "Received"),
        (OUTCOME_PROCESSED, "Processed"),
        (OUTCOME_ALREADY_PROCESSED, "Already processed"),
        (OUTCOME_IGNORED_STALE, "Ignored (stale subscription)"),
        (OUTCOME_UNMATCHED_USER, "Unmatched user (retryable)"),
        (OUTCOME_AMBIGUOUS_USER, "Ambiguous user (terminal)"),
        (OUTCOME_FAILED_TRANSIENT, "Failed (transient)"),
        (OUTCOME_FAILED_PERMANENT, "Failed (permanent)"),
    ]

    # Outcomes that mutate nobody / are safe-but-not-successful. Used by the
    # API/Studio so a clean handler return is never described as a membership
    # update when no user was changed.
    NON_MUTATING_OUTCOMES = frozenset({
        OUTCOME_ALREADY_PROCESSED,
        OUTCOME_IGNORED_STALE,
        OUTCOME_UNMATCHED_USER,
        OUTCOME_AMBIGUOUS_USER,
        OUTCOME_FAILED_TRANSIENT,
        OUTCOME_FAILED_PERMANENT,
    })

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(
        max_length=32,
        choices=SOURCE_CHOICES,
        default=SOURCE_STRIPE_DELIVERY,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stripe_webhook_attempts",
        help_text="Operator who requested a replay; null for Stripe deliveries.",
    )
    stripe_event_id = models.CharField(max_length=255, db_index=True)
    event_type = models.CharField(max_length=255)
    stripe_object_id = models.CharField(max_length=255, blank=True, default="")
    stripe_customer_id = models.CharField(
        max_length=255, blank=True, default="", db_index=True,
    )
    stripe_subscription_id = models.CharField(
        max_length=255, blank=True, default="", db_index=True,
    )
    livemode = models.BooleanField(null=True, blank=True)
    attempt_number = models.IntegerField(default=1)
    outcome = models.CharField(
        max_length=32,
        choices=OUTCOME_CHOICES,
        default=OUTCOME_RECEIVED,
    )
    http_status = models.IntegerField(default=0)
    received_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["event_type", "received_at"]),
            models.Index(fields=["outcome", "received_at"]),
        ]

    def __str__(self):
        return (
            f"{self.event_type} {self.stripe_event_id} "
            f"#{self.attempt_number} ({self.outcome})"
        )
