"""Studio views for recording management."""

import logging

from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from content.models import Recording
from jobs.tasks import async_task
from studio.decorators import staff_required
from studio.utils import is_synced, get_github_edit_url

logger = logging.getLogger(__name__)


@staff_required
def recording_list(request):
    """List all recordings."""
    search = request.GET.get('q', '')

    recordings = Recording.objects.all()
    if search:
        recordings = recordings.filter(title__icontains=search)

    return render(request, 'studio/recordings/list.html', {
        'recordings': recordings,
        'search': search,
    })


@staff_required
def recording_edit(request, recording_id):
    """Edit an existing recording (read-only for synced items)."""
    recording = get_object_or_404(Recording, pk=recording_id)
    synced = is_synced(recording)

    if request.method == 'POST':
        if synced:
            return HttpResponseForbidden(
                'This content is managed in GitHub. Edit it there.'
            )

        recording.title = request.POST.get('title', '').strip()
        recording.slug = request.POST.get('slug', '').strip() or slugify(recording.title)
        recording.description = request.POST.get('description', '')
        recording.youtube_url = request.POST.get('youtube_url', '')
        recording.published = request.POST.get('published') == 'on'
        recording.required_level = int(request.POST.get('required_level', 0))
        tags_raw = request.POST.get('tags', '')
        recording.tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []

        date_str = request.POST.get('date', '')
        if date_str:
            try:
                from datetime import datetime
                recording.date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        recording.save()
        return redirect('studio_recording_edit', recording_id=recording.pk)

    context = {
        'recording': recording,
        'form_action': 'edit',
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(recording),
        'notify_url': reverse('studio_recording_notify', kwargs={'recording_id': recording.pk}),
        'announce_url': reverse('studio_recording_announce_slack', kwargs={'recording_id': recording.pk}),
    }
    return render(request, 'studio/recordings/form.html', context)


@staff_required
@require_POST
def recording_publish_youtube(request, recording_id):
    """Enqueue a background job to upload a recording from S3 to YouTube."""
    recording = get_object_or_404(Recording, pk=recording_id)

    if not recording.s3_url:
        return JsonResponse(
            {'error': 'Recording has no S3 URL. Upload to S3 first.'},
            status=400,
        )

    if recording.youtube_url:
        return JsonResponse(
            {'error': 'Recording already has a YouTube URL.'},
            status=400,
        )

    try:
        task_id = async_task(
            'jobs.tasks.youtube_upload.upload_recording_to_youtube',
            recording.id,
            max_retries=3,
        )
        logger.info(
            'Enqueued YouTube upload for recording %s (task_id=%s)',
            recording.pk, task_id,
        )
        return JsonResponse({
            'status': 'queued',
            'task_id': str(task_id) if task_id else None,
            'message': 'YouTube upload has been queued.',
        })
    except Exception as e:
        logger.exception(
            'Failed to enqueue YouTube upload for recording %s', recording.pk,
        )
        return JsonResponse({'error': str(e)}, status=500)
