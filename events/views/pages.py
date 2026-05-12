import calendar as cal_module
from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from content.access import (
    build_gating_context,
    can_access,
    get_required_tier_name,
)
from events.models import Event, EventGroup, EventJoinClick, EventRegistration
from events.services.calendar_invite import generate_ics
from events.services.cancel_token import (
    CancelTokenExpired,
    CancelTokenInvalid,
    decode_cancel_token,
)
from events.services.display_time import (
    build_event_time_display,
    should_display_event_location,
)

VALID_EVENTS_FILTERS = {'all', 'upcoming', 'past'}


def _get_selected_tags(request):
    """Extract selected tags from query params. Supports ?tag=X&tag=Y."""
    return [t.strip() for t in request.GET.getlist('tag') if t.strip()]


def _filter_by_tags(queryset, selected_tags):
    """Filter a queryset by multiple tags with AND logic.

    Returns a filtered queryset containing only items that have ALL selected tags.
    """
    if not selected_tags:
        return queryset
    matching_ids = []
    for obj in queryset:
        obj_tags = set(obj.tags or [])
        if all(tag in obj_tags for tag in selected_tags):
            matching_ids.append(obj.pk)
    return queryset.filter(pk__in=matching_ids)


def events_calendar(request, year=None, month=None):
    """Monthly calendar grid view for events."""
    today = date.today()
    year = year or today.year
    month = month or today.month

    # Clamp month to valid range
    if month < 1 or month > 12:
        from django.http import Http404
        raise Http404

    # Build calendar grid (Monday start)
    cal = cal_module.Calendar(firstweekday=0)
    month_days = cal.monthdayscalendar(year, month)

    # Get events for this month (non-draft)
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1)
    else:
        month_end = date(year, month + 1, 1)

    events = Event.objects.filter(
        start_datetime__date__gte=month_start,
        start_datetime__date__lt=month_end,
    ).exclude(status='draft').order_by('start_datetime')

    # Map events to days
    events_by_day = {}
    for event in events:
        day = event.start_datetime.date().day
        events_by_day.setdefault(day, []).append(event)

    # Build grid with events
    weeks = []
    for week in month_days:
        week_data = []
        for day in week:
            if day == 0:
                week_data.append({'day': 0, 'events': [], 'is_today': False})
            else:
                week_data.append({
                    'day': day,
                    'events': events_by_day.get(day, []),
                    'is_today': (
                        day == today.day
                        and month == today.month
                        and year == today.year
                    ),
                })
        weeks.append(week_data)

    # Navigation
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    month_name = cal_module.month_name[month]

    # Build agenda list for mobile: only days with events, sorted
    agenda_days = []
    for day_num in sorted(events_by_day.keys()):
        agenda_days.append({
            'day': day_num,
            'date': date(year, month, day_num),
            'events': events_by_day[day_num],
        })

    context = {
        'weeks': weeks,
        'month_name': month_name,
        'year': year,
        'month': month,
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
        'today': today,
        'events_list': events,
        'agenda_days': agenda_days,
    }
    return render(request, 'events/events_calendar.html', context)


