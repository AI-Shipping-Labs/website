"""Project attempts inside the course reader."""

from dataclasses import dataclass

from django.urls import reverse
from django.utils import timezone

from content.models.cohort import CohortEnrollment
from content.models.peer_review import CourseProject
from content.services.attempt_nav import AttemptNavPage, attempt_nav_items
from content.services.course_schedule import cohort_projects
from content.services.learner_attempt_phases import (
    assigned_by_project,
    learner_attempt_phases,
    learner_submissions,
    phase_url,
)
from content.services.project_phase import SUBMIT_URL

CRITERIA_ROW_TITLE = 'Evaluation criteria'


@dataclass(frozen=True)
class ProjectRow:
    project: object
    url: str
    title: str
    phase: object
    is_current: bool
    nav_items: tuple = ()
    has_submission: bool = False
    kind = 'attempt'
    icon = 'folder-git-2'

    @property
    def status(self):
        return self if self.phase is not None else None

    @property
    def label(self):
        return self.phase.badge_label

    @property
    def value(self):
        return self.phase.badge_key

    @property
    def aria_label(self):
        return self.phase.badge_label.lower()


@dataclass(frozen=True)
class CriteriaRow:
    url: str
    title: str
    is_current: bool
    kind = 'criteria'
    icon = 'list-checks'
    phase = None
    status = None


def learner_project_cohort(user, course):
    """The cohort whose attempts the learner sees: dated first, then self-paced."""
    if not getattr(user, 'is_authenticated', False):
        return None
    enrollments = CohortEnrollment.objects.filter(
        user=user, cohort__course=course, cohort__is_active=True,
    ).select_related('cohort')
    cohorts = [e.cohort for e in enrollments]
    dated = [c for c in cohorts if c.mode != 'self_paced']
    if dated:
        return max(dated, key=lambda c: (c.start_date is not None, c.start_date, c.pk))
    return cohorts[0] if cohorts else None


def _module_ids(scoped_module):
    ids = [scoped_module.pk]
    ids.extend(child.pk for child in scoped_module.children.all())
    return ids


def project_rows(user, course, scoped_module, *, current_project=None, cohort=None,
                 nav_page=None):
    """Sidebar rows for the project attempts attached to ``scoped_module``."""
    if scoped_module is None or not course.peer_review_enabled:
        return []
    if cohort is None:
        cohort = learner_project_cohort(user, course)
    projects = list(
        cohort_projects(course, cohort)
        .filter(module_id__in=_module_ids(scoped_module))
        .order_by('submission_due_at', 'pk')
    )
    if current_project is not None and all(p.pk != current_project.pk for p in projects):
        projects.append(current_project)
    return attempt_rows(
        user, course, projects, current_project=current_project, nav_page=nav_page,
    )


def attempt_rows(user, course, projects, *, preview_ids=frozenset(), with_phases=True,
                 current_project=None, nav_page=None):
    projects = list(projects)
    if not projects:
        return []
    open_projects = [p for p in projects if p.pk not in preview_ids]
    submissions, assigned, phases = {}, {}, {}
    if with_phases and open_projects:
        submissions, assigned = _learner_attempt_data(user, open_projects)
        phases = learner_attempt_phases(
            user, course, open_projects, timezone.now(),
            submissions=submissions, assigned=assigned,
        )
    current_pk = getattr(current_project, 'pk', None)
    rows = []
    for project in projects:
        phase = phases.get(project.pk)
        if project.pk in preview_ids:
            url = ''
        elif phase is None:
            url = reverse(SUBMIT_URL, kwargs={'slug': course.slug, 'attempt_slug': project.slug})
        else:
            url = phase_url(course, project, phase)
        nav_items = ()
        if phase is not None and project.pk == current_pk and nav_page is not None:
            nav_items = attempt_nav_items(
                course, project, phase,
                has_submission=project.pk in submissions,
                assigned=assigned.get(project.pk, []), page=nav_page,
            )
        rows.append(ProjectRow(
            project=project,
            url=url,
            title=project.title,
            phase=phase,
            is_current=project.pk == current_pk,
            nav_items=nav_items,
            has_submission=project.pk in submissions,
        ))
    return rows


def _learner_attempt_data(user, projects):
    """The learner's submissions and assigned reviews, two queries for all rows."""
    if not getattr(user, 'is_authenticated', False):
        return {}, {}
    return learner_submissions(user, projects), assigned_by_project(user, projects)


def criteria_module_ids(course, course_projects=None):
    if not course.peer_review_enabled:
        return set()
    if course_projects is not None:
        return {p.module_id for p in course_projects if p.module_id and p.evaluation_criteria}
    return set(
        CourseProject.objects.filter(course=course, module__isnull=False)
        .exclude(evaluation_criteria=[])
        .values_list('module_id', flat=True)
    )


def _criteria_row(course, *, is_current=False, link=True):
    url = ''
    if link:
        url = reverse('course_project_criteria', kwargs={'slug': course.slug})
    return CriteriaRow(url=url, title=CRITERIA_ROW_TITLE, is_current=is_current)


def criteria_row(course, scoped_module, *, is_current=False):
    """The Evaluation criteria row listed after the attempt rows, or None."""
    if scoped_module is None or not course.peer_review_enabled:
        return None
    if not criteria_module_ids(course) & set(_module_ids(scoped_module)):
        return None
    return _criteria_row(course, is_current=is_current)


def module_project_rows(user, course, projects, *, preview_ids=frozenset(),
                        with_phases=True, course_projects=None, can_open_criteria=True):
    projects = [p for p in projects if p.module_id]
    by_module = {}
    for row in attempt_rows(
        user, course, projects, preview_ids=preview_ids, with_phases=with_phases,
    ):
        by_module.setdefault(row.project.module_id, []).append(row)
    for module_id in criteria_module_ids(course, course_projects):
        if can_open_criteria or module_id in by_module:
            by_module.setdefault(module_id, []).append(
                _criteria_row(course, link=can_open_criteria),
            )
    return by_module


def _anchor_unit(module):
    """First unit in the project's module tree, used to build reader navigation."""
    for unit in module.units.all():
        return unit
    for child in module.children.all():
        for unit in child.units.all():
            return unit
    return None


def project_reader_context(request, course, project, *, criteria_current=False,
                           nav_page=None):
    """Reader-shell context (sidebar, module nav) for a project attempt page."""
    from content.services.course_view_progress import (
        build_course_view_navigation_context,
    )

    module = project.module
    if module is None:
        return {}
    root = module.parent if module.parent_id else module
    anchor = _anchor_unit(root)
    if anchor is None:
        return {}
    context = build_course_view_navigation_context(
        request.user, course, anchor.module, anchor,
        request=request, selected_cohort=None,
    )
    current_project = project
    if criteria_current:
        current_project = None
    elif nav_page is None:
        nav_page = AttemptNavPage(kind='')
    context.update(
        unit=None,
        module=root,
        reader_cohort_param='',
        reader_project_rows=project_rows(
            request.user, course, context['scoped_module'],
            current_project=current_project, nav_page=nav_page,
        ),
        reader_criteria_row=criteria_row(
            course, context['scoped_module'], is_current=criteria_current,
        ),
        reader_shell=True,
    )
    return context

