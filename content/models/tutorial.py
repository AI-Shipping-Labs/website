from django.db import models

from content.access import VISIBILITY_CHOICES


class Tutorial(models.Model):
    """Step-by-step tutorial."""
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(blank=True, default='')
    content_markdown = models.TextField(blank=True, default='')
    content_html = models.TextField(blank=True, default='')
    date = models.DateField()
    tags = models.JSONField(default=list, blank=True)
    reading_time = models.CharField(max_length=50, blank=True, default='')
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to view full content.",
    )
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/tutorials/{self.slug}'

    def formatted_date(self):
        return self.date.strftime('%B %d, %Y')

    def short_date(self):
        return self.date.strftime('%b %d, %Y')
