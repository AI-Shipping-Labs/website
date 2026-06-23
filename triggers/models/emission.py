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

    def __str__(self):
        status = "ok" if self.succeeded else "fail"
        return f"delivery #{self.pk} ({status})"
