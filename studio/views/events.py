"""Studio views for event CRUD."""

import logging
from datetime import datetime, timedelta

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from events.models import Event
from integrations.services.zoom import create_meeting
from studio.decorators import staff_required
from studio.utils import get_github_edit_url, is_synced

logger = logging.getLogger(__name__)


def _parse_event_datetime(post_data):
    """Parse separate date, time, and duration fields into start/end datetimes.

    Expects POST fields:
    - event_date: dd/mm/yyyy
    - event_time: HH:MM (24-hour)
    - duration_hours: float (optional, default 1)

    Returns (start_datetime, end_datetime) as naive datetime objects.
    """
    date_str = post_data.get('event_date', '').strip()
    time_str = post_data.get('event_time', '').strip()
    duration_str = post_data.get('duration_hours', '').strip()

    # Parse date (dd/mm/yyyy)
    day, month, year = date_str.split('/')
    parsed_date = datetime(int(year), int(month), int(day))

    # Parse time (HH:MM)
    hour, minute = time_str.split(':')
    start_dt = parsed_date.replace(hour=int(hour), minute=int(minute))

    # Parse duration (default 1 hour)
    duration = float(duration_str) if duration_str else 1.0
    end_dt = start_dt + timedelta(hours=duration)

    return start_dt, end_dt


def _event_form_context(event):
    """Build template context for the event form with pre-populated date/time/duration."""
    context = {
        'event': event,
        'event_date': '',
        'event_time': '',
        'duration_hours': '1',
    }
    if event and event.start_datetime:
        context['event_date'] = event.start_datetime.strftime('%d/%m/%Y')
        context['event_time'] = event.start_datetime.strftime('%H:%M')
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
def event_edit(request, event_id):
    """Edit an existing event (read-only for synced content fields)."""
    event = get_object_or_404(Event, pk=event_id)
    synced = is_synced(event)

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

            event.save()
        else:
            event.title = request.POST.get('title', '').strip()
            event.slug = request.POST.get('slug', '').strip() or slugify(event.title)
            event.description = request.POST.get('description', '')
            event.event_type = request.POST.get('event_type', 'live')
            platform = request.POST.get('platform', 'zoom')
            event.platform = platform
            start_dt, end_dt = _parse_event_datetime(request.POST)
            event.start_datetime = start_dt
            event.end_datetime = end_dt
            event.timezone = request.POST.get('timezone', 'Europe/Berlin')
            event.location = request.POST.get('location', '')
            max_p = request.POST.get('max_participants', '')
            event.max_participants = int(max_p) if max_p else None
            event.status = request.POST.get('status', 'draft')
            event.required_level = int(request.POST.get('required_level', 0))
            tags_raw = request.POST.get('tags', '')
            event.tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []

            # When platform is 'custom', store custom_url in zoom_join_url
            # and clear zoom_meeting_id
            if platform == 'custom':
                event.zoom_join_url = request.POST.get('custom_url', '').strip()
                event.zoom_meeting_id = ''

            event.save()
        return redirect('studio_event_edit', event_id=event.pk)

    context = _event_form_context(event)
    context['form_action'] = 'edit'
    context['is_synced'] = synced
    context['github_edit_url'] = get_github_edit_url(event)
    context['notify_url'] = reverse('studio_event_notify', kwargs={'event_id': event.pk})
    context['announce_url'] = reverse('studio_event_announce_slack', kwargs={'event_id': event.pk})
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
