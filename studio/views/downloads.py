"""Studio views for download/resource management."""

from django.shortcuts import render, redirect, get_object_or_404
from django.utils.text import slugify

from content.models import Download
from studio.decorators import staff_required


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
def download_create(request):
    """Create a new downloadable resource."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        slug = request.POST.get('slug', '').strip() or slugify(title)
        description = request.POST.get('description', '')
        file_url = request.POST.get('file_url', '')
        file_type = request.POST.get('file_type', 'pdf')
        file_size_bytes = int(request.POST.get('file_size_bytes', 0) or 0)
        cover_image_url = request.POST.get('cover_image_url', '')
        published = request.POST.get('published') == 'on'
        required_level = int(request.POST.get('required_level', 0))
        tags_raw = request.POST.get('tags', '')
        tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []

        download = Download.objects.create(
            title=title,
            slug=slug,
            description=description,
            file_url=file_url,
            file_type=file_type,
            file_size_bytes=file_size_bytes,
            cover_image_url=cover_image_url,
            published=published,
            required_level=required_level,
            tags=tags,
        )
        return redirect('studio_download_edit', download_id=download.pk)

    return render(request, 'studio/downloads/form.html', {
        'download': None,
        'form_action': 'create',
    })


@staff_required
def download_edit(request, download_id):
    """Edit an existing download."""
    download = get_object_or_404(Download, pk=download_id)

    if request.method == 'POST':
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

    return render(request, 'studio/downloads/form.html', {
        'download': download,
        'form_action': 'edit',
    })
