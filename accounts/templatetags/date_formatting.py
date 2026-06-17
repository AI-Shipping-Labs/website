"""Semantic date/time vocabulary for Django templates."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django import template
from django.template.defaultfilters import date as django_date
from django.utils import timezone

from accounts.services.timezones import format_user_datetime, is_valid_timezone

register = template.Library()

MEMBER_FULL_DATE = 'F j, Y'
MEMBER_SHORT_DATE = 'M j, Y'
MEMBER_COMPACT_DATE = 'M j'
MEMBER_SHORT_DATETIME = 'M j, Y H:i'
OPERATOR_DATE = 'Y-m-d'
OPERATOR_DATETIME = 'Y-m-d H:i'
OPERATOR_DATETIME_SECONDS = 'Y-m-d H:i:s'
OPERATOR_DATETIME_TZ = 'Y-m-d H:i:s T'
FORM_DATE_VALUE = 'Y-m-d'
OPERATOR_TIME = 'H:i'
EVENT_SOURCE_SHORT_DATETIME = 'D, M j, Y · H:i'
EVENT_SOURCE_FULL_DATETIME = 'l, M j, Y · H:i'


def _format(value, fmt):
    if value in (None, ''):
        return ''
    return django_date(value, fmt)


@register.filter
def member_full_date(value):
    return _format(value, MEMBER_FULL_DATE)


@register.filter
def member_short_date(value):
    return _format(value, MEMBER_SHORT_DATE)


@register.filter
def member_compact_date(value):
    return _format(value, MEMBER_COMPACT_DATE)


@register.filter
def member_short_datetime(value):
    return _format(value, MEMBER_SHORT_DATETIME)


@register.filter
def operator_date(value):
    return _format(value, OPERATOR_DATE)


@register.filter
def operator_datetime(value):
    return _format(value, OPERATOR_DATETIME)


@register.filter
def operator_datetime_seconds(value):
    return _format(value, OPERATOR_DATETIME_SECONDS)


@register.filter
def operator_datetime_tz(value):
    return _format(value, OPERATOR_DATETIME_TZ)


@register.filter
def form_date_value(value):
    return _format(value, FORM_DATE_VALUE)


@register.filter
def operator_time(value):
    return _format(value, OPERATOR_TIME)


@register.simple_tag
def user_event_datetime(value, user):
    """Render a member-specific event/session datetime via the timezone helper."""
    if value in (None, ''):
        return ''
    return format_user_datetime(value, user)


@register.filter
def event_source_short_datetime(event):
    return _format_event_source(event, EVENT_SOURCE_SHORT_DATETIME)


@register.filter
def event_source_full_datetime(event):
    return _format_event_source(event, EVENT_SOURCE_FULL_DATETIME, append_timezone=True)


def _format_event_source(event, fmt, *, append_timezone=False):
    if event in (None, ''):
        return ''
    value = getattr(event, 'start_datetime', None)
    tz_name = getattr(event, 'timezone', '') or ''
    if value in (None, ''):
        return ''
    if not isinstance(value, datetime):
        return ''
    if value.tzinfo is None:
        value = timezone.make_aware(value, ZoneInfo('UTC'))
    if not is_valid_timezone(tz_name):
        tz_name = 'UTC'
    rendered = django_date(value.astimezone(ZoneInfo(tz_name)), fmt)
    if append_timezone:
        return f'{rendered} {tz_name}'
    return rendered
