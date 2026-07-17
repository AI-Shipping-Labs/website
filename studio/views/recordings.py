"""Studio views for recording management (now using Event model)."""

from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from events.models import Event
from studio.decorators import staff_required
from studio.utils import get_github_edit_url, is_synced, studio_pagination_context
from studio.views.notifications import notification_action_context


@staff_required
def recording_list(request):
    """List all events that have recordings."""
    search = request.GET.get('q', '')

    recordings = Event.objects.filter(
        recording_url__isnull=False,
    ).exclude(recording_url='')
    if search:
        recordings = recordings.filter(title__icontains=search)
    pager = studio_pagination_context(request, recordings)

    return render(request, 'studio/recordings/list.html', {
        'recordings': pager['page'].object_list,
        'search': search,
        **pager,
    })


@staff_required
def recording_edit(request, recording_id):
    """Edit recording fields on an event (read-only for synced items)."""
    recording = get_object_or_404(Event, pk=recording_id)
    synced = is_synced(recording)

    if request.method == 'POST':
        if synced:
            return HttpResponseForbidden(
                'This content is managed in GitHub. Edit it there.'
            )

        recording.title = request.POST.get('title', '').strip()
        recording.slug = request.POST.get('slug', '').strip() or slugify(recording.title)
        recording.description = request.POST.get('description', '')
        recording.recording_url = request.POST.get('recording_url', '') or request.POST.get('youtube_url', '')
        recording.published = request.POST.get('published') == 'on'
        recording.required_level = int(request.POST.get('required_level', 0))
        tags_raw = request.POST.get('tags', '')
        recording.tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []

        recording.save()
        return redirect('studio_recording_edit', recording_id=recording.pk)

    context = {
        'recording': recording,
        'form_action': 'edit',
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(recording),
        'notify_url': reverse('studio_recording_notify', kwargs={'recording_id': recording.pk}),
        'announce_url': reverse('studio_recording_announce_slack', kwargs={'recording_id': recording.pk}),
        **notification_action_context('recording', recording),
    }
    return render(request, 'studio/recordings/form.html', context)
