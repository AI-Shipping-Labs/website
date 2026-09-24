"""Course cohort defaults used by sync and learner enrollment paths."""

from content.models.cohort import COHORT_MODE_COHORT, COHORT_MODE_SELF_PACED, Cohort


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
