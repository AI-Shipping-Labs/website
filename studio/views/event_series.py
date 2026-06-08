"""Studio views for event-series CRUD.

Issue #564. An ``EventSeries`` lets staff create a weekly recurring
series in one form submission. Once created the series' only role is to
keep the events linked together — every member event is an independent
row.

The create form generates N ``Event`` rows 7 days apart and atomically
commits the series + events in a single transaction. Validation errors
roll back the whole flow so the database never holds a partial series.

Deleting the series leaves the member events alive (FK is
``on_delete=SET_NULL``); staff must delete each event explicitly.

Issue #575 renamed ``EventGroup`` to ``EventSeries`` everywhere; this
file moved from ``studio/views/event_groups.py`` to
``studio/views/event_series.py`` as part of that rename.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as dj_timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.services.timezones import (
    build_timezone_options,
    get_timezone_label,
    is_valid_timezone,
)
from events.models import Event, EventSeries
from events.services.series_registration import (
    enroll_series_registrants_in_event,
)
from notifications.models import Notification
from notifications.services import NotificationService
from notifications.services.notification_service import series_notification_title
from notifications.services.slack_announcements import (
    post_series_slack_announcement,
)
from studio.decorators import staff_required
from studio.views.events import _default_timezone_for, _should_autodetect_tz

logger = logging.getLogger(__name__)

# Sanity guard so a typo in the form cannot generate 1000 events.
MAX_OCCURRENCES = 26


def _parse_date_str(date_str):
    """Parse dd/mm/yyyy into a ``date``."""
    day, month, year = date_str.split('/')
    return datetime(int(year), int(month), int(day)).date()


def _localize_to_utc(local_date, local_time, tz_name):
    """Combine ``(date, time)`` interpreted in ``tz_name`` and convert to UTC.

    Issue #665. The Studio picker captures wall-clock values in the
    admin's chosen timezone; storage is always UTC so the calendar
    arithmetic on member events stays stable across DST.
    """
    zone = ZoneInfo(tz_name) if is_valid_timezone(tz_name) else ZoneInfo('UTC')
    local_aware = datetime.combine(local_date, local_time).replace(tzinfo=zone)
    return local_aware.astimezone(ZoneInfo('UTC'))


def _generate_unique_slug(base, used=None):
    """Return a slug that does not collide with an existing Event slug.

    If ``base`` already exists, append ``-2``, ``-3`` … until a free slug
    is found. ``used`` is an optional set of slugs created in the same
    transaction (so two siblings inside the same form submission do not
    overlap before they hit the database).
    """
    used = used or set()
    candidate = base
    suffix = 2
    while (
        candidate in used
        or Event.objects.filter(slug=candidate).exists()
    ):
        candidate = f'{base}-{suffix}'
        suffix += 1
    return candidate


@staff_required
def event_series_list(request):
    """List all event series."""
    series_list = EventSeries.objects.all().order_by('-created_at')
    return render(request, 'studio/event_series/list.html', {
        'series_list': series_list,
    })


@staff_required
def event_series_create(request):
    """Create an EventSeries + N Event rows in one transaction.

    On validation failure the whole submission is rolled back so the
    database never holds a partial series — staff see the form again
    with field-level errors and no orphan rows.

    A day-of-week conflict between the supplied start date and a
    different weekday in the user's intent is not rejected: the start
    date is authoritative. ``day_of_week`` is derived from the date.
    """
    errors = {}
    default_tz = _default_timezone_for(request.user)
    form_values = {
        'name': '',
        'slug': '',
        'description': '',
        'start_date': '',
        'start_time': '',
        'duration_hours': '1',
        'occurrences': '6',
        # Issue #665: default to admin's preferred TZ, never 'Europe/Berlin'.
        'timezone': default_tz,
        'required_level': '0',
        'kind': 'standard',
        'platform': 'zoom',
    }

    if request.method == 'POST':
        for key in form_values:
            form_values[key] = request.POST.get(key, form_values[key]).strip()

        name = form_values['name']
        slug = form_values['slug'] or slugify(name)
        description = form_values['description']
        start_date_str = form_values['start_date']
        start_time_str = form_values['start_time']
        duration_str = form_values['duration_hours'] or '1'
        occurrences_str = form_values['occurrences'] or '0'
        # Issue #665: validate the TZ; reject unknown IANA names.
        posted_tz = form_values['timezone'] or default_tz
        if posted_tz and not is_valid_timezone(posted_tz):
            errors['timezone'] = 'Unknown timezone.'
            timezone_value = default_tz
        else:
            timezone_value = posted_tz
        required_level_str = form_values['required_level'] or '0'
        kind = form_values['kind'] or 'standard'
        platform = form_values['platform'] or 'zoom'

        if not name:
            errors['name'] = 'Name is required.'

        try:
            start_date = _parse_date_str(start_date_str)
        except (ValueError, AttributeError):
            errors['start_date'] = 'Start date is required (dd/mm/yyyy).'
            start_date = None

        try:
            hour, minute = start_time_str.split(':')
            start_time = datetime(2000, 1, 1, int(hour), int(minute)).time()
        except (ValueError, AttributeError):
            errors['start_time'] = 'Start time is required (HH:MM, 24h).'
            start_time = None

        try:
            duration_hours = float(duration_str)
            if duration_hours <= 0:
                raise ValueError
        except ValueError:
            errors['duration_hours'] = 'Duration must be a positive number.'
            duration_hours = None

        try:
            occurrences = int(occurrences_str)
        except ValueError:
            errors['occurrences'] = 'Occurrences must be a whole number.'
            occurrences = None
        else:
            if occurrences < 1:
                errors['occurrences'] = (
                    'Occurrences must be at least 1.'
                )
            elif occurrences > MAX_OCCURRENCES:
                errors['occurrences'] = (
                    f'Occurrences cannot exceed {MAX_OCCURRENCES}.'
                )

        try:
            required_level = int(required_level_str)
        except ValueError:
            required_level = 0

        if slug and EventSeries.objects.filter(slug=slug).exists():
            errors['slug'] = 'A series with this slug already exists.'

        if not errors:
            with transaction.atomic():
                series = EventSeries(
                    name=name,
                    slug=slug,
                    description=description,
                    cadence='weekly',
                    cadence_weeks=1,
                    day_of_week=start_date.weekday(),
                    start_time=start_time,
                    timezone=timezone_value,
                )
                series.save()

                used_slugs = set()
                for i in range(1, occurrences + 1):
                    occurrence_date = start_date + timedelta(days=7 * (i - 1))
                    event_start = _localize_to_utc(
                        occurrence_date, start_time, timezone_value,
                    )
                    event_end = event_start + timedelta(hours=duration_hours)

                    base_slug = f'{series.slug}-session-{i}'
                    event_slug = _generate_unique_slug(base_slug, used_slugs)
                    used_slugs.add(event_slug)

                    occurrence = Event.objects.create(
                        title=f'{series.name} — Session {i}',
                        slug=event_slug,
                        description='',
                        kind=kind,
                        platform=platform,
                        start_datetime=event_start,
                        end_datetime=event_end,
                        timezone=timezone_value,
                        status='draft',
                        required_level=required_level,
                        origin='studio',
                        event_series=series,
                        series_position=i,
                        published=True,
                    )
                    # Issue #857: shared auto-enroll hook. A freshly
                    # created series has no registrants yet, so this is a
                    # no-op here — wired for consistency with the other
                    # occurrence-creation paths.
                    enroll_series_registrants_in_event(occurrence)
            return redirect('studio_event_series_detail', series_id=series.pk)

    tz_value = form_values['timezone'] or default_tz
    return render(request, 'studio/event_series/form.html', {
        'form_values': form_values,
        'errors': errors,
        'max_occurrences': MAX_OCCURRENCES,
        'timezone_value': tz_value,
        'timezone_label': get_timezone_label(tz_value) or tz_value,
        'timezone_options': build_timezone_options(),
        # Issue #855: with no saved preference and a fresh GET, default the
        # picker to the browser zone instead of UTC. A re-rendered POST
        # carries the admin's chosen value, so don't auto-detect then.
        'tz_autodetect': (
            _should_autodetect_tz(request.user)
            and request.method != 'POST'
        ),
    })


@staff_required
def event_series_detail(request, series_id):
    """Detail page: lists every member event with edit/delete links."""
    series = get_object_or_404(EventSeries, pk=series_id)

    if request.method == 'POST':
        # Inline edit of series metadata only — schedule fields stay
        # immutable on the series (per-event edits handle drift).
        series.name = request.POST.get('name', series.name).strip() or series.name
        new_slug = request.POST.get('slug', series.slug).strip()
        if new_slug and new_slug != series.slug:
            if not EventSeries.objects.filter(slug=new_slug).exclude(pk=series.pk).exists():
                series.slug = new_slug
        series.description = request.POST.get('description', series.description)
        series.save()
        return redirect('studio_event_series_detail', series_id=series.pk)

    events = series.events.all().order_by(
        'series_position', 'start_datetime',
    )
    # Issue #665: the add-occurrence form inherits the series' TZ; show
    # it in the picker so the admin sees the active zone next to the
    # date input.
    tz_value = series.timezone or _default_timezone_for(request.user)
    return render(request, 'studio/event_series/detail.html', {
        'series': series,
        'events': events,
        'timezone_value': tz_value,
        'timezone_label': get_timezone_label(tz_value) or tz_value,
        'timezone_options': build_timezone_options(),
        # Issue #855: an existing series keeps its stored timezone. Only
        # auto-detect the browser zone when the series has none and the
        # admin has no saved preference.
        'tz_autodetect': (
            not series.timezone
            and _should_autodetect_tz(request.user)
        ),
    })


@staff_required
@require_POST
def event_series_add_occurrence(request, series_id):
    """Append one more event to the series with the next series_position."""
    series = get_object_or_404(EventSeries, pk=series_id)
    start_date_str = request.POST.get('start_date', '').strip()
    duration_str = request.POST.get('duration_hours', '').strip() or '1'

    try:
        start_date = _parse_date_str(start_date_str)
    except (ValueError, AttributeError):
        # Re-render the detail page with a flash-style error.
        events = series.events.all().order_by('series_position', 'start_datetime')
        tz_value = series.timezone or _default_timezone_for(request.user)
        return render(request, 'studio/event_series/detail.html', {
            'series': series,
            'events': events,
            'add_error': 'Start date is required (dd/mm/yyyy).',
            'timezone_value': tz_value,
            'timezone_label': get_timezone_label(tz_value) or tz_value,
            'timezone_options': build_timezone_options(),
            'tz_autodetect': (
                not series.timezone
                and _should_autodetect_tz(request.user)
            ),
        }, status=400)

    try:
        duration_hours = float(duration_str)
        if duration_hours <= 0:
            raise ValueError
    except ValueError:
        duration_hours = 1.0

    max_pos = (
        series.events.exclude(series_position__isnull=True)
        .order_by('-series_position').values_list('series_position', flat=True)
        .first()
    )
    next_pos = (max_pos or 0) + 1

    # Issue #665: combine the picked date with the series' start_time in
    # the chosen TZ (defaults to the series' TZ; admin can override per
    # occurrence) and convert to UTC for storage. A tampered TZ value
    # falls back to the series TZ.
    posted_tz = (request.POST.get('timezone') or '').strip()
    occurrence_tz = (
        posted_tz if posted_tz and is_valid_timezone(posted_tz)
        else (series.timezone or 'UTC')
    )
    event_start = _localize_to_utc(
        start_date, series.start_time, occurrence_tz,
    )
    event_end = event_start + timedelta(hours=duration_hours)

    base_slug = f'{series.slug}-session-{next_pos}'
    event_slug = _generate_unique_slug(base_slug)

    new_event = Event.objects.create(
        title=f'{series.name} — Session {next_pos}',
        slug=event_slug,
        description='',
        kind='standard',
        platform='zoom',
        start_datetime=event_start,
        end_datetime=event_end,
        timezone=occurrence_tz,
        status='draft',
        required_level=0,
        origin='studio',
        event_series=series,
        series_position=next_pos,
        published=True,
    )
    # Issue #857: auto-enroll existing series registrants. Best-effort and
    # idempotent; a draft occurrence enrolls nobody until it is published
    # to ``upcoming`` (the helper gates on ``is_upcoming``).
    enroll_series_registrants_in_event(new_event)
    return redirect('studio_event_series_detail', series_id=series.pk)


def _series_was_recently_notified(series):
    """Return True if this series was notified within the last 24 hours.

    Mirrors ``studio.views.notifications._was_recently_notified`` but keyed
    on the series notification title + public URL (issue #868).
    """
    title = series_notification_title(series)
    url = series.get_absolute_url()
    cutoff = dj_timezone.now() - timedelta(hours=24)
    return Notification.objects.filter(
        title=title,
        url=url,
        created_at__gte=cutoff,
    ).exists()


@staff_required
@require_POST
def event_series_notify(request, series_id):
    """Notify eligible subscribers about the whole series.

    Creates one notification per eligible user deep-linking to the public
    series page. Returns ``{"notified": N}``; 409 when the series was
    already notified within the last 24 hours (issue #868).
    """
    series = get_object_or_404(EventSeries, pk=series_id)

    if _series_was_recently_notified(series):
        return JsonResponse(
            {'error': 'Already notified in the last 24 hours'},
            status=409,
        )

    result = NotificationService.notify_series(series)
    return JsonResponse({'notified': result.get('notified', 0)})


@staff_required
@require_POST
def event_series_announce_slack(request, series_id):
    """Post a single Slack announcement for the whole series (issue #868).

    Returns ``{"posted": true}`` on success. When the series has no
    upcoming sessions, or Slack is not configured / the post failed,
    returns a structured ``{"error": ...}`` with status 500.
    """
    series = get_object_or_404(EventSeries, pk=series_id)

    from notifications.services.slack_announcements import (
        _series_upcoming_sessions,
    )

    if not _series_upcoming_sessions(series):
        return JsonResponse(
            {'error': 'No upcoming sessions to announce.'},
            status=500,
        )

    try:
        posted = post_series_slack_announcement(series)
    except Exception as exc:
        logger.exception(
            'Failed to post series Slack announcement for %s', series_id,
        )
        return JsonResponse({'error': str(exc)}, status=500)

    if posted:
        return JsonResponse({'posted': True})
    return JsonResponse(
        {'error': 'Slack not configured or post failed'},
        status=500,
    )


@staff_required
@require_POST
def event_series_delete(request, series_id):
    """Delete the series; ``SET_NULL`` preserves the member events."""
    series = get_object_or_404(EventSeries, pk=series_id)
    series.delete()
    return redirect('studio_event_list')
