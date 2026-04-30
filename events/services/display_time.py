"""Helpers for public event time display."""

from dataclasses import dataclass
from datetime import UTC
from zoneinfo import ZoneInfo

from accounts.services.timezones import is_valid_timezone
from integrations.config import get_config

DEFAULT_EVENT_DISPLAY_TIMEZONE = 'Europe/Berlin'
EVENT_DISPLAY_TIMEZONE_SETTING = 'EVENT_DISPLAY_TIMEZONE'


@dataclass(frozen=True)
class EventTimeDisplay:
    """Display data for the event-time template partial."""

    start_utc_iso: str
    end_utc_iso: str
    fallback_timezone: str
    fallback_range: str
    browser_timezone_enabled: bool


def get_event_display_timezone():
    """Return the configured public event display timezone."""
    configured_timezone = get_config(
        EVENT_DISPLAY_TIMEZONE_SETTING,
        DEFAULT_EVENT_DISPLAY_TIMEZONE,
    )
    if is_valid_timezone(configured_timezone):
        return configured_timezone
    return DEFAULT_EVENT_DISPLAY_TIMEZONE


def resolve_event_display_timezone(user=None):
    """Resolve a server-side event display timezone.

    Browser timezone is resolved client-side when no valid signed-in preference
    exists, so this returns the account preference or site fallback.
    """
    if user and user.is_authenticated and is_valid_timezone(user.preferred_timezone):
        return user.preferred_timezone
    return get_event_display_timezone()


def should_use_browser_timezone(user=None):
    """Return whether client-side browser timezone may replace the fallback."""
    return not (
        user
        and user.is_authenticated
        and is_valid_timezone(user.preferred_timezone)
    )


def build_event_time_display(event, user=None):
    """Build server fallback + client payload for an event time range."""
    fallback_timezone = resolve_event_display_timezone(user)
    browser_timezone_enabled = should_use_browser_timezone(user)
    return EventTimeDisplay(
        start_utc_iso=_as_utc_iso(event.start_datetime),
        end_utc_iso=_as_utc_iso(event.end_datetime) if event.end_datetime else '',
        fallback_timezone=fallback_timezone,
        fallback_range=format_event_time_range(
            event.start_datetime,
            event.end_datetime,
            fallback_timezone,
        ),
        browser_timezone_enabled=browser_timezone_enabled,
    )


def format_event_time_range(start_datetime, end_datetime, timezone_name):
    """Format an event start/end range in ``timezone_name``."""
    if not is_valid_timezone(timezone_name):
        timezone_name = DEFAULT_EVENT_DISPLAY_TIMEZONE

    tz = ZoneInfo(timezone_name)
    start_local = start_datetime.astimezone(tz)
    start_date = f'{start_local.strftime("%B")} {start_local.day}, {start_local.year}'
    start_time = start_local.strftime('%H:%M')

    if not end_datetime:
        return f'{start_date}, {start_time} {timezone_name}'

    end_local = end_datetime.astimezone(tz)
    end_date = f'{end_local.strftime("%B")} {end_local.day}, {end_local.year}'
    end_time = end_local.strftime('%H:%M')

    if start_local.date() == end_local.date():
        return f'{start_date}, {start_time}-{end_time} {timezone_name}'
    return f'{start_date}, {start_time} - {end_date}, {end_time} {timezone_name}'


def should_display_event_location(event):
    """Suppress stale Zoom location copy for completed/cancelled events."""
    if not event.location:
        return False
    is_stale_zoom = event.is_past and event.location.strip().lower() == 'zoom'
    return not is_stale_zoom


def _as_utc_iso(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')
