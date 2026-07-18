from django.db import models


class EmailCampaign(models.Model):
    """Email campaign for newsletter sends.

    Campaigns target users by minimum tier level and (issue #357) by
    contact tags from ``User.tags``. Tag filters AND with the tier-level
    filter so an operator can scope a send to e.g. "Main+ AND has the
    `early-adopter` tag".
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

    # Issue #692: email-verification audience selector. Default
    # ``verified_only`` keeps the historical filter (only users with
    # ``email_verified=True`` receive the campaign). ``everyone`` drops
    # that filter for legitimate broadcasts (major announcements,
    # billing changes, terms updates) where reaching the full non-
    # unsubscribed audience is desired. ``unsubscribed=False`` is
    # ALWAYS enforced in both modes -- never relaxed.
    AUDIENCE_VERIFICATION_VERIFIED_ONLY = 'verified_only'
    AUDIENCE_VERIFICATION_EVERYONE = 'everyone'
    AUDIENCE_VERIFICATION_CHOICES = [
        (AUDIENCE_VERIFICATION_VERIFIED_ONLY, 'Verified only'),
        (AUDIENCE_VERIFICATION_EVERYONE, 'Everyone (including unverified)'),
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
    # Contact-tag scoping (issue #357). Both fields hold a list of normalized
    # tag strings (see ``accounts/utils/tags.py``). Empty list means "no
    # filter on this side": empty include = include everyone, empty exclude =
    # exclude no one. Both empty = behavior identical to pre-#357.
    target_tags_any = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Recipient must carry at least one of these contact tags. '
            'Empty list = no include filter.'
        ),
    )
    target_tags_none = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Recipient must not carry any of these contact tags. '
            'Empty list = no exclude filter.'
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
    audience_verification = models.CharField(
        max_length=20,
        choices=AUDIENCE_VERIFICATION_CHOICES,
        default=AUDIENCE_VERIFICATION_VERIFIED_ONLY,
        help_text=(
            'Whether to require email_verified=True. "everyone" drops the '
            'verified-only filter; unsubscribed=False is always enforced.'
        ),
    )
    # Issue #1076: optional event-registrant audience. Null = the historical
    # tier/tag/Slack audience (unchanged). Non-null scopes the audience to
    # everyone registered for that event (``EventRegistration.user``), with
    # the existing tier/tag/Slack/verification filters ANDing on top. SET_NULL
    # so deleting an event leaves the campaign as a plain tier/tag campaign
    # rather than cascading the campaign away.
    target_event = models.ForeignKey(
        'events.Event',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text=(
            'When set, the audience is everyone registered for this event '
            '(the tier/tag/Slack/verification filters AND on top). Null = '
            'the historical tier/tag audience.'
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
    is_archived = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            'Whether the campaign is archived (hidden from default list views). '
            'API callers flip this via PATCH; there is no DELETE endpoint.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.subject} ({self.status})'

    def get_eligible_recipients(self):
        """Query users eligible to receive this campaign.

        Returns a queryset of users where ALL of the following hold:
        - effective tier level >= target_min_level
          (base tier or active override)
        - unsubscribed = False (always enforced; never relaxed)
        - email_verified = True UNLESS
          ``audience_verification == 'everyone'`` (issue #692)
        - if ``target_tags_any`` is non-empty: ``user.tags`` contains at
          least one of those tags.
        - if ``target_tags_none`` is non-empty: ``user.tags`` contains
          none of those tags.
        - slack_member matches ``slack_filter`` (any/yes/no)

        Empty tag lists mean "no filter on that side" — both empty
        reproduces the exact pre-#357 behavior.
        """
        from email_app.services.campaign_audience import (
            eligible_campaign_recipients,
        )

        return eligible_campaign_recipients(
            target_min_level=self.target_min_level,
            target_tags_any=self.target_tags_any,
            target_tags_none=self.target_tags_none,
            slack_filter=self.slack_filter,
            audience_verification=self.audience_verification,
            target_event_id=self.target_event_id,
        )

    def get_recipient_count(self):
        """Return the estimated number of eligible recipients."""
        return self.get_eligible_recipients().count()
