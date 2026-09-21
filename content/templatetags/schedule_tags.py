"""Display deadlines in the course schedule's explicit timezone."""

from zoneinfo import ZoneInfo

from django import template
from django.template.defaultfilters import date as django_date
from django.utils import timezone

register = template.Library()


@register.filter
def schedule_datetime(value, timezone_name):
    if value is None:
        return ''
    zone = ZoneInfo(timezone_name or 'UTC')
    local_value = timezone.localtime(value, zone)
    return f'{django_date(local_value, "M j, Y H:i")} {zone.key}'
