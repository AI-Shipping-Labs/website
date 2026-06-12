"""EventSeries model: a recurring series of events created in one Studio action.

The series' job is purely to generate ``Event`` rows at creation time and
provide a UI surface for managing them together. There is no recurrence
engine; once the events are created they are independent rows linked back
to the series via a nullable FK. Deleting the series leaves the events in
place (``on_delete=SET_NULL`` on ``Event.event_series``).

Issue #564 introduced the model under the old name ``EventGroup``;
issue #575 renamed it to ``EventSeries`` everywhere to match the product
term.
"""

from django.db import models
from django.utils.text import slugify

from content.models.mixins import TimestampedModelMixin
from content.utils.markdown import render_markdown
from events.models.event import PUBLIC_EVENT_STATUSES

EVENT_SERIES_CADENCE_CHOICES = [
    ('weekly', 'Weekly'),
]


DAY_OF_WEEK_CHOICES = [
    (0, 'Monday'),
    (1, 'Tuesday'),
    (2, 'Wednesday'),
    (3, 'Thursday'),
    (4, 'Friday'),
    (5, 'Saturday'),
    (6, 'Sunday'),
]


class EventSeries(TimestampedModelMixin, models.Model):
    """A series of related events created together as a weekly cadence."""

    name = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(
        blank=True, default='',
        help_text='Markdown description shown on the public series page.',
    )
    description_html = models.TextField(
        blank=True, default='',
        help_text='Auto-generated HTML from description markdown.',
    )
    cadence = models.CharField(
        max_length=20,
        choices=EVENT_SERIES_CADENCE_CHOICES,
        default='weekly',
        help_text='Cadence label. v1 supports weekly only.',
    )
    day_of_week = models.IntegerField(
        choices=DAY_OF_WEEK_CHOICES,
        default=2,
        help_text=(
            'Day of the week of the first occurrence. Stored on the series '
            'so the cadence label survives per-event drift.'
        ),
    )
    start_time = models.TimeField(
        help_text='Time of day (24-hour) of the first occurrence.',
    )
    timezone = models.CharField(
        max_length=100, default='Europe/Berlin',
        help_text='IANA timezone name, e.g. Europe/Berlin.',
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            'Hide flag. When False the series is hidden from public series '
            'listings; existing occurrences keep their own per-event status.'
        ),
    )
    auto_banner_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text=(
            "Platform-generated OG banner URL (banner-generator Lambda, "
            "issue #788). Templates should prefer an operator cover and fall "
            "back to this. Series have no operator cover today, so this is "
            "the only banner image."
        ),
    )
    custom_banner_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text=(
            "Operator-uploaded custom banner/social image. Survives content "
            "re-sync. Wins over the generated banner; loses to a frontmatter "
            "cover_image_url."
        ),
    )
    auto_banner_title_hash = models.CharField(
        max_length=64, blank=True, default='',
        help_text=(
            "sha256 hex digest of the series ``name`` used to render the "
            "current ``auto_banner_url``. Used to detect name drift between "
            "renders."
        ),
    )
    zoom_meetings_last_run = models.JSONField(
        null=True, blank=True, default=None,
        help_text=(
            "Issue #859: structured summary of the most recent "
            "'Create Zoom meetings for all events' background run. Shape: "
            "``{finished_at, created: [event_id], skipped_existing: n, "
            "skipped_ineligible: n, failed: [{event_id, title, error}]}``. "
            "Null until the action has run at least once."
        ),
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if self.description:
            self.description_html = render_markdown(self.description)
        else:
            self.description_html = ''
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        # Note: the public URL path stays ``/events/groups/<slug>`` even
        # after the rename (issue #575) so external bookmarks keep working.
        # A follow-up issue may flip the public path to ``/events/series/``.
        return f'/events/groups/{self.slug}'

    def get_studio_edit_url(self):
        return f'/studio/event-series/{self.pk}/'

    @property
    def event_count(self):
        return self.events.count()

    @property
    def published_event_count(self):
        # Issue #863: count only occurrences a public visitor can actually see
        # (``upcoming`` / ``completed``). Excludes both ``draft`` and
        # ``cancelled`` so cancelling an occurrence decrements the count and the
        # number matches what the public series page lists.
        return self.events.filter(status__in=PUBLIC_EVENT_STATUSES).count()

    def is_publicly_visible(self):
        """Return True when a non-staff visitor may load the public page.

        Issue #858: an empty / unpublished series should 404 for the public
        rather than show a "no published events yet" placeholder. Two gates
        compose here:

        - ``is_active`` is the staff hide flag. When False the series is
          hidden from the public regardless of how many events it has.
        - At least one member event must be published. ``published_event_count``
          counts the ``upcoming`` / ``completed`` occurrences a visitor
          actually sees; a series whose occurrences are all still draft 404s
          for the public until staff publish at least one.

        Staff bypass this guard in the view so they can preview a series
        before publishing.
        """
        return self.is_active and self.published_event_count > 0
