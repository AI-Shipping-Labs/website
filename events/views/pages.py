from django.shortcuts import render, get_object_or_404

from content.access import (
    build_gating_context, can_access, get_required_tier_name,
)
from events.models import Event, EventRegistration


def events_list(request):
    """Events calendar page with Upcoming and Past sections."""
    # Exclude draft events from public listing
    events = Event.objects.exclude(status='draft')

    upcoming_events = events.filter(
        status__in=['upcoming', 'live'],
    ).order_by('start_datetime')

    past_events = events.filter(
        status__in=['completed', 'cancelled'],
    ).order_by('-start_datetime')

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
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'registered_event_ids': registered_event_ids,
    }
    return render(request, 'events/events_list.html', context)


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

    # Build gating context for unauthorized users
    gating = build_gating_context(user, event, 'event')

    # Determine if we should show the Zoom join link
    show_zoom_link = (
        is_registered
        and event.can_show_zoom_link()
        and event.status in ('upcoming', 'live')
    )

    # Determine required tier name for CTA
    required_tier_name = get_required_tier_name(event.required_level)

    context = {
        'event': event,
        'has_access': has_access,
        'is_registered': is_registered,
        'show_zoom_link': show_zoom_link,
        'required_tier_name': required_tier_name,
    }
    context.update(gating)
    return render(request, 'events/event_detail.html', context)
