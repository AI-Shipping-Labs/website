from django.conf import settings
from django.db import models


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

    class Meta:
        ordering = ['start_date']

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
