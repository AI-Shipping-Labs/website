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

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from analytics.models import UserActivity
from integrations.config import get_config

logger = logging.getLogger(__name__)

# Default retention window for UserActivity rows (issue #853). Longer than
# the 90-day SES audit-log convention because activity history is a CRM
# signal staff actively use, not a transient delivery log — but still
# bounded for storage / PII. Tunable via the Studio-editable
# ``USER_ACTIVITY_RETENTION_DAYS`` setting.
DEFAULT_USER_ACTIVITY_RETENTION_DAYS = 365


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
        if user is None or not getattr(user, 'is_authenticated', False):
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
        if user is None or not getattr(user, 'is_authenticated', False):
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
        target_url = _safe_studio_unit_url(unit.pk)
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


def _safe_studio_unit_url(unit_pk):
    """Build the Studio unit-edit deep link, or '' if reversing fails."""
    try:
        return reverse('studio_unit_edit', args=[unit_pk])
    except NoReverseMatch:
        return ''


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
        target_url=studio_event_url(event.pk),
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
    'record_event_register',
    'studio_course_url',
    'studio_event_url',
    'get_user_activity_retention_days',
    'DEFAULT_USER_ACTIVITY_RETENTION_DAYS',
]
