"""Persisted results of read-only Stripe webhook endpoint verification.

Issue #1314. The verifier inspects the live Stripe webhook endpoint
configuration (URL, mode, status, subscribed events) using the configured
``STRIPE_SECRET_KEY`` and persists the outcome here. It NEVER stores API keys
or signing secrets — Stripe cannot reveal ``whsec_...`` and configuration
inspection alone can never prove the signing secret matches.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

__all__ = ["StripeWebhookEndpointCheck"]


class StripeWebhookEndpointCheck(models.Model):
    """One read-only Stripe webhook-endpoint verification result."""

    SOURCE_STUDIO = "studio"
    SOURCE_API = "api"
    SOURCE_CHOICES = [
        (SOURCE_STUDIO, "Studio"),
        (SOURCE_API, "API"),
    ]

    STATUS_PASS = "pass"
    STATUS_WARNING = "warning"
    STATUS_FAIL = "fail"
    STATUS_CHOICES = [
        (STATUS_PASS, "Pass"),
        (STATUS_WARNING, "Warning"),
        (STATUS_FAIL, "Fail"),
    ]

    KEY_MODE_TEST = "test"
    KEY_MODE_LIVE = "live"
    KEY_MODE_UNKNOWN = "unknown"
    KEY_MODE_CHOICES = [
        (KEY_MODE_TEST, "Test"),
        (KEY_MODE_LIVE, "Live"),
        (KEY_MODE_UNKNOWN, "Unknown"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(
        max_length=16,
        choices=SOURCE_CHOICES,
        default=SOURCE_STUDIO,
    )
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stripe_endpoint_checks",
    )
    checked_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    key_mode = models.CharField(
        max_length=16,
        choices=KEY_MODE_CHOICES,
        default=KEY_MODE_UNKNOWN,
    )
    expected_url = models.CharField(max_length=500, blank=True, default="")
    matching_endpoint_id = models.CharField(max_length=255, blank=True, default="")
    endpoint_status = models.CharField(max_length=64, blank=True, default="")
    enabled_events = models.JSONField(default=list, blank=True)
    missing_events = models.JSONField(default=list, blank=True)
    unexpected_events = models.JSONField(default=list, blank=True)
    api_version = models.CharField(max_length=64, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-checked_at"]

    def __str__(self):
        return f"endpoint-check {self.status} @ {self.checked_at:%Y-%m-%d %H:%M}"
