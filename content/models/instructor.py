"""Instructor as a first-class entity (issue #308).

Centralizes instructor info (name, bio, photo, links) so a single source of
truth can be referenced from Course, Workshop, and Event yaml/markdown by
``instructor_id``. The legacy string fields on those models stay populated
via a sync-time mirror (FIRST resolved instructor's name/bio) so existing
templates render unchanged. Phase 2 will migrate templates to consume the
M2M directly.

Per-content-type through models exist so co-presenters render in
author-controlled order via ``position``. ``on_delete=PROTECT`` on the
through-model FK to ``Instructor`` prevents accidental hard-delete of an
Instructor that still has content referencing it. The intended deletion
path is soft-delete (``status='draft'``) handled by the sync layer when an
instructor's yaml is removed.
"""

from django.db import models

from content.models.mixins import SourceMetadataMixin, TimestampedModelMixin
from content.utils.markdown import render_markdown as _render_markdown


def render_markdown(text):
    """Convert instructor markdown to HTML without external-link rewriting."""
    return _render_markdown(
        text,
        include_external_links=False,
    )


STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('published', 'Published'),
]


class Instructor(SourceMetadataMixin, TimestampedModelMixin, models.Model):
    """A person who teaches courses, workshops, or speaks at events.

    Identified by a stable, human-readable ``instructor_id`` slug
    (e.g. ``alexey-grigorev``). Synced from yaml files under the
    ``instructors/`` subdirectory of the content repo, but rows can also
    originate as backfill records (``source_repo IS NULL``) created from
    legacy string fields prior to this issue.
    """

    instructor_id = models.SlugField(
        max_length=200, unique=True,
        help_text="Stable, human-readable slug (e.g. 'alexey-grigorev').",
    )
    name = models.CharField(max_length=200)
    bio = models.TextField(
        blank=True, default='',
        help_text='Markdown bio rendered to HTML on save.',
    )
    bio_html = models.TextField(
        blank=True, default='', editable=False,
        help_text='Auto-rendered HTML from bio markdown.',
    )
    photo_url = models.URLField(max_length=500, blank=True, default='')
    links = models.JSONField(
        default=list, blank=True,
        help_text='List of {label, url} dicts for social/profile links.',
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft',
    )
    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Render bio markdown to bio_html on save."""
        if self.bio:
            self.bio_html = render_markdown(self.bio)
        else:
            self.bio_html = ''
        super().save(*args, **kwargs)


class CourseInstructor(models.Model):
    """Through model linking Course -> Instructor with display order."""

    course = models.ForeignKey('content.Course', on_delete=models.CASCADE)
    instructor = models.ForeignKey(Instructor, on_delete=models.PROTECT)
    position = models.PositiveIntegerField(
        default=0,
        help_text='Display order; 0 is the primary instructor.',
    )

    class Meta:
        ordering = ['position']
        unique_together = [('course', 'instructor')]

    def __str__(self):
        return f'{self.course} - {self.instructor} (#{self.position})'


class WorkshopInstructor(models.Model):
    """Through model linking Workshop -> Instructor with display order."""

    workshop = models.ForeignKey('content.Workshop', on_delete=models.CASCADE)
    instructor = models.ForeignKey(Instructor, on_delete=models.PROTECT)
    position = models.PositiveIntegerField(
        default=0,
        help_text='Display order; 0 is the primary instructor.',
    )

    class Meta:
        ordering = ['position']
        unique_together = [('workshop', 'instructor')]

    def __str__(self):
        return f'{self.workshop} - {self.instructor} (#{self.position})'
