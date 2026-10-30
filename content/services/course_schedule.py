"""Display-only cohort deadlines for a course syllabus.

The cohort is selected from an enrollment or a validated public preview. Nothing
in this module grants course access or changes submission permissions.
"""

from datetime import UTC

from django.db.models.signals import post_delete, post_save
from django.utils import timezone

from accounts.services.timezones import is_valid_timezone
from content.models import Cohort, CohortEnrollment, Homework
from content.models.peer_review import CourseProject

_PROJECT_EPOCH = 0


def _bump_project_epoch(*_args, **_kwargs) -> None:
    global _PROJECT_EPOCH
    _PROJECT_EPOCH += 1


post_save.connect(_bump_project_epoch, sender=CourseProject)
post_delete.connect(_bump_project_epoch, sender=CourseProject)


def _owned_cohorts(course, user):
    if not user.is_authenticated:
        return []
    return [
        enrollment.cohort
        for enrollment in CohortEnrollment.objects.filter(
            user=user, cohort__course=course,
        ).select_related('cohort').order_by('cohort__start_date', 'pk')
    ]


def _requested_owned_cohort(cohorts, requested_key):
    return next(
        (
            cohort for cohort in cohorts
            if cohort.mode == 'cohort'
            and cohort.external_key
            and cohort.external_key.lower() == requested_key.lower()
        ),
        None,
    )


def _preferred_open_dated(cohorts, today):
    dated = [
        cohort for cohort in cohorts
        if cohort.mode == 'cohort' and cohort.is_active
        and cohort.end_date and cohort.end_date >= today
    ]
    current = [
        cohort for cohort in dated
        if cohort.start_date and cohort.start_date <= today
    ]
    if current:
        return max(current, key=lambda cohort: (cohort.start_date, cohort.pk))
    if dated:
        return min(dated, key=lambda cohort: (cohort.start_date, cohort.pk))
    return None


def has_open_dated_enrollment(user, course, *, today=None):
    """Whether the learner owns a current or upcoming active dated cohort."""
    if not user.is_authenticated:
        return False
    today = today or timezone.localdate()
    return CohortEnrollment.objects.filter(
        user=user,
        cohort__course=course,
        cohort__mode='cohort',
        cohort__is_active=True,
        cohort__end_date__gte=today,
    ).exists()


def _default_owned_cohort(owned):
    today = timezone.localdate()
    dated = _preferred_open_dated(owned, today)
    if dated is not None:
        return dated
    self_paced = next(
        (
            cohort for cohort in owned
            if cohort.mode == 'self_paced' and cohort.is_active
        ),
        None,
    )
    if self_paced is not None:
        return self_paced
    dated = [cohort for cohort in owned if cohort.mode == 'cohort']
    past = [
        cohort for cohort in dated
        if cohort.end_date and cohort.end_date < today
    ]
    if past:
        return max(past, key=lambda cohort: (cohort.end_date, cohort.pk))
    if dated:
        return max(dated, key=lambda cohort: (cohort.start_date, cohort.pk))
    return None


def _resolve_owned_cohort(owned, requested_key, user, active):
    if not requested_key:
        return _default_owned_cohort(owned), False
    cohort = _requested_owned_cohort(owned, requested_key)
    if cohort is not None:
        return cohort, False
    if user.is_staff:
        cohort = active.filter(external_key__iexact=requested_key).first()
        return cohort, cohort is not None
    return None, False


def select_display_cohort(course, user, requested_key=''):
    """Resolve one schedule without letting a URL key override enrollment."""
    active = Cohort.objects.filter(
        course=course, mode='cohort', is_active=True,
    ).order_by('start_date', 'pk')

    owned = _owned_cohorts(course, user)
    if owned:
        return _resolve_owned_cohort(owned, requested_key, user, active)

    if user.is_authenticated and requested_key and user.is_staff:
        cohort = active.filter(external_key__iexact=requested_key).first()
        return cohort, cohort is not None

    if requested_key:
        cohort = active.filter(external_key__iexact=requested_key).first()
        return cohort, cohort is not None
    if course.slug == 'ai-buildcamp':
        cohorts = list(active[:2])
        if len(cohorts) == 1:
            return cohorts[0], True
    return None, False


def default_display_cohorts_by_course(user, course_ids):
    """Resolve each enrolled course's default cohort in one query."""
    owned_by_course = {}
    for course_id in course_ids:
        owned_by_course[course_id] = []
    enrollments = CohortEnrollment.objects.filter(
        user=user, cohort__course_id__in=course_ids,
    ).select_related('cohort').order_by('cohort__start_date', 'pk')
    for enrollment in enrollments:
        course_id = enrollment.cohort.course_id
        owned_by_course[course_id].append(enrollment.cohort)
    defaults = {}
    for course_id, cohorts in owned_by_course.items():
        defaults[course_id] = _default_owned_cohort(cohorts)
    return defaults


