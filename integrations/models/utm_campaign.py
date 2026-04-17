"""UTM campaign and link models for tracking URL attribution."""

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

UTM_SLUG_VALIDATOR = RegexValidator(
    regex=r'^[a-z0-9_]+$',
    message='Use lowercase letters, digits, and underscores only.',
)


class UtmCampaign(models.Model):
    """A logical UTM campaign grouping one or more tracked links."""

    name = models.CharField(
        max_length=200,
        help_text='Human-readable name shown in Studio.',
    )
    slug = models.SlugField(
        max_length=100,
        unique=True,
        validators=[UTM_SLUG_VALIDATOR],
        help_text='Used as utm_campaign value. Lowercase letters, digits, and underscores only.',
    )
    default_utm_source = models.CharField(
        max_length=100,
        help_text='Default source for new links in this campaign (e.g. newsletter).',
    )
    default_utm_medium = models.CharField(
        max_length=100,
        help_text='Default medium for new links in this campaign (e.g. email).',
    )
    notes = models.TextField(blank=True, help_text='Internal notes for the team.')
    is_archived = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='utm_campaigns_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'UTM Campaign'
        verbose_name_plural = 'UTM Campaigns'

    def __str__(self):
        return f'{self.name} ({self.slug})'

    def has_links(self):
        return self.links.exists()


class UtmCampaignLink(models.Model):
    """A single canonical tracking link within a UTM campaign."""

    campaign = models.ForeignKey(
        UtmCampaign,
        on_delete=models.CASCADE,
        related_name='links',
    )
    utm_content = models.SlugField(
        max_length=100,
        validators=[UTM_SLUG_VALIDATOR],
        help_text='Audience or placement tag (e.g. ai_hero_list).',
    )
    utm_term = models.CharField(
        max_length=100,
        blank=True,
        help_text='Optional term, included in the URL only if non-empty.',
    )
    utm_source = models.CharField(
        max_length=100,
        blank=True,
        help_text='Overrides campaign default source when non-empty.',
    )
    utm_medium = models.CharField(
        max_length=100,
        blank=True,
        help_text='Overrides campaign default medium when non-empty.',
    )
    destination = models.CharField(
        max_length=1000,
        help_text='Either a path (e.g. /events/...) or a full URL.',
    )
    label = models.CharField(
        max_length=200,
        blank=True,
        help_text='Short human description (e.g. AI Hero newsletter list).',
    )
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='utm_links_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['utm_content']
        unique_together = [('campaign', 'utm_content')]
        verbose_name = 'UTM Campaign Link'
        verbose_name_plural = 'UTM Campaign Links'

    def __str__(self):
        return f'{self.campaign.slug} / {self.utm_content}'

    def effective_source(self):
        return self.utm_source or self.campaign.default_utm_source

    def effective_medium(self):
        return self.utm_medium or self.campaign.default_utm_medium

    def build_url(self):
        """Return the canonical UTM URL.

        Param order: utm_source, utm_medium, utm_campaign, utm_content,
        then utm_term if present. Preserves any non-UTM query params and
        fragment already present on destination. If destination is a path
        (starts with `/`), prefixes it with SITE_BASE_URL.
        """
        destination = self.destination or ''
        if destination.startswith('/'):
            base = getattr(settings, 'SITE_BASE_URL', 'https://aishippinglabs.com').rstrip('/')
            destination = f'{base}{destination}'

        parsed = urlparse(destination)
        existing_params = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if not k.startswith('utm_')
        ]
        utm_params = [
            ('utm_source', self.effective_source()),
            ('utm_medium', self.effective_medium()),
            ('utm_campaign', self.campaign.slug),
            ('utm_content', self.utm_content),
        ]
        if self.utm_term:
            utm_params.append(('utm_term', self.utm_term))

        all_params = existing_params + utm_params
        new_query = urlencode(all_params)
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))

    def clean(self):
        super().clean()
        if self.utm_content:
            UTM_SLUG_VALIDATOR(self.utm_content)


__all__ = ['UtmCampaign', 'UtmCampaignLink', 'UTM_SLUG_VALIDATOR']
