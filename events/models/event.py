import markdown as md

from django.conf import settings
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


class Event(models.Model):
    """Event for live or async community activities."""

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
    recording = models.ForeignKey(
        'content.Recording',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='events',
        help_text='Link to the recording after event completion.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_datetime']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/events/{self.slug}'

    def save(self, *args, **kwargs):
        if self.description:
            self.description_html = render_markdown(self.description)
        super().save(*args, **kwargs)

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

    def short_date(self):
        return self.start_datetime.strftime('%b %d, %Y')

    def formatted_time(self):
        return self.start_datetime.strftime('%H:%M UTC')
