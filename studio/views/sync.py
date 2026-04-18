"""Studio views for content sync management.

Provides:
- /studio/sync/ - Unified sync dashboard with repo-level card and results
- /studio/sync/history/ - Aggregated sync history per batch
- /studio/sync/<source_id>/trigger/ - Trigger sync for a single source
- /studio/sync/all/ - Trigger sync for all sources (with batch_id)
- /studio/sync/<source_id>/status/ - JSON endpoint for polling sync status
"""

import datetime
import logging
import uuid
from collections import OrderedDict

from django.contrib import messages
from django.db.models import Max, Min
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from integrations.models import ContentSource, SyncLog
from integrations.services.github import sync_content_source
from studio.decorators import staff_required
from studio.worker_health import get_worker_status

logger = logging.getLogger(__name__)


def _worker_warning_suffix():
    """Return ' — worker not running, sync will not start until you run `manage.py qcluster`.'

    or empty string if the worker is running. Appended to async-task success
    messages so users know the queue is filling up but nothing is processing.
    """
    info = get_worker_status()
    if info['expect_worker'] and not info['alive']:
        return (
            ' — worker is not running, sync will not start until you run '
            '`manage.py qcluster`.'
        )
    return ''


def _is_not_configured_error(errors):
    """Return True if all errors indicate a missing/not-configured source (e.g. no credentials)."""
    if not errors:
        return False
    not_configured_keywords = ['not configured', 'no credentials', 'not set up', 'missing']
    return all(
        any(kw in str(err).lower() for kw in not_configured_keywords)
        for err in errors
    )


def _aggregate_batch(logs):
    """Aggregate a queryset of SyncLog entries into a batch summary dict.

    The aggregate dict surfaces ``errors_count`` on every per-type row and at
    the batch level so the sync-status pill (template tag
    ``sync_status_pill``) can render ``Completed with N errors`` for partial
    syncs without dipping back into the SyncLog. See issue #245.
    """
    total_created = 0
    total_updated = 0
    total_deleted = 0
    all_errors = []
    per_type = OrderedDict()
    tiers_synced = False
    tiers_count = 0
    overall_status = 'success'

    for log in logs:
        ct = log.source.get_content_type_display()
        if ct not in per_type:
            per_type[ct] = {
                'content_type': log.source.content_type,
                'display_name': ct,
                'created': 0,
                'updated': 0,
                'deleted': 0,
                'status': log.status,
                'errors_count': 0,
                'items_detail': [],
            }
        entry = per_type[ct]
        entry['created'] += log.items_created
        entry['updated'] += log.items_updated
        entry['deleted'] += log.items_deleted
        entry['items_detail'].extend(log.items_detail or [])
        entry['errors_count'] += len(log.errors or [])
        if log.status == 'failed':
            entry['status'] = 'failed'
        elif log.status == 'partial' and entry['status'] != 'failed':
            entry['status'] = 'partial'

        total_created += log.items_created
        total_updated += log.items_updated
        total_deleted += log.items_deleted
        all_errors.extend(log.errors or [])

        if log.tiers_synced:
            tiers_synced = True
            tiers_count = log.tiers_count

        if log.status == 'failed':
            overall_status = 'failed'
        elif log.status == 'partial' and overall_status != 'failed':
            overall_status = 'partial'

    return {
        'total_created': total_created,
        'total_updated': total_updated,
        'total_deleted': total_deleted,
        'errors': all_errors,
        'errors_count': len(all_errors),
        'per_type': list(per_type.values()),
        'tiers_synced': tiers_synced,
        'tiers_count': tiers_count,
        'overall_status': overall_status,
    }


@staff_required
def sync_dashboard(request):
    """Display unified sync dashboard with one card per repo."""
    sources = ContentSource.objects.all()

    # Group sources by repo_name
    repos = OrderedDict()
    for source in sources:
        if source.repo_name not in repos:
            repos[source.repo_name] = {
                'repo_name': source.repo_name,
                'sources': [],
                'is_private': source.is_private,
                'last_synced_at': None,
                'any_running': False,
                'overall_status': None,
            }
        repo = repos[source.repo_name]
        repo['sources'].append(source)
        if source.last_synced_at:
            if repo['last_synced_at'] is None or source.last_synced_at > repo['last_synced_at']:
                repo['last_synced_at'] = source.last_synced_at
        if source.last_sync_status == 'running':
            repo['any_running'] = True
        if source.last_sync_status:
            if source.last_sync_status == 'failed':
                last_log = SyncLog.objects.filter(source=source).order_by('-started_at').first()
                log_errors = (last_log.errors if last_log else []) or []
                if not _is_not_configured_error(log_errors):
                    repo['overall_status'] = 'failed'
            elif source.last_sync_status == 'partial' and repo['overall_status'] != 'failed':
                repo['overall_status'] = 'partial'
            elif source.last_sync_status == 'running':
                repo['overall_status'] = 'running'
            elif repo['overall_status'] is None:
                repo['overall_status'] = source.last_sync_status

    # Get the most recent batch of sync logs for each repo
    for repo in repos.values():
        source_ids = [s.pk for s in repo['sources']]
        latest_logs = SyncLog.objects.filter(
            source_id__in=source_ids,
        ).exclude(status='running').order_by('-started_at')

        # Find the most recent batch_id or timestamp cluster
        if latest_logs.exists():
            newest = latest_logs.first()
            if newest.batch_id:
                # Scope by repo's sources too — a Sync-All batch shares one
                # batch_id across every repo, so without this filter we'd
                # mis-attribute other repos' logs to this card.
                batch_logs = SyncLog.objects.filter(
                    batch_id=newest.batch_id,
                    source_id__in=source_ids,
                )
            else:
                # Fall back to logs from the same source started within 60s
                batch_logs = SyncLog.objects.filter(
                    source_id__in=source_ids,
                    started_at__gte=newest.started_at - datetime.timedelta(seconds=60),
                    started_at__lte=newest.started_at + datetime.timedelta(seconds=60),
                ).exclude(status='running')
            repo['last_batch'] = _aggregate_batch(batch_logs)
        else:
            repo['last_batch'] = None

        # Surface the latest-batch error count on the repo dict so the
        # status pill can render "Completed with N errors" for ``partial``
        # without having to walk into ``last_batch`` from the template.
        repo['overall_errors_count'] = (
            repo['last_batch']['errors_count'] if repo['last_batch'] else 0
        )

    return render(request, 'studio/sync/dashboard.html', {
        'repos': list(repos.values()),
        'sources': sources,
    })


