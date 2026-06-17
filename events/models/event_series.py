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

import zoneinfo

from django.db import models
from django.utils.text import slugify

from content.access import LEVEL_OPEN, VISIBILITY_CHOICES
from content.models.mixins import TimestampedModelMixin
from content.utils.markdown import render_description_html
from events.models.event import PUBLIC_EVENT_STATUSES

EVENT_SERIES_CADENCE_CHOICES = [
    ('weekly', 'Weekly'),
]

# Issue #877: a weekly series' occurrences are 7 days apart, but a clock
# change inside the series shifts one gap by an hour, which can push the
# day-boundary by one calendar day. We allow +/- 1 day around 7 so a
# genuinely-weekly European series spanning a DST change still reads as
# regular.
WEEKLY_GAP_DAYS = 7
WEEKLY_GAP_TOLERANCE_DAYS = 1


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
    required_level = models.IntegerField(
        default=LEVEL_OPEN,
        choices=VISIBILITY_CHOICES,
        help_text=(
            'Canonical access level for the series (issue #958). New '
            'occurrences inherit this level when no level is supplied, and '
            'occurrence writes via the API are validated against it (a '
            'mismatch is rejected; Studio allows a human-confirmed override). '
            'Changing this value never rewrites the levels of occurrences '
            'that already exist.'
        ),
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
        self.description_html = render_description_html(self.description)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return f'/events/series/{self.pk}/{self.slug}'

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

    def _visible_occurrences(self):
        """Ordered list of the occurrences a public visitor actually sees.

        Issue #877: the cadence label is derived from real occurrences, not
        from the series' first-occurrence fields. We compute over
        ``PUBLIC_EVENT_STATUSES`` (``upcoming`` / ``completed``) — the same
        set the public page lists — so the label a visitor reads matches the
        occurrence cards below it. A staff member previewing a draft-heavy
        series will see a label that counts only the visible occurrences,
        which is acceptable and documented.
        """
        return list(
            self.events.filter(status__in=PUBLIC_EVENT_STATUSES)
            .order_by('start_datetime')
        )

    @property
    def is_regular_cadence(self):
        """True only when the visible occurrences truly follow the weekly claim.

        Issue #854 introduced manually-defined / irregular schedules, so the
        stored ``day_of_week`` / ``start_time`` (the first occurrence) no
        longer describe the whole series. This property is the single source
        of truth for whether the fixed-cadence label is honest. It returns
        ``True`` only when every visible occurrence lands on the stored
        weekday at the stored local start time (each evaluated in that
        occurrence's own timezone), spaced ~7 days apart.
        """
        occurrences = self._visible_occurrences()
        if len(occurrences) < 2:
            # A 0- or 1-occurrence series cannot establish a cadence.
            return False

        for occ in occurrences:
            local = occ.start_datetime.astimezone(
                zoneinfo.ZoneInfo(occ.timezone)
            )
            if local.weekday() != self.day_of_week:
                return False
            if (local.hour, local.minute) != (
                self.start_time.hour, self.start_time.minute,
            ):
                return False

        for earlier, later in zip(occurrences, occurrences[1:]):
            gap_days = (
                later.start_datetime - earlier.start_datetime
            ).days
            if not (
                WEEKLY_GAP_DAYS - WEEKLY_GAP_TOLERANCE_DAYS
                <= gap_days
                <= WEEKLY_GAP_DAYS + WEEKLY_GAP_TOLERANCE_DAYS
            ):
                return False

        return True

    @property
    def schedule_label(self):
        """Rendered header string describing when the series meets.

        Issue #877: derives the label from the real occurrences so it stays
        honest once a schedule drifts (see ``is_regular_cadence``). The
        template renders this verbatim; no inline cadence computation.

        - Regular series keep the legacy phrasing, byte-identical to before.
        - Irregular series get a neutral, accurate summary of the actual
          sessions rather than a false weekly claim.
        - A zero-occurrence series returns ``''`` (the public page 404s for
          empty series; staff preview may still reach this and must not
          raise).
        """
        if self.is_regular_cadence:
            return (
                f'{self.get_cadence_display()} on '
                f'{self.get_day_of_week_display()} at '
                f'{self.start_time.strftime("%H:%M")} {self.timezone}'
            )

        occurrences = self._visible_occurrences()
        if not occurrences:
            return ''

        count = len(occurrences)
        first = occurrences[0].start_datetime.astimezone(
            zoneinfo.ZoneInfo(occurrences[0].timezone)
        )
        last = occurrences[-1].start_datetime.astimezone(
            zoneinfo.ZoneInfo(occurrences[-1].timezone)
        )
        noun = 'session' if count == 1 else 'sessions'
        first_str = first.strftime('%b %d, %Y')
        if count == 1:
            return f'{count} {noun} · {first_str}'
        last_str = last.strftime('%b %d, %Y')
        # En dash between the first and last date.
        return f'{count} {noun} · {first_str} – {last_str}'
