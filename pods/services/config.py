"""Typed readers for the ``pods`` IntegrationSetting group (issue #1918).

Every value resolves through ``integrations.config.get_config`` (DB override
in Studio settings, then environment, then the default below), so operators
change them without a redeploy.
"""

from integrations.config import get_config

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


def default_max_members():
    return _int_config('PODS_DEFAULT_MAX_MEMBERS', 4, minimum=1, maximum=12)


def default_meeting_count():
    return _int_config('PODS_DEFAULT_MEETING_COUNT', 1, minimum=1, maximum=20)


def default_meeting_minutes():
    value = _int_config('PODS_DEFAULT_MEETING_MINUTES', 60)
    return value if value in MEETING_MINUTE_OPTIONS else 60


def max_open_requests_per_member():
    return _int_config('PODS_MAX_OPEN_REQUESTS_PER_MEMBER', 3, minimum=1)


def max_created_per_member():
    return _int_config('PODS_MAX_CREATED_PER_MEMBER', 2, minimum=1)


def suggestion_horizon_days():
    return _int_config('PODS_SUGGESTION_HORIZON_DAYS', 14, minimum=1, maximum=60)


def suggestion_count():
    return _int_config('PODS_SUGGESTION_COUNT', 5, minimum=1, maximum=20)


def stale_request_days():
    return _int_config('PODS_STALE_REQUEST_DAYS', 5, minimum=0)


def rerequest_cooldown_days():
    """Days a student waits after a first decline before asking the same pod again."""
    return _int_config('PODS_REREQUEST_COOLDOWN_DAYS', 14, minimum=0)


def stale_request_alert_enabled():
    """Default-on read of ``PODS_STALE_REQUEST_ALERT_ENABLED``.

    ``is_enabled`` hardcodes a ``'false'`` fallback, so the default-on
    contract reads the raw value with a ``'true'`` default instead (same
    pattern as ``staff_comment_alerts_enabled``).
    """
    raw = get_config('PODS_STALE_REQUEST_ALERT_ENABLED', 'true')
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ('true', '1', 'yes')
