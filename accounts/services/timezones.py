"""Timezone option helpers for account preferences."""

from dataclasses import dataclass
from datetime import UTC
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.utils import timezone


@dataclass(frozen=True)
class TimezoneOption:
    value: str
    label: str
    offset_minutes: int


def is_valid_timezone(timezone_name):
    """Return whether ``timezone_name`` is a valid IANA timezone."""
    if not timezone_name:
        return False
    try:
        ZoneInfo(str(timezone_name))
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def build_timezone_options():
    """Return IANA timezones labeled with current GMT offset, west to east."""
    now_utc = timezone.now().astimezone(UTC)
    options = []
    for timezone_name in available_timezones():
        tz = ZoneInfo(timezone_name)
        offset = now_utc.astimezone(tz).utcoffset()
        if offset is None:
            continue
        offset_minutes = int(offset.total_seconds() // 60)
        options.append(
            TimezoneOption(
                value=timezone_name,
                label=f"{_format_offset(offset_minutes)} {timezone_name}",
                offset_minutes=offset_minutes,
            )
        )
    return sorted(options, key=lambda option: (option.offset_minutes, option.value))


def get_timezone_label(timezone_name):
    """Return the current offset label for a timezone, or empty string."""
    if not is_valid_timezone(timezone_name):
        return ""
    now_utc = timezone.now().astimezone(UTC)
    offset = now_utc.astimezone(ZoneInfo(timezone_name)).utcoffset()
    if offset is None:
        return timezone_name
    return f"{_format_offset(int(offset.total_seconds() // 60))} {timezone_name}"


def _format_offset(offset_minutes):
    sign = "+" if offset_minutes >= 0 else "-"
    absolute_minutes = abs(offset_minutes)
    hours, minutes = divmod(absolute_minutes, 60)
    return f"GMT{sign}{hours:02d}:{minutes:02d}"
