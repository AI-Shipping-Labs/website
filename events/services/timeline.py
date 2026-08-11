"""Canonical context construction for public Events timelines."""

from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.db.models import Count
from django.utils import timezone

from accounts.services.timezones import is_valid_timezone
from events.models import EventRegistration
from events.services.time_windows import upcoming_events_queryset


def build_upcoming_rows(upcoming_events):
    """Collapse recurring occurrences into chronological logical rows."""
    series_buckets = {}
    series_order = []
    standalone = []
    for event in upcoming_events:
        if event.event_series_id:
            bucket = series_buckets.get(event.event_series_id)
            if bucket is None:
                bucket = []
                series_buckets[event.event_series_id] = bucket
                series_order.append(event.event_series_id)
            bucket.append(event)
        else:
            standalone.append(event)

    rows = [
        {
            'kind': 'event',
            'event': event,
            'sort_dt': event.start_datetime,
        }
        for event in standalone
    ]

    for series_id in series_order:
        occurrences = series_buckets[series_id]
        if len(occurrences) < 2:
            event = occurrences[0]
            rows.append({
                'kind': 'event',
                'event': event,
                'sort_dt': event.start_datetime,
            })
            continue
        count = len(occurrences)
        rows.append({
            'kind': 'series',
            'series': occurrences[0].event_series,
            'next_occurrence': occurrences[0],
            'count': count,
            'remaining_count': count - 1,
            'sort_dt': occurrences[0].start_datetime,
        })

    rows.sort(key=lambda row: row['sort_dt'])
    return rows


def viewer_timezone(user):
    """Resolve the viewer timezone used by the public Events timeline."""
    if not getattr(user, 'is_authenticated', False):
        return None
    tz_name = getattr(user, 'preferred_timezone', '')
    if is_valid_timezone(tz_name):
        return ZoneInfo(tz_name)
    return ZoneInfo('UTC')


def event_local_datetime(start_datetime, event_timezone, viewer_tz):
    """Localize an event start for canonical grouping and display."""
    dt = start_datetime
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    if viewer_tz is not None:
        return dt.astimezone(viewer_tz)
    tz_name = event_timezone if is_valid_timezone(event_timezone) else 'UTC'
    return dt.astimezone(ZoneInfo(tz_name))


def format_time_label(local_dt):
    """Return the compact 24-hour clock label used by timeline rows."""
    return local_dt.strftime('%H:%M')


def group_timeline_days(rows, viewer_tz):
    """Group already-ordered logical rows into viewer-local date buckets."""
    days = []
    current = None
    for row in rows:
        anchor = row.get('event') or row.get('next_occurrence')
        local_dt = event_local_datetime(
            anchor.start_datetime,
            anchor.timezone,
            viewer_tz,
        )
        row['display_time'] = format_time_label(local_dt)
        iso_date = local_dt.date().isoformat()
        if current is None or current['iso_date'] != iso_date:
            current = {
                'iso_date': iso_date,
                'date_label': f'{local_dt.strftime("%b")} {local_dt.day}',
                'weekday_label': local_dt.strftime('%A'),
                'rows': [],
            }
            days.append(current)
        current['rows'].append(row)
    return days


def build_public_upcoming_timeline(user, *, now=None, row_limit=None):
    """Build canonical public-upcoming rows, day groups, and viewer signals.

    ``row_limit`` is deliberately applied after series collapsing. This lets a
    preview render the first logical row without turning the first occurrence
    of a recurring series into an incorrect standalone card.
    """
    now = now or timezone.now()
    upcoming_events = (
        upcoming_events_queryset(now=now)
        .annotate(_attendee_count=Count('registrations'))
        .select_related('event_series')
        .order_by('start_datetime')
    )
    rows = build_upcoming_rows(upcoming_events)
    if row_limit is not None:
        rows = rows[:row_limit]

    resolved_timezone = viewer_timezone(user)
    registered_event_ids = set()
    if user.is_authenticated:
        registered_event_ids = set(
            EventRegistration.objects.filter(user=user).values_list(
                'event_id',
                flat=True,
            )
        )

    return {
        'upcoming_rows': rows,
        'upcoming_days': group_timeline_days(rows, resolved_timezone),
        'registered_event_ids': registered_event_ids,
        'events_display_timezone': (
            str(resolved_timezone) if resolved_timezone is not None else ''
        ),
    }
