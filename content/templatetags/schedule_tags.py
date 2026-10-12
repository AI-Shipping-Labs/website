"""Display deadlines in the course schedule's explicit timezone."""

from zoneinfo import ZoneInfo

from django import template
from django.template.defaultfilters import date as django_date
from django.utils import timezone

from content.services.course_schedule import schedule_instant_iso

register = template.Library()


@register.filter
def schedule_datetime(value, timezone_name):
    return _format_schedule_datetime(value, timezone_name, include_timezone=True)


@register.filter
def schedule_datetime_local(value, timezone_name):
    """Format a schedule time without repeating its zone label."""
    return _format_schedule_datetime(value, timezone_name, include_timezone=False)


@register.filter
def schedule_instant(value):
    """UTC instant for the ``datetime`` attribute and client re-formatting.

    Issue #1795: the server renders a truthful fallback-zone label; the
    client needs the underlying UTC instant to localize on first visit.
    """
    return schedule_instant_iso(value)


def _format_schedule_datetime(value, timezone_name, *, include_timezone):
    if value is None:
        return ''
    zone = ZoneInfo(timezone_name or 'UTC')
    local_value = timezone.localtime(value, zone)
    formatted = django_date(local_value, "M j, Y H:i")
    return f'{formatted} {zone.key}' if include_timezone else formatted
