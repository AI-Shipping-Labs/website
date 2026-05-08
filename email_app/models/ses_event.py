"""Audit log for incoming SES/SNS notifications (issue #453).

Every notification we receive on the SES bounce/complaint webhook writes one
row here, regardless of whether it matched a User. The raw SNS payload is
preserved so operators can investigate "user X was unsubscribed because Y
bounced on Z campaign" after the fact.

Retention: 90 days. The cleanup job is filed as a follow-up; this model is
write-only from the application side.

The ``message_id`` column is the SNS ``MessageId`` and is unique. The webhook
view uses ``get_or_create`` on it for idempotency: SNS retries the same
notification on a non-2xx response, and we don't want to double-flip
``unsubscribed`` or double-tag a user just because the second delivery raced
the first one.
"""

from django.conf import settings
from django.db import models


class SesEvent(models.Model):
    """One row per SNS notification received on /api/ses-events."""

    EVENT_TYPE_BOUNCE_PERMANENT = "bounce_permanent"
    EVENT_TYPE_BOUNCE_TRANSIENT = "bounce_transient"
    EVENT_TYPE_BOUNCE_OTHER = "bounce_other"
    EVENT_TYPE_COMPLAINT = "complaint"
    EVENT_TYPE_DELIVERY = "delivery"
    EVENT_TYPE_OPEN = "open"
    EVENT_TYPE_CLICK = "click"
    EVENT_TYPE_SUBSCRIPTION_CONFIRMATION = "subscription_confirmation"
    EVENT_TYPE_UNSUBSCRIBE_CONFIRMATION = "unsubscribe_confirmation"
    EVENT_TYPE_OTHER = "other"

    EVENT_TYPE_CHOICES = [
        (EVENT_TYPE_BOUNCE_PERMANENT, "Bounce (permanent)"),
        (EVENT_TYPE_BOUNCE_TRANSIENT, "Bounce (transient)"),
        (EVENT_TYPE_BOUNCE_OTHER, "Bounce (other)"),
        (EVENT_TYPE_COMPLAINT, "Complaint"),
        (EVENT_TYPE_DELIVERY, "Delivery"),
        (EVENT_TYPE_OPEN, "Open"),
        (EVENT_TYPE_CLICK, "Click"),
        (EVENT_TYPE_SUBSCRIPTION_CONFIRMATION, "SubscriptionConfirmation"),
        (EVENT_TYPE_UNSUBSCRIBE_CONFIRMATION, "UnsubscribeConfirmation"),
        (EVENT_TYPE_OTHER, "Other"),
    ]

    received_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="When the SNS notification reached our webhook.",
    )
    event_type = models.CharField(
        max_length=40,
        choices=EVENT_TYPE_CHOICES,
        db_index=True,
        help_text="Classified event type derived from the SNS payload.",
    )
    message_id = models.CharField(
        max_length=255,
        unique=True,
        help_text=(
            "SNS MessageId. Used as the idempotency key for retried "
            "deliveries of the same notification."
        ),
    )
    raw_payload = models.JSONField(
        help_text="The unmodified SNS payload as posted to the webhook.",
    )
    recipient_email = models.EmailField(
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Recipient address parsed from the inner SES message, if any. "
            "Blank for SubscriptionConfirmation / Delivery events without "
            "a single recipient."
        ),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="ses_events",
        null=True,
        blank=True,
        help_text=(
            "Matched User row, if any. Null when the recipient address is "
            "not in our database (still logged for audit)."
        ),
    )
    # Issue #495: correlate bounce/complaint events back to the specific
    # transactional or campaign send that produced them. Populated when
    # the inner SES mail.messageId matches an EmailLog.ses_message_id;
    # null when the event arrives without correlation data (or for older
    # rows that pre-date the field).
    email_log = models.ForeignKey(
        "email_app.EmailLog",
        on_delete=models.SET_NULL,
        related_name="ses_events",
        null=True,
        blank=True,
        help_text=(
            "Matched EmailLog row when the SES mail.messageId on the "
            "incoming event lines up with a transactional/campaign send."
        ),
    )
    bounce_type = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            'SES bounceType ("Permanent", "Transient", "Undetermined") for '
            'bounce events; empty for non-bounce events.'
        ),
    )
    bounce_subtype = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text='SES bounceSubType (e.g. "General", "NoEmail", "Suppressed").',
    )
    diagnostic_code = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Diagnostic / status / reason text from the bounced or "
            "complained recipient entry (where present)."
        ),
    )
    action_taken = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Short description of what the webhook did with this event "
            "(e.g. 'unsubscribed and tagged bounced', 'soft_bounce_count=2', "
            "'no-op')."
        ),
    )

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.event_type} {self.recipient_email or self.message_id}"
