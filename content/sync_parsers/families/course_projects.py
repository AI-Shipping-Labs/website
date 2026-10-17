"""Sync source-managed project attempts with mode-aware deadline rules."""

import datetime

from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from content.models import Module
from content.models.cohort import COHORT_MODE_SELF_PACED
from content.models.peer_review import CourseProject
from content.sync_parsers.common import GitHubSyncError


def sync_course_projects(course, course_data, rel_path):
    """Validate every source entry, then upsert without deleting old attempts."""
    entries = course_data.get('projects', [])
    if not isinstance(entries, list):
        raise GitHubSyncError(
            f'Invalid projects in {rel_path}/course.yaml: expected a list'
        )
    if entries and not course.peer_review_enabled:
        raise GitHubSyncError(
            f'Projects in {rel_path}/course.yaml require peer_review_enabled: true'
        )
    parsed = _parse_project_entries(course, entries, rel_path)
    for slug, defaults in parsed:
        CourseProject.objects.update_or_create(
            course=course, slug=slug, defaults=defaults,
        )


def _parse_project_entries(course, entries, rel_path):
    parsed = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise GitHubSyncError(
                f'Invalid projects entry in {rel_path}/course.yaml: expected a mapping'
            )
        slug, title = _project_identity(entry, rel_path, seen)
        cohort = _project_cohort(course, entry, slug)
        defaults = {
            'title': title,
            'cohort': cohort,
            'module': _project_module(course, entry, slug, rel_path),
            'peer_review_count': _review_count(entry, slug),
            **_project_dates(entry, cohort, slug, rel_path),
        }
        parsed.append((slug, defaults))
    return parsed


def _project_identity(entry, rel_path, seen):
    slug = entry.get('slug')
    title = entry.get('title')
    if not isinstance(slug, str) or not slug:
        raise GitHubSyncError(
            f'Projects in {rel_path}/course.yaml require slug and title'
        )
    if not isinstance(title, str) or not title.strip():
        raise GitHubSyncError(
            f'Projects in {rel_path}/course.yaml require slug and title'
        )
    _validate_project_slug(slug, rel_path)
    if len(title.strip()) > 200:
        raise GitHubSyncError(
            f'Project {slug!r} in {rel_path}/course.yaml exceeds slug or title length'
        )
    if slug in seen:
        raise GitHubSyncError(
            f'Duplicate project slug {slug!r} in {rel_path}/course.yaml'
        )
    seen.add(slug)
    return slug, title.strip()


def _validate_project_slug(slug, rel_path):
    try:
        validate_slug(slug)
    except ValidationError as exc:
        raise GitHubSyncError(
            f'Project {slug!r} in {rel_path}/course.yaml has invalid slug'
        ) from exc
    if len(slug) > 100:
        raise GitHubSyncError(
            f'Project {slug!r} in {rel_path}/course.yaml exceeds slug or title length'
        )


def _project_cohort(course, entry, slug):
    cohort_key = entry.get('cohort_key')
    if not cohort_key:
        return None
    cohort = course.aisl_cohorts.filter(external_key=cohort_key).first()
    if cohort is None:
        raise GitHubSyncError(
            f'Project {slug!r} references unknown cohort_key {cohort_key!r}'
        )
    return cohort


def _project_dates(entry, cohort, slug, rel_path):
    if cohort is not None and cohort.mode == COHORT_MODE_SELF_PACED:
        return _self_paced_project_dates(entry, slug, rel_path)
    submission = _aware_project_date(entry, 'submission_due_at', slug, rel_path)
    review = _aware_project_date(entry, 'review_due_at', slug, rel_path)
    if review <= submission:
        raise GitHubSyncError(
            f'Project {slug!r} review_due_at must follow submission_due_at'
        )
    return {'submission_due_at': submission, 'review_due_at': review}


def _self_paced_project_dates(entry, slug, rel_path):
    fields = ('submission_due_at', 'review_due_at')
    present = [field for field in fields if field in entry]
    if not present:
        return {'submission_due_at': None, 'review_due_at': None}
    if len(present) == 1:
        raise GitHubSyncError(
            f'Self-paced project {slug!r} in {rel_path}/course.yaml must omit '
            'both submission_due_at and review_due_at'
        )
    raise GitHubSyncError(
        f'Self-paced project {slug!r} in {rel_path}/course.yaml must not set '
        'fixed submission_due_at/review_due_at deadlines'
    )


def _aware_project_date(entry, field, slug, rel_path):
    raw_value = entry.get(field)
    value = None
    if isinstance(raw_value, datetime.datetime):
        value = raw_value
    elif isinstance(raw_value, str):
        value = parse_datetime(raw_value)
    if value is None or timezone.is_naive(value):
        raise GitHubSyncError(
            f'Project {slug!r} in {rel_path}/course.yaml requires '
            f'timezone-aware {field}'
        )
    return value


def _review_count(entry, slug):
    count = entry.get('peer_review_count')
    if count is None:
        return None
    if isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 10:
        return count
    raise GitHubSyncError(
        f'Project {slug!r} peer_review_count must be 1–10'
    )


def _project_module(course, entry, slug, rel_path):
    module_path = entry.get('module_path')
    if not isinstance(module_path, str) or not module_path.strip():
        raise GitHubSyncError(
            f'Project {slug!r} in {rel_path}/course.yaml requires module_path'
        )
    module = None
    for component in module_path.split('/'):
        module = _module_component(course, module, component, slug, module_path)
    if module.parent_id is not None:
        raise GitHubSyncError(
            f'Project {slug!r} module_path must point to a top-level module'
        )
    return module


def _module_component(course, parent, component, slug, module_path):
    if not component:
        raise GitHubSyncError(
            f'Project {slug!r} has invalid module_path {module_path!r}'
        )
    module = Module.objects.filter(
        course=course, parent=parent, slug=component,
    ).first()
    if module is None:
        raise GitHubSyncError(
            f'Project {slug!r} references unknown module_path {module_path!r}'
        )
    return module
