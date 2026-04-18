"""Course Enrollment model — issue #236.

The ``Enrollment`` is an explicit "I'm taking this course" signal,
separate from per-unit ``UserCourseProgress``. The dashboard's
"Continue Learning" panel queries enrollments rather than inferring
"in progress" from completed units, so that:

- Users who haven't marked any unit complete still appear on their
  dashboard once they click Enroll.
- Users who have completed units but have no active enrollment don't
  silently disappear (the backfill migration creates an enrollment for
  every (user, course) pair that has any completed unit).

Re-enrollment after unenroll is permitted via a partial unique index on
``(user, course) WHERE unenrolled_at IS NULL``.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from content.models.course import Course

SOURCE_MANUAL = 'manual'
SOURCE_AUTO_PROGRESS = 'auto_progress'
SOURCE_ADMIN = 'admin'

SOURCE_CHOICES = [
    (SOURCE_MANUAL, 'Manual'),
    (SOURCE_AUTO_PROGRESS, 'Auto (first lesson complete)'),
    (SOURCE_ADMIN, 'Admin (Studio)'),
]


class Enrollment(models.Model):
    """An explicit user-course enrollment record.

    See module docstring for context. ``unenrolled_at`` is the soft-delete
    marker; the partial unique index lets a user re-enroll after they have
    unenrolled without colliding with the historical row.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='enrollments',
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='enrollments',
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)
    unenrolled_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_MANUAL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'course'],
                condition=Q(unenrolled_at__isnull=True),
                name='unique_active_enrollment',
            ),
        ]
        ordering = ['-enrolled_at']

    def __str__(self):
        state = 'unenrolled' if self.unenrolled_at else 'active'
        return f'{self.user} -> {self.course.title} ({state})'

    @property
    def is_active(self):
        return self.unenrolled_at is None
