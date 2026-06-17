from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from content.access import VISIBILITY_CHOICES
from content.models.mixins import (
    SourceMetadataMixin,
    SyncedContentIdentityMixin,
    TimestampedModelMixin,
)
from content.utils.markdown import (
    render_description_html,
    render_markdown,  # noqa: F401  re-exported: renderer-parity tests import it from here
)

EVENT_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('upcoming', 'Upcoming'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
]

# Issue #863: statuses that are hidden from public visitors. ``draft`` is the
# pre-publish state and ``cancelled`` occurrences should disappear entirely for
# visitors (no "Cancelled" badge). Staff still see them so they can manage them.
# This single set is the authoritative definition reused by the public events
# list, calendar, public series page, and ``EventSeries.published_event_count``.
HIDDEN_FROM_PUBLIC_STATUSES = {'draft', 'cancelled'}

# The complementary set: statuses an occurrence can hold and still be public.
PUBLIC_EVENT_STATUSES = {'upcoming', 'completed'}

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

# Issue #673: cap slug length on save so a giant title can't produce a
# 200+ char URL. Capped at 70 chars (parity with Luma) and rounded back to
# the last ``-`` separator if 70 lands mid-word, so the truncated tail is
# whole words rather than a half-clipped one.
EVENT_SLUG_MAX_LENGTH = 70


def _truncate_event_slug(slug):
    """Return ``slug`` truncated to ``EVENT_SLUG_MAX_LENGTH``.

    The slug is purely cosmetic in the new ``/events/<id>/<slug>`` URL
    pattern (issue #673), so we cap its length to keep canonical URLs
    short. The truncation rules:

    1. Short slugs are returned unchanged.
    2. If the truncation point lands inside a word (the next character
       would NOT be ``-``), walk back to the previous ``-`` so the
       truncated tail is a whole word.
    3. Strip trailing ``-`` so the URL never ends with a stray dash.
    4. If steps 1-3 produce an empty string (e.g. slug was a single
       long word with no dashes), fall back to the un-truncated value
       so we never silently emit a blank slug.
    """
    if not slug or len(slug) <= EVENT_SLUG_MAX_LENGTH:
        return slug

    # If the 70th char is mid-word (next char isn't a boundary), walk
    # back to the previous ``-`` so we end on a whole word.
    truncated = slug[:EVENT_SLUG_MAX_LENGTH]
    next_char = slug[EVENT_SLUG_MAX_LENGTH]
    if next_char != '-' and '-' in truncated:
        truncated = truncated.rsplit('-', 1)[0]

    truncated = truncated.rstrip('-')
    if not truncated:
        return slug
    return truncated


def _effective_end_datetime(start_datetime, end_datetime):
    if end_datetime is not None:
        return end_datetime
    if start_datetime is None:
        return None
    return start_datetime + timedelta(hours=1)


# Issue #579: canonical list of supported third-party hosts for the
# "Hosted on X" pill. Adding a new partner is a one-line code change
# here plus a Studio dropdown re-render. The display label is what
# renders in the pill, so the value column must be exactly the casing
# we want shown on the public site.
EXTERNAL_HOST_CHOICES = [
    ('', 'Community-hosted'),
    ('Maven', 'Maven'),
    ('Luma', 'Luma'),
    ('DataTalksClub', 'DataTalksClub'),
]

_STATIC_HOST_PHOTO_BY_SLUG = {
    'alexey-grigorev': 'alexey.png',
    'valeriia-kuka': 'valeriia.png',
}


def render_host_markdown(text):
    """Convert event host markdown to HTML without external-link rewriting."""
    return render_markdown(
        text,
        include_external_links=False,
    )


