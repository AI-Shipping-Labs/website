"""Timezone option helpers for account preferences."""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.utils import timezone

# Default datetime format used by ``format_user_datetime`` when no ``fmt``
# is supplied by the caller. Matches the style produced by
# ``events.services.display_time.format_event_time_range`` (without the
# trailing zone label, which the helper appends separately).
DEFAULT_USER_DATETIME_FORMAT = "%B %d, %Y, %H:%M"


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


def format_user_datetime(value, user, *, fmt=None):
    """Format ``value`` in the recipient's timezone for transactional emails.

    Issue #666: emails must render times in the recipient's IANA timezone so
    the user does not have to convert UTC mentally. The helper is the single
    source of truth for that conversion.

    Contract:
        - When ``user`` has a valid ``preferred_timezone``: convert ``value``
          to that zone, format with ``fmt`` (default
          :data:`DEFAULT_USER_DATETIME_FORMAT`), and append the IANA name —
          e.g. ``"March 21, 2026, 18:00 Europe/Berlin"``.
        - When ``user`` is ``None`` or ``user.preferred_timezone`` is empty
          or invalid: format in UTC and append the literal ``UTC`` token so
          the recipient can convert unambiguously.
        - Naive datetimes are treated as UTC (matching Django's stored
          datetimes in this project).
        - Uses :class:`zoneinfo.ZoneInfo` exclusively; never depends on
          ``pytz``.

    Args:
        value: A ``datetime`` instance. Naive datetimes are interpreted as
            UTC.
        user: A ``User`` instance, or ``None``. The helper reads
            ``user.preferred_timezone``.
        fmt: Optional ``strftime`` format string. Defaults to
            :data:`DEFAULT_USER_DATETIME_FORMAT`.

    Returns:
        A string ``"<formatted time> <tz label>"``.
    """
    if not isinstance(value, datetime):
        raise TypeError(
            f"format_user_datetime requires a datetime instance, got "
            f"{type(value).__name__}",
        )

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    format_string = fmt or DEFAULT_USER_DATETIME_FORMAT

    tz_name = getattr(user, "preferred_timezone", "") if user is not None else ""
    if is_valid_timezone(tz_name):
        localized = value.astimezone(ZoneInfo(tz_name))
        return f"{localized.strftime(format_string)} {tz_name}"

    utc_value = value.astimezone(UTC)
    return f"{utc_value.strftime(format_string)} UTC"


# Issue #963: the account page fragment that scrolls a recipient to the
# Display Preferences / timezone control. Appended to ``site_url`` to build
# the in-email "set/update your timezone" link target. The account page is
# login-gated; an anonymous click lands on the standard login redirect
# (``/accounts/login/?next=/account/``), which is acceptable.
TIMEZONE_PREFERENCE_FRAGMENT = "/account/#display-preferences-section"


def build_timezone_account_url(site_url):
    """Return the absolute link to the account timezone control.

    ``site_url`` is the site base URL already present in every email
    context. The returned URL points at the Display Preferences section of
    the authenticated account page (issue #963).
    """
    return f"{(site_url or '').rstrip('/')}{TIMEZONE_PREFERENCE_FRAGMENT}"


def build_timezone_email_line(user, link_url):
    """Return the contextual "set/update your timezone" line for an event email.

    Issue #963: event emails render future times in the recipient's
    timezone (or UTC fallback via :func:`format_user_datetime`). This
    helper returns a single markdown sentence, with the account-timezone
    link already embedded, whose wording matches how the time was actually
    rendered. The variant is chosen off the SAME ``is_valid_timezone``
    check ``format_user_datetime`` uses, so copy can never contradict the
    rendered time.

    Args:
        user: The recipient ``User`` (or ``None``). Read for
            ``preferred_timezone``.
        link_url: The absolute account-timezone link the sentence links to
            (see :func:`build_timezone_account_url`).

    Returns:
        A markdown string. The UTC-fallback variant ("Set your timezone")
        when the recipient has no valid ``preferred_timezone``; the quieter
        "Change your timezone" variant when a valid IANA zone is set.
    """
    tz_name = getattr(user, "preferred_timezone", "") if user is not None else ""
    if is_valid_timezone(tz_name):
        return (
            "Times above are shown in your timezone. Wrong zone? "
            f"[Change your timezone]({link_url})."
        )
    return (
        "Times above are shown in UTC. "
        f"[Set your timezone]({link_url}) to see them in your local time."
    )
