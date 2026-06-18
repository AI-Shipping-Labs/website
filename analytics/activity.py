"""Curated per-user activity recording for the CRM timeline (issue #853).

``record_activity`` is the single defensive write helper, modelled on
``analytics.tasks.record_visit`` and the defensive pattern in
``analytics.signals.create_user_attribution`` /
``CampaignTrackingMiddleware``: it writes one ``UserActivity`` row and MUST
NOT raise into the calling request. Any failure is caught and logged.

Anonymous (not-logged-in) actions are out of scope for Phase 1 — only
authenticated users get a CRM record.

The retention window is a single named setting (``USER_ACTIVITY_RETENTION_DAYS``)
read through the IntegrationSetting framework so it can be tuned from Studio
without a redeploy.
"""

import logging
from urllib.parse import urlsplit

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from accounts.utils.user_checks import is_authenticated_user
from analytics.models import UserActivity
from content.models.course import Course, Unit
from events.models.event import Event
from integrations.config import get_config

logger = logging.getLogger(__name__)

# Default retention window for UserActivity rows (issue #853). Longer than
# the 90-day SES audit-log convention because activity history is a CRM
# signal staff actively use, not a transient delivery log — but still
# bounded for storage / PII. Tunable via the Studio-editable
# ``USER_ACTIVITY_RETENTION_DAYS`` setting.
DEFAULT_USER_ACTIVITY_RETENTION_DAYS = 365
MANAGEMENT_ACTIVITY_URL_PREFIXES = ('/studio', '/admin')


