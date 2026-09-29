"""Course cohort defaults used by sync and learner enrollment paths."""

from django.db import transaction
from django.utils import timezone

from content.models.cohort import (
    COHORT_MODE_COHORT,
    COHORT_MODE_SELF_PACED,
    Cohort,
    CohortEnrollment,
)


def ensure_course_self_paced_cohort(course):
    """Ensure a course without a dated cohort has its default cohort.

    A generated self-paced cohort fills in course data that has no real
    scheduled offering. It must never be added beside a dated cohort such
    as Buildcamp's Cohort 4.
    """
    if Cohort.objects.filter(course=course, mode=COHORT_MODE_COHORT).exists():
        return None

    cohort = Cohort.objects.filter(
        course=course, mode=COHORT_MODE_SELF_PACED,
    ).first()
    if cohort is not None:
        return cohort

    cohort, _created = Cohort.objects.get_or_create(
        course=course,
        mode=COHORT_MODE_SELF_PACED,
        defaults={
            'name': 'Self-paced',
            'external_key': '',
            'start_date': None,
            'end_date': None,
        },
    )
    return cohort


def get_course_cohort_by_key(course, cohort_key):
    """Return ``course``'s cohort whose external key (the ``?cohort=`` value)
    matches ``cohort_key`` case-insensitively, or ``None``."""
    cohort_key = str(cohort_key or '').strip()
    if not cohort_key:
        return None
    return (
        Cohort.objects.filter(course=course)
        .exclude(external_key='')
        .filter(external_key__iexact=cohort_key)
        .first()
    )


def ordered_course_cohorts(course, today=None):
    """Return ``course``'s cohorts for staff pickers.

    Dated cohorts come first: current and upcoming ones by start date, then
    past ones newest first. Self-paced cohorts come last.
    """
    today = today or timezone.localdate()
    cohorts = list(Cohort.objects.filter(course=course))
    dated = [c for c in cohorts if c.mode == COHORT_MODE_COHORT]
    live = sorted(
        (c for c in dated if c.end_date is None or c.end_date >= today),
        key=lambda c: (c.start_date or today, c.pk),
    )
    past = sorted(
        (c for c in dated if c.end_date is not None and c.end_date < today),
        key=lambda c: (c.start_date or today, c.pk),
        reverse=True,
    )
    rest = [c for c in cohorts if c.mode != COHORT_MODE_COHORT]
    return live + past + rest


def assign_cohort_enrollment(user, cohort, *, replace_dated=False):
    """Idempotently put ``user`` into ``cohort``; return ``(row, created)``.

    Existing self-paced membership is always kept: dated-cohort enrollments
    take precedence in ``select_display_cohort`` and
    ``ensure_self_paced_cohort_enrollment`` is a no-op once one exists.
    With ``replace_dated=True`` (a staff "change cohort" move) the user's
    other dated cohorts in the same course are removed, so ``cohort`` is
    the only dated cohort left.
    """
    with transaction.atomic():
        enrollment, created = CohortEnrollment.objects.get_or_create(
            cohort=cohort, user=user,
        )
        if replace_dated:
            CohortEnrollment.objects.filter(
                user=user,
                cohort__course_id=cohort.course_id,
                cohort__mode=COHORT_MODE_COHORT,
            ).exclude(cohort=cohort).delete()
    return enrollment, created
