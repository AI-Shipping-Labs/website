"""Weekly availability: validation, saving and summaries (issue #1918).

A window is a weekday plus a local start/end time in 30-minute steps.
``24:00`` (or ``00:00`` as an end time) means midnight at the end of the
day; a window never crosses midnight.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.services.timezones import is_valid_timezone
from pods.models import (
    MAX_WINDOWS_PER_PROFILE,
    PREFERENCE_IF_NEEDED,
    PREFERENCE_PREFERRED,
    WEEKDAY_NAMES,
    AvailabilityProfile,
    AvailabilityWindow,
)
from pods.services.people import humanize_age

STALE_AFTER_DAYS = 21

MSG_STEP = 'Use times on the hour or half hour, for example 18:00 or 18:30.'
MSG_CROSS_MIDNIGHT = 'To cover time after midnight, add a second window on the next day.'
MSG_EMPTY = 'End time must be after the start time.'
MSG_INCOMPLETE = 'Add both a start and an end time, or remove the window.'
MSG_TOO_MANY = f'You can add up to {MAX_WINDOWS_PER_PROFILE} windows a week.'
MSG_TIMEZONE = 'Choose a valid timezone.'


def overlap_message(weekday):
    return f'Windows on {WEEKDAY_NAMES[weekday]} overlap. Merge them into one window.'


class AvailabilityError(ValueError):
    """Raised with a member-facing message when windows are invalid."""


@dataclass(frozen=True)
class WindowSpec:
    weekday: int
    start_minute: int
    end_minute: int
    preference: str = PREFERENCE_PREFERRED


def parse_time(raw, *, is_end=False):
    """``'18:30'`` -> 1110. ``'24:00'``, and ``'00:00'`` as an end, -> 1440.

    Returns ``None`` for an unparseable value.
    """
    raw = (raw or '').strip()
    if not raw:
        return None
    parts = raw.split(':')
    if len(parts) < 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if hours == 24 and minutes == 0:
        return 1440
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    value = hours * 60 + minutes
    if is_end and value == 0:
        return 1440
    return value


def format_minute(value):
    """1110 -> ``'18:30'``; 1440 -> ``'24:00'``."""
    hours, minutes = divmod(int(value), 60)
    return f'{hours:02d}:{minutes:02d}'


def input_time_value(value):
    """Value for ``<input type="time">``: midnight end renders as 00:00."""
    return '00:00' if int(value) == 1440 else format_minute(value)


def validate_windows(specs):
    """Raise ``AvailabilityError`` for the first invalid window, else return
    the specs sorted by weekday and start."""
    specs = list(specs)
    if len(specs) > MAX_WINDOWS_PER_PROFILE:
        raise AvailabilityError(MSG_TOO_MANY)
    for spec in specs:
        if spec.weekday not in range(7):
            raise AvailabilityError('Choose a day for every window.')
        if spec.preference not in (PREFERENCE_PREFERRED, PREFERENCE_IF_NEEDED):
            raise AvailabilityError('Choose Works well or If needed for every window.')
        if spec.start_minute % 30 or spec.end_minute % 30:
            raise AvailabilityError(MSG_STEP)
        if not (0 <= spec.start_minute <= 1410 and 30 <= spec.end_minute <= 1440):
            raise AvailabilityError(MSG_STEP)
        if spec.end_minute == spec.start_minute:
            raise AvailabilityError(MSG_EMPTY)
        if spec.end_minute < spec.start_minute:
            raise AvailabilityError(MSG_CROSS_MIDNIGHT)
    ordered = sorted(specs, key=lambda s: (s.weekday, s.start_minute, s.end_minute))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.weekday == current.weekday and current.start_minute < previous.end_minute:
            raise AvailabilityError(overlap_message(current.weekday))
    return ordered


def parse_window_rows(post):
    """Collect ``window-<n>-*`` rows from a POST QueryDict.

    Rows where both times are blank are ignored (an untouched new row).
    Raises ``AvailabilityError`` for a half-filled or unparseable row.
    """
    indexes = set()
    for key in post.keys():
        if key.startswith('window-') and key.endswith('-weekday'):
            indexes.add(key[len('window-'):-len('-weekday')])
    specs = []
    for index in sorted(indexes, key=lambda value: (len(value), value)):
        prefix = f'window-{index}-'
        raw_start = post.get(prefix + 'start', '')
        raw_end = post.get(prefix + 'end', '')
        if not raw_start.strip() and not raw_end.strip():
            continue
        if not raw_start.strip() or not raw_end.strip():
            raise AvailabilityError(MSG_INCOMPLETE)
        start = parse_time(raw_start)
        end = parse_time(raw_end, is_end=True)
        if start is None or end is None or start == 1440:
            raise AvailabilityError(MSG_STEP)
        try:
            weekday = int(post.get(prefix + 'weekday', ''))
        except (TypeError, ValueError):
            raise AvailabilityError('Choose a day for every window.') from None
        preference = post.get(prefix + 'preference', PREFERENCE_PREFERRED) or PREFERENCE_PREFERRED
        specs.append(WindowSpec(weekday, start, end, preference))
    return specs


def get_profile(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    return (
        AvailabilityProfile.objects.filter(user=user)
        .prefetch_related('windows')
        .first()
    )


@transaction.atomic
def save_availability(user, timezone_name, specs):
    """Replace ``user``'s windows; returns the profile.

    Also sets ``User.preferred_timezone`` when it was empty, so event times
    elsewhere on the site follow the zone the member just chose.
    """
    if not is_valid_timezone(timezone_name):
        raise AvailabilityError(MSG_TIMEZONE)
    ordered = validate_windows(specs)
    profile, _created = AvailabilityProfile.objects.select_for_update().get_or_create(
        user=user, defaults={'timezone': timezone_name},
    )
    profile.timezone = timezone_name
    profile.save()
    profile.windows.all().delete()
    AvailabilityWindow.objects.bulk_create([
        AvailabilityWindow(
            profile=profile,
            weekday=spec.weekday,
            start_minute=spec.start_minute,
            end_minute=spec.end_minute,
            preference=spec.preference,
        )
        for spec in ordered
    ])
    if not (user.preferred_timezone or '').strip():
        user.preferred_timezone = timezone_name
        user.save(update_fields=['preferred_timezone'])
    return profile


def has_windows(profile):
    return profile is not None and bool(list(profile.windows.all()))


def is_stale(profile, now=None):
    if profile is None:
        return False
    now = now or timezone.now()
    return profile.updated_at < now - timedelta(days=STALE_AFTER_DAYS)


def freshness_line(profile, now=None):
    """``Updated 3 weeks ago - still right?`` once older than 21 days."""
    if not is_stale(profile, now):
        return ''
    return f'Updated {humanize_age(profile.updated_at, now)} - still right?'


def summary_rows(profile):
    """Rows for the compact summary: one per weekday that has windows."""
    if profile is None:
        return []
    by_day = {}
    for window in profile.windows.all():
        by_day.setdefault(window.weekday, []).append({
            'start': format_minute(window.start_minute),
            'end': format_minute(window.end_minute),
            'if_needed': window.preference == PREFERENCE_IF_NEEDED,
        })
    return [
        {'weekday': weekday, 'day': WEEKDAY_NAMES[weekday], 'windows': windows}
        for weekday, windows in sorted(by_day.items())
    ]


def editor_days(profile=None, specs=None):
    """Seven weekday rows for the editor, each with its windows' input values."""
    if specs is None:
        specs = [
            WindowSpec(w.weekday, w.start_minute, w.end_minute, w.preference)
            for w in (profile.windows.all() if profile is not None else [])
        ]
    days = []
    for weekday, name in enumerate(WEEKDAY_NAMES):
        windows = [
            {
                'index': f'{weekday}-{position}',
                'start': input_time_value(spec.start_minute),
                'end': input_time_value(spec.end_minute),
                'preference': spec.preference,
            }
            for position, spec in enumerate(s for s in specs if s.weekday == weekday)
        ]
        days.append({'weekday': weekday, 'name': name, 'windows': windows})
    return days