def get_user_activity_retention_days():
    """Resolve the UserActivity retention window in days.

    Reads ``USER_ACTIVITY_RETENTION_DAYS`` via the IntegrationSetting
    framework (DB override -> env -> default). Falls back to the default
    when the configured value is missing or not a positive integer.
    """
    raw = get_config(
        'USER_ACTIVITY_RETENTION_DAYS',
        str(DEFAULT_USER_ACTIVITY_RETENTION_DAYS),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_USER_ACTIVITY_RETENTION_DAYS
    if value <= 0:
        return DEFAULT_USER_ACTIVITY_RETENTION_DAYS
    return value


def record_activity(
    user,
    event_type,
    *,
    label='',
    object_type='',
    object_id='',
    target_url='',
    occurred_at=None,
):
    """Write one ``UserActivity`` row. Never raises into the caller.

    Skips anonymous users (no ``pk`` / not authenticated). Returns the
    created ``UserActivity`` instance, or ``None`` if nothing was written
    (anonymous user, or a caught error).
    """
    try:
        if not is_authenticated_user(user):
            return None
        if getattr(user, 'pk', None) is None:
            return None

        return UserActivity.objects.create(
            user=user,
            event_type=event_type,
            occurred_at=occurred_at or timezone.now(),
            label=(label or '')[:255],
            object_type=(object_type or '')[:40],
            object_id=str(object_id or '')[:64],
            target_url=(target_url or '')[:500],
        )
    except Exception:
        # Intentional broad catch: recording activity must NEVER break the
        # calling request (signup, enroll, lesson view, payment webhook,
        # SES callback). Log and move on.
        logger.exception(
            'Failed to record UserActivity event_type=%s for user=%s',
            event_type,
            getattr(user, 'pk', None),
        )
        return None


def record_lesson_open(user, *, unit, dedupe_minutes=30):
    """Record a ``lesson_open`` with a dedupe window.

    Does NOT write a new row if the same user opened the same unit
    (same ``object_id``) within the last ``dedupe_minutes``. Defensive —
    never raises into the request.
    """
    try:
        if not is_authenticated_user(user):
            return None
        if getattr(user, 'pk', None) is None:
            return None

        object_id = str(unit.pk)
        cutoff = timezone.now() - timezone.timedelta(minutes=dedupe_minutes)
        recent_exists = UserActivity.objects.filter(
            user=user,
            event_type=UserActivity.EVENT_LESSON_OPEN,
            object_id=object_id,
            occurred_at__gte=cutoff,
        ).exists()
        if recent_exists:
            return None

        module = unit.module
        label = f'Opened lesson: {module.title} / {unit.title}'
        target_url = public_unit_activity_url(unit)
        return record_activity(
            user,
            UserActivity.EVENT_LESSON_OPEN,
            label=label,
            object_type='unit',
            object_id=object_id,
            target_url=target_url,
        )
    except Exception:
        logger.exception(
            'Failed to record lesson_open for user=%s unit=%s',
            getattr(user, 'pk', None),
            getattr(unit, 'pk', None),
        )
        return None


def _safe_public_url(name, *args):
    """Reverse a named public URL, returning '' on ``NoReverseMatch``.

    Used by ``record_resource_view`` to denormalise the PUBLIC content URL
    the member actually saw, so staff can click through from the CRM
    timeline to the resource the member engaged with.
    """
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ''


def is_safe_public_activity_url(target_url):
    """Return True for same-site non-management activity destinations."""
    value = (target_url or '').strip()
    if not value.startswith('/') or value.startswith('//'):
        return False
    path = urlsplit(value).path
    for prefix in MANAGEMENT_ACTIVITY_URL_PREFIXES:
        if path == prefix or path.startswith(f'{prefix}/'):
            return False
    return True


def public_course_activity_url(course):
    """Return the public/member course URL when the course page can resolve."""
    if course is None or getattr(course, 'status', '') != 'published':
        return ''
    return course.get_absolute_url() or ''


def public_unit_activity_url(unit):
    """Return the public/member lesson URL when the lesson page can resolve."""
    if unit is None:
        return ''
    module = getattr(unit, 'module', None)
    course = getattr(module, 'course', None)
    if course is None or getattr(course, 'status', '') != 'published':
        return ''
    return unit.get_absolute_url() or ''


def public_event_activity_url(event):
    """Return the canonical public/member event URL when the event exists."""
    if event is None:
        return ''
    return event.get_absolute_url() or ''


def resolve_activity_target_url(
    activity,
    *,
    courses_by_slug=None,
    units_by_pk=None,
    events_by_slug=None,
):
    """Apply the shared CRM activity-link policy to one activity row."""
    courses_by_slug = courses_by_slug or {}
    units_by_pk = units_by_pk or {}
    events_by_slug = events_by_slug or {}

    event_type = activity.event_type
    object_id = str(activity.object_id or '')

    if event_type == UserActivity.EVENT_COURSE_ENROLL and object_id:
        target = public_course_activity_url(courses_by_slug.get(object_id))
        if target:
            return target

    if event_type == UserActivity.EVENT_LESSON_OPEN and object_id:
        try:
            unit_pk = int(object_id)
        except (TypeError, ValueError):
            unit_pk = None
        if unit_pk is not None:
            target = public_unit_activity_url(units_by_pk.get(unit_pk))
            if target:
                return target

    if event_type in {
        UserActivity.EVENT_EVENT_REGISTER,
        UserActivity.EVENT_EVENT_JOIN,
    } and object_id:
        target = public_event_activity_url(events_by_slug.get(object_id))
        if target:
            return target

    stored_target = activity.target_url or ''
    if is_safe_public_activity_url(stored_target):
        return stored_target
    return ''


def resolve_activity_target_urls(activities):
    """Batch-resolve public targets for visible activity rows.

    The returned mapping is keyed by ``UserActivity.pk`` and uses bounded
    lookups by object type, avoiding one query per timeline row.
    """
    course_slugs = set()
    unit_pks = set()
    event_slugs = set()

    for activity in activities:
        object_id = str(activity.object_id or '')
        if not object_id:
            continue
        if activity.event_type == UserActivity.EVENT_COURSE_ENROLL:
            course_slugs.add(object_id)
        elif activity.event_type == UserActivity.EVENT_LESSON_OPEN:
            try:
                unit_pks.add(int(object_id))
            except (TypeError, ValueError):
                continue
        elif activity.event_type in {
            UserActivity.EVENT_EVENT_REGISTER,
            UserActivity.EVENT_EVENT_JOIN,
        }:
            event_slugs.add(object_id)

    courses_by_slug = {}
    if course_slugs:
        courses_by_slug = {
            course.slug: course
            for course in Course.objects.filter(
                slug__in=course_slugs,
                status='published',
            )
        }

    units_by_pk = {}
    if unit_pks:
        units_by_pk = {
            unit.pk: unit
            for unit in Unit.objects.select_related(
                'module',
                'module__course',
            ).filter(
                pk__in=unit_pks,
                module__course__status='published',
            )
        }

    events_by_slug = {}
    if event_slugs:
        events_by_slug = {
            event.slug: event
            for event in Event.objects.filter(slug__in=event_slugs)
        }

    return {
        activity.pk: resolve_activity_target_url(
            activity,
            courses_by_slug=courses_by_slug,
            units_by_pk=units_by_pk,
            events_by_slug=events_by_slug,
        )
        for activity in activities
    }


# Human-readable kind labels for ``resource_view`` rows (issue #773). Maps
# the stored ``object_type`` to the noun used in the timeline label
# ("Viewed {kind}: {title}").
RESOURCE_VIEW_KIND_LABELS = {
    'article': 'article',
    'project': 'project',
    'tutorial': 'tutorial',
    'recording': 'recording',
    'curated_link': 'curated resource',
    'download': 'download',
}


def record_resource_view(
    user,
    *,
    object_type,
    object_id,
    title,
    target_url='',
    dedupe_minutes=360,
):
    """Record a ``resource_view`` for a logged-in member (issue #773).

    Mirrors :func:`record_lesson_open`: skips anonymous users, dedupes one
    row per (user, resource) within ``dedupe_minutes`` (default 360 = 6h),
    and NEVER raises into the content request. Callers must already have
    confirmed the member can access the resource (``can_access`` True) —
    paywalled teasers a member bounced off are deliberately not recorded.

    ``object_type`` is the content kind (article/project/tutorial/recording/
    curated_link/download); ``object_id`` is the resource slug or pk;
    ``target_url`` is the PUBLIC content URL the member saw. Stores no raw
    IP / user-agent / querystring. Returns the created row, or ``None``
    (anonymous user, deduped, or a caught error).
    """
    try:
        if not is_authenticated_user(user):
            return None
        if getattr(user, 'pk', None) is None:
            return None

        object_id_str = str(object_id or '')
        cutoff = timezone.now() - timezone.timedelta(minutes=dedupe_minutes)
        recent_exists = UserActivity.objects.filter(
            user=user,
            event_type=UserActivity.EVENT_RESOURCE_VIEW,
            object_type=object_type,
            object_id=object_id_str[:64],
            occurred_at__gte=cutoff,
        ).exists()
        if recent_exists:
            return None

        kind = RESOURCE_VIEW_KIND_LABELS.get(object_type, object_type)
        label = f'Viewed {kind}: {title}'
        return record_activity(
            user,
            UserActivity.EVENT_RESOURCE_VIEW,
            label=label,
            object_type=object_type,
            object_id=object_id_str,
            target_url=target_url,
        )
    except Exception:
        logger.exception(
            'Failed to record resource_view for user=%s object_type=%s '
            'object_id=%s',
            getattr(user, 'pk', None),
            object_type,
            object_id,
        )
        return None


def record_event_register(user, event):
    """Record an `event_register` activity row for the CRM timeline.

    Defensive — never raises into the registration path.
    """
    return record_activity(
        user,
        UserActivity.EVENT_EVENT_REGISTER,
        label=f'Registered for event: {event.title}',
        object_type='event',
        object_id=event.slug,
        target_url=public_event_activity_url(event),
    )


def record_course_enroll(user, course):
    """Record a `course_enroll` activity row for the CRM timeline."""
    return record_activity(
        user,
        UserActivity.EVENT_COURSE_ENROLL,
        label=f'Enrolled in course: {course.title}',
        object_type='course',
        object_id=course.slug,
        target_url=public_course_activity_url(course),
    )


def record_event_join(user, event):
    """Record an `event_join` activity row for the CRM timeline."""
    return record_activity(
        user,
        UserActivity.EVENT_EVENT_JOIN,
        label=f'Joined event: {event.title}',
        object_type='event',
        object_id=event.slug,
        target_url=public_event_activity_url(event),
    )


def studio_course_url(course_pk):
    """Build the Studio course-edit deep link, or '' if reversing fails."""
    try:
        return reverse('studio_course_edit', args=[course_pk])
    except NoReverseMatch:
        return ''


def studio_event_url(event_pk):
    """Build the Studio event-edit deep link, or '' if reversing fails."""
    try:
        return reverse('studio_event_edit', args=[event_pk])
    except NoReverseMatch:
        return ''


__all__ = [
    'record_activity',
    'record_lesson_open',
    'record_resource_view',
    'record_event_register',
    'record_course_enroll',
    'record_event_join',
    'resolve_activity_target_url',
    'resolve_activity_target_urls',
    'is_safe_public_activity_url',
    'public_course_activity_url',
    'public_unit_activity_url',
    'public_event_activity_url',
    'studio_course_url',
    'studio_event_url',
    'get_user_activity_retention_days',
    'DEFAULT_USER_ACTIVITY_RETENTION_DAYS',
]
