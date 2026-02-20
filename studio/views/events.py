"""Studio views for event CRUD."""

from django.shortcuts import render, redirect, get_object_or_404
from django.utils.text import slugify

from events.models import Event
from studio.decorators import staff_required


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
    """Create a new event."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        slug = request.POST.get('slug', '').strip() or slugify(title)
        description = request.POST.get('description', '')
        event_type = request.POST.get('event_type', 'live')
        start_datetime = request.POST.get('start_datetime', '')
        end_datetime = request.POST.get('end_datetime', '') or None
        tz = request.POST.get('timezone', 'Europe/Berlin')
        location = request.POST.get('location', '')
        max_participants = request.POST.get('max_participants', '') or None
        status = request.POST.get('status', 'draft')
        required_level = int(request.POST.get('required_level', 0))
        tags_raw = request.POST.get('tags', '')
        tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []

        if max_participants:
            max_participants = int(max_participants)

        event = Event.objects.create(
            title=title,
            slug=slug,
            description=description,
            event_type=event_type,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            timezone=tz,
            location=location,
            max_participants=max_participants,
            status=status,
            required_level=required_level,
            tags=tags,
        )
        return redirect('studio_event_edit', event_id=event.pk)

    return render(request, 'studio/events/form.html', {
        'event': None,
        'form_action': 'create',
    })


@staff_required
def event_edit(request, event_id):
    """Edit an existing event."""
    event = get_object_or_404(Event, pk=event_id)

    if request.method == 'POST':
        event.title = request.POST.get('title', '').strip()
        event.slug = request.POST.get('slug', '').strip() or slugify(event.title)
        event.description = request.POST.get('description', '')
        event.event_type = request.POST.get('event_type', 'live')
        event.start_datetime = request.POST.get('start_datetime', event.start_datetime)
        end_dt = request.POST.get('end_datetime', '')
        event.end_datetime = end_dt if end_dt else None
        event.timezone = request.POST.get('timezone', 'Europe/Berlin')
        event.location = request.POST.get('location', '')
        max_p = request.POST.get('max_participants', '')
        event.max_participants = int(max_p) if max_p else None
        event.status = request.POST.get('status', 'draft')
        event.required_level = int(request.POST.get('required_level', 0))
        tags_raw = request.POST.get('tags', '')
        event.tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []
        event.save()
        return redirect('studio_event_edit', event_id=event.pk)

    return render(request, 'studio/events/form.html', {
        'event': event,
        'form_action': 'edit',
    })
