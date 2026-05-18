"""Studio views for event CRUD."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.services.timezones import (
    build_timezone_options,
    get_timezone_label,
    is_valid_timezone,
)
from events.models import Event
from events.models.event import EXTERNAL_HOST_CHOICES
from integrations.services.zoom import create_meeting
from studio.decorators import staff_required
from studio.utils import get_github_edit_url, is_synced
from studio.views.form_helpers import parse_comma_separated_tags

logger = logging.getLogger(__name__)

_VALID_EXTERNAL_HOSTS = {value for value, _ in EXTERNAL_HOST_CHOICES}


def _coerce_external_host(raw):
    """Issue #579. POSTs from the Studio dropdown are constrained, but
    a tampered or stale form post could land here with an arbitrary
    string. Coerce anything outside the canonical list to '' so we
    never persist a non-canonical value.
    """
    value = (raw or '').strip()
    return value if value in _VALID_EXTERNAL_HOSTS else ''


def _default_timezone_for(user):
    """Return the admin's preferred TZ when valid, else ``settings.TIME_ZONE``.

    Issue #665. New Studio forms default the timezone chooser to the
    logged-in admin's profile timezone; if it isn't set (or is somehow
    invalid), fall back to the project's ``TIME_ZONE`` (UTC). Never
    return the historical 'Europe/Berlin' fallback — the issue
    explicitly removes that hardcode.
    """
    candidate = getattr(user, 'preferred_timezone', '') or ''
    if candidate and is_valid_timezone(candidate):
        return candidate
    return settings.TIME_ZONE or 'UTC'


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


@staff_required
def event_list(request):
    """List all events with status filter."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('q', '')

    events = Event.objects.all()
    if status_filter:
        events = events.filter(status=status_filter)
    if search:
        events = events.filter(title__icontains=search)

    return render(request, 'studio/events/list.html', {
        'events': events,
        'status_filter': status_filter,
        'search': search,
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
        'max_participants': '',
        'external_host': '',
        'custom_url': '',
    }

    if request.method == 'POST':
        for key in form_values:
            form_values[key] = request.POST.get(key, form_values[key]).strip()

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

        try:
            required_level = int(form_values['required_level'] or '0')
        except ValueError:
            required_level = 0

        max_p_raw = form_values['max_participants']
        try:
            max_participants = int(max_p_raw) if max_p_raw else None
        except ValueError:
            max_participants = None

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
                max_participants=max_participants,
                status=status,
                external_host=external_host,
                origin='studio',
                published=True,
            )
            if platform == 'custom':
                event.zoom_join_url = custom_url
            event.save()
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
    }
    return render(request, 'studio/events/form.html', context)


@staff_required
def event_edit(request, event_id):
    """Edit an existing event (read-only for synced content fields)."""
    event = get_object_or_404(Event, pk=event_id)
    synced = is_synced(event)
    default_tz = _default_timezone_for(request.user)

    if request.method == 'POST':
        if synced:
            # Synced events: only allow operational fields
            max_p = request.POST.get('max_participants', '')
            event.max_participants = int(max_p) if max_p else None
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

            event.save()
        else:
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
                return render(request, 'studio/events/form.html', context)
            start_dt, end_dt = _parse_event_datetime(request.POST, posted_tz)
            event.start_datetime = start_dt
            event.end_datetime = end_dt
            event.timezone = posted_tz
            event.location = request.POST.get('location', '')
            max_p = request.POST.get('max_participants', '')
            event.max_participants = int(max_p) if max_p else None
            event.status = request.POST.get('status', 'draft')
            event.required_level = int(request.POST.get('required_level', 0))
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

            event.save()
        return redirect('studio_event_edit', event_id=event.pk)

    context = _event_form_context(event, default_tz)
    context['form_action'] = 'edit'
    context['is_synced'] = synced
    context['github_edit_url'] = get_github_edit_url(event)
    context['notify_url'] = reverse('studio_event_notify', kwargs={'event_id': event.pk})
    context['announce_url'] = reverse('studio_event_announce_slack', kwargs={'event_id': event.pk})
    # ``form_values`` and ``errors`` are only meaningful on the create flow
    # (issue #574). Provide empty defaults here so the shared template's
    # ``form_values.foo`` lookups resolve cleanly when rendering edit.
    context['form_values'] = {}
    context['errors'] = {}
    context['external_host_choices'] = EXTERNAL_HOST_CHOICES
    tz_value = context['timezone_value']
    context['timezone_label'] = get_timezone_label(tz_value) or tz_value
    context['timezone_options'] = build_timezone_options()
    return render(request, 'studio/events/form.html', context)


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
