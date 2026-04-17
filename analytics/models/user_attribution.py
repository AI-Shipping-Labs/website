"""UserAttribution: snapshot of first/last-touch UTM data at signup time."""

from django.conf import settings
from django.db import models

SIGNUP_PATH_CHOICES = [
    ('email_password', 'Email + password'),
    ('newsletter', 'Newsletter'),
    ('slack_oauth', 'Slack OAuth'),
    ('google_oauth', 'Google OAuth'),
    ('github_oauth', 'GitHub OAuth'),
    ('stripe_checkout', 'Stripe Checkout'),
    ('admin_created', 'Admin / createsuperuser'),
    ('unknown', 'Unknown'),
]


class UserAttribution(models.Model):
    """Snapshot of UTM attribution captured at signup time.

    Exactly one row per User. Created by a `post_save` signal in
    `analytics.signals`. Reads cookies/session via the request bound to
    the thread by `analytics.middleware.CampaignTrackingMiddleware`.

    Empty string is used in place of NULL for missing UTM values to keep
    aggregation queries simple. Timestamps stay NULL when no touch was
    recorded so dashboards can distinguish "anonymous user signed up
    without any UTM history" from "signed up via UTM right now".
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='attribution',
        primary_key=True,
    )

    # First-touch (the very first UTM landing this browser ever made; sticky)
    first_touch_utm_source = models.CharField(max_length=255, blank=True, default='')
    first_touch_utm_medium = models.CharField(max_length=255, blank=True, default='')
    first_touch_utm_campaign = models.CharField(max_length=255, blank=True, default='')
    first_touch_utm_content = models.CharField(max_length=255, blank=True, default='')
    first_touch_utm_term = models.CharField(max_length=255, blank=True, default='')
    first_touch_campaign = models.ForeignKey(
        'integrations.UtmCampaign',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Resolved by matching first-touch utm_campaign to UtmCampaign.slug at signup. '
                  'NULL when no matching campaign exists.',
    )
    first_touch_ts = models.DateTimeField(null=True, blank=True)

    # Last-touch (the most recent UTM landing this session)
    last_touch_utm_source = models.CharField(max_length=255, blank=True, default='')
    last_touch_utm_medium = models.CharField(max_length=255, blank=True, default='')
    last_touch_utm_campaign = models.CharField(max_length=255, blank=True, default='')
    last_touch_utm_content = models.CharField(max_length=255, blank=True, default='')
    last_touch_utm_term = models.CharField(max_length=255, blank=True, default='')
    last_touch_campaign = models.ForeignKey(
        'integrations.UtmCampaign',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Resolved by matching last-touch utm_campaign to UtmCampaign.slug at signup.',
    )
    last_touch_ts = models.DateTimeField(null=True, blank=True)

    # How the user signed up
    signup_path = models.CharField(
        max_length=32,
        choices=SIGNUP_PATH_CHOICES,
        default='unknown',
    )

    # The aslab_aid cookie value at signup time, used to backfill prior visits
    anonymous_id = models.CharField(max_length=64, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'User Attribution'
        verbose_name_plural = 'User Attributions'

    def __str__(self):
        return f'{self.user_id} ({self.signup_path})'


__all__ = ['UserAttribution', 'SIGNUP_PATH_CHOICES']