@staff_required
def sync_history(request, source_id=None):
    """Display aggregated sync history per batch."""
    sources = ContentSource.objects.all()
    source_ids = [s.pk for s in sources]

    # Get all sync logs, grouped by batch
    all_logs = SyncLog.objects.filter(
        source_id__in=source_ids,
    ).select_related('source').order_by('-started_at')[:200]

    # Group by batch_id or by timestamp proximity
    batches = []
    seen_batch_ids = set()
    seen_log_ids = set()

    for log in all_logs:
        if log.pk in seen_log_ids:
            continue

        if log.batch_id and log.batch_id not in seen_batch_ids:
            seen_batch_ids.add(log.batch_id)
            batch_logs = SyncLog.objects.filter(
                batch_id=log.batch_id,
            ).select_related('source')
            for bl in batch_logs:
                seen_log_ids.add(bl.pk)
            agg = _aggregate_batch(batch_logs)
            agg['started_at'] = batch_logs.aggregate(
                min_start=Min('started_at'),
            )['min_start']
            agg['finished_at'] = batch_logs.aggregate(
                max_finish=Max('finished_at'),
            )['max_finish']
            agg['batch_id'] = str(log.batch_id)
            agg['log_count'] = batch_logs.count()
            batches.append(agg)
        elif not log.batch_id:
            seen_log_ids.add(log.pk)
            agg = _aggregate_batch([log])
            agg['started_at'] = log.started_at
            agg['finished_at'] = log.finished_at
            agg['batch_id'] = None
            agg['log_count'] = 1
            batches.append(agg)

    return render(request, 'studio/sync/history.html', {
        'batches': batches[:50],
    })


@staff_required
@require_POST
def sync_trigger(request, source_id):
    """Trigger a sync for a single content source.

    Redirects back to ``/studio/sync/`` so the operator stays on the sync
    dashboard and can see the inline indicator update. The flash message
    includes a link to ``/studio/worker/`` for operators who want to watch
    the job land in the queue. See issue #239.
    """
    source = get_object_or_404(ContentSource, pk=source_id)

    try:
        try:
            from django_q.tasks import async_task
            async_task(
                'integrations.services.github.sync_content_source',
                source,
                task_name=f'sync-{source.repo_name}',
            )
            warning = _worker_warning_suffix()
            label = source.repo_name
            if source.content_path:
                label = f'{label} ({source.content_path})'
            base_msg = format_html(
                'Sync queued for {label}. You can see the status '
                '<a href="/studio/worker/" class="underline">here</a>{warning}',
                label=label,
                warning=warning,
            )
            if warning:
                messages.warning(request, base_msg)
            else:
                messages.success(request, base_msg)
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
    """Trigger sync for all content sources with a shared batch_id.

    Redirects back to ``/studio/sync/`` so the operator stays on the sync
    dashboard and watches every per-source row update in place. The flash
    message includes a link to ``/studio/worker/`` for operators who want
    to watch the batch flow through the queue. See issue #239.
    """
    sources = ContentSource.objects.all()
    count = sources.count()
    batch_id = uuid.uuid4()

    for source in sources:
        try:
            try:
                from django_q.tasks import async_task
                async_task(
                    'integrations.services.github.sync_content_source',
                    source,
                    batch_id=batch_id,
                    task_name=f'sync-{source.repo_name}-{source.content_type}',
                )
            except ImportError:
                sync_content_source(source, batch_id=batch_id)
        except Exception:
            logger.exception('Error triggering sync for %s', source.repo_name)

    warning = _worker_warning_suffix()
    base_msg = format_html(
        'Sync queued for {count} source{plural}. You can see the status '
        '<a href="/studio/worker/" class="underline">here</a>{warning}',
        count=count,
        plural='' if count == 1 else 's',
        warning=warning,
    )
    if warning:
        messages.warning(request, base_msg)
    else:
        messages.success(request, base_msg)
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
