"""Who may see a cohort's pods, and how members are named (issue #1918)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

from accounts.services.timezones import get_timezone_label, is_valid_timezone
from content.access import LEVEL_MAIN, can_access, get_user_level
from content.models import CohortEnrollment
from content.models.cohort import COHORT_MODE_COHORT

UNNAMED_MEMBER = 'Unnamed member'


def display_name(user):
    """``Anna K.``: first name plus last-name initial; never the email."""
    if user is None:
        return UNNAMED_MEMBER
    first = (user.first_name or '').strip()
    if not first:
        return UNNAMED_MEMBER
    last = (user.last_name or '').strip()
    return f'{first} {last[0].upper()}.' if last else first


def is_dated_cohort(cohort):
    return cohort is not None and cohort.mode == COHORT_MODE_COHORT


def is_enrolled_in_cohort(user, cohort):
    if not getattr(user, 'is_authenticated', False) or cohort is None:
        return False
    return CohortEnrollment.objects.filter(user=user, cohort=cohort).exists()


def is_cohort_participant(user, cohort):
    """Enrolled in the dated cohort and still has access to its course."""
    return (
        is_dated_cohort(cohort)
        and is_enrolled_in_cohort(user, cohort)
        and can_access(user, cohort.course)
    )


def can_view_cohort_pods(user, cohort):
    if not getattr(user, 'is_authenticated', False) or not is_dated_cohort(cohort):
        return False
    return bool(user.is_staff) or is_cohort_participant(user, cohort)


def is_slack_eligible(user):
    """Same rule as ``/community/slack``: Main or above."""
    return get_user_level(user) >= LEVEL_MAIN


def user_timezone_name(user, profile=None):
    """The member's availability zone, else their preferred timezone, else ''."""
    if profile is None:
        profile = getattr(user, '_pods_profile', None)
    if profile is not None and is_valid_timezone(profile.timezone):
        return profile.timezone
    preferred = getattr(user, 'preferred_timezone', '') or ''
    return preferred if is_valid_timezone(preferred) else ''


def timezone_label(timezone_name):
    """``GMT+05:30 Asia/Kolkata``."""
    return get_timezone_label(timezone_name) if timezone_name else ''


def offset_minutes(timezone_name, now=None):
    now = now or timezone.now()
    offset = now.astimezone(ZoneInfo(timezone_name)).utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


def short_offset(minutes):
    """``GMT+1``, ``GMT+5:30``, ``GMT-4``."""
    sign = '+' if minutes >= 0 else '-'
    hours, mins = divmod(abs(minutes), 60)
    return f'GMT{sign}{hours}:{mins:02d}' if mins else f'GMT{sign}{hours}'


def offset_range_label(timezone_names, now=None):
    """``Members in GMT+1 to GMT+5:30`` for a set of zones, or ''."""
    offsets = sorted({offset_minutes(name, now) for name in timezone_names if name})
    if not offsets:
        return ''
    if len(offsets) == 1:
        return f'Members in {short_offset(offsets[0])}'
    return f'Members in {short_offset(offsets[0])} to {short_offset(offsets[-1])}'


def city_from_zone(timezone_name):
    """``Europe/Berlin`` -> ``Berlin``; ``America/New_York`` -> ``New York``."""
    if not timezone_name:
        return ''
    return timezone_name.rsplit('/', 1)[-1].replace('_', ' ')


def humanize_age(value, now=None):
    """``today``, ``1 day ago``, ``5 days ago``, ``3 weeks ago``."""
    now = now or timezone.now()
    if isinstance(value, datetime):
        days = max((now - value).days, 0)
    else:
        days = 0
    if days == 0:
        return 'today'
    if days == 1:
        return '1 day ago'
    if days < 14:
        return f'{days} days ago'
    weeks = days // 7
    return f'{weeks} weeks ago'
