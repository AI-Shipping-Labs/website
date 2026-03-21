"""Studio views for content sync management.

Provides:
- /studio/sync/ - Content sources list with sync status and controls
- /studio/sync/<source_id>/ - Sync history for a source
- /studio/sync/<source_id>/trigger/ - Trigger sync for a single source
- /studio/sync/all/ - Trigger sync for all sources
- /studio/sync/<source_id>/status/ - JSON endpoint for polling sync status
"""

import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from integrations.models import ContentSource, SyncLog
from integrations.services.github import sync_content_source
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


@staff_required
def sync_dashboard(request):
    """Display all content sources with their sync status."""
    sources = ContentSource.objects.all()
    return render(request, 'studio/sync/dashboard.html', {
        'sources': sources,
    })


@staff_required
def sync_history(request, source_id):
    """Display sync history for a specific content source."""
    source = get_object_or_404(ContentSource, pk=source_id)
    logs = SyncLog.objects.filter(source=source)[:50]
    return render(request, 'studio/sync/history.html', {
        'source': source,
        'logs': logs,
    })


@staff_required
@require_POST
def sync_trigger(request, source_id):
    """Trigger a sync for a single content source."""
    source = get_object_or_404(ContentSource, pk=source_id)

    try:
        try:
            from django_q.tasks import async_task
            async_task(
                'integrations.services.github.sync_content_source',
                source,
                task_name=f'sync-{source.repo_name}',
            )
            messages.success(
                request,
                f'Sync queued for {source.repo_name}'
                + (f' ({source.content_path})' if source.content_path else ''),
            )
        except ImportError:
            sync_content_source(source)
            messages.success(
                request,
                f'Sync completed for {source.repo_name}'
                + (f' ({source.content_path})' if source.content_path else ''),
            )
    except Exception as e:
        logger.exception('Error triggering sync for %s', source.repo_name)
        messages.error(
            request,
            f'Sync failed for {source.repo_name}: {e}',
        )

    return redirect('studio_sync_dashboard')


@staff_required
@require_POST
def sync_all(request):
    """Trigger sync for all content sources."""
    sources = ContentSource.objects.all()
    count = sources.count()

    for source in sources:
        try:
            try:
                from django_q.tasks import async_task
                async_task(
                    'integrations.services.github.sync_content_source',
                    source,
                    task_name=f'sync-{source.repo_name}',
                )
            except ImportError:
                sync_content_source(source)
        except Exception as e:
            logger.exception('Error triggering sync for %s', source.repo_name)

    messages.success(request, f'Sync triggered for {count} sources.')
    return redirect('studio_sync_dashboard')


@staff_required
def sync_status(request, source_id):
    """JSON endpoint returning current sync status for a source (for polling)."""
    source = get_object_or_404(ContentSource, pk=source_id)
    return JsonResponse({
        'id': str(source.pk),
        'last_sync_status': source.last_sync_status,
        'last_synced_at': source.last_synced_at.isoformat() if source.last_synced_at else None,
    })
