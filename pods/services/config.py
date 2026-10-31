"""Typed readers for the ``pods`` IntegrationSetting group (issue #1918).

Every value resolves through ``integrations.config.get_config`` (DB override
in Studio settings, then environment, then the default below), so operators
change them without a redeploy.
"""

from integrations.config import get_bool_config, get_config

MEETING_MINUTE_OPTIONS = (30, 45, 60, 90)


def _int_config(key, default, *, minimum=None, maximum=None):
    raw = get_config(key, str(default))
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def enabled_course_slugs():
    raw = get_config('PODS_COURSE_SLUGS', '') or ''
    return {part.strip() for part in str(raw).split(',') if part.strip()}


def pods_enabled_for_course(course):
    return bool(course) and course.slug in enabled_course_slugs()


def default_meeting_count():
    return _int_config('PODS_DEFAULT_MEETING_COUNT', 1, minimum=1, maximum=20)


def default_meeting_minutes():
    value = _int_config('PODS_DEFAULT_MEETING_MINUTES', 60)
    return value if value in MEETING_MINUTE_OPTIONS else 60


def max_created_per_member():
    return _int_config('PODS_MAX_CREATED_PER_MEMBER', 2, minimum=1)


def suggestion_horizon_days():
    return _int_config('PODS_SUGGESTION_HORIZON_DAYS', 14, minimum=1, maximum=60)


def suggestion_count():
    return _int_config('PODS_SUGGESTION_COUNT', 5, minimum=1, maximum=20)


def _default_on(key):
    """Default-on boolean read of ``key``.

    ``is_enabled`` hardcodes a ``'false'`` fallback, so the default-on
    contract reads through :func:`get_bool_config` with ``default=True`` (same
    pattern as ``staff_comment_alerts_enabled``).
    """
    return get_bool_config(key, default=True)


def stuck_proposal_hours():
    """Hours a proposal may wait for answers before it is stuck (issue #1935)."""
    return _int_config('PODS_STUCK_PROPOSAL_HOURS', 48, minimum=1, maximum=336)


def stuck_proposal_alert_enabled():
    """Default-on read of ``PODS_STUCK_PROPOSAL_ALERT_ENABLED`` (issue #1935)."""
    return _default_on('PODS_STUCK_PROPOSAL_ALERT_ENABLED')


def chat_enabled():
    """Default-on read of ``PODS_CHAT_ENABLED`` (issue #1920): the pilot kill switch."""
    return _default_on('PODS_CHAT_ENABLED')


def chat_messages_per_hour():
    """Messages one member may post across all pods in a rolling hour (issue #1920)."""
    return _int_config('PODS_CHAT_MESSAGES_PER_HOUR', 30, minimum=1, maximum=500)


def meeting_reminder_hours():
    """Hours before a scheduled pod meeting when the bell reminder goes out (issue #1919)."""
    return _int_config('PODS_MEETING_REMINDER_HOURS', 24, minimum=1, maximum=72)


def first_meeting_nudge_days():
    """Days after the second member joins before the dashboard ``Plan your
    first meeting`` row appears (issue #1928)."""
    return _int_config('PODS_FIRST_MEETING_NUDGE_DAYS', 3, minimum=0, maximum=30)


def artifacts_enabled():
    """Default-on read of ``PODS_ARTIFACTS_ENABLED`` (issue #1922): the member-side kill switch."""
    return _default_on('PODS_ARTIFACTS_ENABLED')


def artifact_max_kb():
    """Largest pod note or transcript in KB of UTF-8 text (issue #1922)."""
    return _int_config('PODS_ARTIFACT_MAX_KB', 500, minimum=1, maximum=2048)
