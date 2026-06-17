from django.conf import settings
from django.db import models


class EmailLog(models.Model):
    """Log of every email sent through the email service.

    Tracks both transactional emails (welcome, payment_failed, etc.)
    and campaign emails. Campaign emails have a non-null campaign FK.
    """

    campaign = models.ForeignKey(
        'email_app.EmailCampaign',
        on_delete=models.SET_NULL,
        related_name='email_logs',
        null=True,
        blank=True,
        help_text='Associated campaign (null for transactional emails).',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='email_logs',
        null=True,
        blank=True,
        help_text=(
            'User who received the email. Null for sends to an address that '
            'is not a registered platform user (e.g. an event host mailbox); '
            'see ``recipient_email`` in that case.'
        ),
    )
    # ``recipient_email`` records non-user destinations for legacy or
    # integration sends. Most event emails attach directly to ``user``.
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='email_logs',
        null=True,
        blank=True,
        help_text='Event this email relates to (null for non-event emails).',
    )
    recipient_email = models.EmailField(
        blank=True,
        default='',
        help_text=(
            'Destination address. Populated for sends to a non-user mailbox '
            '(e.g. an event host); blank when ``user`` carries the address.'
        ),
    )
    email_type = models.CharField(
        max_length=100,
        help_text=(
            'Type of email: "campaign", "welcome", "payment_failed", '
            '"cancellation", "community_invite", "lead_magnet_delivery", '
            '"event_reminder", etc.'
        ),
    )
    sent_at = models.DateTimeField(
        auto_now_add=True,
        help_text='When the email was sent.',
    )
    ses_message_id = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text='Amazon SES message ID for delivery tracking.',
    )
    opened_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Timestamp of the first SES open event, if any.',
    )
    opens = models.PositiveIntegerField(
        default=0,
        help_text='Count of SES open events received for this email.',
    )
    clicked_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Timestamp of the first SES click event, if any.',
    )
    clicks = models.PositiveIntegerField(
        default=0,
        help_text='Count of SES click events received for this email.',
    )
    # Bounce / complaint correlation (issue #495). These columns let staff
    # answer "did this bounce come from a campaign, signup verification,
    # verification reminder, or lead-magnet email?" by reading EmailLog
    # alone, without joining through SesEvent. Populated by the
    # /api/ses-events webhook when an SES bounce or complaint payload's
    # inner mail.messageId matches this row's ses_message_id.
    bounced_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Timestamp of the first SES bounce event for this email.',
    )
    bounce_type = models.CharField(
        max_length=32,
        blank=True,
        default='',
        help_text='SES bounceType: "Permanent", "Transient", or "Undetermined".',
    )
    bounce_subtype = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text='SES bounceSubType, e.g. "General", "NoEmail", "Suppressed".',
    )
    bounce_diagnostic = models.TextField(
        blank=True,
        default='',
        help_text=(
            'Diagnostic / status / reason text from the bounced recipient '
            'entry (e.g. "smtp; 550 5.1.1 user unknown").'
        ),
    )
    complained_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Timestamp of the first SES complaint event for this email.',
    )

    class Meta:
        ordering = ['-sent_at']
        constraints = [
            # Per-recipient idempotency for campaign sends:
            # one EmailLog per (campaign, user) when campaign is set.
            # Transactional emails (campaign IS NULL) are unaffected;
            # a partial index allows multiple null-campaign rows per user.
            models.UniqueConstraint(
                fields=['campaign', 'user'],
                condition=models.Q(campaign__isnull=False),
                name='unique_campaign_recipient',
            ),
        ]

    def __str__(self):
        recipient = self.user or self.recipient_email or 'unknown'
        return f'{self.email_type} to {recipient} at {self.sent_at}'
