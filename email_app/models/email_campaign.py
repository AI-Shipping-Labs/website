from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from django.utils import timezone


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

    # Issue #358: tri-state Slack-membership filter applied alongside
    # the tier filter. Operators use this to target campaigns at users
    # who are or aren't already in the Slack workspace (e.g. nudging
    # main-tier users who haven't joined to do so).
    SLACK_FILTER_ANY = 'any'
    SLACK_FILTER_YES = 'yes'
    SLACK_FILTER_NO = 'no'
    SLACK_FILTER_CHOICES = [
        (SLACK_FILTER_ANY, 'Any'),
        (SLACK_FILTER_YES, 'Members only'),
        (SLACK_FILTER_NO, 'Non-members only'),
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
    slack_filter = models.CharField(
        max_length=10,
        choices=SLACK_FILTER_CHOICES,
        default=SLACK_FILTER_ANY,
        help_text=(
            'Restrict recipients by verified Slack workspace membership. '
            '"any" applies no filter; "yes" sends only to members; '
            '"no" sends only to non-members. Issue #358.'
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
        - effective tier level >= target_min_level
          (base tier or active override)
        - unsubscribed = False
        - email_verified = True
        - slack_member matches ``slack_filter`` (any/yes/no)
        """
        User = get_user_model()
        qs = (
            User.objects.filter(
                unsubscribed=False,
                email_verified=True,
            )
            .filter(
                Q(tier__level__gte=self.target_min_level)
                | Q(
                    tier_overrides__is_active=True,
                    tier_overrides__expires_at__gt=timezone.now(),
                    tier_overrides__override_tier__level__gte=self.target_min_level,
                )
            )
        )
        if self.slack_filter == self.SLACK_FILTER_YES:
            qs = qs.filter(slack_member=True)
        elif self.slack_filter == self.SLACK_FILTER_NO:
            qs = qs.filter(slack_member=False)
        return qs.distinct()

    def get_recipient_count(self):
        """Return the estimated number of eligible recipients."""
        return self.get_eligible_recipients().count()
