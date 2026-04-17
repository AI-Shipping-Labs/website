import markdown as md
from django.db import models
from django.utils import timezone

from content.access import VISIBILITY_CHOICES


def render_markdown(text):
    """Convert markdown to HTML."""
    return md.markdown(
        text,
        extensions=[
            'fenced_code',
            'codehilite',
            'tables',
            'attr_list',
            'md_in_html',
        ],
        extension_configs={
            'codehilite': {
                'css_class': 'codehilite',
                'guess_lang': False,
            },
        },
    )


EVENT_TYPE_CHOICES = [
    ('live', 'Live'),
    ('async', 'Async'),
]

EVENT_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('upcoming', 'Upcoming'),
    ('live', 'Live'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
]

EVENT_PLATFORM_CHOICES = [
    ('zoom', 'Zoom'),
    ('custom', 'Custom URL'),
]


class Event(models.Model):
    """Event for live or async community activities.

    Also stores recording data inline (previously in a separate Recording model).
    Completed events with a recording_url are shown on /event-recordings.
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
    event_type = models.CharField(
        max_length=10,
        choices=EVENT_TYPE_CHOICES,
        default='live',
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
        help_text='Zoom meeting ID (populated for live events).',
    )
    zoom_join_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='Zoom join URL (populated for live events).',
    )
    location = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Location description (e.g. "Zoom" for live, or a GitHub repo for async).',
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
    content_id = models.UUIDField(
        unique=True, null=True, blank=True,
        help_text='Stable UUID from frontmatter for linking user-generated data.',
    )
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
        help_text='Controls visibility on the recordings page.',
    )
    published_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Datetime of first publish.',
    )
    speaker_name = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Speaker name from content repo frontmatter.',
    )
    speaker_bio = models.TextField(
        blank=True, default='',
        help_text='Speaker bio from content repo frontmatter.',
    )
    cover_image_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='Cover image URL from content repo.',
    )
    source_repo = models.CharField(
        max_length=300, blank=True, null=True, default=None,
        help_text='GitHub repo this content was synced from.',
    )
    source_path = models.CharField(
        max_length=500, blank=True, null=True, default=None,
        help_text='File path within the source repo.',
    )
    source_commit = models.CharField(
        max_length=40, blank=True, null=True, default=None,
        help_text='Git commit SHA of the last sync.',
    )
    ics_sequence = models.PositiveIntegerField(
        default=0,
        help_text='Sequence number for .ics calendar invite updates.',
    )
    recap = models.JSONField(
        default=dict, blank=True,
        help_text='Structured recap landing page sections. See docs for schema.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_datetime']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/events/{self.slug}'

    def get_recording_url(self):
        """Return the URL for the recording detail page."""
        return f'/event-recordings/{self.slug}'

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

        super().save(*args, **kwargs)

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
        return bool(self.recap)

    def get_recap_url(self):
        """Return the URL for the recap landing page."""
        return f'/events/{self.slug}/recap'

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
        """Return True if the Zoom join link should be shown (within 15 min of start)."""
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