def pickable_cohorts(course, *, today=None):
    """Dated cohorts a learner with course access may join from course Home.

    Current and upcoming cohorts, earliest first. Past cohorts are offered
    (latest first) only when nothing is current or upcoming.
    """
    today = today or timezone.localdate()
    active = list(Cohort.objects.filter(
        course=course, mode='cohort', is_active=True,
    ).order_by('start_date', 'pk'))
    open_cohorts = [
        cohort for cohort in active
        if cohort.end_date is None or cohort.end_date >= today
    ]
    if open_cohorts:
        return open_cohorts
    return sorted(active, key=lambda cohort: (cohort.start_date, cohort.pk), reverse=True)


def schedule_timezone_name(course, user):
    """Use the learner's timezone, or the Buildcamp's Berlin schedule zone."""
    preferred = getattr(user, 'preferred_timezone', '') if user.is_authenticated else ''
    if preferred and is_valid_timezone(preferred):
        return preferred
    return 'Europe/Berlin' if course.slug == 'ai-buildcamp' else 'UTC'


def schedule_instant_iso(value):
    """UTC ``...Z`` instant behind a schedule label (issue #1795).

    Feeds semantic ``<time datetime>`` markup and the first-visit client
    re-formatting in ``static/js/course-schedule.js``. Whether the client may
    replace the server fallback zone is decided by the event-page policy,
    ``events.services.display_time.should_use_browser_timezone``.
    """
    if value is None:
        return ''
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _cohort_has_projects(projects, cohort) -> bool:
    if (
        getattr(cohort, '_has_projects_epoch', None) == _PROJECT_EPOCH
        and getattr(cohort, '_has_projects_course_id', None) == cohort.course_id
    ):
        return cohort._has_projects_cached
    has_projects = projects.filter(cohort=cohort).exists()
    cohort._has_projects_cached = has_projects
    cohort._has_projects_epoch = _PROJECT_EPOCH
    cohort._has_projects_course_id = cohort.course_id
    return has_projects


def cohort_projects(course, cohort):
    """Project attempts for one cohort.

    A cohort's own attempts replace the course-wide (unscoped) ones; the
    unscoped attempts apply only when the cohort has none of its own.
    """
    projects = CourseProject.objects.filter(course=course)
    if cohort is not None and _cohort_has_projects(projects, cohort):
        return projects.filter(cohort=cohort)
    return projects.filter(cohort__isnull=True)


def _syllabus_units_and_parents(course):
    units = []
    module_parents = {}
    for top_module in course.get_syllabus():
        module_parents[top_module.pk] = top_module.parent_id
        for unit in top_module.units.all():
            units.append({
                'id': unit.pk,
                'module_id': unit.module_id,
                'kind': unit.kind,
                'source_content_id': unit.source_content_id,
            })
        for child in top_module.children.all():
            module_parents[child.pk] = child.parent_id
            for unit in child.units.all():
                units.append({
                    'id': unit.pk,
                    'module_id': unit.module_id,
                    'kind': unit.kind,
                    'source_content_id': unit.source_content_id,
                })
    return units, module_parents


def build_deadline_context(course, cohort):
    """Map homework by content ID and projects by module, including ancestors."""
    if cohort is None:
        return {}, {}
    homework_due = {}
    homework_rows = Homework.objects.filter(
        cohort=cohort, content_id__isnull=False,
    ).values('content_id', 'due_date')
    for row in homework_rows:
        homework_due[row['content_id']] = row['due_date']
    units, module_parents = _syllabus_units_and_parents(course)
    project_rows = list(cohort_projects(course, cohort)
                        .values('module_id', 'submission_due_at', 'review_due_at'))
    deadlines = {}
    per_module = {}
    _add_homework_deadlines(
        units, homework_due, deadlines, per_module, module_parents,
    )
    _add_project_deadlines(project_rows, per_module, module_parents)
    return deadlines, _deadline_summaries(per_module)


def _add_homework_deadlines(
    units, homework_due, deadlines, per_module, module_parents,
):
    for unit in units:
        if unit['kind'] != 'homework':
            continue
        due = homework_due.get(unit['source_content_id'])
        deadlines[unit['id']] = due
        if due:
            _add_to_module_ancestors(
                per_module, module_parents, unit['module_id'], due,
            )


def _add_project_deadlines(project_rows, per_module, module_parents):
    for project in project_rows:
        if not project['module_id']:
            continue
        for field in ('submission_due_at', 'review_due_at'):
            _add_to_module_ancestors(
                per_module, module_parents, project['module_id'], project[field],
            )


def _add_to_module_ancestors(per_module, module_parents, module_id, due):
    if due is None:
        return
    while module_id is not None:
        per_module.setdefault(module_id, []).append(due)
        module_id = module_parents.get(module_id)


def _deadline_summaries(per_module):
    now = timezone.now()
    summaries = {}
    for module_id, dates in per_module.items():
        upcoming = [due for due in dates if due >= now]
        summaries[module_id] = {
            'next_due': min(upcoming) if upcoming else max(dates),
            'count': len(dates),
            'is_upcoming': bool(upcoming),
        }
    return summaries
