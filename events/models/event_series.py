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
    cadence_weeks = models.PositiveIntegerField(
        default=1,
        help_text=(
            'Reserved for the "every N weeks" stretch goal. v1 always '
            'stores 1 and the generator uses 7 days between occurrences.'
        ),
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

    @property
    def event_count(self):
        return self.events.count()

    @property
    def published_event_count(self):
        return self.events.exclude(status='draft').count()
