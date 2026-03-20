"""Redirect model for configurable URL redirects."""

from django.db import models


class Redirect(models.Model):
    """A URL redirect mapping source_path to target_path."""

    REDIRECT_TYPE_CHOICES = [
        (301, 'Permanent (301)'),
        (302, 'Temporary (302)'),
    ]

    source_path = models.CharField(
        max_length=500,
        unique=True,
        db_index=True,
        help_text='Source URL path, e.g. /ai-engineer-resources',
    )
    target_path = models.CharField(
        max_length=500,
        help_text='Target URL path, e.g. /interview',
    )
    redirect_type = models.IntegerField(
        choices=REDIRECT_TYPE_CHOICES,
        default=301,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['source_path']

    def __str__(self):
        return f'{self.source_path} -> {self.target_path} ({self.redirect_type})'
