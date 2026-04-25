from django.db import models


class WebhookEvent(models.Model):
    """Stores processed Stripe webhook events for idempotency.

    Before processing any webhook event, we check if its stripe_event_id
    already exists. If so, we skip processing to prevent duplicate actions.

    A row in this table represents a *terminal* outcome for that Stripe
    event from our side: either the handler ran cleanly (``processed``)
    or the handler raised a permanent, non-retryable error
    (``failed_permanent``). In both cases we want Stripe to stop
    retrying. Transient handler failures intentionally do NOT create a
    row, so Stripe's retry can run the handler again.
    """

    STATUS_PROCESSED = "processed"
    STATUS_FAILED_PERMANENT = "failed_permanent"
    STATUS_CHOICES = [
        (STATUS_PROCESSED, "Processed"),
        (STATUS_FAILED_PERMANENT, "Failed (permanent)"),
    ]

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
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_PROCESSED,
        help_text=(
            "Terminal outcome. 'processed' = handler ran cleanly. "
            "'failed_permanent' = handler raised WebhookPermanentError; "
            "Stripe is told to stop retrying."
        ),
    )
    error_message = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Exception summary for failed_permanent rows (truncated). "
            "Empty for processed rows."
        ),
    )

    class Meta:
        ordering = ["-processed_at"]

    def __str__(self):
        return f"{self.event_type} ({self.stripe_event_id})"