def events_list(request):
    """Events list page with Upcoming and Past sections.

    Accepts ``?filter=`` with values ``all`` (default), ``upcoming``, or
    ``past``. The past surface filters to completed events that have a
    recording URL, supports tag filtering via ``?tag=``, and paginates at
    20 per page.
    """
    # Exclude draft events from public listing
    events = Event.objects.exclude(status='draft')

    filter_mode = request.GET.get('filter', 'all').strip().lower()
    if filter_mode not in VALID_EVENTS_FILTERS:
        filter_mode = 'all'

    selected_tags = _get_selected_tags(request)

    upcoming_events = events.filter(status='upcoming').order_by('start_datetime')

    # For the "past" surface we only show completed events with a recording
    # (and honor the ``published`` flag). The default "all" view also
    # includes cancelled events and does not require a recording.
    past_with_recording_qs = events.filter(
        status='completed',
        published=True,
    ).exclude(
        recording_url='',
    ).exclude(
        recording_url__isnull=True,
    ).order_by('-start_datetime')

    past_all_qs = events.filter(
        status__in=['completed', 'cancelled'],
    ).order_by('-start_datetime')

    # Collect all tags from past-with-recording events for the tag filter UI
    all_past_tags = set()
    for event in past_with_recording_qs:
        if event.tags:
            all_past_tags.update(event.tags)
    all_past_tags = sorted(all_past_tags)

    # Apply tag filtering only on past-with-recording list.
    past_filtered = _filter_by_tags(past_with_recording_qs, selected_tags)

    # Paginate the past-with-recording list (20 per page) when filter=past.
    page_obj = None
    is_paginated = False
    if filter_mode == 'past':
        paginator = Paginator(past_filtered, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        is_paginated = page_obj.has_other_pages()
        past_events = page_obj
    elif filter_mode == 'all':
        past_events = past_all_qs
    else:
        # upcoming: we don't render past section
        past_events = past_all_qs.none()

    # Annotate events with registration info for authenticated users
    user = request.user
    registered_event_ids = set()
    if user.is_authenticated:
        registered_event_ids = set(
            EventRegistration.objects.filter(
                user=user,
            ).values_list('event_id', flat=True)
        )

    context = {
        'filter_mode': filter_mode,
        'show_upcoming': filter_mode in ('all', 'upcoming'),
        'show_past': filter_mode in ('all', 'past'),
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'page_obj': page_obj,
        'is_paginated': is_paginated,
        'all_past_tags': all_past_tags,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'registered_event_ids': registered_event_ids,
        'base_path': '/events',
    }
    return render(request, 'events/events_list.html', context)


@login_required
def event_join_redirect(request, slug):
    """Redirect registered users to the event join URL, tracking each click."""
    event = get_object_or_404(Event, slug=slug)

    # Draft events return 404 for non-staff users
    if event.status == 'draft' and not request.user.is_staff:
        raise Http404

    # User must be registered for the event
    is_registered = EventRegistration.objects.filter(
        event=event, user=request.user,
    ).exists()
    if not is_registered:
        return redirect('event_detail', slug=event.slug)

    # Past events show an unavailable page. The "Back to event" link there
    # already surfaces the inline recording on /events/<slug>.
    if event.status in ('completed', 'cancelled'):
        return render(request, 'events/join_unavailable.html', {
            'event': event,
            'reason': 'past',
        })

    # No join URL yet
    if not event.zoom_join_url:
        return render(request, 'events/join_unavailable.html', {
            'event': event,
            'reason': 'no_url',
        })

    # Record click and redirect
    EventJoinClick.objects.create(event=event, user=request.user)
    return redirect(event.zoom_join_url)


@ensure_csrf_cookie
def event_detail(request, slug):
    """Event detail page - always visible to everyone.

    Issue #513: this view sets the ``csrftoken`` cookie for fresh
    anonymous visitors so the email-only registration form rendered in
    ``templates/events/event_detail.html`` can read it via
    ``getCookie('csrftoken')`` and POST successfully. Without this
    decorator the first POST from an anonymous session would return 403.
    """
    event = get_object_or_404(Event, slug=slug)
    # Draft events should not be publicly visible
    if event.status == 'draft' and not request.user.is_staff:
        from django.http import Http404
        raise Http404

    user = request.user

    # Issue #572: external events bypass the in-app registration flow
    # entirely. The detail page renders an outbound Join card instead
    # of the registration card, ignoring ``required_level`` for access
    # control (the third-party platform handles access on their side).
    is_external = event.is_external

    # Check access for registration gating
    has_access = can_access(user, event)

    # Check if user is registered
    is_registered = False
    if user.is_authenticated:
        is_registered = EventRegistration.objects.filter(
            event=event, user=user,
        ).exists()

    # Build gating context for the upcoming-event registration CTA. The
    # event detail page is announcement-only (issue #426) — recording
    # playback and its paywall live on the linked Workshop, so we always
    # use the 'event' gating copy here.
    gating = build_gating_context(user, event, 'event')

    # Determine if we should show the join link.
    show_join_link = (
        is_registered
        and event.can_show_zoom_link()
        and event.status == 'upcoming'
    )

    # Determine required tier name for CTA
    required_tier_name = get_required_tier_name(event.required_level)

    # Issue #513: anonymous email-only registration flow. After a
    # successful POST the JS reloads the page with ``?registered=<email>``
    # so the template can render a confirmation block instead of the
    # signup form. We do NOT trust this query param to mean a row was
    # actually created — only that the JS thinks the registration
    # succeeded. The block is confirmation copy, not access control.
    #
    # Issue #572: external events never offer in-app registration so the
    # ``?registered=<email>`` confirmation block is suppressed for them
    # — there's nothing to confirm.
    anon_registered_email = ''
    anon_registered_account_created = False
    if (
        not user.is_authenticated
        and event.status == 'upcoming'
        and not is_external
    ):
        raw_email = (request.GET.get('registered') or '').strip()
        # Only render the confirmation when the email looks like an
        # email; ignores junk like ``?registered=1``.
        if raw_email and '@' in raw_email and '.' in raw_email.split('@', 1)[-1]:
            anon_registered_email = raw_email
            anon_registered_account_created = (
                request.GET.get('account_created') == '1'
            )

    context = {
        'event': event,
        'event_time_display': build_event_time_display(event, user),
        'has_access': has_access,
        'is_registered': is_registered,
        'show_event_location': should_display_event_location(event),
        'show_zoom_link': show_join_link,
        'required_tier_name': required_tier_name,
        # Issue #484: ordered list of speakers/instructors for the
        # detail-page header. Pre-computed so the template can branch on
        # presence without re-querying the through-model.
        'event_instructors': event.ordered_instructors,
        # Issue #484: surface a download URL for the .ics file so
        # registered users can re-add the event to their calendar
        # independent of email delivery.
        'event_ics_url': f'/events/{event.slug}/calendar.ics',
        # Issue #513: post-registration confirmation copy for the
        # anonymous email-only flow.
        'anon_registered_email': anon_registered_email,
        'anon_registered_account_created': anon_registered_account_created,
        # Issue #572: pre-computed branch flag the template uses to swap
        # the registration card for the external Join card.
        'is_external_event': is_external,
    }
    context.update(gating)
    return render(request, 'events/event_detail.html', context)


def event_group_public(request, slug):
    """Public series index page.

    Issue #564. Shows the group's metadata and every published member
    event. Anonymous visitors see the page; per-event tier gating
    happens on the individual event detail / registration as today.

    Draft events are hidden from anonymous and non-staff visitors. Staff
    see every member event so the page is useful for previewing a
    series before publishing.
    """
    group = get_object_or_404(EventGroup, slug=slug)
    events = group.events.all().order_by('series_position', 'start_datetime')
    if not request.user.is_staff:
        events = events.exclude(status='draft')

    return render(request, 'events/event_group.html', {
        'group': group,
        'events': events,
    })


def _resolve_cancel_state(slug, token):
    """Decode the token and load the registration row.

    Returns a tuple ``(state, context)`` where ``state`` is one of
    ``"confirm"``, ``"invalid"``, ``"expired"``, ``"already_cancelled"``,
    or ``"event_finished"`` and ``context`` is a dict of template
    variables. The view layer wraps the result in either the confirm or
    the result template.

    The same logic backs both the GET confirmation page and the POST
    action so the user sees consistent messaging across the two-step
    flow.
    """
    event_url = f'/events/{slug}'

    if not token:
        return 'invalid', {
            'message': 'This cancellation link is incomplete.',
            'event_url': event_url,
        }

    try:
        payload = decode_cancel_token(token)
    except CancelTokenExpired:
        return 'expired', {
            'message': (
                'This cancellation link has expired. Open the event '
                'page to manage your registration.'
            ),
            'event_url': event_url,
        }
    except CancelTokenInvalid:
        return 'invalid', {
            'message': 'This cancellation link is invalid.',
            'event_url': event_url,
        }

    try:
        event = Event.objects.get(slug=slug)
    except Event.DoesNotExist:
        return 'invalid', {
            'message': 'This cancellation link is invalid.',
            'event_url': event_url,
        }

    if payload['event_id'] != event.pk:
        return 'invalid', {
            'message': 'This cancellation link is invalid.',
            'event_url': event_url,
        }

    registration = EventRegistration.objects.filter(
        pk=payload['registration_id'],
        event_id=payload['event_id'],
        user_id=payload['user_id'],
    ).first()

    if registration is None:
        return 'already_cancelled', {
            'event': event,
            'event_url': event_url,
            'message': "You're not registered for this event. No action needed.",
        }

    if event.status != 'upcoming':
        return 'event_finished', {
            'event': event,
            'event_url': event_url,
            'registration': registration,
            'message': (
                'This event has already started or finished. '
                'Cancellation is no longer available.'
            ),
        }

    return 'confirm', {
        'event': event,
        'event_url': event_url,
        'registration': registration,
        'event_datetime': event.formatted_start(),
    }


def cancel_registration_page(request, slug):
    """Render the cancel-registration confirmation page (GET).

    The signed token in the URL is the authorization, so the user does
    NOT need to be signed in. A two-step GET-then-POST flow defeats
    email-prefetch auto-cancellation: the GET is read-only and only
    renders the form; the POST (issued by the visible button) performs
    the actual cancellation.
    """
    token = request.GET.get('token', '')
    state, ctx = _resolve_cancel_state(slug, token)
    ctx['state'] = state
    if state == 'confirm':
        ctx['action_url'] = (
            f'/api/events/{slug}/cancel-registration?token={token}'
        )
    return render(request, 'events/cancel_registration_confirm.html', ctx)


def event_calendar_ics(request, slug):
    """Return the ``.ics`` calendar invite for an event as a downloadable file.

    Issue #484: registered users get a calendar invite by email, but email
    delivery can fail or be filtered. Exposing a stable download URL on the
    event detail page lets the user add the event to their calendar without
    relying on email at all.

    The endpoint is public for non-draft events. The .ics file contains the
    title, start/end time, description, and the join URL — all the same
    information that already shows on the public detail page.
    """
    event = get_object_or_404(Event, slug=slug)
    if event.status == 'draft' and not request.user.is_staff:
        raise Http404

    ics_bytes = generate_ics(event)
    response = HttpResponse(ics_bytes, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="{event.slug}.ics"'
    )
    return response
