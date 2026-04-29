
import markdown
from django.db import models

from content.models.mixins import SourceMetadataMixin, TimestampedModelMixin


def render_markdown(text):
    """Convert markdown to HTML with syntax highlighting."""
    return markdown.markdown(
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


class InterviewCategory(SourceMetadataMixin, TimestampedModelMixin, models.Model):
    """An interview questions category synced from GitHub content repo."""

    slug = models.SlugField(max_length=300, unique=True)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=50, blank=True, default='')
    sections_json = models.JSONField(
        default=list, blank=True,
        help_text="List of section objects with id, title, intro, qa.",
    )
    body_markdown = models.TextField(
        blank=True, default='',
        help_text="Markdown body content from the file.",
    )
    class Meta:
        ordering = ['slug']
        verbose_name_plural = 'interview categories'

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/interview/{self.slug}'
