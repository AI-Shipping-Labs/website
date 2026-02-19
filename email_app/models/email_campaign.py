from django.db import models


class EmailCampaign(models.Model):
    """Email campaign for newsletter sends.

    Campaigns target users by minimum tier level and track
    send status and recipient counts.
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
    ]

    subject = models.CharField(max_length=255)
    body = models.TextField(
        help_text='Campaign body in markdown or HTML.',
    )
    target_min_level = models.IntegerField(
        default=0,
        help_text=(
            'Minimum tier level to receive this campaign. '
            '0 = everyone, 1 = Basic+, 2 = Main+, 3 = Premium.'
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the campaign was sent.',
    )
    sent_count = models.IntegerField(
        default=0,
        help_text='Number of recipients.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.subject} ({self.status})'
