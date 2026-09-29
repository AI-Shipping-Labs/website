"""Course cohort defaults, staff pickers, and event-series health checks."""

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from content.models import Unit
from content.models.cohort import (
    COHORT_MODE_COHORT,
    COHORT_MODE_SELF_PACED,
    Cohort,
    CohortEnrollment,
)
from content.models.course import UNIT_KIND_EVENT
from events.models import Event, EventSeries
from events.models.event import PUBLIC_EVENT_STATUSES


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


# --- Cohort event-series health (production incident follow-up) ---
#
# A dated cohort's session units resolve their live session by looking up
# ``Event.series_position == Unit.session_position`` inside the cohort's
# ``event_series`` (``content.services.course_units.resolve_session_event``).
# When the link is missing, points at an empty series, or the series skips
# a position, every affected session silently renders "Not scheduled".
# ``cohort_series_warnings`` is the single owner of that diagnosis; the
# staff API, the Studio cohort pages and the Studio dashboard health item
# all read it.

COHORT_WARNING_NO_EVENT_SERIES = 'no_event_series'
COHORT_WARNING_EMPTY_EVENT_SERIES = 'empty_event_series'
COHORT_WARNING_MISSING_SESSION_POSITIONS = 'missing_session_positions'
# Every dated cohort must link a series, so a missing link is an error;
# the other two are warnings about a linked series' contents.
COHORT_WARNING_LEVEL_ERROR = 'error'
COHORT_WARNING_LEVEL_WARNING = 'warning'


def _format_positions(positions):
    return ', '.join(str(position) for position in positions)


def cohort_series_warnings(cohorts):
    """Return ``{cohort.pk: [warning, ...]}`` for ``cohorts``.

    Each warning is ``{'code': ..., 'level': ..., 'message': ...}`` (plus
    ``'missing_positions'`` for the coverage warning). ``level`` is
    ``'error'`` for a missing link (every dated cohort must have one) and
    ``'warning'`` otherwise. Only
    ``mode='cohort'`` cohorts are diagnosed; self-paced cohorts never carry
    a series and always map to ``[]``. A dated cohort is flagged when:

    - it has no ``event_series`` (``no_event_series``, error level);
    - its series has no published (``upcoming``/``completed``) events
      (``empty_event_series``);
    - its series' published ``series_position`` values do not cover every
      ``session_position`` of the course's ``kind='event'`` units
      (``missing_session_positions``).

    Runs a fixed number of queries regardless of how many cohorts are
    passed, so list pages and the dashboard can call it once.
    """
    cohorts = list(cohorts)
    result = {cohort.pk: [] for cohort in cohorts}
    dated = [cohort for cohort in cohorts if cohort.mode == COHORT_MODE_COHORT]
    if not dated:
        return result

    series_ids = {c.event_series_id for c in dated if c.event_series_id}
    positions_by_series = {series_id: set() for series_id in series_ids}
    published_count_by_series = dict.fromkeys(series_ids, 0)
    for series_id, position in Event.objects.filter(
        event_series_id__in=series_ids,
        status__in=PUBLIC_EVENT_STATUSES,
    ).values_list('event_series_id', 'series_position'):
        published_count_by_series[series_id] += 1
        if position is not None:
            positions_by_series[series_id].add(position)

    course_ids = {cohort.course_id for cohort in dated}
    unit_positions_by_course = {course_id: set() for course_id in course_ids}
    for course_id, position in Unit.objects.filter(
        module__course_id__in=course_ids,
        kind=UNIT_KIND_EVENT,
        session_position__isnull=False,
    ).values_list('module__course_id', 'session_position'):
        unit_positions_by_course[course_id].add(position)

    for cohort in dated:
        warnings = result[cohort.pk]
        series_id = cohort.event_series_id
        if not series_id:
            warnings.append({
                'code': COHORT_WARNING_NO_EVENT_SERIES,
                'level': COHORT_WARNING_LEVEL_ERROR,
                'message': (
                    'No event series is linked, so every live session in '
                    'this cohort shows "Not scheduled".'
                ),
            })
            continue
        if published_count_by_series[series_id] == 0:
            warnings.append({
                'code': COHORT_WARNING_EMPTY_EVENT_SERIES,
                'level': COHORT_WARNING_LEVEL_WARNING,
                'message': (
                    'The linked event series has no published events, so '
                    'every live session in this cohort shows "Not scheduled".'
                ),
            })
            continue
        missing = sorted(
            unit_positions_by_course[cohort.course_id]
            - positions_by_series[series_id]
        )
        if missing:
            warnings.append({
                'code': COHORT_WARNING_MISSING_SESSION_POSITIONS,
                'level': COHORT_WARNING_LEVEL_WARNING,
                'message': (
                    'The linked event series has no published event for '
                    f'session position {_format_positions(missing)}, so '
                    'those sessions show "Not scheduled".'
                ),
                'missing_positions': missing,
            })
    return result


def active_cohorts_with_series_warnings(today=None):
    """Return ``[(cohort, warnings)]`` for live dated cohorts with problems.

    The health-check view of ``cohort_series_warnings``: only active
    ``mode='cohort'`` cohorts that have not ended yet, because a finished
    cohort without a series is history, not an incident.
    """
    today = today or timezone.localdate()
    cohorts = list(
        Cohort.objects.filter(is_active=True, mode=COHORT_MODE_COHORT)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .select_related('course')
        .order_by('start_date', 'pk')
    )
    warnings_by_cohort = cohort_series_warnings(cohorts)
    return [
        (cohort, warnings_by_cohort[cohort.pk])
        for cohort in cohorts
        if warnings_by_cohort[cohort.pk]
    ]


def _is_retired_series(series):
    """True for series an operator should not pick for a cohort."""
    return not series.is_active or 'delete me' in series.name.lower()


def cohort_event_series_options():
    """Return every ``EventSeries`` for the cohort event-series picker.

    Each series carries ``num_events`` (all occurrences) and
    ``is_retired``. Active series come first by name; inactive series and
    ones named "DELETE ME" (duplicates awaiting deletion) sort last, so an
    operator does not link a cohort to an empty duplicate by accident.
    """
    series_list = list(
        EventSeries.objects.annotate(num_events=Count('events')).order_by('name', 'pk')
    )
    for series in series_list:
        series.is_retired = _is_retired_series(series)
    return sorted(series_list, key=lambda series: series.is_retired)
