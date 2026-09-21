"""Display-only cohort deadlines for a course syllabus.

The cohort is selected from an enrollment or a validated public preview. Nothing
in this module grants course access or changes submission permissions.
"""

from django.db.models import Q
from django.utils import timezone

from accounts.services.timezones import is_valid_timezone

from content.models import Cohort, CohortEnrollment, Homework, Module, Unit
from content.models.peer_review import CourseProject


def select_display_cohort(course, user, requested_key=''):
    """Resolve one schedule without letting a URL key override enrollment."""
    active = Cohort.objects.filter(
        course=course, mode='cohort', is_active=True,
    ).order_by('start_date', 'pk')
    if requested_key and user.is_authenticated and user.is_staff:
        cohort = active.filter(external_key__iexact=requested_key).first()
        return cohort, cohort is not None

    if user.is_authenticated:
        enrollments = list(
            CohortEnrollment.objects.filter(
                user=user, cohort__course=course, cohort__mode='cohort',
            ).select_related('cohort').order_by('cohort__start_date', 'pk')
        )
        if enrollments:
            if requested_key:
                cohort = next(
                    (enrollment.cohort for enrollment in enrollments
                     if enrollment.cohort.external_key.lower() == requested_key.lower()
                     and enrollment.cohort.external_key),
                    None,
                )
                return cohort, False
            today = timezone.localdate()
            cohorts = [enrollment.cohort for enrollment in enrollments]
            current = [cohort for cohort in cohorts if cohort.is_active
                       and cohort.start_date <= today <= cohort.end_date]
            if current:
                return max(current, key=lambda cohort: (cohort.start_date, cohort.pk)), False
            upcoming = [cohort for cohort in cohorts if cohort.is_active
                        and cohort.start_date > today]
            if upcoming:
                return min(upcoming, key=lambda cohort: (cohort.start_date, cohort.pk)), False
            past = [cohort for cohort in cohorts if cohort.end_date < today]
            if past:
                return max(past, key=lambda cohort: (cohort.end_date, cohort.pk)), False
            return max(cohorts, key=lambda cohort: (cohort.start_date, cohort.pk)), False

    if requested_key:
        cohort = active.filter(external_key__iexact=requested_key).first()
        return cohort, cohort is not None
    if course.slug == 'ai-buildcamp':
        cohorts = list(active[:2])
        if len(cohorts) == 1:
            return cohorts[0], True
    return None, False


def schedule_timezone_name(course, user):
    """Use the learner's timezone, or the Buildcamp's Berlin schedule zone."""
    preferred = getattr(user, 'preferred_timezone', '') if user.is_authenticated else ''
    if preferred and is_valid_timezone(preferred):
        return preferred
    return 'Europe/Berlin' if course.slug == 'ai-buildcamp' else 'UTC'


def build_deadline_context(course, cohort):
    """Map homework by content ID and projects by module, including ancestors."""
    if cohort is None:
        return {}, {}

    homework_due = {
        row['content_id']: row['due_date']
        for row in Homework.objects.filter(cohort=cohort, content_id__isnull=False)
        .values('content_id', 'due_date')
    }
    units = list(Unit.objects.filter(module__course=course)
                 .values('id', 'module_id', 'kind', 'source_content_id'))
    module_parents = dict(Module.objects.filter(course=course)
                          .values_list('id', 'parent_id'))
    project_rows = list(CourseProject.objects.filter(course=course)
                        .filter(Q(cohort=cohort) | Q(cohort__isnull=True))
                        .values('module_id', 'submission_due_at', 'review_due_at'))
    deadlines = {}
    per_module = {}

    def add_to_ancestors(module_id, due):
        while module_id is not None:
            per_module.setdefault(module_id, []).append(due)
            module_id = module_parents.get(module_id)

    for unit in units:
        if unit['kind'] != 'homework':
            continue
        due = homework_due.get(unit['source_content_id'])
        deadlines[unit['id']] = due
        if due:
            add_to_ancestors(unit['module_id'], due)

    for project in project_rows:
        if project['module_id']:
            add_to_ancestors(project['module_id'], project['submission_due_at'])
            add_to_ancestors(project['module_id'], project['review_due_at'])

    now = timezone.now()
    summaries = {}
    for module_id, dates in per_module.items():
        upcoming = [due for due in dates if due >= now]
        summaries[module_id] = {
            'next_due': min(upcoming) if upcoming else max(dates),
            'count': len(dates),
            'is_upcoming': bool(upcoming),
        }
    return deadlines, summaries
