"""Studio views for event CRUD."""

import csv
import datetime as _datetime
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.db.models import Avg
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone as djtimezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.services.timezones import (
    build_timezone_options,
    get_timezone_label,
    is_valid_timezone,
)
from events.models import Event, EventFeedback, EventRegistration
from events.models.event import EXTERNAL_HOST_CHOICES
from events.tasks.notify_reschedule import enqueue_reschedule_notice
from events.tasks.send_post_event_followup import enqueue_post_event_followup
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

    count = EventRegistration.objects.filter(event=event).count()
    if count > 0:
        enqueue_reschedule_notice(event.pk, old_start.isoformat())

    label = 'attendee' if count == 1 else 'attendees'
    messages.success(
        request,
        f'Rescheduling notice sent to {count} registered {label}.',
    )


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

            # Issue #680: post_event_summary is a host-authored recap body
            # for the follow-up email. Editable even on synced rows so
            # staff can write the recap without round-tripping through
            # the content repo.
            event.post_event_summary = request.POST.get(
                'post_event_summary', '',
            )

            event.save()
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

            # Issue #680: post_event_summary is a host-authored recap body
            # for the follow-up email. Markdown; blank is permitted (the
            # task substitutes a generic fallback string).
            event.post_event_summary = request.POST.get(
                'post_event_summary', '',
            )

            event.save()

            # Issue #670: detect a meaningful start-time change and
            # notify registered attendees. The trigger fires only when
            # both old and new starts are non-null, both are in the
            # future, and the delta is >= 60s. End-only edits and
            # past-event edits stay silent.
            _maybe_notify_reschedule(request, event, old_start)
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
    writer.writerow(['email', 'name', 'registered_at', 'tier'])
    for reg in registrations:
        user = reg.user
        name = user.get_full_name() or ''
        tier_name = user.tier.name if user.tier_id else 'Free'
        writer.writerow([
            user.email,
            name,
            reg.registered_at.isoformat() if reg.registered_at else '',
            tier_name,
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

    - ``event.status == 'completed'``.
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

    if event.status != 'completed':
        messages.error(
            request,
            'Follow-up emails can only be sent for completed events.',
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
