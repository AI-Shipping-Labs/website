"""Studio views for recording CRUD."""

from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.text import slugify

from content.models import Recording
from studio.decorators import staff_required


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
def recording_create(request):
    """Create a new recording."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        slug = request.POST.get('slug', '').strip() or slugify(title)
        description = request.POST.get('description', '')
        date_str = request.POST.get('date', '')
        youtube_url = request.POST.get('youtube_url', '')
        published = request.POST.get('published') == 'on'
        required_level = int(request.POST.get('required_level', 0))
        tags_raw = request.POST.get('tags', '')
        tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []

        date = timezone.now().date()
        if date_str:
            try:
                from datetime import datetime
                date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        recording = Recording.objects.create(
            title=title,
            slug=slug,
            description=description,
            date=date,
            youtube_url=youtube_url,
            published=published,
            required_level=required_level,
            tags=tags,
        )
        return redirect('studio_recording_edit', recording_id=recording.pk)

    return render(request, 'studio/recordings/form.html', {
        'recording': None,
        'form_action': 'create',
    })


@staff_required
def recording_edit(request, recording_id):
    """Edit an existing recording."""
    recording = get_object_or_404(Recording, pk=recording_id)

    if request.method == 'POST':
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

    return render(request, 'studio/recordings/form.html', {
        'recording': recording,
        'form_action': 'edit',
    })
