"""Studio views for event CRUD."""

import csv
import datetime as _datetime
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Avg, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone as djtimezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.services.timezones import (
    build_timezone_options,
    format_user_datetime,
    get_timezone_label,
    is_valid_timezone,
)
from content.access import VISIBILITY_CHOICES
from events.models import Event, EventFeedback, EventHost, EventRegistration, Host
from events.models.event import EXTERNAL_HOST_CHOICES
from events.services.display_time import resolve_event_creation_timezone
from events.services.host_invite import (
    maybe_send_initial_host_invite,
    send_host_reschedule_invite,
)
from events.services.host_registration import maybe_register_host_as_attendee
from events.tasks.notify_reschedule import enqueue_reschedule_notice
from events.tasks.send_post_event_followup import enqueue_post_event_followup
from integrations.services.banner_generator.dispatch import enqueue_if_missing
from integrations.services.zoom import create_meeting
from studio.decorators import staff_required
from studio.services.banner_panel import banner_panel_context
from studio.utils import get_github_edit_url, is_synced
from studio.views.form_helpers import parse_comma_separated_tags

logger = logging.getLogger(__name__)

_VALID_EXTERNAL_HOSTS = {value for value, _ in EXTERNAL_HOST_CHOICES}
_VALID_EVENT_REQUIRED_LEVELS = {value for value, _ in VISIBILITY_CHOICES}
EVENT_LIST_PAST_PAGE_SIZE = 25

EVENT_KIND_ICON_MAP = {
    'standard': 'calendar',
    'workshop': 'wrench',
    'meetup': 'users',
    'q_and_a': 'message-circle-question',
}
EVENT_PLATFORM_ICON_MAP = {
    'zoom': 'video',
    'custom': 'link',
}


def annotate_derived_status(event, now=None):
    """Set ``derived_status`` / ``derived_status_label`` on a single Event.

    Single-sourced derivation (#893) shared by the events-list view and
    the event-series detail view so both render the same
    ``{% studio_status_badge %}``.

    Single-status precedence (#820): draft / cancelled win over the
    time-based label; otherwise the label is purely time-derived, so a
    legacy ``completed`` row with a future end reads Upcoming.
    """
    if now is None:
        now = djtimezone.now()
    effective_end = event.end_datetime or (
        event.start_datetime + timedelta(hours=1)
    )
    is_future = now < effective_end

    if event.status == 'draft':
        event.derived_status = 'draft'
        event.derived_status_label = 'Draft'
    elif event.status == 'cancelled':
        event.derived_status = 'cancelled'
        event.derived_status_label = 'Cancelled'
    elif is_future:
        event.derived_status = 'upcoming'
        event.derived_status_label = 'Upcoming'
    else:
        event.derived_status = 'past'
        event.derived_status_label = 'Past'
    return event


def _coerce_external_host(raw):
    """Issue #579. POSTs from the Studio dropdown are constrained, but
    a tampered or stale form post could land here with an arbitrary
    string. Coerce anything outside the canonical list to '' so we
    never persist a non-canonical value.
    """
    value = (raw or '').strip()
    return value if value in _VALID_EXTERNAL_HOSTS else ''


def _parse_required_level(value, fallback):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed in _VALID_EVENT_REQUIRED_LEVELS else fallback


def _default_timezone_for(user):
    """Return the default timezone for newly-created Studio events.

    Issue #665. New Studio forms default the timezone chooser to the
    logged-in admin's profile timezone; if it isn't set (or is somehow
    invalid), use the same site-default chain as API creation:
    profile preference -> ``EVENT_DISPLAY_TIMEZONE`` config -> UTC.
    """
    return resolve_event_creation_timezone(user)


def _should_autodetect_tz(user):
    """Return True when the picker should fall back to the browser timezone.

    Issue #855. When the admin has not saved a valid ``preferred_timezone``,
    the server-rendered default is the bare ``settings.TIME_ZONE`` (UTC) — a
    poor default for an organizer in another zone. In that case the shared
    datetime picker auto-detects the browser timezone client-side. A saved
    profile timezone (or any explicit event/series timezone the caller passes
    instead) takes precedence and must not be overridden, so this returns
    False whenever the user already has a valid preference.
    """
    candidate = getattr(user, 'preferred_timezone', '') or ''
    return not (candidate and is_valid_timezone(candidate))


