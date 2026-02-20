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
from integrations.services.github import sync_content_source

logger = logging.getLogger(__name__)


@staff_member_required
def admin_sync_dashboard(request):
    """Display all content sources with their sync status."""
    sources = ContentSource.objects.all()
    context = {
        'sources': sources,
        'title': 'Content Sync',
    }
    return render(request, 'integrations/admin_sync.html', context)


@staff_member_required
def admin_sync_history(request, source_id):
    """Display sync history for a specific content source."""
    source = get_object_or_404(ContentSource, pk=source_id)
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

    try:
        # Try background task first
        try:
            from django_q.tasks import async_task
            async_task(
                'integrations.services.github.sync_content_source',
                source,
                task_name=f'sync-{source.repo_name}',
            )
            message = f'Sync queued for {source.repo_name}'
        except ImportError:
            sync_content_source(source)
            message = f'Sync completed for {source.repo_name}'

        if request.headers.get('Accept') == 'application/json':
            return JsonResponse({'status': 'ok', 'message': message})

    except Exception as e:
        logger.exception('Error triggering sync for %s', source.repo_name)
        message = f'Sync failed for {source.repo_name}: {e}'
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
    sources = ContentSource.objects.all()

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

    if request.headers.get('Accept') == 'application/json':
        return JsonResponse({
            'status': 'ok',
            'message': f'Sync triggered for {sources.count()} sources',
        })

    return redirect('admin_sync_dashboard')
