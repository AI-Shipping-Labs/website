from django.conf import settings
from django.db import models
from django.db.models import Q


class Cohort(models.Model):
    """A cohort is a time-bound group enrollment for a course."""

    course = models.ForeignKey(
        'content.Course',
        on_delete=models.CASCADE,
        related_name='cohorts',
    )
    name = models.CharField(
        max_length=200,
        help_text='e.g. "March 2026 Cohort"',
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    max_participants = models.IntegerField(
        null=True, blank=True,
        help_text="Maximum number of participants. Leave blank for unlimited.",
    )
    # Issue #1660: mirrors ``Sprint.event_series`` / ``Book.event_series``.
    # ``SET_NULL`` (not ``CASCADE``): deleting the series only severs the
    # link, the cohort itself is preserved. A cohort links to at most one
    # series; the FK carries no uniqueness constraint the other way
    # (mirrors the existing Sprint/Book precedent).
    event_series = models.ForeignKey(
        'events.EventSeries',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='cohorts',
        help_text=(
            'Optional recurring meeting series (e.g. office hours) whose '
            'occurrences are surfaced to enrolled cohort members on the '
            'course page. Deleting the series unlinks the cohort; the '
            'cohort itself is preserved.'
        ),
    )
    external_key = models.CharField(
        max_length=255, blank=True, default='', db_default='',
        help_text=(
            "Maven's cohort identifier string, matched case-insensitively "
            "against the Maven webhook's cohort_key at enrollment time "
            "(issue #1659). Source-owned from course.yaml's cohorts: list; "
            "blank means no external mapping. Unique per course when set."
        ),
    )

    class Meta:
        ordering = ['start_date']
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'external_key'],
                condition=~Q(external_key=''),
                name='unique_cohort_course_external_key',
            ),
        ]

    def __str__(self):
        return f'{self.course.title} - {self.name}'

    @property
    def enrollment_count(self):
        """Return the number of users enrolled in this cohort."""
        return self.enrollments.count()

    @property
    def is_full(self):
        """Return True if cohort is at max capacity."""
        if self.max_participants is None:
            return False
        return self.enrollment_count >= self.max_participants

    @property
    def spots_remaining(self):
        """Return the number of remaining spots, or None if unlimited."""
        if self.max_participants is None:
            return None
        return max(0, self.max_participants - self.enrollment_count)


class CohortEnrollment(models.Model):
    """Tracks a user's enrollment in a specific cohort."""

    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name='enrollments',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='cohort_enrollments',
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('cohort', 'user')]

    def __str__(self):
        return f'{self.user} - {self.cohort.name}'
