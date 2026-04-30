"""Admin views for content sync management.

Provides:
- /admin/sync/ - Content sources list with sync status and controls
- /admin/sync/<source_id>/history/ - Sync history for a source
- /admin/sync/<source_id>/trigger/ - Trigger sync for a single source
- /admin/sync/all/ - Trigger sync for all sources
"""

import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from integrations.models import ContentSource, SyncLog
from integrations.services.content_sync_queue import (
    enqueue_content_sync,
    enqueue_content_syncs,
)

logger = logging.getLogger(__name__)


def _last_sync_errors_count(source):
    """Number of errors in the most recent SyncLog for ``source`` (0 if none).

    Surfaces the per-source error count to the admin sync templates so the
    status pill can render ``Completed with N errors`` for ``partial`` syncs.
    See issue #245.
    """
    last_log = (
        SyncLog.objects.filter(source=source)
        .order_by('-started_at')
        .only('errors')
        .first()
    )
    if not last_log:
        return 0
    return len(last_log.errors or [])


@staff_member_required
def admin_sync_dashboard(request):
    """Display all content sources with their sync status."""
    sources = list(ContentSource.objects.all())
    for source in sources:
        # Attach the per-source error count so the template can render the
        # ``Completed with N errors`` label without extra DB chatter.
        source.last_errors_count = _last_sync_errors_count(source)
    context = {
        'sources': sources,
        'title': 'Content Sync',
    }
    return render(request, 'integrations/admin_sync.html', context)


@staff_member_required
def admin_sync_history(request, source_id):
    """Display sync history for a specific content source."""
    source = get_object_or_404(ContentSource, pk=source_id)
    source.last_errors_count = _last_sync_errors_count(source)
    logs = SyncLog.objects.filter(source=source)[:50]
    context = {
        'source': source,
        'logs': logs,
        'title': f'Sync History: {source.repo_name}',
    }
    return render(request, 'integrations/admin_sync_history.html', context)


@staff_member_required
@require_POST
def admin_sync_trigger(request, source_id):
    """Trigger a sync for a single content source."""
    source = get_object_or_404(ContentSource, pk=source_id)

    result = enqueue_content_sync(source)
    if result.ok:
        message = result.message
        if request.headers.get('Accept') == 'application/json':
            return JsonResponse({'status': 'ok', 'message': message})
    else:
        logger.error(
            'Error triggering sync for %s: %s',
            source.repo_name,
            result.error,
        )
        message = result.message
        if request.headers.get('Accept') == 'application/json':
            return JsonResponse(
                {'status': 'error', 'message': message},
                status=500,
            )

    return redirect('admin_sync_dashboard')


@staff_member_required
@require_POST
def admin_sync_all(request):
    """Trigger sync for all content sources."""
    sources = list(ContentSource.objects.all())

    results = enqueue_content_syncs(sources)
    for result in results:
        if not result.ok:
            logger.error(
                'Error triggering sync for %s: %s',
                result.source.repo_name,
                result.error,
            )

    if request.headers.get('Accept') == 'application/json':
        return JsonResponse({
            'status': 'ok',
            'message': f'Sync triggered for {len(sources)} sources',
        })

    return redirect('admin_sync_dashboard')
