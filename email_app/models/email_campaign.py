from django.contrib.auth import get_user_model
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

    TARGET_LEVEL_CHOICES = [
        (0, 'Everyone (including free)'),
        (10, 'Basic and above'),
        (20, 'Main and above'),
        (30, 'Premium only'),
    ]

    subject = models.CharField(max_length=255)
    body = models.TextField(
        help_text='Campaign body in markdown or HTML.',
    )
    target_min_level = models.IntegerField(
        default=0,
        choices=TARGET_LEVEL_CHOICES,
        help_text=(
            'Minimum tier level to receive this campaign. '
            '0 = everyone, 10 = Basic+, 20 = Main+, 30 = Premium.'
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

    def get_eligible_recipients(self):
        """Query users eligible to receive this campaign.

        Returns a queryset of users where:
        - tier.level >= target_min_level
        - unsubscribed = False
        - email_verified = True
        """
        User = get_user_model()
        return User.objects.filter(
            tier__level__gte=self.target_min_level,
            unsubscribed=False,
            email_verified=True,
        )

    def get_recipient_count(self):
        """Return the estimated number of eligible recipients."""
        return self.get_eligible_recipients().count()
