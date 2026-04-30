from django.db import models

from content.models.mixins import SourceMetadataMixin, TimestampedModelMixin
from content.utils.markdown import render_markdown as _render_markdown


def render_markdown(text):
    """Convert interview category markdown to HTML with the interview subset."""
    return _render_markdown(
        text,
        include_mermaid=False,
        include_external_links=False,
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
