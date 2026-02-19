from django.db import models
from django.utils import timezone

from content.access import VISIBILITY_CHOICES


class Recording(models.Model):
    """Event recording / workshop resource."""
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(blank=True, default='')
    event = models.ForeignKey(
        'events.Event',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='recordings',
        help_text='Link to the originating event, if any.',
    )
    date = models.DateField()
    tags = models.JSONField(default=list, blank=True)
    level = models.CharField(max_length=100, blank=True, default='')
    google_embed_url = models.URLField(max_length=500, blank=True, default='')
    youtube_url = models.URLField(max_length=500, blank=True, default='')
    timestamps = models.JSONField(default=list, blank=True)
    materials = models.JSONField(default=list, blank=True)
    core_tools = models.JSONField(default=list, blank=True)
    learning_objectives = models.JSONField(default=list, blank=True)
    outcome = models.TextField(blank=True, default='')
    related_course = models.CharField(max_length=300, blank=True, default='')
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to view full content.",
    )
    published = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        """Normalize tags and sync published_at with published flag."""
        from content.utils.tags import normalize_tags
        self.tags = normalize_tags(self.tags)

        if self.published and not self.published_at:
            self.published_at = timezone.now()
        elif not self.published:
            self.published_at = None
        super().save(*args, **kwargs)

    @property
    def video_url(self):
        """Return the primary video URL (youtube_url or google_embed_url)."""
        return self.youtube_url or self.google_embed_url

    def get_absolute_url(self):
        return f'/event-recordings/{self.slug}'

    def formatted_date(self):
        return self.date.strftime('%B %d, %Y')

    def short_date(self):
        return self.date.strftime('%b %d, %Y')
