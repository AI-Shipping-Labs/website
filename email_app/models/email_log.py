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
        help_text='User who received the email.',
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
        return f'{self.email_type} to {self.user} at {self.sent_at}'
