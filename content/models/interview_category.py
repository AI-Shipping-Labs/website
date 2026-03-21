import json

import markdown

from django.db import models


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


class InterviewCategory(models.Model):
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
    source_repo = models.CharField(
        max_length=300, blank=True, null=True, default=None,
    )
    source_path = models.CharField(
        max_length=500, blank=True, null=True, default=None,
    )
    source_commit = models.CharField(
        max_length=40, blank=True, null=True, default=None,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['slug']
        verbose_name_plural = 'interview categories'

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/interview/{self.slug}'
