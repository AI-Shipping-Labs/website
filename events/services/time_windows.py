from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from events.models import Event
from events.models.event import HIDDEN_FROM_PUBLIC_STATUSES

DEFAULT_EVENT_DURATION = timedelta(hours=1)


def _field_name(field_prefix, field):
    return f'{field_prefix}{field}'


def upcoming_window_q(now=None, *, field_prefix=''):
    """Return the canonical time-derived upcoming event window."""
    now = now or timezone.now()
    return (
        Q(**{f'{_field_name(field_prefix, "end_datetime")}__gt': now})
        | Q(
            **{
                f'{_field_name(field_prefix, "end_datetime")}__isnull': True,
                f'{_field_name(field_prefix, "start_datetime")}__gt': (
                    now - DEFAULT_EVENT_DURATION
                ),
            }
        )
    )


def past_window_q(now=None, *, field_prefix=''):
    """Return the canonical time-derived past event window."""
    now = now or timezone.now()
    return (
        Q(**{f'{_field_name(field_prefix, "end_datetime")}__lte': now})
        | Q(
            **{
                f'{_field_name(field_prefix, "end_datetime")}__isnull': True,
                f'{_field_name(field_prefix, "start_datetime")}__lte': (
                    now - DEFAULT_EVENT_DURATION
                ),
            }
        )
    )


def public_events_queryset(queryset=None):
    """Return events visible on public/member event listing surfaces."""
    queryset = queryset if queryset is not None else Event.objects.all()
    return queryset.exclude(status__in=HIDDEN_FROM_PUBLIC_STATUSES)


def upcoming_events_queryset(queryset=None, *, now=None, public=True):
    """Return events whose effective end is still in the future."""
    queryset = queryset if queryset is not None else Event.objects.all()
    if public:
        queryset = public_events_queryset(queryset)
    return queryset.filter(upcoming_window_q(now))


def past_events_queryset(queryset=None, *, now=None, public=True):
    """Return events whose effective end has passed."""
    queryset = queryset if queryset is not None else Event.objects.all()
    if public:
        queryset = public_events_queryset(queryset)
    return queryset.filter(past_window_q(now))


def past_recording_events_queryset(queryset=None, *, now=None):
    """Return public finished events that have a publishable recording."""
    return (
        past_events_queryset(queryset, now=now, public=True)
        .filter(published=True)
        .exclude(recording_url='')
        .exclude(recording_url__isnull=True)
    )


def registered_upcoming_events(user, *, now=None, limit=3):
    """Return registered future events for the member dashboard."""
    from events.models import EventRegistration

    now = now or timezone.now()
    registrations = (
        EventRegistration.objects
        .filter(
            user=user,
            event__start_datetime__gt=now,
        )
        .filter(upcoming_window_q(now, field_prefix='event__'))
        .exclude(event__status__in=HIDDEN_FROM_PUBLIC_STATUSES)
        .select_related('event')
        .order_by('event__start_datetime')
    )
    if limit is not None:
        registrations = registrations[:limit]
    return [registration.event for registration in registrations]
