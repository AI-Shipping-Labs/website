"""CampaignVisit model: one row per UTM-bearing pageview."""

from django.conf import settings
from django.db import models


class CampaignVisit(models.Model):
    """A single landing on the site with utm_* query params present.

    Resolves to a `UtmCampaign` (FK) by `utm_campaign` slug at write time.
    Anonymous visitors are tracked by an `aslab_aid` cookie UUID4. If the
    visitor is logged in, `user` is populated for downstream attribution.
    Raw IP is never stored — only a salted SHA-256 hash used for unique-visitor
    counting in dashboards.
    """

    campaign = models.ForeignKey(
        'integrations.UtmCampaign',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='visits',
        help_text='Resolved by matching utm_campaign to UtmCampaign.slug at write time. '
                  'NULL when no matching campaign exists.',
    )
    utm_source = models.CharField(max_length=100, blank=True, db_index=True)
    utm_medium = models.CharField(max_length=100, blank=True)
    utm_campaign = models.CharField(max_length=200, blank=True)
    utm_content = models.CharField(max_length=200, blank=True)
    utm_term = models.CharField(max_length=200, blank=True)
    path = models.CharField(max_length=500, blank=True, help_text='Landing path, no querystring.')
    referrer = models.CharField(max_length=500, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    ip_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text='SHA-256 of (client_ip + IP_HASH_SALT). Empty if salt not configured.',
    )
    anonymous_id = models.CharField(
        max_length=36,
        db_index=True,
        help_text='UUID4 from the aslab_aid cookie. Stable across visits for the same browser.',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='campaign_visits',
    )
    ts = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Campaign Visit'
        verbose_name_plural = 'Campaign Visits'
        ordering = ['-ts']
        indexes = [
            models.Index(fields=['campaign', '-ts'], name='analytics_visit_camp_ts_idx'),
            models.Index(fields=['anonymous_id', '-ts'], name='analytics_visit_anon_ts_idx'),
            models.Index(fields=['utm_campaign', '-ts'], name='analytics_visit_utmc_ts_idx'),
        ]

    def __str__(self):
        return f'{self.utm_source}/{self.utm_medium}/{self.utm_campaign} -> {self.path} @ {self.ts:%Y-%m-%d %H:%M}'


__all__ = ['CampaignVisit']
