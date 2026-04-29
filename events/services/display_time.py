"""Helpers for public event time display."""

from dataclasses import dataclass
from datetime import UTC
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from integrations.config import get_config

DEFAULT_EVENT_DISPLAY_TIMEZONE = 'Europe/Berlin'
EVENT_DISPLAY_TIMEZONE_SETTING = 'EVENT_DISPLAY_TIMEZONE'

EVENT_TIMEZONE_CHOICES = [
    'Europe/Berlin',
    'UTC',
    'America/New_York',
    'America/Los_Angeles',
    'Europe/London',
    'Europe/Paris',
    'Asia/Tokyo',
    'Asia/Singapore',
    'Australia/Sydney',
]


@dataclass(frozen=True)
class EventTimeDisplay:
    """Display data for the event-time template partial."""

    start_utc_iso: str
    end_utc_iso: str
    fallback_timezone: str
    fallback_range: str
    timezone_choices: list[str]


def is_valid_timezone(timezone_name):
    """Return whether ``timezone_name`` is a valid IANA timezone."""
    if not timezone_name:
        return False
    try:
        ZoneInfo(str(timezone_name))
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def get_event_display_timezone():
    """Return the configured public event display timezone."""
    configured_timezone = get_config(
        EVENT_DISPLAY_TIMEZONE_SETTING,
        DEFAULT_EVENT_DISPLAY_TIMEZONE,
    )
    if is_valid_timezone(configured_timezone):
        return configured_timezone
    return DEFAULT_EVENT_DISPLAY_TIMEZONE


def build_event_time_display(event):
    """Build server fallback + client payload for an event time range."""
    fallback_timezone = get_event_display_timezone()
    timezone_choices = _build_timezone_choices(fallback_timezone)
    return EventTimeDisplay(
        start_utc_iso=_as_utc_iso(event.start_datetime),
        end_utc_iso=_as_utc_iso(event.end_datetime) if event.end_datetime else '',
        fallback_timezone=fallback_timezone,
        fallback_range=format_event_time_range(
            event.start_datetime,
            event.end_datetime,
            fallback_timezone,
        ),
        timezone_choices=timezone_choices,
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


def _build_timezone_choices(default_timezone):
    choices = [default_timezone]
    choices.extend(EVENT_TIMEZONE_CHOICES)
    return list(dict.fromkeys(choices))


def _as_utc_iso(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')
