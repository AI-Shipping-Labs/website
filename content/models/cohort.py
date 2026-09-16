from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

COHORT_MODE_COHORT = 'cohort'
COHORT_MODE_SELF_PACED = 'self_paced'

COHORT_MODE_CHOICES = [
    (COHORT_MODE_COHORT, 'Cohort'),
    (COHORT_MODE_SELF_PACED, 'Self-paced'),
]


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
    # Issue #1674: dates are required for mode='cohort' (today's behaviour,
    # made explicit) and must be null for mode='self_paced' — enforced in
    # clean(). Nullable so a self-paced cohort can exist with no dates at
    # all, which is what makes drip and event resolution fall back to
    # "always available" / "the fallback recording" for it.
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    mode = models.CharField(
        max_length=20, choices=COHORT_MODE_CHOICES,
        default=COHORT_MODE_COHORT, db_default=COHORT_MODE_COHORT,
        help_text=(
            "'cohort' (default): a dated, time-bound cohort — today's "
            "behaviour. 'self_paced': no dates, no event series, "
            "unlimited capacity. Matches curriculum.Cohort.mode in the "
            "future community_base package exactly (issue #1674)."
        ),
    )
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
            # Issue #1674: at most one self-paced cohort per course, mirroring
            # curriculum's ``cb_cohort_self_paced_unique`` in community_base.
            models.UniqueConstraint(
                fields=['course'],
                condition=Q(mode=COHORT_MODE_SELF_PACED),
                name='cohort_self_paced_unique_per_course',
            ),
        ]

    def __str__(self):
        return f'{self.course.title} - {self.name}'

    def clean(self):
        super().clean()
        if self.mode == COHORT_MODE_COHORT:
            if self.start_date is None or self.end_date is None:
                raise ValidationError(
                    "A dated cohort (mode='cohort') requires both "
                    "start_date and end_date."
                )
        elif self.mode == COHORT_MODE_SELF_PACED:
            if self.start_date is not None or self.end_date is not None:
                raise ValidationError(
                    "A self-paced cohort (mode='self_paced') must not "
                    "have start_date/end_date set."
                )
            if self.event_series_id is not None:
                raise ValidationError(
                    "A self-paced cohort (mode='self_paced') must not "
                    "have an event_series — it has no live-session series."
                )
            if self.max_participants is not None:
                raise ValidationError(
                    "A self-paced cohort (mode='self_paced') must not "
                    "have max_participants — capacity does not apply."
                )

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
