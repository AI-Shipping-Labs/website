"""Runtime configuration for content-sync watchdog thresholds."""

from integrations.config import get_config

DEFAULT_SYNC_QUEUED_THRESHOLD_MINUTES = 10
DEFAULT_SYNC_RUNNING_THRESHOLD_MINUTES = 30


def _positive_int_config(key, default):
    """Return a positive integer setting, falling back on invalid values."""
    raw_value = get_config(key, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def sync_queued_threshold_minutes():
    """Minutes a queued sync may wait before the watchdog fails it."""
    return _positive_int_config(
        'SYNC_QUEUED_THRESHOLD_MINUTES',
        DEFAULT_SYNC_QUEUED_THRESHOLD_MINUTES,
    )


def sync_running_threshold_minutes():
    """Minutes a running sync may take before the watchdog fails it."""
    return _positive_int_config(
        'SYNC_RUNNING_THRESHOLD_MINUTES',
        DEFAULT_SYNC_RUNNING_THRESHOLD_MINUTES,
    )
