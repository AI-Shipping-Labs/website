"""CallHost model for the member-facing "Request a call" feature (#870).

A small, staff-managed table of bookable hosts (Alexey, Valeria). Each
host uses their own external scheduler (Calendly / Google appointment
scheduling), so the booking link is configurable in Studio with no code
deploy. Per-host availability is derived from a staff-maintained capacity
setting (Phase 1); Phase 2 (#884) auto-maintains ``current_load`` from
Calendly webhooks.
"""

from django.db import models
from django.templatetags.static import static

# Known seed hosts whose static photo filename differs from their slug.
# Falls back to ``{slug}.png`` for any host not listed here.
_STATIC_PHOTO_BY_SLUG = {
    'alexey': 'alexey.png',
    'valeria': 'valeriia.png',
}


class CallHost(models.Model):
    """A bookable 1:1 call host shown on ``/request-a-call``.

    ``is_available`` is derived (not stored): a host is available when
    they are active AND have spare capacity (``capacity > current_load``).
    Unavailable hosts stay visible with a status, they are never hidden.
    """

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
        help_text='Photo URL. Falls back to the static asset for the slug when blank.',
    )
    booking_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="The host's scheduler link (Calendly / Google appointment scheduling).",
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Show this host on the request-a-call page.',
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

    def __str__(self):
        return self.name

    @property
    def is_available(self):
        """True only when the host is active and has spare capacity."""
        return self.is_active and self.capacity > self.current_load

    @property
    def display_photo_url(self):
        """The configured photo, falling back to the static asset by slug."""
        if self.photo_url:
            return self.photo_url
        filename = _STATIC_PHOTO_BY_SLUG.get(self.slug, f'{self.slug}.png')
        return static(filename)