def _parse_event_datetime(post_data, tz_name):
    """Parse separate date, time, and duration fields into start/end datetimes.

    Expects POST fields:
    - event_date: dd/mm/yyyy
    - event_time: HH:MM (24-hour)
    - duration_hours: float (optional, default 1)

    Returns ``(start_utc, end_utc)`` as timezone-aware UTC datetimes. The
    wall-clock ``(date, time)`` pair is interpreted in ``tz_name`` and
    then converted to UTC (issue #665). When ``tz_name`` is missing or
    invalid we fall back to UTC so a bad chooser value cannot lose data.
    """
    date_str = post_data.get('event_date', '').strip()
    time_str = post_data.get('event_time', '').strip()
    duration_str = post_data.get('duration_hours', '').strip()

    # Parse date (dd/mm/yyyy)
    day, month, year = date_str.split('/')
    parsed_date = datetime(int(year), int(month), int(day))

    # Parse time (HH:MM)
    hour, minute = time_str.split(':')
    local_naive = parsed_date.replace(hour=int(hour), minute=int(minute))

    # Localize and convert to UTC. is_valid_timezone() guards the lookup
    # so a tampered tz value falls back to UTC instead of raising.
    zone_name = tz_name if (tz_name and is_valid_timezone(tz_name)) else 'UTC'
    local_aware = local_naive.replace(tzinfo=ZoneInfo(zone_name))
    start_utc = local_aware.astimezone(ZoneInfo('UTC'))

    # Parse duration (default 1 hour)
    duration = float(duration_str) if duration_str else 1.0
    end_utc = start_utc + timedelta(hours=duration)

    return start_utc, end_utc


def _event_form_context(event, default_tz):
    """Build template context for the event form.

    The Date and Time inputs are pre-populated by rendering the stored
    UTC instant in the event's own timezone (issue #665) so the
    round-trip preserves the wall-clock value the admin originally
    typed. ``default_tz`` is the fallback for events with no
    ``timezone`` set.
    """
    context = {
        'event': event,
        'event_date': '',
        'event_time': '',
        'duration_hours': '1',
        'timezone_value': default_tz,
    }
    if event and event.start_datetime:
        tz_name = event.timezone or default_tz
        if not is_valid_timezone(tz_name):
            tz_name = 'UTC'
        context['timezone_value'] = tz_name

        local_start = _render_in_tz(event.start_datetime, tz_name)
        context['event_date'] = local_start.strftime('%d/%m/%Y')
        context['event_time'] = local_start.strftime('%H:%M')

        if event.end_datetime:
            delta = event.end_datetime - event.start_datetime
            hours = delta.total_seconds() / 3600
            # Format nicely: show integer if whole number, else one decimal
            if hours == int(hours):
                context['duration_hours'] = str(int(hours))
            else:
                context['duration_hours'] = str(round(hours, 1))
        else:
            context['duration_hours'] = '1'

        # Issue #855: the "Resolved" line on the edit form was unlabeled and
        # rendered the stored UTC instant with no zone, so "16:00" looked
        # ambiguous. Provide both the UTC values and the equivalent in the
        # event's selected zone, each labeled, so there is no ambiguity.
        context['resolved_tz_name'] = tz_name
        context['resolved_start_utc'] = _render_in_tz(
            event.start_datetime, 'UTC',
        ).strftime('%d/%m/%Y %H:%M')
        context['resolved_start_local'] = local_start.strftime(
            '%d/%m/%Y %H:%M',
        )
        if event.end_datetime:
            context['resolved_end_utc'] = _render_in_tz(
                event.end_datetime, 'UTC',
            ).strftime('%d/%m/%Y %H:%M')
            context['resolved_end_local'] = _render_in_tz(
                event.end_datetime, tz_name,
            ).strftime('%d/%m/%Y %H:%M')
    return context


def _selected_host_ids_for_event(event):
    if event is None:
        return []
    return [host.id for host in event.ordered_hosts]


def _host_options(selected_host_ids=None):
    selected_host_ids = selected_host_ids or []
    return Host.objects.filter(
        Q(is_active=True) | Q(id__in=selected_host_ids)
    ).order_by('name')


def _validate_host_ids(raw_values):
    host_ids = []
    for value in raw_values:
        try:
            host_id = int(value)
        except (TypeError, ValueError):
            return [], 'Unknown host selected.'
        host_ids.append(host_id)
    if len(set(host_ids)) != len(host_ids):
        return [], 'Duplicate hosts are not allowed.'
    existing_ids = set(
        Host.objects.filter(id__in=host_ids).values_list('id', flat=True)
    )
    if any(host_id not in existing_ids for host_id in host_ids):
        return [], 'Unknown host selected.'
    return host_ids, ''


def _set_event_hosts(event, host_ids):
    EventHost.objects.filter(event=event).delete()
    EventHost.objects.bulk_create([
        EventHost(event=event, host_id=host_id, position=position)
        for position, host_id in enumerate(host_ids)
    ])
    event._prefetched_objects_cache = {}


def _apply_host_context(context, selected_host_ids=None):
    selected_host_ids = selected_host_ids or _selected_host_ids_for_event(
        context.get('event'),
    )
    context['host_options'] = _host_options(selected_host_ids)
    context['selected_host_ids'] = selected_host_ids
    return context


def _render_in_tz(dt, tz_name):
    """Return ``dt`` rendered in ``tz_name``. Accepts naive (= UTC) or aware."""
    zone = ZoneInfo(tz_name) if is_valid_timezone(tz_name) else ZoneInfo('UTC')
    if dt.tzinfo is None:
        # Treat naive as UTC (Django settings have USE_TZ=True, TIME_ZONE='UTC').
        aware = dt.replace(tzinfo=ZoneInfo('UTC'))
    else:
        aware = dt
    return aware.astimezone(zone)


