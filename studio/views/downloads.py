"""Studio views for download/resource management."""

from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.text import slugify

from content.models import Download
from studio.decorators import staff_required
from studio.utils import is_synced, get_github_edit_url


@staff_required
def download_list(request):
    """List all downloadable resources."""
    search = request.GET.get('q', '')

    downloads = Download.objects.all()
    if search:
        downloads = downloads.filter(title__icontains=search)

    return render(request, 'studio/downloads/list.html', {
        'downloads': downloads,
        'search': search,
    })


@staff_required
def download_edit(request, download_id):
    """Edit an existing download (read-only for synced items)."""
    download = get_object_or_404(Download, pk=download_id)
    synced = is_synced(download)

    if request.method == 'POST':
        if synced:
            return HttpResponseForbidden(
                'This content is managed in GitHub. Edit it there.'
            )

        download.title = request.POST.get('title', '').strip()
        download.slug = request.POST.get('slug', '').strip() or slugify(download.title)
        download.description = request.POST.get('description', '')
        download.file_url = request.POST.get('file_url', '')
        download.file_type = request.POST.get('file_type', 'pdf')
        download.file_size_bytes = int(request.POST.get('file_size_bytes', 0) or 0)
        download.cover_image_url = request.POST.get('cover_image_url', '')
        download.published = request.POST.get('published') == 'on'
        download.required_level = int(request.POST.get('required_level', 0))
        tags_raw = request.POST.get('tags', '')
        download.tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []
        download.save()
        return redirect('studio_download_edit', download_id=download.pk)

    context = {
        'download': download,
        'form_action': 'edit',
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(download),
        'notify_url': reverse('studio_download_notify', kwargs={'download_id': download.pk}),
        'announce_url': reverse('studio_download_announce_slack', kwargs={'download_id': download.pk}),
    }
    return render(request, 'studio/downloads/form.html', context)
