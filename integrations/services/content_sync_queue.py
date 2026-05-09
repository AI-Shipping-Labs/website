"""Shared enqueue service for GitHub content sync tasks."""

from dataclasses import dataclass

from integrations.models import SyncLog
from integrations.services.github import sync_content_source

SYNC_TASK_PATH = 'integrations.services.github.sync_content_source'


@dataclass(frozen=True)
class ContentSyncQueueResult:
    """Structured outcome from enqueueing or running a content sync."""

    ok: bool
    queued: bool
    ran_inline: bool
    source: object
    batch_id: object = None
    message: str = ''
    error: str = ''
    task_id: object = None


def _mark_source_queued(source, batch_id=None):
    """Create the queued SyncLog row and mark the source as queued."""
    previous_status = source.last_sync_status
    queued_log = SyncLog.objects.create(
        source=source,
        batch_id=batch_id,
        status='queued',
    )
    source.last_sync_status = 'queued'
    source.save(update_fields=['last_sync_status', 'updated_at'])
    return previous_status, queued_log


def _clear_queued_state(source, previous_status, queued_log):
    """Undo a queued marker when enqueueing fails before a worker can run."""
    queued_log.delete()
    source.last_sync_status = previous_status
    source.save(update_fields=['last_sync_status', 'updated_at'])


def _enqueue_async_task(source, batch_id=None, force=False, task_name=None):
    """Import django-q lazily so missing django-q can fall back inline."""
    from django_q.tasks import async_task

    kwargs = {
        'force': force,
        'task_name': task_name or f'sync-{source.repo_name}',
    }
    if batch_id is not None:
        kwargs['batch_id'] = batch_id
    return async_task(SYNC_TASK_PATH, source, **kwargs)


def enqueue_content_sync(
    source,
    batch_id=None,
    force=False,
    mark_queued=True,
    task_name=None,
):
    """Queue one content source sync, falling back inline when django-q is absent."""
    queued_marker = None
    if mark_queued:
        queued_marker = _mark_source_queued(source, batch_id=batch_id)

    try:
        task_id = _enqueue_async_task(
            source,
            batch_id=batch_id,
            force=force,
            task_name=task_name,
        )
    except ImportError:
        if queued_marker is not None:
            _clear_queued_state(source, *queued_marker)
        try:
            sync_content_source(source, batch_id=batch_id, force=force)
        except Exception as exc:
            return ContentSyncQueueResult(
                ok=False,
                queued=False,
                ran_inline=True,
                source=source,
                batch_id=batch_id,
                message=f'Sync failed for {source.repo_name}: {exc}',
                error=str(exc),
            )
        return ContentSyncQueueResult(
            ok=True,
            queued=False,
            ran_inline=True,
            source=source,
            batch_id=batch_id,
            message=f'Sync completed for {source.repo_name}',
        )
    except Exception as exc:
        if queued_marker is not None:
            _clear_queued_state(source, *queued_marker)
        return ContentSyncQueueResult(
            ok=False,
            queued=False,
            ran_inline=False,
            source=source,
            batch_id=batch_id,
            message=f'Sync failed for {source.repo_name}: {exc}',
            error=str(exc),
        )

    return ContentSyncQueueResult(
        ok=True,
        queued=True,
        ran_inline=False,
        source=source,
        batch_id=batch_id,
        message=f'Sync queued for {source.repo_name}',
        task_id=task_id,
    )


def enqueue_content_syncs(
    sources,
    batch_id=None,
    force=False,
    mark_queued=True,
):
    """Queue multiple content sources, returning one result per source."""
    return [
        enqueue_content_sync(
            source,
            batch_id=batch_id,
            force=force,
            mark_queued=mark_queued,
            task_name=f'sync-{source.repo_name}',
        )
        for source in sources
    ]
