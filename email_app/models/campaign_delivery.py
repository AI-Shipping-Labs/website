from django.conf import settings
from django.db import models


class CampaignDelivery(models.Model):
    """Durable pre-side-effect ledger for one campaign recipient."""

    class State(models.TextChoices):
        PENDING = 'pending', 'Pending'
        DISPATCHING = 'dispatching', 'Dispatching'
        SENT = 'sent', 'Sent'
        SKIPPED = 'skipped', 'Skipped'
        FAILED = 'failed', 'Failed'
        AMBIGUOUS = 'ambiguous', 'Ambiguous'
        ASSUMED_SENT = 'assumed_sent', 'Assumed sent'

    class Resolution(models.TextChoices):
        RETRY = 'retry', 'Retry'
        ASSUME_SENT = 'assume_sent', 'Assume sent'

    campaign = models.ForeignKey(
        'email_app.EmailCampaign',
        on_delete=models.CASCADE,
        related_name='deliveries',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='campaign_deliveries',
        null=True,
        blank=True,
    )
    recipient_user_pk = models.PositiveBigIntegerField()
    recipient_email = models.EmailField()
    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.PENDING,
        db_index=True,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    claim_token = models.UUIDField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ses_message_id = models.CharField(max_length=255, blank=True, default='')
    email_log = models.OneToOneField(
        'email_app.EmailLog',
        on_delete=models.SET_NULL,
        related_name='campaign_delivery',
        null=True,
        blank=True,
    )
    skip_reason = models.CharField(max_length=100, blank=True, default='')
    last_error = models.CharField(max_length=500, blank=True, default='')
    resolution = models.CharField(
        max_length=20,
        choices=Resolution.choices,
        blank=True,
        default='',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='resolved_campaign_deliveries',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['recipient_email', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['campaign', 'recipient_user_pk'],
                name='unique_campaign_delivery_recipient',
            ),
        ]

    def __str__(self):
        return f'{self.campaign_id}:{self.recipient_email} ({self.state})'
