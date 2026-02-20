from django.db import models


class WebhookEvent(models.Model):
    """Stores processed Stripe webhook events for idempotency.

    Before processing any webhook event, we check if its stripe_event_id
    already exists. If so, we skip processing to prevent duplicate actions.
    """

    stripe_event_id = models.CharField(
        max_length=255,
        unique=True,
        help_text="Stripe event ID (e.g. evt_xxx). Used for idempotency.",
    )
    event_type = models.CharField(
        max_length=255,
        help_text="Stripe event type (e.g. checkout.session.completed).",
    )
    processed_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw event payload for debugging.",
    )

    class Meta:
        ordering = ["-processed_at"]

    def __str__(self):
        return f"{self.event_type} ({self.stripe_event_id})"
