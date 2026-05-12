from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from content.access import VISIBILITY_CHOICES
from content.models.mixins import (
    SourceMetadataMixin,
    SyncedContentIdentityMixin,
    TimestampedModelMixin,
)
from content.utils.markdown import render_markdown

EVENT_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('upcoming', 'Upcoming'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
]

EVENT_PLATFORM_CHOICES = [
    ('zoom', 'Zoom'),
    ('custom', 'Custom URL'),
]

EVENT_KIND_CHOICES = [
    ('standard', 'Standard'),
    ('workshop', 'Workshop'),
    ('meetup', 'Meetup'),
    ('q_and_a', 'Q&A'),
]

EVENT_ORIGIN_CHOICES = [
    ('github', 'GitHub'),
    ('studio', 'Studio'),
]


class Event(
    SyncedContentIdentityMixin,
    SourceMetadataMixin,
    TimestampedModelMixin,
    models.Model,
):
    """Event for community activities.

    Also stores recording data inline (previously in a separate Recording model).
    Completed events with a recording_url are shown on /events?filter=past.
    """

    slug = models.SlugField(max_length=300, unique=True)
    title = models.CharField(max_length=300)
    description = models.TextField(
        blank=True, default='',
        help_text='Markdown description of the event.',
    )
    description_html = models.TextField(
        blank=True, default='',
        help_text='Auto-generated HTML from description markdown.',
    )
    kind = models.CharField(
        max_length=20,
        choices=EVENT_KIND_CHOICES,
        default='standard',
        help_text=(
            'Event sub-type used by the unified events feed: standard '
            'recording, workshop (linked to a Workshop row), meetup, or Q&A.'
        ),
    )
    platform = models.CharField(
        max_length=20,
        choices=EVENT_PLATFORM_CHOICES,
        default='zoom',
        help_text='Platform for the event: Zoom or a custom external URL.',
    )
    start_datetime = models.DateTimeField(
        help_text='Start date/time of the event.',
    )
    end_datetime = models.DateTimeField(
        null=True, blank=True,
        help_text='End date/time of the event (optional).',
    )
    timezone = models.CharField(
        max_length=100, default='Europe/Berlin',
        help_text='Timezone for the event (e.g. Europe/Berlin).',
    )
    zoom_meeting_id = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Zoom meeting ID for events hosted on Zoom.',
    )
    zoom_join_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='Join URL for Zoom or custom-platform events.',
    )
    location = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Location description, such as Zoom, Discord, or an external resource.',
    )
    tags = models.JSONField(default=list, blank=True)
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text='Minimum tier level required to register.',
    )
    max_participants = models.IntegerField(
        null=True, blank=True,
        help_text='Maximum number of participants. Null means unlimited.',
    )
    status = models.CharField(
        max_length=20,
        choices=EVENT_STATUS_CHOICES,
        default='draft',
    )

    # --- Recording fields (previously on content.Recording) ---
    recording_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='Primary video URL (YouTube, S3, etc.).',
    )
    recording_s3_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='S3 raw recording file URL.',
    )
    recording_embed_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='Legacy Google Drive embed URL.',
    )
    transcript_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='URL to the VTT transcript file.',
    )
    transcript_text = models.TextField(
        blank=True, default='',
        help_text='Plain-text transcript content for display and search.',
    )
    timestamps = models.JSONField(
        default=list, blank=True,
        help_text='JSON list of {time_seconds, label}.',
    )
    materials = models.JSONField(
        default=list, blank=True,
        help_text='JSON list of {title, url, type}.',
    )
    core_tools = models.JSONField(
        default=list, blank=True,
        help_text='JSON list of tool names.',
    )
    learning_objectives = models.JSONField(
        default=list, blank=True,
        help_text='JSON list of objective strings.',
    )
    outcome = models.TextField(
        blank=True, default='',
        help_text='Text describing expected outcome.',
    )
    related_course = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Slug of a related course.',
    )
    published = models.BooleanField(
        default=True,
        help_text='Controls visibility on the /events?filter=past page.',
    )
    published_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Datetime of first publish.',
    )
    instructors = models.ManyToManyField(
        'content.Instructor',
        through='events.EventInstructor',
        related_name='events',
        blank=True,
        help_text=(
            'Instructors / speakers for this event. Order is controlled via '
            'the EventInstructor.position field; the first instructor is the '
            'primary speaker shown on listings and cards.'
        ),
    )
    cover_image_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='Cover image URL from content repo.',
    )
    ics_sequence = models.PositiveIntegerField(
        default=0,
        help_text='Sequence number for .ics calendar invite updates.',
    )
    recap = models.JSONField(
        default=dict, blank=True,
        help_text='Structured recap landing page sections. See docs for schema.',
    )
    recap_file = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Content-repo markdown file used to render the recap page.',
    )
    recap_markdown = models.TextField(
        blank=True, default='',
        help_text='Raw synced markdown body for the recap page.',
    )
    recap_html = models.TextField(
        blank=True, default='',
        help_text='Rendered recap HTML, including content-repo includes.',
    )
    recap_data = models.JSONField(
        default=dict, blank=True,
        help_text='Data from recap frontmatter / event recap_data for include rendering.',
    )

    # Issue #564: explicit origin gate. ``github`` means the row is synced
    # from a content repo and ``source_repo`` must be non-empty; ``studio``
    # means the row was authored in Studio (e.g. via the event-series flow)
    # and ``source_repo`` must be empty. The invariant is enforced in
    # ``Event.save()`` so a future code path cannot silently mix the two.
    origin = models.CharField(
        max_length=20,
        choices=EVENT_ORIGIN_CHOICES,
        default='studio',
        help_text=(
            'Authoritative source-of-truth gate. ``github`` rows are '
            'managed by the sync pipeline; ``studio`` rows are authored '
            'in Studio and never touched by sync.'
        ),
    )
    event_series = models.ForeignKey(
        'events.EventSeries',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='events',
        help_text=(
            'Optional parent series. Deleting the series preserves the '
            'events but unlinks them.'
        ),
    )
    series_position = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='1-indexed position within the parent event series.',
    )

    # Issue #572: third-party host indicator. Empty string (the default)
    # means the event is community-hosted (current behavior). A non-empty
    # value flips the event to "external" mode: the listing shows a
    # "Hosted on X" pill, and the detail page replaces the registration
    # card with an outbound Join card. ``required_level`` no longer gates
    # access for external events — the partner controls access on their
    # platform.
    external_host = models.CharField(
        max_length=100, blank=True, default='',
        help_text=(
            'Display name of the third-party host (e.g. "Maven", "Luma", '
            '"DataTalksClub"). Leave blank for community-hosted events.'
        ),
    )

    class Meta:
        ordering = ['-start_datetime']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/events/{self.slug}'

    def get_recording_url(self):
        """Returns the canonical URL for viewing the event (and its recording, if present)."""
        return f'/events/{self.slug}'

    def save(self, *args, **kwargs):
        from content.utils.tags import normalize_tags
        self.tags = normalize_tags(self.tags)

        if self.description:
            self.description_html = render_markdown(self.description)

        # Sync published_at with published flag
        if self.published and not self.published_at:
            self.published_at = timezone.now()
        elif not self.published:
            self.published_at = None

        # Issue #564: enforce the origin invariant.
        # ``github`` MUST have a non-empty ``source_repo``; ``studio`` MUST
        # NOT. Violation raises ``ValidationError`` so a careless code path
        # cannot silently mix the two sources.
        has_source_repo = bool(self.source_repo)
        if self.origin == 'github' and not has_source_repo:
            raise ValidationError(
                "Event.origin='github' requires a non-empty source_repo. "
                "Set source_repo or change origin to 'studio'."
            )
        if self.origin == 'studio' and has_source_repo:
            raise ValidationError(
                "Event.origin='studio' must have an empty source_repo. "
                "Clear source_repo or change origin to 'github'."
            )

        super().save(*args, **kwargs)

    @property
    def ordered_instructors(self):
        """Return ``Instructor`` rows in ``EventInstructor.position`` order."""
        return list(self.instructors.order_by('eventinstructor__position'))

    @property
    def primary_instructor(self):
        """First instructor (speaker) by position, or ``None`` when unset."""
        return self.instructors.order_by(
            'eventinstructor__position',
        ).first()

    @property
    def video_url(self):
        """Return the primary video URL (s3, recording_url, or embed)."""
        return self.recording_s3_url or self.recording_url or self.recording_embed_url

    @property
    def has_recording(self):
        """Return True if this event has a recording."""
        return bool(self.video_url)

    @property
    def has_recap(self):
        """Return True if this event has any recap data."""
        return bool(self.recap_html)

    def get_recap_url(self):
        """Return the URL for the recap landing page."""
        return f'/events/{self.slug}/recap'

    @property
    def is_external(self):
        """Return True when this event is hosted on a third-party platform.

        Issue #572. Templates and views branch on this to render the
        outbound "Hosted on X" pill and the external Join card instead
        of the in-app registration card. Stripped to keep whitespace-only
        values from accidentally flipping an event to external mode.
        """
        return bool((self.external_host or '').strip())

    @property
    def is_upcoming(self):
        return self.status == 'upcoming'

    @property
    def is_past(self):
        return self.status in ('completed', 'cancelled')

    @property
    def registration_count(self):
        return self.registrations.count()

    @property
    def spots_remaining(self):
        """Return spots remaining if max_participants is set, else None."""
        if self.max_participants is None:
            return None
        return max(0, self.max_participants - self.registration_count)

    @property
    def is_full(self):
        """Return True if event is at capacity."""
        if self.max_participants is None:
            return False
        return self.registration_count >= self.max_participants

    @property
    def join_click_count(self):
        """Return the total number of join clicks for this event."""
        return self.join_clicks.count()

    def can_show_zoom_link(self):
        """Return True if the join link should be shown within 15 minutes of start."""
        if not self.zoom_join_url:
            return False
        now = timezone.now()
        minutes_until_start = (self.start_datetime - now).total_seconds() / 60
        return minutes_until_start <= 15

    def formatted_start(self):
        """Return a formatted start datetime string."""
        return self.start_datetime.strftime('%B %d, %Y at %H:%M UTC')

    def formatted_date(self):
        """Return a formatted date string."""
        return self.start_datetime.strftime('%B %d, %Y')

    def short_date(self):
        return self.start_datetime.strftime('%b %d, %Y')

    def formatted_time(self):
        return self.start_datetime.strftime('%H:%M UTC')


class EventInstructor(models.Model):
    """Through model linking Event -> Instructor with display order.

    Lives in the events app (alongside Event) so the cross-app FK direction
    is clean: events depends on content (Instructor), not the other way.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    instructor = models.ForeignKey(
        'content.Instructor', on_delete=models.PROTECT,
    )
    position = models.PositiveIntegerField(
        default=0,
        help_text='Display order; 0 is the primary speaker.',
    )

    class Meta:
        ordering = ['position']
        unique_together = [('event', 'instructor')]

    def __str__(self):
        return f'{self.event} - {self.instructor} (#{self.position})'