class Host(TimestampedModelMixin, models.Model):
    """A person who hosts community events."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    title = models.CharField(max_length=200, blank=True, default='')
    bio = models.TextField(
        blank=True, default='',
        help_text='Markdown bio rendered to HTML on save.',
    )
    bio_html = models.TextField(
        blank=True, default='', editable=False,
        help_text='Auto-rendered HTML from bio markdown.',
    )
    photo_url = models.URLField(
        max_length=500,
        blank=True,
        default='',
        help_text='Photo URL. Falls back to a static asset for seeded hosts.',
    )
    email = models.EmailField(
        blank=True,
        default='',
        help_text='Display/contact email only; not used for calendar invites.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Render bio markdown to bio_html on save."""
        if self.bio:
            self.bio_html = render_host_markdown(self.bio)
        else:
            self.bio_html = ''
        super().save(*args, **kwargs)

    @property
    def display_photo_url(self):
        """The configured photo, falling back to the static asset by slug."""
        if self.photo_url:
            return self.photo_url
        filename = _STATIC_HOST_PHOTO_BY_SLUG.get(self.slug, f'{self.slug}.png')
        return static(filename)


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
    hosts = models.ManyToManyField(
        'events.Host',
        through='events.EventHost',
        related_name='events',
        blank=True,
        help_text=(
            'Hosts for this event. Order is controlled via the '
            'EventHost.position field.'
        ),
    )
    cover_image_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='Cover image URL from content repo.',
    )
    auto_banner_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text=(
            "Platform-generated OG banner URL (banner-generator Lambda, "
            "issue #895). Overwritten by the auto-banner pipeline; templates "
            "should prefer ``cover_image_url`` and fall back to this."
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
            "sha256 hex digest of the title used to render the current "
            "``auto_banner_url``. Used to detect title drift between syncs."
        ),
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

    # Issue #680: host-authored recap body for the post-event follow-up
    # email. Markdown. Optional — when blank the follow-up template uses a
    # generic fallback string so a host who forgets to fill this in still
    # gets a usable email.
    post_event_summary = models.TextField(
        blank=True, default='',
        help_text=(
            'Markdown summary used as the body of the post-event follow-up '
            'email. Leave blank for the generic fallback copy.'
        ),
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
    # Platform user email for the event host attendee. When it resolves to a
    # user, the save path auto-registers that user so normal attendee
    # confirmation, reminder, and reschedule emails apply.
    host_email = models.EmailField(
        blank=True, default='',
        help_text=(
            'Email address of a platform user who should be auto-registered '
            'as the event host attendee. Leave blank to skip host '
            'auto-registration.'
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
    title_is_auto = models.BooleanField(
        default=True,
        help_text=(
            'True when the occurrence title is auto-generated from the '
            'series name + chronological position, so it is rewritten on '
            'series rename / renumber. Set False the moment an operator '
            'supplies an explicit title (then it is sacrosanct).'
        ),
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
        choices=EXTERNAL_HOST_CHOICES,
        help_text=(
            'Third-party host shown as a "Hosted on X" pill. Supported: '
            'Maven, Luma, DataTalksClub. Leave blank for community-hosted '
            'events.'
        ),
    )

    class Meta:
        ordering = ['-start_datetime']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        """Return the canonical ``/events/<id>/<slug>`` URL.

        Issue #673: URL encodes the integer primary key first, then the
        slug as a cosmetic segment. ``event_detail`` reads the id only;
        the slug is verified against the stored value and a mismatch
        triggers a 301 to the canonical form.

        Returns ``''`` for unsaved rows (``self.id is None``) so admin
        previews and ``__str__`` don't raise ``NoReverseMatch``.
        """
        if self.id is None:
            return ''
        return reverse(
            'event_detail',
            kwargs={'event_id': self.id, 'slug': self.slug},
        )

    def get_studio_edit_url(self):
        return f'/studio/events/{self.pk}/edit'

    def get_recording_url(self):
        """Return the canonical URL for viewing the event (and its recording).

        Issue #673: de-duplicated with ``get_absolute_url`` — both
        surfaces resolve to the same id+slug URL.
        """
        return self.get_absolute_url()

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        update_field_names = None
        if update_fields is not None:
            update_field_names = set(update_fields)

        old_schedule = None
        should_check_schedule = not self._state.adding and self.pk is not None
        if should_check_schedule and update_field_names is not None:
            should_check_schedule = bool(
                {'start_datetime', 'end_datetime'} & update_field_names
            )
        if should_check_schedule:
            old_schedule = (
                type(self).objects
                .filter(pk=self.pk)
                .values('start_datetime', 'end_datetime', 'ics_sequence')
                .first()
            )

        from content.utils.tags import normalize_tags
        self.tags = normalize_tags(self.tags)

        self.description_html = render_description_html(self.description)

        # Issue #673: cap slug length so a 200-char title cannot produce
        # a giant ``/events/<id>/<200-char-slug>`` URL. Truncates on the
        # last ``-`` boundary so the tail is a whole word.
        if self.slug:
            self.slug = _truncate_event_slug(self.slug)

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

        if old_schedule is not None:
            old_effective_end = _effective_end_datetime(
                old_schedule['start_datetime'],
                old_schedule['end_datetime'],
            )
            new_effective_end = _effective_end_datetime(
                self.start_datetime,
                self.end_datetime,
            )
            schedule_changed = (
                self.start_datetime != old_schedule['start_datetime']
                or new_effective_end != old_effective_end
            )
            if schedule_changed:
                old_sequence = old_schedule['ics_sequence'] or 0
                self.ics_sequence = max(self.ics_sequence or 0, old_sequence) + 1
                if update_field_names is not None:
                    update_field_names.update({'ics_sequence', 'updated_at'})
                    kwargs['update_fields'] = update_field_names

        super().save(*args, **kwargs)

    @property
    def ordered_instructors(self):
        """Return ``Instructor`` rows in ``EventInstructor.position`` order."""
        return list(self.instructors.order_by('eventinstructor__position'))

    @property
    def ordered_hosts(self):
        """Return ``Host`` rows in ``EventHost.position`` order."""
        prefetched = getattr(self, '_prefetched_objects_cache', {})
        if 'event_host_links' in prefetched:
            return [link.host for link in prefetched['event_host_links']]
        return [
            link.host
            for link in self.event_host_links.select_related('host').order_by(
                'position',
            )
        ]

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
    def effective_end_datetime(self):
        """Return the effective end of the event.

        Issue #712 / #713: when ``end_datetime`` is explicitly set we
        use it; otherwise an event is assumed to last 1 hour from its
        ``start_datetime`` (mirrors the ``.ics`` fallback in
        ``events/services/calendar_invite.py`` and the cron logic in
        ``events/tasks/complete_finished_events.py``).
        """
        return _effective_end_datetime(self.start_datetime, self.end_datetime)

    @property
    def is_upcoming(self):
        """Return True when the event is still scheduled in the future.

        Issue #713: time-derived. An event is "upcoming" only when it
        is neither draft nor cancelled AND ``now`` is before
        ``effective_end_datetime``. The legacy ``status='completed'``
        flag is intentionally ignored — once an end time has passed an
        event is past regardless of the stored status, and a legacy
        ``completed`` row with a future end time renders upcoming.
        """
        if self.status in ('draft', 'cancelled'):
            return False
        return timezone.now() < self.effective_end_datetime

    @property
    def is_past(self):
        """Return True when the event has finished or been cancelled.

        Issue #713: time-derived. Cancelled events are always treated
        as past so they never offer registration or join links.
        Drafts return ``False`` (drafts are not in any state for
        visitors — they 404 anyway). Otherwise an event is past once
        ``now >= effective_end_datetime``.
        """
        if self.status == 'cancelled':
            return True
        if self.status == 'draft':
            return False
        return timezone.now() >= self.effective_end_datetime

    @property
    def registration_count(self):
        return self.registrations.count()

    @property
    def attendee_count(self):
        """Return attendee count, preferring an annotated value when present.

        Issue #668: callers may annotate ``Count('registrations')`` onto
        the queryset as ``_attendee_count`` to render the social-proof
        chip on list-style pages (e.g. the event series view) without an
        N+1. The detail view does not annotate; it falls back to the
        single-row ``registration_count`` property.
        """
        if hasattr(self, '_attendee_count'):
            return self._attendee_count
        return self.registration_count

    @property
    def join_click_count(self):
        """Return the total number of join clicks for this event."""
        return self.join_clicks.count()

    def can_show_zoom_link(self):
        """Return True during the 5-minute pre-start/live join window."""
        if not self.zoom_join_url or self.status in ('draft', 'cancelled'):
            return False
        if not self.start_datetime or not self.effective_end_datetime:
            return False
        now = timezone.now()
        join_window_opens = self.start_datetime - timedelta(minutes=5)
        return join_window_opens <= now <= self.effective_end_datetime

    def formatted_start(self):
        """Return a formatted start datetime string."""
        return self.start_datetime.strftime('%B %d, %Y at %H:%M UTC')

    def formatted_date(self):
        """Return a formatted date string."""
        value = self.start_datetime
        return f'{value.strftime("%B")} {value.day}, {value.year}'

    def short_date(self):
        value = self.start_datetime
        return f'{value.strftime("%b")} {value.day}, {value.year}'

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


class EventHost(models.Model):
    """Through model linking Event -> Host with display order."""

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='event_host_links',
    )
    host = models.ForeignKey(
        'events.Host',
        on_delete=models.PROTECT,
        related_name='event_host_links',
    )
    position = models.PositiveIntegerField(
        default=0,
        help_text='Display order; 0 is the primary host.',
    )

    class Meta:
        ordering = ['position']
        unique_together = [('event', 'host')]

    def __str__(self):
        return f'{self.event} - {self.host} (#{self.position})'
