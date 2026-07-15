"""Studio views for download/resource management."""

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from content.models import Download
from studio.decorators import staff_required
from studio.services.banner_panel import banner_panel_context
from studio.utils import get_github_edit_url, is_synced, studio_pagination_context
from studio.views.form_helpers import (
    parse_comma_separated_tags,
    reject_synced_content_post,
)


@staff_required
def download_list(request):
    """List all downloadable resources."""
    search = request.GET.get('q', '')

    downloads = Download.objects.all()
    if search:
        downloads = downloads.filter(title__icontains=search)
    pager = studio_pagination_context(request, downloads)

    return render(request, 'studio/downloads/list.html', {
        'downloads': pager['page'].object_list,
        'search': search,
        **pager,
    })


@staff_required
def download_edit(request, download_id):
    """Edit an existing download (read-only for synced items)."""
    download = get_object_or_404(Download, pk=download_id)
    synced = is_synced(download)

    if request.method == 'POST':
        if synced:
            return reject_synced_content_post()

        download.title = request.POST.get('title', '').strip()
        download.slug = request.POST.get('slug', '').strip() or slugify(download.title)
        download.description = request.POST.get('description', '')
        from content.services.download_validation import (
            DownloadMetadataError,
            validate_download_metadata,
        )
        replacement_url = request.POST.get('file_url', '').strip()
        replacement_key = request.POST.get('storage_key', '').strip()
        publish_requested = request.POST.get('published') == 'on'
        try:
            secure_metadata = validate_download_metadata(
                storage_key=replacement_key or download.storage_key,
                file_type=request.POST.get('file_type', 'pdf'),
                file_size_bytes=request.POST.get('file_size_bytes', 0),
                required_level=request.POST.get('required_level', 0),
                asset_mime_type=request.POST.get('asset_mime_type', ''),
            )
        except DownloadMetadataError as exc:
            if publish_requested:
                return render(request, 'studio/downloads/form.html', {
                    'download': download,
                    'form_action': 'edit',
                    'is_synced': synced,
                    'form_error': str(exc),
                    'github_edit_url': get_github_edit_url(download),
                }, status=400)
            secure_metadata = None
        if publish_requested and secure_metadata is not None:
            from content.services.download_delivery import (
                verify_download_object_exists,
            )
            try:
                verify_download_object_exists(secure_metadata['storage_key'])
            except ValueError as exc:
                blocked_reason = (
                    'Private object validation failed; correct the asset '
                    'and try publishing again.'
                )
                Download.objects.filter(pk=download.pk).update(
                    published=False,
                    delivery_blocked_reason=blocked_reason,
                )
                download.published = False
                download.delivery_blocked_reason = blocked_reason
                return render(request, 'studio/downloads/form.html', {
                    'download': download,
                    'form_action': 'edit',
                    'is_synced': synced,
                    'form_error': str(exc),
                    'github_edit_url': get_github_edit_url(download),
                }, status=400)
        if replacement_url:
            download.file_url = replacement_url
        if secure_metadata is not None:
            for field, value in secure_metadata.items():
                setattr(download, field, value)
            if publish_requested:
                download.delivery_blocked_reason = ''
        download.cover_image_url = request.POST.get('cover_image_url', '')
        download.published = publish_requested
        download.tags = parse_comma_separated_tags(request.POST.get('tags', ''))
        download.save()
        return redirect('studio_download_edit', download_id=download.pk)

    context = {
        'download': download,
        'form_action': 'edit',
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(download),
        'notify_url': reverse('studio_download_notify', kwargs={'download_id': download.pk}),
        'announce_url': reverse('studio_download_announce_slack', kwargs={'download_id': download.pk}),
        # Issues #788/#931: banner / social-image panel.
        **banner_panel_context(
            content_type='download',
            record=download,
            regenerate_url_name='studio_download_regenerate_banner',
            upload_url_name='studio_download_upload_banner',
            remove_url_name='studio_download_remove_banner',
            url_kwarg='download_id',
        ),
    }
    return render(request, 'studio/downloads/form.html', context)
