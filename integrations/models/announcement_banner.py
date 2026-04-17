"""AnnouncementBanner model for the site-wide announcement banner.

The banner is a singleton (always pk=1) edited from Studio. The view uses
``AnnouncementBanner.objects.get_or_create(pk=1)`` to ensure it exists on
first access.
"""

from django.db import models


class AnnouncementBanner(models.Model):
    """Site-wide announcement banner shown above the public header.

    Stored as a single row (pk=1). When ``message`` or ``link_url`` change on
    save, ``version`` is bumped so previously-dismissed users see the banner
    again on their next page load.
    """

    message = models.CharField(
        max_length=200,
        help_text='Banner text shown between the dot and the link suffix.',
    )
    link_url = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text='URL the banner links to. May be a relative path or absolute URL.',
    )
    link_label = models.CharField(
        max_length=60,
        blank=True,
        default='Read more',
        help_text='Text of the trailing accent-underlined link.',
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text='Master on/off switch.',
    )
    is_dismissible = models.BooleanField(
        default=True,
        help_text='When True, render an X close button.',
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text='Bumped when message or link_url changes; used as the dismissal cookie key.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Announcement banner'
        verbose_name_plural = 'Announcement banners'

    def __str__(self):
        status = 'on' if self.is_enabled else 'off'
        return f'AnnouncementBanner({status}): {self.message[:50]}'

    @classmethod
    def get_singleton(cls):
        """Return the singleton banner row, creating it if missing."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
