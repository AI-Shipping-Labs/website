"""Internal CallHost model for member-facing Call profiles.

The model/table name remains for backward compatibility. Bookability is a
simple operator-authored link contract: active profiles with a booking URL
are shown to members. Legacy capacity fields remain for dormant integration
compatibility but do not affect visible behavior.
"""

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.templatetags.static import static

# Known seed hosts with backward-compatible bundled profile photos.
_STATIC_PHOTO_BY_SLUG = {
    'alexey': 'alexey.png',
    'valeria': 'valeriia.png',
}

_HTTP_URL_VALIDATOR = URLValidator(schemes=['http', 'https'])


def is_usable_http_url(value):
    """Return whether a stored value is a non-whitespace HTTP(S) URL."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        _HTTP_URL_VALIDATOR(value)
    except ValidationError:
        return False
    return True


class CallHost(models.Model):
    """A staff-managed Call profile shown on ``/request-a-call``."""

    name = models.CharField(max_length=120, help_text='Display name, e.g. "Alexey Grigorev".')
    slug = models.SlugField(unique=True, help_text='Stable key, e.g. "alexey", "valeria".')
    role_label = models.CharField(
        max_length=160,
        blank=True,
        help_text='e.g. "Co-founder & ML Engineer".',
    )
    photo_url = models.CharField(
        max_length=500,
        blank=True,
        help_text='Optional profile photo URL.',
    )
    booking_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="The profile's scheduler link.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Show on Request a call (requires a booking URL).',
    )
    capacity = models.PositiveIntegerField(
        default=0,
        help_text='How many people can be taken now. 0 means none.',
    )
    current_load = models.PositiveIntegerField(
        default=0,
        help_text='Staff-maintained count of pending/booked calls.',
    )
    order = models.PositiveIntegerField(default=0, help_text='Display order (lower first).')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Call profile'
        verbose_name_plural = 'Call profiles'

    def __str__(self):
        return self.name

    @property
    def is_available(self):
        """Backward-compatible alias for the link-based bookability contract."""
        return self.is_active and is_usable_http_url(self.booking_url)

    @property
    def usable_booking_url(self):
        """Return the safe scheduler URL rendered to members, or blank."""
        return self.booking_url if is_usable_http_url(self.booking_url) else ''

    @property
    def display_photo_url(self):
        """Configured photo, with legacy static fallbacks for known founders."""
        if self.photo_url:
            return self.photo_url
        filename = _STATIC_PHOTO_BY_SLUG.get(self.slug)
        return static(filename) if filename else ''
