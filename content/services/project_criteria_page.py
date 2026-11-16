"""The course's Evaluation criteria page, generated from course.yaml."""

from zoneinfo import ZoneInfo

from django.template.defaultfilters import date as django_date
from django.urls import reverse
from django.utils import timezone

from content.models.peer_review import CourseProject
from content.services.course_schedule import cohort_projects, schedule_timezone_name
from content.services.learner_attempt_phases import learner_attempt_phases
from content.services.project_criteria import criteria_summary
from content.services.project_reader import learner_project_cohort

OPEN_BADGE = 'open'


def criteria_project(course, projects):
    """The attempt whose criteria the page shows."""
    for project in projects:
        if project.evaluation_criteria:
            return project
    return (
        CourseProject.objects.filter(course=course)
        .exclude(evaluation_criteria=[])
        .select_related('module')
        .order_by('pk')
        .first()
    )


def _module_ids(module):
    root = module.parent if module.parent_id else module
    ids = [root.pk]
    ids.extend(child.pk for child in root.children.all())
    return ids


def _open_attempt(user, course, projects, module):
    """Earliest attempt in ``module``'s tree the learner can still submit."""
    if module is None:
        return None
    module_ids = _module_ids(module)
    candidates = []
    for project in projects:
        if project.module_id in module_ids:
            candidates.append(project)
    phases = learner_attempt_phases(user, course, candidates, timezone.now())
    for project in candidates:
        if phases[project.pk].badge_key == OPEN_BADGE:
            return project
    return None


def _submit_cta(user, course, project):
    zone = schedule_timezone_name(course, user)
    due = ''
    if project.submission_due_at is not None:
        due = django_date(timezone.localtime(project.submission_due_at, ZoneInfo(zone)), 'M j')
    return {
        'url': reverse('course_project_submit', kwargs={
            'slug': course.slug, 'attempt_slug': project.slug,
        }),
        'title': project.title,
        'due': due,
    }


def criteria_page_context(user, course):
    """Context for the criteria page, or None when the course has no criteria."""
    cohort = learner_project_cohort(user, course)
    projects = list(cohort_projects(course, cohort).order_by('submission_due_at', 'pk'))
    project = criteria_project(course, projects)
    if project is None:
        return None
    cta = None
    open_project = _open_attempt(user, course, projects, project.module)
    if open_project is not None:
        cta = _submit_cta(user, course, open_project)
    return {
        'criteria_project': project,
        'project_criteria': criteria_summary(project.evaluation_criteria),
        'criteria_intro_html': course.project_criteria_intro_html,
        'criteria_outro_html': course.project_criteria_outro_html,
        'criteria_cta': cta,
    }
