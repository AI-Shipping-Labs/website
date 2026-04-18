"""Studio views for content sync management.

Provides:
- /studio/sync/ - Unified sync dashboard with repo-level card and results
- /studio/sync/history/ - Aggregated sync history per batch
- /studio/sync/<source_id>/trigger/ - Trigger sync for a single source
- /studio/sync/<repo_name>/trigger-repo/ - Trigger sync for every source
  sharing one repo_name (fan-out under one button, see issue #232)
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


_COURSE_LEVELS = (
    ('course', 'Courses'),
    ('module', 'Modules'),
    ('unit', 'Lessons (units)'),
)


def _build_course_level_breakdown(items_detail):
    """Group ``items_detail`` items by content_type for course-type sources.

    For course syncs the flat ``items_detail`` list mixes courses, modules,
    and units. The dashboard renders a per-level row + expandable list for
    each (issue #224), so we pre-compute the grouping in the aggregator
    rather than re-walking the list in the template.
    """
    levels = OrderedDict()
    for level_key, label in _COURSE_LEVELS:
        levels[level_key] = {
            'level': level_key,
            'label': label,
            'created': 0,
            'updated': 0,
            'deleted': 0,
            'items': [],
        }
    for item in items_detail or []:
        ct = item.get('content_type')
        if ct not in levels:
            continue
        action = item.get('action')
        if action == 'created':
            levels[ct]['created'] += 1
        elif action == 'updated':
            levels[ct]['updated'] += 1
        elif action == 'deleted':
            levels[ct]['deleted'] += 1
        levels[ct]['items'].append(item)
    return list(levels.values())


def _aggregate_batch(logs):
    """Aggregate a queryset of SyncLog entries into a batch summary dict.

    The aggregate dict surfaces ``errors_count`` on every per-type row and at
    the batch level so the sync-status pill (template tag
    ``sync_status_pill``) can render ``Completed with N errors`` for partial
    syncs without dipping back into the SyncLog. See issue #245.

    For ``content_type='course'`` rows, the aggregator also attaches a
    ``course_breakdown`` list — one entry per level (courses, modules,
    units) with its own counts and item list — so the dashboard can render
    the per-level breakdown and expandable changed-pages list (issue #224).
    """
    total_created = 0
    total_updated = 0
    total_unchanged = 0
    total_deleted = 0
    all_errors = []
    per_type = OrderedDict()
    tiers_synced = False
    tiers_count = 0
    # Track the overall status. ``None`` means we haven't seen any logs
    # yet; once we have, ``skipped``/``success`` get demoted by a worse
    # status (failed > partial > success/skipped).
    overall_status = None
    seen_non_skipped = False

    for log in logs:
        ct = log.source.get_content_type_display()
        if ct not in per_type:
            per_type[ct] = {
                'content_type': log.source.content_type,
                'display_name': ct,
                'created': 0,
                'updated': 0,
                'unchanged': 0,
                'deleted': 0,
                'status': log.status,
                'errors_count': 0,
                'items_detail': [],
                # Issue #235: surface the commit SHA each per-type row ran
                # against (or, for skip rows, the SHA we compared HEAD to).
                'commit_sha': log.commit_sha or '',
                'short_commit_sha': log.short_commit_sha,
                'commit_url': log.commit_url,
                'is_skipped': log.status == 'skipped',
            }
        entry = per_type[ct]
        entry['created'] += log.items_created
        entry['updated'] += log.items_updated
        entry['unchanged'] += log.items_unchanged
        entry['deleted'] += log.items_deleted
        entry['items_detail'].extend(log.items_detail or [])
        # ``skipped`` rows store their reason ("HEAD unchanged" or
        # "Sync already in progress") in ``errors`` for compatibility,
        # but those aren't actual errors — don't count them toward
        # the dashboard's red error panel or the "Completed with N
        # errors" pill (issue #235).
        if log.status != 'skipped':
            entry['errors_count'] += len(log.errors or [])
        if log.status == 'failed':
            entry['status'] = 'failed'
        elif log.status == 'partial' and entry['status'] != 'failed':
            entry['status'] = 'partial'

        total_created += log.items_created
        total_updated += log.items_updated
        total_unchanged += log.items_unchanged
        total_deleted += log.items_deleted
        if log.status != 'skipped':
            all_errors.extend(log.errors or [])

        if log.tiers_synced:
            tiers_synced = True
            tiers_count = log.tiers_count

        if log.status == 'failed':
            overall_status = 'failed'
        elif log.status == 'partial' and overall_status != 'failed':
            overall_status = 'partial'
        elif log.status == 'success' and overall_status not in (
            'failed', 'partial',
        ):
            overall_status = 'success'
            seen_non_skipped = True
        elif log.status == 'skipped' and overall_status is None:
            # Stays as 'skipped' unless a later (worse) log overrides.
            overall_status = 'skipped'

    # If every log in the batch was a skip, surface that to the pill;
    # otherwise default to success when nothing worse happened.
    if overall_status is None:
        overall_status = 'success'
    elif overall_status == 'skipped' and seen_non_skipped:
        overall_status = 'success'

    # Compute course-level breakdown (issue #224) for course-type sources.
    for entry in per_type.values():
        if entry['content_type'] == 'course':
            entry['course_breakdown'] = _build_course_level_breakdown(
                entry['items_detail'],
            )

    return {
        'total_created': total_created,
        'total_updated': total_updated,
        'total_unchanged': total_unchanged,
        'total_deleted': total_deleted,
        'errors': all_errors,
        'errors_count': len(all_errors),
        'per_type': list(per_type.values()),
        'tiers_synced': tiers_synced,
        'tiers_count': tiers_count,
        'overall_status': overall_status,
    }


def _build_repos_context():
    """Build the per-repo dashboard payload (cards + last batches).

    Extracted so the auto-refresh fragment endpoint (issue #243) can reuse
    the same aggregation without re-rendering the page chrome.

    Returns a dict with ``repos`` (list of repo dicts) and ``sources`` (the
    flat queryset, kept for callers that still need it).
    """
    # Order by ``repo_name`` (then ``content_type``) so the dashboard is
    # deterministic — relying on insertion order produced flaky card layouts
    # when sources were added in different sequences across environments.
    sources = ContentSource.objects.all().order_by('repo_name', 'content_type')

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

        # Issue #235: expose a sorted list of (source, last_synced_commit)
        # for the per-card SHA strip. Empty when the repo has only one
        # source AND no recorded commit yet — the strip is hidden in that
        # case to keep the card clean for never-synced repos.
        sources_sorted = sorted(repo['sources'], key=lambda s: s.content_type)
        any_synced = any(s.last_synced_commit for s in sources_sorted)
        repo['sources_with_commits'] = sources_sorted if any_synced else []

    repos_list = list(repos.values())
    return {
        'repos': repos_list,
        'sources': sources,
        'any_running': any(r['any_running'] for r in repos_list),
    }


@staff_required
def sync_dashboard(request):
    """Display unified sync dashboard with one card per repo.

    Supports a ``?fragment=status`` query param that returns just the
    per-repo cards partial. Used by the lightweight JS poller (issue #243)
    so a row that finishes syncing flips from ``running`` to its final
    status without the operator having to refresh the page.
    """
    context = _build_repos_context()

    if request.GET.get('fragment') == 'status':
        # Auto-refresh endpoint: just the cards section, no chrome.
        return render(request, 'studio/sync/_repos_section.html', context)

    return render(request, 'studio/sync/dashboard.html', context)


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


def _force_flag(request):
    """Read the ``force`` flag from a Studio sync POST.

    Accepts truthy strings (``"1"``, ``"true"``, ``"on"``) so the form can
    use either a hidden input or a checkbox. The default is False so the
    HEAD-SHA skip check (issue #235) stays opt-out.
    """
    raw = (request.POST.get('force') or '').strip().lower()
    return raw in ('1', 'true', 'on', 'yes')


@staff_required
@require_POST
def sync_trigger(request, source_id):
    """Trigger a sync for a single content source.

    Redirects back to ``/studio/sync/`` so the operator stays on the sync
    dashboard and can see the inline indicator update. The flash message
    includes a link to ``/studio/worker/`` for operators who want to watch
    the job land in the queue. See issue #239.

    If the POST includes ``force=1`` (issue #235's "Force resync" button),
    the sync bypasses the HEAD-SHA skip check.
    """
    source = get_object_or_404(ContentSource, pk=source_id)
    force = _force_flag(request)

    try:
        try:
            from django_q.tasks import async_task
            async_task(
                'integrations.services.github.sync_content_source',
                source,
                force=force,
                task_name=f'sync-{source.repo_name}',
            )
            warning = _worker_warning_suffix()
            label = source.repo_name
            if source.content_path:
                label = f'{label} ({source.content_path})'
            verb = 'Force resync queued' if force else 'Sync queued'
            base_msg = format_html(
                '{verb} for {label}. You can see the status '
                '<a href="/studio/worker/" class="underline">here</a>{warning}',
                verb=verb,
                label=label,
                warning=warning,
            )
            if warning:
                messages.warning(request, base_msg)
            else:
                messages.success(request, base_msg)
        except ImportError:
            sync_content_source(source, force=force)
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
def sync_repo_trigger(request, repo_name):
    """Trigger sync for every ContentSource sharing one ``repo_name``.

    The dashboard renders one ``Sync now`` button per repo card. Clicking it
    fans out an ``async_task`` for every ContentSource with that repo name,
    all sharing one ``batch_id`` so the batch shows up as a single row in
    history and the per-card ``last_batch`` aggregator finds them together.
    See issue #232.
    """
    sources = list(ContentSource.objects.filter(repo_name=repo_name))
    if not sources:
        messages.error(request, f'No content sources configured for {repo_name}.')
        return redirect('studio_sync_dashboard')

    batch_id = uuid.uuid4()
    count = len(sources)
    force = _force_flag(request)

    for source in sources:
        try:
            try:
                from django_q.tasks import async_task
                async_task(
                    'integrations.services.github.sync_content_source',
                    source,
                    batch_id=batch_id,
                    force=force,
                    task_name=f'sync-{source.repo_name}-{source.content_type}',
                )
            except ImportError:
                sync_content_source(source, batch_id=batch_id, force=force)
        except Exception:
            logger.exception('Error triggering sync for %s', source.repo_name)

    warning = _worker_warning_suffix()
    verb = 'Force resync queued' if force else 'Sync queued'
    base_msg = format_html(
        '{verb} for {repo_name} ({count} source{plural}). You can see '
        'the status <a href="/studio/worker/" class="underline">here</a>'
        '{warning}',
        verb=verb,
        repo_name=repo_name,
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
    force = _force_flag(request)

    for source in sources:
        try:
            try:
                from django_q.tasks import async_task
                async_task(
                    'integrations.services.github.sync_content_source',
                    source,
                    batch_id=batch_id,
                    force=force,
                    task_name=f'sync-{source.repo_name}-{source.content_type}',
                )
            except ImportError:
                sync_content_source(source, batch_id=batch_id, force=force)
        except Exception:
            logger.exception('Error triggering sync for %s', source.repo_name)

    warning = _worker_warning_suffix()
    verb = 'Force resync queued' if force else 'Sync queued'
    base_msg = format_html(
        '{verb} for {count} source{plural}. You can see the status '
        '<a href="/studio/worker/" class="underline">here</a>{warning}',
        verb=verb,
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
