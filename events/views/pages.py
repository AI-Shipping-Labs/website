import calendar as cal_module
from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from content.access import (
    build_gating_context,
    can_access,
    get_required_tier_name,
)
from events.models import Event, EventJoinClick, EventRegistration
from events.services.calendar_invite import generate_ics
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


def event_detail(request, slug):
    """Event detail page - always visible to everyone."""
    event = get_object_or_404(Event, slug=slug)
    # Draft events should not be publicly visible
    if event.status == 'draft' and not request.user.is_staff:
        from django.http import Http404
        raise Http404

    user = request.user

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
    }
    context.update(gating)
    return render(request, 'events/event_detail.html', context)


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