def event_kind_icon(kind):
    """Return the Lucide icon name for a Studio event kind."""
    return EVENT_KIND_ICON_MAP.get(kind, 'calendar')


def event_platform_icon(platform):
    """Return the Lucide icon name for a Studio event platform."""
    return EVENT_PLATFORM_ICON_MAP.get(platform, 'link')


def _decorate_event_list_row(event, user, now=None):
    """Attach list-only presentation metadata to an Event instance."""
    annotate_derived_status(event, now=now)
    event.kind_icon = event_kind_icon(event.kind)
    event.kind_label = event.get_kind_display()
    event.platform_icon = event_platform_icon(event.platform)
    event.platform_label = event.get_platform_display()
    event.start_datetime_display = format_user_datetime(
        event.start_datetime,
        user,
        fmt='%b %d, %Y, %H:%M',
    )
    return event


def _filtered_events_queryset(request):
    """Return the base Studio events queryset after search/status filters."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('q', '')

    events = Event.objects.select_related('event_series').all()
    if status_filter:
        events = events.filter(status=status_filter)
    if search:
        events = events.filter(title__icontains=search)
    return events, status_filter, search


def _split_events_for_studio(events, user):
    """Partition filtered events into Upcoming and Past groups."""
    now = djtimezone.now()
    one_hour = timedelta(hours=1)
    upcoming_events = []
    past_events = []
    for event in events:
        effective_end = event.end_datetime or (
            event.start_datetime + one_hour
        )
        is_future = now < effective_end

        _decorate_event_list_row(event, user, now=now)

        # Grouping: cancelled always sits in Past (consistent with
        # ``is_past``); everything else groups by the time comparison so
        # draft rows still land in a logical section.
        if event.status == 'cancelled':
            past_events.append(event)
        elif is_future:
            upcoming_events.append(event)
        else:
            past_events.append(event)

    upcoming_events.sort(key=lambda e: e.start_datetime)
    past_events.sort(key=lambda e: e.start_datetime, reverse=True)
    return upcoming_events, past_events


def _coerce_page_number(raw, num_pages):
    """Clamp the ``?page=`` query param into ``[1, num_pages]``."""
    try:
        page_num = int(raw)
    except (TypeError, ValueError):
        return 1
    if page_num < 1:
        return 1
    if page_num > num_pages:
        return num_pages
    return page_num


def _pager_querystring(request, page_number):
    """Build a pager querystring while preserving active filters."""
    params = request.GET.copy()
    params['page'] = str(page_number)
    return '?' + params.urlencode()


def _event_pager_context(request, page, paginator):
    """Build template context for the past-events pager partial."""
    if page.has_previous():
        first_url = _pager_querystring(request, 1)
        prev_url = _pager_querystring(request, page.previous_page_number())
    else:
        first_url = None
        prev_url = None
    if page.has_next():
        next_url = _pager_querystring(request, page.next_page_number())
        last_url = _pager_querystring(request, paginator.num_pages)
    else:
        next_url = None
        last_url = None

    return {
        'page': page,
        'paginator': paginator,
        'show_pager': paginator.num_pages > 1,
        'pager_first_url': first_url,
        'pager_prev_url': prev_url,
        'pager_next_url': next_url,
        'pager_last_url': last_url,
        'page_start_index': page.start_index(),
        'page_end_index': page.end_index(),
        'filtered_total': paginator.count,
    }


def _maybe_notify_reschedule(request, event, old_start):
    """Issue #670: enqueue rescheduling notices after a date-changing save.

    Trigger rules (all must hold):

    - ``old_start`` and ``event.start_datetime`` are both non-null.
    - ``old_start > now()`` — past events are excluded; emailing
      "this event has been rescheduled" for an event that already
      happened is worse than silence.
    - ``abs(new - old).total_seconds() >= 60`` — the form parser
      zeros seconds, so anything smaller is a no-op re-save and must
      not enqueue.

    When the trigger fires:

    - Bumps ``event.ics_sequence`` and persists it so the re-issued
      ``.ics`` carries a higher SEQUENCE than the original registration
      invite — calendar clients then overwrite the existing entry
      instead of creating a duplicate.
    - Enqueues the two-stage fan-out task with the ISO-encoded
      ``old_start`` (Django-Q arguments must be JSON-serialisable).
    - Flashes a success message with the PRE-FILTER registration count
      (admins see the audience size, not the deliveries — that way a
      user toggling ``unsubscribed`` between save and send doesn't
      produce a discrepancy in the flash).
    """
    if old_start is None or event.start_datetime is None:
        return
    if abs((event.start_datetime - old_start).total_seconds()) < 60:
        return
    now = djtimezone.now()
    if old_start <= now:
        return
    # Admin moved an upcoming event into the past: the email would
    # advertise a time the recipient cannot reach.
    if event.start_datetime <= now:
        return

    # Bump SEQUENCE before the fan-out so the per-user .ics already
    # carries the new value. ics_sequence is a PositiveIntegerField
    # so a single increment is enough; the previous registration
    # invite carried the pre-bump value.
    event.ics_sequence = (event.ics_sequence or 0) + 1
    event.save(update_fields=['ics_sequence'])

    # Issue #861: re-issue the host's calendar invite with the bumped
    # SEQUENCE so their entry moves to the new time (UID unchanged →
    # UPDATE, not duplicate). Best-effort; never breaks the save.
    send_host_reschedule_invite(event)

    count = EventRegistration.objects.filter(event=event).count()
    if count > 0:
        enqueue_reschedule_notice(event.pk, old_start.isoformat())

    # Issue #869: series subscribers get the canonical UPDATE as a
    # multi-event series invite (the per-event reschedule notice above
    # skips them to avoid double emails). Enqueue the series fan-out for
    # an occurrence that belongs to a series.
    if event.event_series_id:
        from events.tasks.notify_series_invite import enqueue_series_update
        enqueue_series_update(event.pk)

    label = 'attendee' if count == 1 else 'attendees'
    messages.success(
        request,
        f'Rescheduling notice sent to {count} registered {label}.',
    )


def _maybe_notify_series_cancellation(event, old_status):
    """Issue #869: notify series subscribers when an occurrence is cancelled.

    Fires only on a transition INTO ``cancelled`` for an occurrence that
    belongs to a series. Bumps ``ics_sequence`` so the ``METHOD:CANCEL``
    ``.ics`` carries a higher SEQUENCE than the last invite the subscriber
    received (calendar clients only honour a CANCEL whose SEQUENCE is
    greater-or-equal), then enqueues the per-subscriber CANCEL fan-out.

    API cancellations stay notification-free per the #678 contract — this
    is wired only into the Studio edit path.
    """
    if not event.event_series_id:
        return
    if event.status != 'cancelled' or old_status == 'cancelled':
        return

    event.ics_sequence = (event.ics_sequence or 0) + 1
    event.save(update_fields=['ics_sequence'])

    from events.tasks.notify_series_invite import enqueue_series_cancellation
    enqueue_series_cancellation(event.pk)


@staff_required
def event_list(request):
    """List upcoming Studio events only.

    Search and stored-status filters still apply. Past events live behind
    the dedicated paginated ``event_list_past`` view so the default page
    stays focused on actionable upcoming rows.
    """
    events, status_filter, search = _filtered_events_queryset(request)
    upcoming_events, past_events = _split_events_for_studio(
        events,
        request.user,
    )

    return render(request, 'studio/events/list.html', {
        'upcoming_events': upcoming_events,
        'upcoming_count': len(upcoming_events),
        'past_count': len(past_events),
        'has_events': bool(upcoming_events),
        'has_any_events': Event.objects.exists(),
        'status_filter': status_filter,
        'search': search,
        'is_past_view': False,
    })


@staff_required
def event_list_past(request):
    """List past Studio events with search/status filters and pagination."""
    events, status_filter, search = _filtered_events_queryset(request)
    _upcoming_events, past_events = _split_events_for_studio(
        events,
        request.user,
    )
    paginator = Paginator(past_events, EVENT_LIST_PAST_PAGE_SIZE)
    page_number = _coerce_page_number(
        request.GET.get('page'),
        paginator.num_pages or 1,
    )
    page = paginator.page(page_number)
    pager_context = _event_pager_context(request, page, paginator)

    return render(request, 'studio/events/list.html', {
        'past_events': page.object_list,
        'past_count': paginator.count,
        'has_events': bool(page.object_list),
        'has_any_events': Event.objects.exists(),
        'status_filter': status_filter,
        'search': search,
        'is_past_view': True,
        **pager_context,
    })


@staff_required
def event_create(request):
    """Create a single one-off Studio event.

    GET renders the create form (reusing ``templates/studio/events/form.html``
    with no ``event`` in context). POST validates input, creates an ``Event``
    row with ``origin='studio'`` and an empty ``source_repo``, then redirects
    to ``studio_event_edit`` so staff land on the full edit form (with
    sidebar panels) for the new event.

    Issue #574.
    """
    errors = {}
    default_tz = _default_timezone_for(request.user)
    form_values = {
        'title': '',
        'slug': '',
        'description': '',
        'event_date': '',
        'event_time': '',
        'duration_hours': '1',
        # Issue #665: default to admin's preferred TZ, never 'Europe/Berlin'.
        'timezone': default_tz,
        'platform': 'zoom',
        'status': 'draft',
        'required_level': '0',
        'location': '',
        'tags': '',
        'external_host': '',
        'custom_url': '',
        'host_email': '',
    }
    selected_host_ids = []

    if request.method == 'POST':
        for key in form_values:
            form_values[key] = request.POST.get(key, form_values[key]).strip()
        selected_host_ids, host_error = _validate_host_ids(
            request.POST.getlist('host_ids'),
        )
        if host_error:
            errors['host_ids'] = host_error

        title = form_values['title']
        slug = form_values['slug'] or slugify(title)
        description = form_values['description']
        # Resolve the timezone from the POST; fall back to the admin
        # default. Reject a tampered/unknown IANA name with a field
        # error (issue #665).
        posted_tz = form_values['timezone'] or default_tz
        if posted_tz and not is_valid_timezone(posted_tz):
            errors['timezone'] = 'Unknown timezone.'
            timezone_value = default_tz
        else:
            timezone_value = posted_tz
        platform = form_values['platform'] or 'zoom'
        status = form_values['status'] or 'draft'
        location = form_values['location']
        external_host = _coerce_external_host(form_values['external_host'])
        form_values['external_host'] = external_host
        custom_url = form_values['custom_url']

        if not title:
            errors['title'] = 'Title is required.'

        start_dt = None
        end_dt = None
        try:
            start_dt, end_dt = _parse_event_datetime(
                request.POST, timezone_value,
            )
        except (ValueError, AttributeError):
            if not form_values['event_date']:
                errors['event_date'] = 'Date is required (dd/mm/yyyy).'
            else:
                errors['event_date'] = 'Date must be in dd/mm/yyyy format.'
            if not form_values['event_time']:
                errors['event_time'] = 'Time is required (HH:MM, 24h).'

        required_level = _parse_required_level(
            form_values['required_level'] or '0', 0,
        )

        if title and slug and Event.objects.filter(slug=slug).exists():
            errors['slug'] = 'An event with this slug already exists.'

        if not errors:
            event = Event(
                title=title,
                slug=slug,
                description=description,
                kind='standard',
                platform=platform,
                start_datetime=start_dt,
                end_datetime=end_dt,
                timezone=timezone_value,
                location=location,
                tags=parse_comma_separated_tags(form_values['tags']),
                required_level=required_level,
                status=status,
                external_host=external_host,
                host_email=form_values['host_email'],
                origin='studio',
                published=True,
            )
            if platform == 'custom':
                event.zoom_join_url = custom_url
            event.save()
            _set_event_hosts(event, selected_host_ids)
            # Issue #895: enqueue an auto-banner render for the new event
            # (fire-and-forget; no-ops when banner-generator is disabled or
            # a cover image is supplied).
            enqueue_if_missing('event', event.pk)
            # Issue #861: send the host their calendar invite when the event
            # is created in a published (non-draft) state. Best-effort and
            # idempotent — never breaks the save.
            maybe_send_initial_host_invite(event)
            maybe_register_host_as_attendee(event)
            return redirect('studio_event_edit', event_id=event.pk)

    # Determine TZ value used for the shared picker partial.
    tz_value = form_values['timezone'] or default_tz
    context = {
        'event': None,
        'is_synced': False,
        'form_action': 'create',
        'errors': errors,
        'form_values': form_values,
        'event_date': form_values['event_date'],
        'event_time': form_values['event_time'],
        'duration_hours': form_values['duration_hours'] or '1',
        'external_host_choices': EXTERNAL_HOST_CHOICES,
        'timezone_value': tz_value,
        'timezone_label': get_timezone_label(tz_value) or tz_value,
        'timezone_options': build_timezone_options(),
        'tz_settings_link': _should_autodetect_tz(request.user),
        # Issue #855: on a fresh create with no user-chosen value, let the
        # browser zone win over the bare UTC fallback. A re-rendered POST
        # carries the admin's chosen value, so don't auto-detect then.
        'tz_autodetect': (
            _should_autodetect_tz(request.user)
            and request.method != 'POST'
        ),
    }
    _apply_host_context(context, selected_host_ids)
    return render(request, 'studio/events/form.html', context)


@staff_required
def event_edit(request, event_id):
    """Edit an existing event (read-only for synced content fields)."""
    event = get_object_or_404(Event, pk=event_id)
    synced = is_synced(event)
    default_tz = _default_timezone_for(request.user)

    if request.method == 'POST':
        # Issue #869: snapshot the persisted status before any in-memory
        # mutation so we can detect a transition INTO ``cancelled`` after
        # save and notify series subscribers (CANCEL their calendar entry).
        old_status = Event.objects.values_list('status', flat=True).get(pk=event.pk)
        selected_host_ids, host_error = _validate_host_ids(
            request.POST.getlist('host_ids'),
        )
        if host_error:
            context = _event_form_context(event, default_tz)
            context['form_action'] = 'edit'
            context['is_synced'] = synced
            context['github_edit_url'] = get_github_edit_url(event)
            context['notify_url'] = reverse(
                'studio_event_notify', kwargs={'event_id': event.pk},
            )
            context['announce_url'] = reverse(
                'studio_event_announce_slack',
                kwargs={'event_id': event.pk},
            )
            context['form_values'] = {}
            context['errors'] = {'host_ids': host_error}
            context['external_host_choices'] = EXTERNAL_HOST_CHOICES
            tz_value = context['timezone_value']
            context['timezone_label'] = get_timezone_label(tz_value) or tz_value
            context['timezone_options'] = build_timezone_options()
            context['tz_settings_link'] = _should_autodetect_tz(request.user)
            _apply_host_context(context, selected_host_ids)
            return render(request, 'studio/events/form.html', context)
        if synced:
            # Synced events: only allow operational fields
            event.status = request.POST.get('status', event.status)

            platform = request.POST.get('platform', event.platform)
            event.platform = platform
            if platform == 'custom':
                event.zoom_join_url = request.POST.get('custom_url', '').strip()
                event.zoom_meeting_id = ''

            # Issue #572: external_host is a content-level attribute that
            # staff need to edit even on synced rows (e.g. to mark an
            # incoming Maven cohort partner before the content repo learns
            # the new frontmatter key). Persist it unconditionally; empty
            # string is the community-hosted default.
            event.external_host = _coerce_external_host(
                request.POST.get('external_host', ''),
            )

            # Issue #680: post_event_summary is a host-authored recap body
            # for the follow-up email. Editable even on synced rows so
            # staff can write the recap without round-tripping through
            # the content repo.
            event.post_event_summary = request.POST.get(
                'post_event_summary', '',
            )

            # Issue #861: host mailbox for the host calendar invite is
            # operational metadata (not synced content), so it is editable
            # even on synced rows.
            event.host_email = request.POST.get('host_email', '').strip()

            event.save()
            _set_event_hosts(event, selected_host_ids)
            # Issue #857: publishing/editing a series occurrence into a
            # registrable state auto-enrolls existing series registrants.
            # Best-effort, idempotent, gated on ``is_upcoming``.
            if event.event_series_id:
                from events.services.series_registration import (
                    enroll_series_registrants_in_event,
                )
                enroll_series_registrants_in_event(event)
            # Issue #869: a status flip to cancelled removes the occurrence
            # from series subscribers' calendars via a METHOD:CANCEL .ics.
            _maybe_notify_series_cancellation(event, old_status)
            # Issue #861: a synced event flipping to a published state still
            # gets a one-time host invite (to the default mailbox or any
            # host_email). EmailLog-guarded; best-effort.
            maybe_send_initial_host_invite(event)
            maybe_register_host_as_attendee(event)
        else:
            # Issue #670: snapshot the persisted start time BEFORE we
            # mutate the in-memory event. The Studio form re-parses
            # date+time+duration into UTC on every save, so a no-op
            # re-save still hits ``event.save()`` — we use
            # field-level comparison after save to decide whether to
            # notify, not "did the form do a write?" semantics.
            old_event = Event.objects.get(pk=event.pk)
            old_start = old_event.start_datetime

            event.title = request.POST.get('title', '').strip()
            event.slug = request.POST.get('slug', '').strip() or slugify(event.title)
            event.description = request.POST.get('description', '')
            platform = request.POST.get('platform', 'zoom')
            event.platform = platform
            # Issue #665: resolve TZ from the POST; reject unknown names.
            posted_tz = (request.POST.get('timezone') or '').strip()
            if not posted_tz:
                posted_tz = event.timezone or default_tz
            if posted_tz and not is_valid_timezone(posted_tz):
                # Round-trip the form with a field-level error so the
                # admin can fix the chooser; don't silently coerce.
                context = _event_form_context(event, default_tz)
                context['form_action'] = 'edit'
                context['is_synced'] = synced
                context['github_edit_url'] = get_github_edit_url(event)
                context['notify_url'] = reverse(
                    'studio_event_notify', kwargs={'event_id': event.pk},
                )
                context['announce_url'] = reverse(
                    'studio_event_announce_slack',
                    kwargs={'event_id': event.pk},
                )
                context['form_values'] = {}
                context['errors'] = {'timezone': 'Unknown timezone.'}
                context['external_host_choices'] = EXTERNAL_HOST_CHOICES
                tz_value = context['timezone_value']
                context['timezone_label'] = (
                    get_timezone_label(tz_value) or tz_value
                )
                context['timezone_options'] = build_timezone_options()
                context['tz_settings_link'] = _should_autodetect_tz(request.user)
                _apply_host_context(context, selected_host_ids)
                return render(request, 'studio/events/form.html', context)
            start_dt, end_dt = _parse_event_datetime(request.POST, posted_tz)
            event.start_datetime = start_dt
            event.end_datetime = end_dt
            event.timezone = posted_tz
            event.location = request.POST.get('location', '')
            event.status = request.POST.get('status', 'draft')
            event.required_level = _parse_required_level(
                request.POST.get('required_level'), event.required_level,
            )
            event.tags = parse_comma_separated_tags(request.POST.get('tags', ''))
            # Issue #579: external_host is constrained to the canonical
            # partner list. Empty string means community-hosted; any
            # non-empty canonical value flips the event into external
            # mode. Tampered/unknown POST values coerce to ''.
            event.external_host = _coerce_external_host(
                request.POST.get('external_host', ''),
            )

            # When platform is custom, store the external join URL in the
            # existing join URL field and clear Zoom-specific metadata.
            if platform == 'custom':
                event.zoom_join_url = request.POST.get('custom_url', '').strip()
                event.zoom_meeting_id = ''

            # Issue #680: post_event_summary is a host-authored recap body
            # for the follow-up email. Markdown; blank is permitted (the
            # task substitutes a generic fallback string).
            event.post_event_summary = request.POST.get(
                'post_event_summary', '',
            )

            # Issue #861: host mailbox for the host calendar invite.
            event.host_email = request.POST.get('host_email', '').strip()

            event.save()
            _set_event_hosts(event, selected_host_ids)

            # Issue #857: publishing/editing a series occurrence into a
            # registrable state auto-enrolls existing series registrants.
            if event.event_series_id:
                from events.services.series_registration import (
                    enroll_series_registrants_in_event,
                )
                enroll_series_registrants_in_event(event)

            # Issue #670: detect a meaningful start-time change and
            # notify registered attendees. The trigger fires only when
            # both old and new starts are non-null, both are in the
            # future, and the delta is >= 60s. End-only edits and
            # past-event edits stay silent.
            _maybe_notify_reschedule(request, event, old_start)

            # Issue #861: send the host their initial calendar invite when
            # the event is (now) published and they have not been invited
            # yet — e.g. a draft being published, or a host_email added
            # after the first publish. EmailLog-guarded so a plain re-save
            # never re-sends. Best-effort; never breaks the save.
            maybe_send_initial_host_invite(event)
            maybe_register_host_as_attendee(event)

            # Issue #869: a status flip to cancelled removes the occurrence
            # from series subscribers' calendars via a METHOD:CANCEL .ics.
            _maybe_notify_series_cancellation(event, old_status)

            # Issue #895: a title change drifts the auto-banner title hash,
            # so re-enqueue the render. ``enqueue_if_missing`` short-circuits
            # when the hash is unchanged (re-save without a title edit) or a
            # cover image is set, and no-ops when banner-generator is disabled.
            enqueue_if_missing('event', event.pk)
        return redirect('studio_event_edit', event_id=event.pk)

    context = _event_form_context(event, default_tz)
    context['form_action'] = 'edit'
    context['is_synced'] = synced
    context['github_edit_url'] = get_github_edit_url(event)
    context['notify_url'] = reverse('studio_event_notify', kwargs={'event_id': event.pk})
    context['announce_url'] = reverse('studio_event_announce_slack', kwargs={'event_id': event.pk})
    # Issue #680: manual "Send follow-up now" button. The button is
    # enabled only when the event is completed AND has a recording URL
    # (gated in the template). The view itself accepts only completed
    # events with a recording — see ``event_send_followup`` below.
    context['send_followup_url'] = reverse(
        'studio_event_send_followup', kwargs={'event_id': event.pk},
    )
    # ``form_values`` and ``errors`` are only meaningful on the create flow
    # (issue #574). Provide empty defaults here so the shared template's
    # ``form_values.foo`` lookups resolve cleanly when rendering edit.
    context['form_values'] = {}
    context['errors'] = {}
    context['external_host_choices'] = EXTERNAL_HOST_CHOICES
    tz_value = context['timezone_value']
    context['timezone_label'] = get_timezone_label(tz_value) or tz_value
    context['timezone_options'] = build_timezone_options()
    context['tz_settings_link'] = _should_autodetect_tz(request.user)
    _apply_host_context(context)
    # Issue #701: surface registered attendees on the edit page so
    # operators can see and export the roster without dropping into
    # Django admin. ``-registered_at`` matches the model's default
    # ordering and the spec.
    registrations = (
        EventRegistration.objects
        .filter(event=event)
        .select_related('user', 'user__tier')
        .order_by('-registered_at')
    )
    context['registrations'] = registrations
    context['registration_count'] = registrations.count()
    # Issue #936: per-event attendance rollup. ``joined_count`` is the
    # number of registrations whose ``joined_at`` is set (first
    # live-window join click). Rendered as ``joined_count /
    # registration_count`` in the State panel.
    context['joined_count'] = registrations.filter(
        joined_at__isnull=False,
    ).count()
    context['registrations_csv_url'] = reverse(
        'studio_event_registrations_csv', kwargs={'event_id': event.pk},
    )
    # Issue #679: per-event Feedback panel on the Studio edit page.
    # Mirrors the shape of the Registered attendees panel. The aggregate
    # excludes rating-null rows; the comment count counts non-empty
    # comments only.
    feedback_entries = (
        EventFeedback.objects
        .filter(event=event)
        .select_related('user')
        .order_by('-created_at')
    )
    feedback_avg = feedback_entries.aggregate(avg=Avg('rating'))['avg']
    if feedback_avg is not None:
        feedback_avg = round(feedback_avg, 1)
    context['feedback_entries'] = feedback_entries
    context['feedback_count'] = (
        feedback_entries.filter(rating__isnull=False).count()
    )
    context['feedback_avg'] = feedback_avg
    context['feedback_comment_count'] = (
        feedback_entries.exclude(comment='').count()
    )

    # Issues #895/#931: banner / social-image panel (parity with
    # articles/etc). Only meaningful on the edit flow, where ``event`` exists.
    context.update(banner_panel_context(
        content_type='event',
        record=event,
        regenerate_url_name='studio_event_regenerate_banner',
        upload_url_name='studio_event_upload_banner',
        remove_url_name='studio_event_remove_banner',
        url_kwarg='event_id',
    ))
    # Issue #995: JSON status endpoint that the in-place banner loader polls.
    # Only events expose it today, so the shared partial progressively
    # enhances only when ``banner_status_url`` is present in context.
    context['banner_status_url'] = reverse(
        'studio_event_banner_status', kwargs={'event_id': event.pk},
    )
    return render(request, 'studio/events/form.html', context)


@staff_required
def event_registrations_csv(request, event_id):
    """Export the roster for ``event_id`` as CSV.

    Issue #701. Mirrors the shape of ``studio.views.users.user_export_csv``:
    ``HttpResponse(content_type='text/csv')`` + ``csv.writer`` + an attachment
    filename with a UTC timestamp. Session-gated via ``@staff_required``; no
    token mechanism. Columns are locked at ``email, name, registered_at, tier``
    (in that order). The ``registered_at`` cell is ISO 8601 UTC. ``tier``
    defaults to ``Free`` when the user row has no tier FK set.
    """
    event = get_object_or_404(Event, pk=event_id)

    registrations = (
        EventRegistration.objects
        .filter(event=event)
        .select_related('user', 'user__tier')
        .order_by('-registered_at')
    )

    timestamp = (
        djtimezone.now()
        .astimezone(_datetime.timezone.utc)
        .strftime('%Y%m%d-%H%M%S')
    )
    filename = f'event-{event.slug}-registrations-{timestamp}.csv'

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    # Issue #936: ``joined_at`` is appended after the locked
    # ``email, name, registered_at, tier`` columns (those keep their
    # order). Cell is ISO 8601 UTC when set, empty string when null.
    writer.writerow(['email', 'name', 'registered_at', 'tier', 'joined_at'])
    for reg in registrations:
        user = reg.user
        name = user.get_full_name() or ''
        tier_name = user.tier.name if user.tier_id else 'Free'
        writer.writerow([
            user.email,
            name,
            reg.registered_at.isoformat() if reg.registered_at else '',
            tier_name,
            (
                reg.joined_at
                .astimezone(_datetime.timezone.utc)
                .isoformat()
                if reg.joined_at else ''
            ),
        ])

    return response


@staff_required
@require_POST
def event_send_followup(request, event_id):
    """Issue #680: manual "Send follow-up now" trigger.

    The Studio button on completed events fires this endpoint. The
    handler is the documented escape hatch for cases where the cron
    skipped the follow-up because the recording URL was empty at the
    moment the event flipped to ``completed`` — staff populate the
    URL later and press the button to fan out.

    Gates (must hold; otherwise the request flashes an error and
    redirects back to the edit page without enqueuing):

    - ``event.is_past`` (time-derived; issue #713).
    - ``event.recording_s3_url`` or ``event.recording_url`` is set.

    Idempotency:

    - The per-user task already dedups via
      ``EventReminderLog.get_or_create(event, user, interval='followup')``.
      A second press over the same audience is a no-op — every user
      already has a log row.
    - We also surface a different flash message ("already sent") when
      the cron has already enqueued a fan-out for this event, so the
      operator gets immediate feedback instead of waiting for the
      worker to no-op.
    """
    event = get_object_or_404(Event, pk=event_id)

    # Issue #713: gate on the time-derived ``is_past`` so a stale
    # ``status='upcoming'`` row whose end has passed can still trigger
    # the follow-up via the Studio escape hatch without waiting for the
    # daily cron.
    if not event.is_past:
        messages.error(
            request,
            'Follow-up emails can only be sent after the event has ended.',
        )
        return redirect('studio_event_edit', event_id=event.pk)

    recording_url = event.recording_s3_url or event.recording_url
    if not recording_url:
        messages.error(
            request,
            'Set a recording URL before sending the follow-up email.',
        )
        return redirect('studio_event_edit', event_id=event.pk)

    from notifications.models import EventReminderLog

    if EventReminderLog.objects.filter(
        event=event, interval='followup',
    ).exists():
        messages.info(
            request,
            'A post-event follow-up has already been sent for this event.',
        )
        return redirect('studio_event_edit', event_id=event.pk)

    enqueue_post_event_followup(event.pk)

    registration_count = EventRegistration.objects.filter(event=event).count()
    label = 'attendee' if registration_count == 1 else 'attendees'
    messages.success(
        request,
        f'Follow-up email queued for {registration_count} {label}.',
    )
    return redirect('studio_event_edit', event_id=event.pk)


@staff_required
@require_POST
def event_create_zoom(request, event_id):
    """Create a Zoom meeting for an existing event."""
    event = get_object_or_404(Event, pk=event_id)

    if event.zoom_meeting_id:
        return JsonResponse({'error': 'Event already has a Zoom meeting'}, status=400)

    try:
        result = create_meeting(event)
        event.zoom_meeting_id = result['meeting_id']
        event.zoom_join_url = result['join_url']
        event.save(update_fields=['zoom_meeting_id', 'zoom_join_url'])
        return JsonResponse({
            'meeting_id': result['meeting_id'],
            'join_url': result['join_url'],
        })
    except Exception as e:
        logger.exception('Failed to create Zoom meeting for event %s', event.pk)
        return JsonResponse({'error': str(e)}, status=500)
