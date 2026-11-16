"""Course announcements authored in Studio (issue #1942).

AISL overlay: not a community-base curriculum change. Each announcement
belongs to one cohort of its course: only that cohort's members see the
published row on the course Home Announcements tab, and the first publish
emails that cohort's actively enrolled members once.
"""

from django.conf import settings
from django.db import models

from content.models.course import Course


class CourseAnnouncement(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PUBLISHED, 'Published'),
    ]

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='announcements',
    )
    # SET_NULL: deleting a cohort hides its announcements from learners
    # (a null cohort has no audience) instead of deleting the history.
    cohort = models.ForeignKey(
        'content.Cohort',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='announcements',
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='authored_course_announcements',
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    email_queued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-published_at', '-created_at']

    def __str__(self):
        return f'{self.title} ({self.course.slug})'
